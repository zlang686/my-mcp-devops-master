"""工具权限中间件：按当前用户权限码控制 MCP 工具的访问。

拦截 tools/call 请求，从请求 HTTP headers 构造/复用 DevOpsClient，
调用 GET /api/devops/uc/permissions/employees 拉取当前用户权限码列表，
与 TOOL_PERMISSIONS 映射表比对，不匹配则拒绝执行。

权限按"生效项目"校验：client 经 ClientRegistry.get 获取，会话内
switch_project 切换后自动解析为目标项目的 client（见 session_context.py），
权限码随之按目标项目拉取比对——不会出现按旧项目查权限、往新项目写入。

策略（用户已确认）：
- 权限码按凭据缓存一次（DevOpsClient.get_permissions 双检锁）；
- 权限接口失败/返回形状异常 → fail-closed 拒绝；
- 缺 X-DevOps-Project-ID → fail-closed 拒绝并提示配置该 header；
- 未配置映射的工具默认放行；
- 拒绝以正常工具结果返回 JSON 文本 {"error", "required_permission"}，
  不抛异常、不置 isError，与现有工具层错误契约一致。

注册方式：mcp v2 低层 middleware（官方标注 provisional，2.x 小版本可能变动），
注册点收敛在 server.py 底部一行。
"""
import json
import logging

import mcp_types
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

logger = logging.getLogger(__name__)

# 工具名 → 所需权限码（DevOps 平台权限接口返回的权限码）
#
# 有意不映射的工具（未映射 = 放行）：
# - list_projects / switch_project：权限码均为项目内业务权限，无对应码；
#   前者只读且按 token 自限（只返回本人可访问项目）；后者自身 fail-closed
#   （verify_token + get_permissions + 项目列表可达性三重校验，失败拒绝
#   且不改变现状），且切换后的业务调用仍经本中间件按目标项目校验。
TOOL_PERMISSIONS: dict[str, str] = {
    # 工作项看板
    "get_workitem_list": "project_kanban_workitem_create",
    "create_workitem": "project_kanban_workitem_create",
    "add_workitem_comment": "project_kanban_workitem_edit",
    "get_next_workitem_status_list": "project_kanban_workitem_edit",
    "change_workitem_status": "project_kanban_workitem_edit",
    "update_workitem_description": "project_kanban_workitem_edit",
    "get_workitem_details": "project_kanban_workitem_process",
    # 附件（跟随工作项详情权限）
    "get_attachment_preview": "project_kanban_workitem_process",
    "get_attachment_chunk": "project_kanban_workitem_process",
    "get_attachment_resource": "project_kanban_workitem_process",
    "get_attachment_image": "project_kanban_workitem_process",
    # 测试用例
    "create_testcase_group": "project_testcase_create",
    "get_testcase_groups": "project_testcase_create",
    "create_testcase": "project_testcase_group_create",
    "get_testcase_list": "project_testcase_group_create",
}


def _deny(reason: str, required_permission: str) -> mcp_types.CallToolResult:
    """构造拒绝结果：正常工具输出（非 isError）的 JSON 文本，与工具层错误契约一致。"""
    payload = {"error": reason, "required_permission": required_permission}
    return mcp_types.CallToolResult(
        is_error=True,content=[mcp_types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]
    )


async def permission_middleware(
    ctx: ServerRequestContext, call_next: CallNext
) -> HandlerResult:
    """mcp v2 低层 middleware：tools/call 前置权限校验，其余方法直接放行。"""
    if ctx.method != "tools/call":
        return await call_next(ctx)

    # 未配置映射的工具默认放行
    params = ctx.params or {}
    tool_name = params.get("name")
    required = TOOL_PERMISSIONS.get(tool_name)
    if required is None:
        return await call_next(ctx)

    # 延迟导入避免与 server.py 底部对本模块的导入成环
    from server import _registry

    # 从 HTTP 请求取 headers（Starlette Request，小写键），构造/复用客户端
    headers = getattr(ctx.request, "headers", None)
    if headers is None:
        logger.warning(f"拒绝执行工具 {tool_name}：请求不携带 HTTP headers，无法获取凭据")
        return _deny("权限校验失败：请求不携带 HTTP headers，无法获取 DevOps 凭据", required)
    try:
        client = await _registry.get(headers)
    except ValueError as e:
        logger.warning(f"拒绝执行工具 {tool_name}：凭据不完整（{e}）")
        return _deny(f"权限校验失败：{e}", required)
    except Exception as e:
        # 后端不可达 / token 无效等 → fail-closed 拒绝（不向上抛异常）
        logger.warning(f"拒绝执行工具 {tool_name}：凭据校验失败（{type(e).__name__}: {e}）")
        return _deny("权限校验失败：无法连接 DevOps 或 token 无效，已拒绝执行", required)

    # 权限接口按 empId+projectId 查询，缺项目 ID 无法校验 → fail-closed
    if not client.project_id:
        logger.warning(f"拒绝执行工具 {tool_name}：缺少 X-DevOps-Project-ID")
        return _deny(
            "缺少 X-DevOps-Project-ID，无法进行权限校验，请在 MCP 客户端配置该 header",
            required,
        )

    # 拉取当前用户权限码（按凭据缓存）；失败/形状异常 → fail-closed 拒绝
    try:
        perms = await client.get_permissions()
    except Exception as e:
        logger.warning(f"拒绝执行工具 {tool_name}：权限校验服务不可用（{type(e).__name__}: {e}）")
        return _deny("权限校验服务不可用，已拒绝执行", required)

    if required not in perms:
        logger.warning(
            f"拒绝执行工具 {tool_name}：需要权限 {required}，"
            f"用户 {client._user_info.userName} 未授予"
        )
        return _deny(
            f"无权限执行 {tool_name}：需要权限 {required}，当前用户未授予",
            required,
        )

    return await call_next(ctx)
