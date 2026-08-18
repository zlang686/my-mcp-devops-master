"""MCP 服务器公共层：MCPServer 实例（mcp v2）、凭据键控的 DevOpsClient 全局注册表、权限中间件注册。

各业务域工具模块（tools/）从本模块导入 mcp 与 get_client，
通过 @mcp.tool 装饰器在 import 时完成注册；server.py 不依赖 tools/，无循环导入。

mcp v2 的 lifespan 是全局的（startup 进入一次，不再按会话），
原“每会话一个 DevOpsClient”改为模块级 ClientRegistry：
传输层与状态层分离——全进程共享 1 个 httpx 连接池 + 1 个全局并发闸门
（连接数/后端并发与用户数解耦），按凭据 6 元组只缓存轻量状态
（配置、已验证的用户信息、权限码），LRU 仅限内存占用。
"""
import asyncio
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Mapping, Optional

import httpx
from mcp.server.mcpserver import Context, MCPServer

from devops_client import MAX_CONCURRENT_REQUESTS, REQUEST_TIMEOUT, DevOpsClient

logger = logging.getLogger(__name__)

# Header 名称定义：MCP 客户端连接时通过这些 headers 传递 DevOps 配置。
# HTTP 传输下 header 名以小写到达（Starlette Headers 大小写不敏感），查找统一用小写；
# 报错信息中保留展示大小写。
_REQUIRED_HEADERS = {
    "base_url": ("x-devops-base-url", "X-DevOps-Base-URL"),
    "afc_token": ("x-devops-afctoken", "X-DevOps-afcToken"),
    "project_id": ("x-devops-project-id", "X-DevOps-Project-ID")
}
_OPTIONAL_HEADERS = {
    "iteration_id": ("x-devops-iteration-id", "X-DevOps-Iteration-ID"),
    "module_id": ("x-devops-module-id", "X-DevOps-Module-ID"),
    "version_id": ("x-devops-version-id", "X-DevOps-Version-ID"),
}


class ClientRegistry:
    """按凭据键控的 DevOpsClient 注册表 + 全进程共享传输层。

    传输/状态分离：
    - 共享 1 个 httpx.AsyncClient 连接池（认证是每请求 header，连接与凭据无关）
      与 1 个全局 Semaphore —— 无论多少用户，对后端的并发严格
      ≤ MAX_CONCURRENT_REQUESTS，连接数不随用户数增长；
    - 以 (base_url, afc_token, project_id, iteration_id, module_id, version_id)
      为键缓存轻量状态（配置 + UserInfo + 权限码，每条仅几 KB），双检锁复用；
    - LRU 上限只防内存增长：淘汰即丢弃（无连接可关），>上限用户不会引起
      连接/权限接口抖动，仅其状态缓存需重建（重跑一次 verify + 权限拉取）。
    """

    def __init__(self, maxsize: int = 256):
        self._clients: OrderedDict[tuple, DevOpsClient] = OrderedDict()
        self._lock = asyncio.Lock()
        self._maxsize = maxsize
        # 共享传输层（懒创建，首个客户端构造时建立）
        self._http: Optional[httpx.AsyncClient] = None
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    def _transport(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self._http

    async def get(self, headers: Mapping[str, str]) -> DevOpsClient:
        """按 headers 中的凭据获取（必要时构造并验证）DevOpsClient。

        Raises:
            ValueError: 缺少必填 header（X-DevOps-Base-URL / X-DevOps-afcToken / X-DevOps-Project-ID）
            RuntimeError / httpx.HTTPError: token 校验失败（verify_token 抛出）
        """
        kwargs: dict[str, str] = {}
        # 归一化为小写键：Starlette Headers 本身大小写不敏感（键为小写），
        # 但普通 dict（测试/内部构造）可能携带大小写混合键，统一转换以兼容两者
        normalized = {str(k).lower(): v for k, v in headers.items()}
        for field, (lookup, display) in _REQUIRED_HEADERS.items():
            value = normalized.get(lookup)
            if not value:
                raise ValueError(f"missing required header {display}")
            kwargs[field] = value
        for field, (lookup, _) in _OPTIONAL_HEADERS.items():
            kwargs[field] = normalized.get(lookup, "")

        key = (
            kwargs["base_url"], kwargs["afc_token"], kwargs["project_id"],
            kwargs["iteration_id"], kwargs["module_id"], kwargs["version_id"],
        )

        # 快路径：纯同步操作在 asyncio 中原子，无锁命中即返回
        client = self._clients.get(key)
        if client is not None:
            self._clients.move_to_end(key)
            return client

        # 慢路径：双检锁避免并发首调重复构造/重复校验 token
        async with self._lock:
            client = self._clients.get(key)
            if client is not None:
                self._clients.move_to_end(key)
                return client
            client = DevOpsClient(
                **kwargs, http_client=self._transport(), semaphore=self._semaphore,
            )
            await client.verify_token()
            self._clients[key] = client
            logger.info(f"已构造 DevOpsClient（注册表当前 {len(self._clients)} 个）")
            # 超额淘汰最久未使用者的状态缓存（共享连接池不受影响）
            while len(self._clients) > self._maxsize:
                self._clients.popitem(last=False)
            return client

    async def aclose_all(self) -> None:
        """关闭共享连接池并清空状态缓存（服务关停时调用）。"""
        async with self._lock:
            self._clients.clear()
            http, self._http = self._http, None
        if http is not None:
            try:
                await http.aclose()
            except Exception:
                logger.exception("关闭共享 HTTP 连接池失败")


# 模块级单例：middleware 与工具层共用同一注册表
_registry = ClientRegistry()


async def get_client(ctx: Context) -> DevOpsClient:
    """从请求上下文获取（必要时构造）DevOpsClient。

    读取请求 HTTP headers 中的 DevOps 配置，经全局 ClientRegistry
    按凭据复用已验证的客户端（含已校验的 afc_token 与用户信息）。
    """
    headers = ctx.headers
    if headers is None:
        raise ValueError("当前传输不携带 HTTP headers，无法获取 DevOps 配置")
    return await _registry.get(headers)


# v2 lifespan 全局仅一次：借 shutdown 时机关闭共享连接池并清空凭据状态缓存
@asynccontextmanager
async def _shutdown_lifespan(app: MCPServer):
    try:
        yield {}
    finally:
        await _registry.aclose_all()


# 初始化 MCP 服务器（mcp v2：MCPServer 取代 FastMCP）
mcp: MCPServer = MCPServer("devops-mcp-master", lifespan=_shutdown_lifespan)

# 注册工具权限中间件（v2 低层 middleware，官方标注 provisional）。
# 置底导入：permissions.middleware 在函数体内延迟回导 _registry，此处在
# _registry/mcp 就绪后完成注册，两个模块任意顺序导入均不成环。
from permissions import permission_middleware  # noqa: E402

mcp.middleware.append(permission_middleware)
