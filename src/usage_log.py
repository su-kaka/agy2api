"""
请求日志服务：usage_log 表的写入与统计
- record_usage_log: 请求完成时写入一条日志（不抛异常，不影响主流程）
  写入先进入内存队列，由后台任务批量落库，避免在请求热路径上等待 SQLite 写锁
- extract_usage_tokens: 从 Gemini 响应/流式块中提取 usageMetadata token 计数
"""

import asyncio
import time
import uuid
from typing import Any, Dict, Optional

import config
from log import log

from src.storage.database import get_database


def new_request_id() -> str:
    """生成短请求 ID"""
    return uuid.uuid4().hex[:16]


# ====================== 异步批量写入队列 ======================

# 内存队列：请求热路径只做 append（O(1)），后台任务批量写库
_pending_logs: list = []
_queue_lock = asyncio.Lock()
# 后台批量刷新任务（应用启动后首次写入时惰性创建）
_flush_task: Optional[asyncio.Task] = None
# 批量落库的间隔（秒）；缓冲区满时立即触发刷新
_FLUSH_INTERVAL = 2.0
# 缓冲区上限（超过立即批量落库，防止异常高峰下内存增长）
_MAX_PENDING = 500

_FLUSH_LOOP_STOP = False


async def _flush_pending_logs() -> None:
    """将待写日志批量落库（分批事务，单条失败不影响整批）"""
    global _pending_logs
    async with _queue_lock:
        batch = _pending_logs
        _pending_logs = []
    if not batch:
        return

    database = await get_database()
    for entry in batch:
        try:
            await database.insert_usage_log(entry)
        except Exception as e:
            log.debug(f"请求日志写入失败（不影响主流程）: {e}")


async def _flush_loop() -> None:
    """后台定时批量落库循环"""
    while True:
        await asyncio.sleep(_FLUSH_INTERVAL)
        try:
            await _flush_pending_logs()
        except Exception as e:
            log.debug(f"请求日志批量落库异常: {e}")


def _ensure_flush_loop() -> None:
    """确保后台刷新任务存在（惰性启动，随事件循环生命周期）"""
    global _flush_task
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.get_event_loop().create_task(_flush_loop())


async def flush_usage_logs() -> None:
    """立即落库所有待写日志（应用关闭时调用，避免丢日志）"""
    await _flush_pending_logs()


def _enqueue_log(entry: Dict[str, Any]) -> None:
    """入队一条日志；缓冲区超限时同步触发一次批量落库"""
    global _pending_logs
    _pending_logs.append(entry)
    if len(_pending_logs) >= _MAX_PENDING:
        try:
            # 已在事件循环内：调度立即刷新（不阻塞请求）
            asyncio.get_event_loop().create_task(_flush_pending_logs())
        except Exception:
            pass


async def _write_log(entry: Dict[str, Any]) -> None:
    """日志写入入口：开关校验后进入批量队列"""
    try:
        if not await config.get_usage_log_enabled():
            return
        _ensure_flush_loop()
        _enqueue_log({**entry, "created_at": entry.get("created_at") or time.time()})
    except Exception as e:
        log.debug(f"请求日志入队失败（不影响主流程）: {e}")


def extract_usage_tokens(source: Any) -> Dict[str, int]:
    """
    从 Gemini 响应结构中提取 token 用量。

    兼容:
    - 非流式: {"usageMetadata": {...}} 或 {"response": {"usageMetadata": {...}}}
    - 流式块: data 字符串内的 usageMetadata（通常只在最后一个块出现）

    Gemini 语义（与 PR#432 对齐）:
    - promptTokenCount 含缓存 token（cachedContentTokenCount）
    - completion = candidatesTokenCount + thoughtsTokenCount

    同时兼容 v1internal 新版 Interactions 格式（对齐 Antigravity-Manager CanonicalUsage::from_gemini）:
    - total_input_tokens / total_output_tokens / total_thought_tokens / total_cached_tokens
    """
    prompt_tokens = completion_tokens = total_tokens = cached_tokens = 0

    if isinstance(source, (str, bytes)):
        # 流式 SSE 块：解析 JSON 提取 usageMetadata
        try:
            text = source.decode("utf-8") if isinstance(source, bytes) else source
            if text.startswith("data: "):
                text = text[len("data: "):].strip()
            if not text or text == "[DONE]":
                return {}
            source = __import__("json").loads(text)
        except Exception:
            return {}

    if not isinstance(source, dict):
        return {}

    metadata = source.get("usageMetadata")
    if not isinstance(metadata, dict):
        # 展开包装: {"response": {...}}
        response = source.get("response")
        if isinstance(response, dict):
            metadata = response.get("usageMetadata")
    if not isinstance(metadata, dict):
        return {}

    def _first_int(*keys: str) -> int:
        for key in keys:
            value = metadata.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return 0

    prompt_tokens = _first_int("promptTokenCount", "total_input_tokens", "input_tokens")
    reasoning_tokens = _first_int("thoughtsTokenCount", "total_thought_tokens")
    candidates_tokens = _first_int("candidatesTokenCount", "total_output_tokens")
    completion_tokens = candidates_tokens + reasoning_tokens
    total_tokens = _first_int("totalTokenCount", "total_tokens") or (prompt_tokens + completion_tokens)

    # 缓存命中数：旧版 cachedContentTokenCount / 新版 total_cached_tokens，及 Anthropic 风格明细
    cached_tokens = _first_int(
        "cachedContentTokenCount",
        "cached_content_token_count",
        "total_cached_tokens",
        "cached_tokens",
        "cache_read_input_tokens",
    )
    if not cached_tokens:
        for details_key in ("prompt_tokens_details", "input_tokens_details"):
            details = metadata.get(details_key)
            if isinstance(details, dict):
                value = details.get("cached_tokens")
                if value is not None:
                    try:
                        cached_tokens = int(value)
                    except (TypeError, ValueError):
                        cached_tokens = 0
                    break

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
    }


async def record_usage_log(entry: Dict[str, Any]) -> None:
    """
    写入一条请求日志。任何异常仅记录告警，绝不影响主请求流程。

    日志先进内存队列由后台任务批量落库，不阻塞请求热路径。

    必要字段: model_name, success
    可选字段: api_format, client_ip, request_id, credential_email, is_stream,
              status_code, use_time, prompt_tokens, completion_tokens, total_tokens, cached_tokens, error_message,
              request_log_file（请求体详细日志的磁盘文件索引，空=未记录）
    """
    await _write_log(entry)


async def clear_usage_logs(days: int) -> int:
    """清理 N 天前的日志，返回删除条数"""
    await flush_usage_logs()
    database = await get_database()
    cutoff = time.time() - days * 86400
    return await database.delete_usage_logs_before(cutoff)
