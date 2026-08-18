"""工作项域工具：查询/创建工作项、评论、状态变更、详情。"""
import logging
from typing import Annotated, Any, Dict, List, Optional, Union

from mcp.server.mcpserver import Context
from pydantic import Field

from server import get_client, mcp

logger = logging.getLogger(__name__)

# 工作项类型映射（键统一为 workitemType，与读取侧 .get("workitemType") 保持一致）
workitem_type: Dict[str, Dict[str, str]] = {
    "2": {"workitemType": "user-story", "workitemTypeName": "故事"},
    "3": {"workitemType": "task", "workitemTypeName": "任务"},
    "4": {"workitemType": "bug", "workitemTypeName": "bug"},
    "5": {"workitemType": "risk", "workitemTypeName": "风险"},
}


def format_time_estimate(seconds: Any) -> Optional[str]:
    """将工作项预估时间（秒）转为可读格式，1d=8h。
    例如 28800 -> "1d", 14400 -> "4h", 36000 -> "1d2h"。
    """
    if seconds is None:
        return None
    try:
        total_seconds = int(seconds)
    except (TypeError, ValueError):
        return None
    if total_seconds <= 0:
        return "0h"
    total_hours = round(total_seconds / 3600)
    days, rem_hours = divmod(total_hours, 8)
    if days > 0 and rem_hours > 0:
        return f"{days}d{rem_hours}h"
    if days > 0:
        return f"{days}d"
    return f"{rem_hours}h"


