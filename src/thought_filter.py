"""
思维链片段过滤。

后端不遵守 thinkingConfig.includeThoughts 时，在响应侧剔除 thought:true 的片段，
使“返回思维链到前端”开关在关闭时依然生效。
"""
import json
from typing import Any, Dict, Union


def strip_thought_parts(payload: Dict[str, Any]) -> bool:
    """移除 candidates[].content.parts[] 中 thought==true 的片段，返回是否发生改动。"""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return False
    changed = False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        kept = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]
        if len(kept) != len(parts):
            content["parts"] = kept
            changed = True
    return changed


def _target_payload(data: Any) -> Any:
    """取出真正含 candidates 的载荷，兼容 {"response": {...}} 包装。"""
    if isinstance(data, dict) and isinstance(data.get("response"), dict) and "candidates" not in data:
        return data["response"]
    return data


def filter_sse_chunk(chunk: Union[str, bytes]) -> Union[str, bytes]:
    """过滤单条 Gemini SSE data 块的思考片段；无法解析或无需过滤时原样返回（保持 bytes/str 类型）。"""
    if isinstance(chunk, bytes):
        try:
            text = chunk.decode("utf-8")
        except Exception:
            return chunk
    elif isinstance(chunk, str):
        text = chunk
    else:
        return chunk

    stripped = text.strip()
    if not stripped.startswith("data:"):
        return chunk
    body = stripped[len("data:"):].strip()
    if not body or body == "[DONE]":
        return chunk
    try:
        data = json.loads(body)
    except Exception:
        return chunk

    target = _target_payload(data)
    if not isinstance(target, dict) or not strip_thought_parts(target):
        return chunk

    out = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
    return out.encode("utf-8") if isinstance(chunk, bytes) else out


def filter_response_bytes(content: bytes) -> bytes:
    """过滤非流式完整响应体中的思考片段；无法解析或无需过滤时原样返回。"""
    if not content:
        return content
    try:
        data = json.loads(content.decode("utf-8"))
    except Exception:
        return content
    target = _target_payload(data)
    if not isinstance(target, dict) or not strip_thought_parts(target):
        return content
    return json.dumps(data, ensure_ascii=False).encode("utf-8")
