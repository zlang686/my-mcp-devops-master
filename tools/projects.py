"""项目/会话域工具：列出当前用户可访问的项目、会话内切换默认项目。

配套关系：list_projects 供发现可选项目（get_project /
/pm/projects/actions/querybyuser，按 token 自限），switch_project 把
当前会话的默认项目上下文写入 session_context.sessions（覆盖解析收敛在
ClientRegistry.get，见 server.py / session_context.py）。
"""
import logging
from typing import Annotated, Any, Dict, List, Optional

from mcp.server.mcpserver import Context
from pydantic import Field

from server import _registry, get_client, mcp
from session_context import SessionContext, session_id_from_headers, sessions

logger = logging.getLogger(__name__)

# projectType → 中文说明（实测返回 D/M；未知值原样保留）
_PROJECT_TYPE_NAMES = {"D": "开发项目", "M": "维护项目"}


def _parse_project_items(raw: Any) -> Optional[List[Dict[str, Any]]]:
    """防御性解析 get_project() 响应：实测为 {"data": [...]} 分页包装；
    兼容裸 list。形状异常返回 None（不猜测）。"""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return None


def _reshape_projects(
    items: List[Dict[str, Any]], current_project_id: str
) -> Dict[str, Any]:
    """重塑项目列表为精简形状（dict 包装，禁止裸 list）。

    project_id 两侧 str() 强转比较：header/store 里是字符串，后端返回
    的 projectId 实测也是字符串，但强转防御 JSON 数值化的可能。
    """
    reshaped = []
    for project in items:
        pid = str(project.get("projectId", ""))
        ptype = str(project.get("projectType") or "")
        reshaped.append({
            "project_id": pid,
            "project_code": project.get("projectCode"),
            "project_name": project.get("projectName"),
            "project_type": ptype,
            "project_type_name": _PROJECT_TYPE_NAMES.get(ptype, ptype),
            "project_status": project.get("projectStatus"),
            "is_current": pid == str(current_project_id),
        })
    return {"total": len(reshaped), "items": reshaped}


@mcp.tool(structured_output=False, description="""查询当前登录用户可访问的全部 DevOps 项目（含开发项目与维护项目）。

【用途】需要切换工作项目时，先调用本工具获取目标项目的 project_id（数值型字符串，如 "341"），再调用 switch_project 完成切换。

【返回】{"total": 数量, "items": [{"project_id", "project_code", "project_name", "project_type", "project_type_name"(开发项目/维护项目), "project_status", "is_current"}]}，其中 is_current=true 为当前生效项目（header 默认或会话切换后）。
""")
async def list_projects(ctx: Context) -> Dict[str, Any]:
    """列出当前用户可访问的项目（/pm/projects/actions/querybyuser）。"""
    logger.info("查询当前用户可访问的项目列表")
    try:
        client = await get_client(ctx)
        raw = await client.get_project()
        items = _parse_project_items(raw)
        if items is None:
            return {
                "error": (
                    f"项目列表接口返回形状异常（期望列表或 dict 包装的 data 列表，"
                    f"实际 {type(raw).__name__}），后端接口可能已变化"
                )
            }
        return _reshape_projects(items, client.project_id)
    except Exception as e:
        logger.exception("查询项目列表失败")
        return {"error": f"查询项目列表失败：{type(e).__name__}: {e}"}