@mcp.tool(structured_output=False, description="""查询工作项列表，支持按工作项key、状态、类型筛选，返回工作项详细信息。

【参数格式】
- workitem_status / workitem_type_id 均为逗号分隔的字符串，为空表示不筛选。
  例：workitem_status="open,in-progress,developing", workitem_type_id="2,3,4"

【工作项类型 workitem_type_id 取值】
  2 = 故事(user-story)
  3 = 任务(task)
  4 = bug
  5 = 风险(risk)

【工作项状态 workitem_status 取值（不同类型支持的状态不同）】
  [bug 4]    open=待解决, in-progress=处理中, to-be-tested=待测试, testing=测试中, verified=验证通过, reopened=重新打开, closed=已关闭
  [风险 5]   open=待解决, in-progress=处理中, resolved=已解决, reopened=重新打开, closed=已关闭
  [故事 2]   open=待开发, developing=开发中, to-be-tested=待测试, testing=测试中, verified=验证通过, released=已发布
  [任务 3]   to-do=待办, in-progress=处理中, done=完成

【关键规则】同一中文状态在不同类型下可能对应不同英文值，需按用户查询的类型把对应英文值取并集后传入。
  例如"处理中"：对 bug/任务/风险 → in-progress；对故事 → developing(开发中)。若用户同时查 bug 和故事，应传入 "in-progress,developing"。
  多余的状态值无害（不匹配的类型自动不命中），可放心把可能命中的值都加上。

【示例】用户问"查询待解决、处理中和重新打开的任务、bug和故事"：
  - 类型：任务(3)+bug(4)+故事(2) → workitem_type_id="2,3,4"
  - 待解决 → open(bug/故事) + to-do(任务，待办视作待解决)
  - 处理中 → in-progress(bug/任务) + developing(故事)
  - 重新打开 → reopened(bug；风险也有 reopened 但本次不含风险)
  - 取并集 → workitem_status="open,to-do,in-progress,developing,reopened"
""")
async def get_workitem_list(
    ctx: Context,
    workitem_key: Annotated[str, Field(description="工作项key/标题模糊匹配，为空不过滤")] = "",
    workitem_status: Annotated[str, Field(description="状态筛选，逗号分隔，取值见工具说明，为空不过滤")] = "",
    workitem_type_id: Annotated[str, Field(description="类型ID筛选，逗号分隔，取值见工具说明，为空不过滤")] = "",
    offset: Annotated[int, Field(description="分页偏移，默认0")] = 0,
    limit: Annotated[int, Field(description="每页数量，默认20")] = 20,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """查询项目下的当前用户相关的工作项"""
    logger.info(f"开始查询工作项，状态: {workitem_status}, 偏移: {offset}, 限制: {limit}")
    try:
        client = await get_client(ctx)
        data = await client.query_workitem_list(workitem_key, workitem_status,workitem_type_id, offset, limit)
        workitems: List[Dict[str, Any]] = []
        for workitem in data["data"]:
            workitems.append({
                "workitemId":workitem.get("workitemId"),
                "workitemKey":workitem.get("workitemKey"),
                "iterationId":workitem.get("iterationId"),
                "versionId":workitem.get("versionId"),
                "prmoduleIdiority":workitem.get("moduleId"),
                "title":workitem.get("title"),
                "description":workitem.get("description"),
                "priority":workitem.get("priority"),
                "workitemStatus":workitem.get("workitemStatus"),
                "workitemStatusName":workitem.get("workitemStatusName"),
                # str() 强转：workitem_type 的键为字符串，但 JSON 响应可能返回整数类型ID
                "workitemType": workitem_type.get(str(workitem.get("workitemTypeId")), {}).get("workitemType", "unknown"),
                "workitemTypeName": workitem_type.get(str(workitem.get("workitemTypeId")), {}).get("workitemTypeName", "未知"),
                "createTime":workitem.get("createTime"),
                "dueTime":workitem.get("dueTime"),
                "timeEstimate":format_time_estimate(workitem.get("timeEstimate")),
            })
        logger.info(f"成功获取 {len(workitems)} 个工作项")
        return workitems
    except Exception as e:
        logger.error(f"获取工作项列表失败: {str(e)}")
        return {"error": f"获取工作项列表失败: {str(e)}"}

@mcp.tool(structured_output=False, description="""创建新的工作项。

必填：title(标题)、description(描述)、priority(优先级)、workitem_type(工作项类型)、man_hour(评估工时，单位：小时)
可选：parent_workitem_id(父工作项ID，为空表示根工作项)、due_time(截止日期，格式 YYYY-MM-DD)

【description 富文本格式】
  描述字段支持 HTML 富文本，请直接提供 HTML 片段（无需 <html>/<body> 外壳）。
  支持标签：p(段落)、br(换行)、strong/b(加粗)、em/i(斜体)、u(下划线)、
           ul/ol/li(列表)、h1-h6(标题)、blockquote(引用)、code/pre(代码)、a(链接)。
  示例：<p>实现登录功能</p><ul><li>用户名密码登录</li><li>单点登录</li></ul>
  注意：传入纯文本（无 HTML 标签）时将自动包装为 <p> 段落，换行符不渲染，请主动用 HTML 标签排版。

【取值说明】
  workitem_type: story(故事) / task(任务) / bug / risk(风险)
  priority:      highest(最高) / high(高) / medium(中等) / low(低) / lowest(最低)
  man_hour:      评估工时，单位为小时（内部自动转换为秒）

返回创建后的工作项关键信息（含 workitem_key、状态、负责人等）。""")
async def create_workitem(
    ctx: Context,
    title: str,
    description: str,
    priority: str,
    workitem_type: str,
    man_hour: int,
    parent_workitem_id: Optional[str] = None,
    due_time: Optional[str] = None,
) -> Dict[str, Any]:
    """创建工作项"""
    logger.info(f"开始创建工作项: {title}, type={workitem_type}, priority={priority}")
    try:
        client = await get_client(ctx)
        r = await client.create_workitem(
            title, description, priority, workitem_type,
            man_hour, parent_workitem_id, due_time,
        )
        logger.info(f"工作项创建成功: {r.get('workitem_key')}")
        return r
    except ValueError as e:
        # 参数校验错误：返回明确的校验提示，便于调用方修正
        logger.warning(f"创建工作项参数校验失败: {str(e)}")
        return {"error": f"参数校验失败: {str(e)}"}
    except Exception as e:
        logger.error(f"创建工作项失败: {str(e)}")
        return {"error": f"创建工作项失败: {str(e)}"}

@mcp.tool(structured_output=False, description="为指定工作项添加评论，需要提供项目ID、工作项ID和评论内容")
async def add_workitem_comment(ctx: Context, project_id: str, workitem_id: str, comment: str) -> Dict[str, Any]:
    """添加工作项评论"""
    logger.info(f"开始为工作项 {workitem_id} 添加评论")
    try:
        client = await get_client(ctx)
        # client.add_workitem_comment 已返回解析后的 dict，不能再调 .json()
        data = await client.add_workitem_comment(project_id, workitem_id, comment)
        logger.info("评论添加成功")
        return data
    except Exception as e:
        logger.error(f"添加评论失败: {str(e)}")
        return {"error": f"添加评论失败: {str(e)}"}

@mcp.tool(structured_output=False, description="""变更指定工作项的状态，需要提供工作项ID和新的状态。

服务端会先查询工作项的真实当前状态并校验转换是否合法，非法转换将返回错误及当前状态允许的目标状态列表。

【工作项类型与状态转换规则】（必须遵守，仅下列转换合法）
  bug（工作项类型ID=4）:
    closed(已关闭)      -> reopened(重新打开)
    in-progress(处理中) -> closed(关闭), open(转为待办), to-be-tested(解决完成)
    open(待解决)        -> closed(关闭), in-progress(处理中), to-be-tested(解决完成)
    reopened(重新打开)  -> closed(关闭), in-progress(处理中)
    testing(测试中)     -> closed(关闭), reopened(打回), verified(验证通过)
    to-be-tested(待测试)-> closed(关闭), reopened(打回), testing(开始测试)
    verified(验证通过)  -> closed(关闭)

  风险（工作项类型ID=5）:
    closed(已关闭)      -> reopened(重新打开)
    in-progress(处理中) -> closed(关闭), resolved(已解决)
    open(待解决)        -> closed(关闭), in-progress(处理中), resolved(已解决)
    reopened(重新打开)  -> closed(关闭), in-progress(处理中), resolved(已解决)
    resolved(已解决)    -> closed(关闭), reopened(重新打开)

  故事（工作项类型ID=2）:
    developing(开发中)  -> open(转为待办), to-be-tested(开发完成)
    open(待开发)        -> developing(开发中)
    testing(测试中)     -> open(打回), verified(验证通过)
    to-be-tested(待测试)-> open(打回), testing(测试中)
    verified(验证通过)  -> open(重新打开), released(发布)

  任务（工作项类型ID=3）:
    done(完成)          -> in-progress(重新处理), to-do(重新打开)
    in-progress(处理中) -> done(完成), to-do(转为待办)
    to-do(待办)         -> done(完成), in-progress(开始处理)

【注意】同一中文状态在不同类型下可能对应不同英文值（如"处理中"对 bug 是 in-progress，对故事是 developing）。变更状态时传入的 workitem_status 必须是目标类型的合法英文值。

【示例】将一个 bug 从"待解决"变为"处理中"：change_workitem_status(workitem_id="xxx", workitem_status="in-progress")
""")
async def change_workitem_status(ctx: Context, workitem_id: str, workitem_status: str) -> Dict[str, Any]:
    """变更工作项状态"""
    logger.info(f"开始变更工作项 {workitem_id} 的状态为 {workitem_status}")
    try:
        client = await get_client(ctx)
        # 服务端校验：查询真实当前状态，检查转换是否合法
        check = await client.validate_status_transition(workitem_id, workitem_status)
        if not check["valid"]:
            logger.warning(f"非法状态转换: 类型={check['workitem_type']}, 当前状态={check['current_status']}, 目标状态={workitem_status}, 允许={check['allowed_statuses']}")
            return {
                "error": f"非法状态转换：当前状态 {check['current_status']} 不允许变更为 {workitem_status}",
                "workitem_type": check["workitem_type"],
                "current_status": check["current_status"],
                "allowed_statuses": check["allowed_statuses"],
            }
        r = await client.change_workitem_status(workitem_id, workitem_status)
        logger.info("工作项状态变更成功")
        return r
    except Exception as e:
        logger.error(f"变更工作项状态失败: {str(e)}")
        return {"error": f"变更工作项状态失败: {str(e)}"}


@mcp.tool(structured_output=False, description="根据工作项id获取指定工作项的详细信息，包括标题、状态、优先级、负责人、描述和附件元数据")
async def get_workitem_details(ctx: Context, workitem_id: str) -> Dict[str, Any]:
    """获取工作项详情和附件元数据"""
    logger.info(f"开始获取工作项 {workitem_id} 的详情")
    try:
        client = await get_client(ctx)
        data = await client.get_workitem_details(workitem_id)

        attachments: List[Dict[str, Any]] = []

        for att in data.get("attachments", []):
            attachments.append({
                "fileName": att.get("fileName"),
                "fileType": att.get("fileType"),
                "fileSize": att.get("fileSize"),
                "fileUrl": att.get("fileUrl")
            })

        logger.info(f"成功获取工作项详情，包含 {len(attachments)} 个附件")
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            # 优先 workitemStatus（与列表接口一致），兼容仅返回 status 的响应
            "status": data.get("workitemStatus", data.get("status")),
            "priority": data.get("priority"),
            "assignee": data.get("assignee"),
            "description": data.get("description"),
            "attachments": attachments
        }
    except Exception as e:
        logger.error(f"获取工作项详情失败: {str(e)}")
        return {"error": f"获取工作项详情失败: {str(e)}"}
