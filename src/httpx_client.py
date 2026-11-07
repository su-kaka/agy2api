"""
通用的HTTP客户端模块
为所有需要使用httpx的模块提供统一的客户端配置和方法
保持通用性，不与特定业务逻辑耦合

- 共享 AsyncClient（按代理配置指纹缓存），复用 TCP/TLS 连接，
  避免每请求新建客户端造成的握手开销
- 代理配置变化（面板热更新）时指纹变化，旧客户端惰性关闭重建
- 超时等请求级参数在每次请求时传入，不受客户端实例缓存影响
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx
import json
from fastapi import Response

from config import get_proxy_config
from log import log

# ====================== 共享客户端 ======================

# (代理指纹, AsyncClient) 列表；正常情况下最多两个条目（无代理/有代理）
_shared_clients: List[Tuple[str, httpx.AsyncClient]] = []
_shared_clients_lock = asyncio.Lock()


def _client_fingerprint(proxy_url: Optional[str]) -> str:
    """客户端指纹：代理 URL 不同则不能复用同一客户端"""
    return proxy_url or ""


async def _build_client(proxy_url: Optional[str]) -> httpx.AsyncClient:
    """按当前代理配置构建客户端"""
    client_kwargs: Dict[str, Any] = {"timeout": None}
    if proxy_url:
        client_kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**client_kwargs)


async def _safe_aclose(client: httpx.AsyncClient) -> None:
    """安全关闭客户端，异常仅告警"""
    try:
        await client.aclose()
    except Exception as e:
        log.warning(f"关闭旧 httpx 客户端失败: {e}")


async def _get_shared_client(**kwargs) -> httpx.AsyncClient:
    """
    获取共享的 AsyncClient（按代理指纹缓存复用）。

    调用方不得关闭该客户端（由模块统一管理生命周期，
    web.py 应用关闭时调用 close_shared_clients）。
    """
    proxy_url = await get_proxy_config()
    fingerprint = _client_fingerprint(proxy_url)

    # 快路径：指纹匹配直接复用（无锁读）
    for fp, client in _shared_clients:
        if fp == fingerprint:
            return client

    # 慢路径：创建或重建客户端
    async with _shared_clients_lock:
        # 双重检查（并发下可能已被其他请求创建）
        for fp, client in _shared_clients:
            if fp == fingerprint:
                return client

        # 代理热更新：指纹不匹配的旧客户端移出并异步关闭
        stale: List[httpx.AsyncClient] = []
        kept: List[Tuple[str, httpx.AsyncClient]] = []
        for fp, client in _shared_clients:
            if fp == fingerprint:
                kept.append((fp, client))
            else:
                stale.append(client)
        _shared_clients[:] = kept

        new_client = await _build_client(proxy_url)
        _shared_clients.append((fingerprint, new_client))

        # 惰性关闭旧客户端，不阻塞当前请求
        for old in stale:
            try:
                asyncio.create_task(_safe_aclose(old))
            except Exception as e:
                log.warning(f"调度旧客户端关闭失败: {e}")

        return new_client


async def close_shared_clients() -> None:
    """关闭所有共享客户端（应用关闭时调用）"""
    async with _shared_clients_lock:
        clients = [c for _, c in _shared_clients]
        _shared_clients.clear()
    for client in clients:
        await _safe_aclose(client)


# ====================== 客户端获取（兼容旧接口） ======================

@asynccontextmanager
async def get_client(
    timeout: float = 30.0, **kwargs
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """获取配置好的异步HTTP客户端（共享复用）"""
    yield await _get_shared_client()


@asynccontextmanager
async def get_streaming_client(
    timeout: float = None, **kwargs
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """获取用于流式请求的HTTP客户端（共享复用）"""
    yield await _get_shared_client()


# ====================== 异步请求方法 ======================

async def get_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """通用异步GET请求"""
    client = await _get_shared_client()
    return await client.get(url, headers=headers, timeout=timeout, **kwargs)


async def post_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 900.0,
    **kwargs,
) -> httpx.Response:
    """通用异步POST请求"""
    client = await _get_shared_client()
    return await client.post(
        url, data=data, json=json, headers=headers, timeout=timeout, **kwargs
    )


# 调试用：设为 True 时所有流式请求都返回 429
_MOCK_STREAM_429 = False

async def stream_post_async(
    url: str,
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    """流式异步POST请求"""
    if _MOCK_STREAM_429:
        log.warning(f"[MOCK] stream_post_async: 返回模拟429错误")
        yield Response(
            content=json.dumps({"error": {"code": 429, "message": "mock rate limit", "status": "RESOURCE_EXHAUSTED"}}),
            status_code=429,
        )
        return

    client = await _get_shared_client()
    async with client.stream(
        "POST", url, json=body, headers=headers, **kwargs
    ) as r:
        # 错误直接返回
        if r.status_code != 200:
            yield Response(await r.aread(), r.status_code, dict(r.headers))
            return

        # 如果native=True，直接返回bytes流
        if native:
            async for chunk in r.aiter_bytes():
                yield chunk
        else:
            # 通过aiter_lines转化成str流返回
            async for line in r.aiter_lines():
                yield line