@mcp.tool(structured_output=False, description="""会话内切换默认 DevOps 项目（只影响当前会话，不改 MCP 客户端 header 配置，也不影响其他会话）。

【使用流程】先 list_projects 获取目标项目的 project_id（数值型字符串），再调用本工具；切换成功后本会话后续的工具调用（查询/创建工作项、测试用例等）默认作用于目标项目。

【参数】
- project_id：目标项目 ID（来自 list_projects）；传空字符串 "" 表示重置回 X-DevOps-Project-ID header 配置的默认项目。
- iteration_id / module_id / version_id：可选上下文，仅创建工作项时使用；不传则重置为空（原项目的迭代/模块/版本对新项目无意义），需要时传目标项目的对应 ID。

【重要语义】
- 切换是会话级的：客户端重连或新会话后自动恢复 header 默认项目，届时需重新切换；
- 切换前校验目标项目可达性与权限（token 校验、权限接口、项目列表），失败则拒绝且不改变现状；
- 按工作项 ID 直接操作的工具（如 add_workitem_comment）需传工作项所属项目的 project_id 参数，不受本切换影响。

【返回】成功 {"status": "switched"/"reset", "project_id", "project_name"?, "iteration_id", "module_id", "version_id", "note"}；失败 {"error": ...}（可选附带 available_projects）。
""")
async def switch_project(
    ctx: Context,
    project_id: Annotated[str, Field(description="目标项目ID（list_projects 返回的数值型字符串）；传空表示重置回 header 默认项目")] = "",
    iteration_id: Annotated[str, Field(description="目标项目迭代ID，可选，不传则置空")] = "",
    module_id: Annotated[str, Field(description="目标项目模块ID，可选，不传则置空")] = "",
    version_id: Annotated[str, Field(description="目标项目版本ID，可选，不传则置空")] = "",
) -> Dict[str, Any]:
    """会话内切换默认项目上下文（覆盖 4 个项目字段；重连自动回落 header 模式）。"""
    try:
        headers = ctx.headers
        if headers is None:
            return {"error": "当前传输不携带 HTTP headers，无法获取 DevOps 配置"}
        sid = session_id_from_headers(headers)
        if not sid:
            return {
                "error": (
                    "当前客户端无会话 ID（stateless 或 stdio 传输），不支持会话内切换；"
                    "请在 MCP 客户端配置 X-DevOps-Project-ID header 指定项目"
                )
            }

        # 重置：清除会话覆盖，回落 header 默认项目
        if not project_id:
            had = sessions.clear(sid)
            client = await get_client(ctx)  # store 已清，自动回落 header 模式
            note = (
                "本会话已恢复 X-DevOps-Project-ID header 默认项目"
                if had
                else "本会话本就未切换（当前即 header 默认项目）"
            )
            return {"status": "reset", "project_id": client.project_id, "note": note}

        # 试构造目标项目 client：显式 override 完全取代旧值；verify 失败
        # 不入注册表缓存、不写会话覆盖——现状不变
        target = SessionContext(project_id, iteration_id, module_id, version_id)
        try:
            client = await _registry.get(headers, override=target)
        except Exception as e:
            return {"error": f"切换失败：目标项目不可用或凭据无效（{type(e).__name__}: {e}）"}

        # fail-closed 权限可达性：目标项目权限码拉不到即拒绝切换
        # （成功则顺带预热目标项目的权限缓存）
        try:
            await client.get_permissions()
        except Exception as e:
            return {
                "error": (
                    f"切换失败：无法获取目标项目权限（项目不存在或无访问权限；"
                    f"{type(e).__name__}: {e}）"
                )
            }

        # 可达性硬校验（best-effort）：目标项目应在当前用户项目列表中；
        # 形状异常时跳过（logger.warning），由上方两级校验兜底
        raw = await client.get_project()
        items = _parse_project_items(raw)
        project_name = None
        if items is None:
            logger.warning(f"项目列表返回形状异常，跳过可达性硬校验（project_id={project_id}）")
        else:
            match = next(
                (p for p in items if str(p.get("projectId", "")) == str(project_id)), None
            )
            if match is None:
                # 展示 header 默认项目为 current，便于对比选择
                default_pid = next(
                    (str(v) for k, v in headers.items()
                     if str(k).lower() == "x-devops-project-id"),
                    "",
                )
                return {
                    "error": f"目标项目 {project_id} 不在当前用户可访问项目列表中",
                    "available_projects": _reshape_projects(items, default_pid),
                }
            project_name = match.get("projectName")

        sessions.set(sid, target)
        logger.info(f"会话 {sid[:8]}… 已切换默认项目 → {project_id}({project_name})")
        return {
            "status": "switched",
            "session_id_head": sid[:8],
            "project_id": project_id,
            "project_name": project_name,
            "iteration_id": iteration_id,
            "module_id": module_id,
            "version_id": version_id,
            "note": (
                "本会话后续工具调用默认作用于该项目；iteration/module/version 已按传入值生效"
                "（未传则置空）。客户端重连或新会话后自动恢复 X-DevOps-Project-ID 默认项目，"
                "届时需重新切换。按工作项 ID 操作的工具（如 add_workitem_comment）仍需传"
                "工作项所属项目的 project_id 参数。"
            ),
        }
    except Exception as e:
        logger.exception("切换项目失败")
        return {"error": f"切换项目失败：{type(e).__name__}: {e}"}
