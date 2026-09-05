"""会话级 DevOps 项目切换状态：mcp-session-id → 生效上下文覆盖。

背景：MCP 客户端（如 Claude Code）在 server 配置里用 X-DevOps-Project-ID
固定注入项目；而一个代码库可能对应多个 DevOps 项目（开发项目 + 维护项目）。
switch_project 工具（tools/projects.py）把"当前会话的默认项目上下文"覆盖为
目标项目，该会话后续的工具调用自动作用于目标项目。

解析单点在 ClientRegistry.get()（server.py）：从 headers 读 mcp-session-id
→ 查本 store；命中则用覆盖值整体替换凭据 kwargs 的 4 个项目字段
（project/iteration/module/version）。安全边界：afc_token / base_url
永不可被覆盖——切项目 ≠ 切身份 / 切后端；权限中间件同样经由
ClientRegistry.get 获取 client，权限校验天然按"生效项目"执行。

生命周期与限制：
- 会话结束 / 客户端重连没有回调通知，旧条目靠 LRU 上限兜底；新会话有新
  session id，旧条目无害（不会被命中）；
- LRU 淘汰 = 该会话静默回落 header 默认项目（WARNING 日志）；
- stateless（2026-07-28）/ stdio 客户端不携带 mcp-session-id → resolve
  永远返回 None → 纯 header 模式，行为与现状一致；
- 方法全部为纯同步：asyncio 单线程内原子（与 ClientRegistry 快路径同惯例）。
"""
import logging
from collections import OrderedDict
from typing import Mapping, NamedTuple, Optional

logger = logging.getLogger(__name__)

# 会话 ID header：HTTP 传输下小写到达（Starlette），与 SDK streamable_http
# 的 Mcp-Session-Id 一致；查找统一小写比较以兼容大小写混合的普通 dict
SESSION_ID_HEADER = "mcp-session-id"

# 与 ClientRegistry 的 maxsize（256）对齐：每个被切换的会话至多派生 1 个新
# client 缓存键，两层同步避免会话数超过 client 上限后互相驱逐状态缓存
MAX_SESSION_OVERRIDES = 256


class SessionContext(NamedTuple):
    """会话级生效上下文覆盖：4 个项目字段整体替换（缺省字段重置为空）。

    iteration/module/version 仅在创建工作项时消费；切换项目时旧项目的
    值对新项目无意义，故未显式传新值的一律置空。
    """

    project_id: str
    iteration_id: str = ""
    module_id: str = ""
    version_id: str = ""


def session_id_from_headers(headers: Mapping[str, str]) -> str:
    """从请求 headers 提取会话 ID（大小写不敏感）；无则返回空串。"""
    for k, v in headers.items():
        if str(k).lower() == SESSION_ID_HEADER:
            return str(v)
    return ""


class SessionContextStore:
    """mcp-session-id → SessionContext 的有界 LRU 存储。"""

    def __init__(self, maxsize: int = MAX_SESSION_OVERRIDES):
        self._overrides: OrderedDict[str, SessionContext] = OrderedDict()
        self._maxsize = maxsize

    def resolve(self, headers: Mapping[str, str]) -> Optional[SessionContext]:
        """按请求 headers 解析会话覆盖；未切换 / 无会话 ID → None（回落 header 模式）。"""
        sid = session_id_from_headers(headers)
        if not sid:
            return None
        override = self._overrides.get(sid)
        if override is not None:
            self._overrides.move_to_end(sid)
        return override

    def set(self, session_id: str, override: SessionContext) -> None:
        """记录会话覆盖；LRU 超限时淘汰最旧会话（其将回落 header 默认项目）。"""
        old = self._overrides.get(session_id)
        self._overrides[session_id] = override
        self._overrides.move_to_end(session_id)
        logger.info(
            f"会话 {session_id[:8]}… 切换默认项目: "
            f"{old.project_id if old else '(header默认)'} → {override.project_id}"
        )
        while len(self._overrides) > self._maxsize:
            evicted_sid, evicted = self._overrides.popitem(last=False)
            logger.warning(
                f"会话覆盖数超出上限 {self._maxsize}，淘汰会话 {evicted_sid[:8]}…"
                f"（项目 {evicted.project_id}），该会话将回落 header 默认项目"
            )

    def clear(self, session_id: str) -> bool:
        """清除会话覆盖（恢复 header 默认项目）。返回清除前是否存在。"""
        existed = self._overrides.pop(session_id, None) is not None
        if existed:
            logger.info(f"会话 {session_id[:8]}… 重置为 header 默认项目")
        return existed

    def clear_all(self) -> None:
        """清空全部会话覆盖（服务关停时调用）。"""
        self._overrides.clear()


# 模块级单例：server.py（ClientRegistry.get 自动解析）与 tools/projects.py
# （switch_project 写入/清除）共用同一份状态
sessions = SessionContextStore()
