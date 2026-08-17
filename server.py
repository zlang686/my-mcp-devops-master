"""MCP 服务器公共层：FastMCP 实例、会话生命周期、DevOpsClient 构造。

各业务域工具模块（tools/）从本模块导入 mcp 与 get_client，
通过 @mcp.tool 装饰器在 import 时完成注册；server.py 不依赖 tools/，无循环导入。
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

from mcp.server.fastmcp import Context, FastMCP

from devops_client import DevOpsClient

logger = logging.getLogger(__name__)

# Header 名称定义：MCP 客户端连接时通过这些 headers 传递 DevOps 配置
_REQUIRED_HEADERS = {
    "base_url": "X-DevOps-Base-URL",
    "afc_token": "X-DevOps-afcToken",
}
_OPTIONAL_HEADERS = {
    "project_id": "X-DevOps-Project-ID",
    "iteration_id": "X-DevOps-Iteration-ID",
    "module_id": "X-DevOps-Module-ID",
    "version_id": "X-DevOps-Version-ID",
}


async def get_client(ctx: Context) -> DevOpsClient:
    """从会话上下文中获取（必要时构造）DevOpsClient。

    首次调用时从 HTTP headers 读取配置并构造客户端，缓存到 lifespan_context；
    后续调用直接复用（含已校验的 afc_token 与用户信息）。
    """
    lifespan_state = ctx.request_context.lifespan_context
    # 已构造：直接返回（同会话复用，避免重复校验 token）
    if lifespan_state.get("client") is not None:
        return lifespan_state["client"]

    # 并发保护：同会话内多工具并发首次调用时避免重复构造
    async with lifespan_state["lock"]:
        if lifespan_state.get("client") is not None:
            return lifespan_state["client"]

        headers = ctx.request_context.request.headers
        kwargs: Dict[str, str] = {}
        for field, header_name in _REQUIRED_HEADERS.items():
            value = headers.get(header_name)
            if not value:
                raise ValueError(f"missing required header {header_name}")
            kwargs[field] = value
        for field, header_name in _OPTIONAL_HEADERS.items():
            kwargs[field] = headers.get(header_name, "")

        client = DevOpsClient(**kwargs)
        await client.verify_token()
        lifespan_state["client"] = client
        return client


# 初始化 MCP 服务器
# 每个客户端连接（mcp-session-id）对应一次 lifespan 进入/退出，
# 在此处为每个会话懒构造独立的 DevOpsClient。
@asynccontextmanager
async def app_lifespan(app: FastMCP):
    # 会话结束时关闭 DevOpsClient 底层的 HTTP 连接池
    state: Dict[str, Any] = {"client": None, "lock": asyncio.Lock()}
    try:
        yield state
    finally:
        client = state.get("client")
        if client is not None:
            await client.aclose()


mcp: FastMCP = FastMCP("devops-mcp-master", lifespan=app_lifespan)
