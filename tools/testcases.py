"""测试用例域工具：分组创建/查询、用例创建/查询。"""
import json
import logging
from typing import Any, Dict, List, Optional, Union

from mcp.server.mcpserver import Context
from pydantic import BaseModel, TypeAdapter

from server import get_client, mcp

logger = logging.getLogger(__name__)


class Step(BaseModel):
    sortno: Optional[int] = None
    step: str
    result: str


def dump_steps(steps: List[Step]) -> str:
    """按列表顺序自动编号（忽略调用方传入的 sortno）并序列化为 operationStep JSON 字符串。

    通过 model_copy 生成新实例，不修改调用方传入的 Step 对象。
    """
    numbered = [s.model_copy(update={"sortno": i}) for i, s in enumerate(steps, start=1)]
    return TypeAdapter(List[Step]).dump_json(numbered).decode()


def parse_operation_step(raw: Any) -> Any:
    """将后端返回的 operationStep JSON 字符串解析为列表，解析失败原样返回。"""
    if not raw or not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


@mcp.tool(structured_output=False, description="""创建测试用例分组。

必填：group_name(分组名称)
可选：parent_group_id(父分组ID，为空表示根分组；父分组ID可通过 get_testcase_groups 查询)

返回创建后的分组关键信息（含 group_id）。""")
async def create_testcase_group(ctx: Context, group_name: str, parent_group_id: Optional[str] = None) -> Dict[str, Any]:
    """创建测试用例分组"""
    logger.info(f"开始创建测试用例分组: {group_name}, parent_group_id={parent_group_id}")
    try:
        client = await get_client(ctx)
        r = await client.create_testcase_group(group_name, parent_group_id)
        logger.info(f"测试用例分组创建成功: {r.get('group_id')}")
        return r
    except Exception as e:
        logger.error(f"创建测试用例分组失败: {str(e)}")
        return {"error": f"创建测试用例分组失败: {str(e)}"}


@mcp.tool(structured_output=False, description="查询项目下的测试用例分组列表，返回分组树信息（含父分组ID、hasChild、子分组ID列表、用例数统计）。创建测试用例前可先调用本工具获取目标 group_id。")
async def get_testcase_groups(ctx: Context) -> Dict[str, Any]:
    """查询测试用例分组列表"""
    logger.info("开始查询测试用例分组列表")
    try:
        client = await get_client(ctx)
        data = await client.get_testcase_groups()
        groups: List[Dict[str, Any]] = []
        for g in data:
            groups.append({
                "groupId": g.get("groupId"),
                "groupName": g.get("groupName"),
                "parentGroupId": g.get("parentGroupId"),
                "hasChild": g.get("hasChild"),
                "childGroupIds": g.get("childGroupIds"),
                "caseCount": g.get("caseCount"),
                "caseCountExcludeChild": g.get("caseCountExcludeChild"),
                "createTime": g.get("createTime"),
            })
        logger.info(f"成功获取 {len(groups)} 个测试用例分组")
        # 单块返回整个 JSON 数组：SDK 会把 list 拍平成每元素一个 TextContent 块，
        # 多块形态下部分模型/客户端只读首块（实测丢失除第一条外的全部数据）
        return {
            "total": len(data),"groups": groups,
        }
    except Exception as e:
        logger.error(f"获取测试用例分组失败: {str(e)}")
        return {"error": f"获取测试用例分组失败: {str(e)}"}


@mcp.tool(structured_output=False, description="""在指定分组下创建测试用例。

必填：case_title(用例标题)、steps(操作步骤列表)、group_id(所属分组ID，必须来自 get_testcase_groups 查询结果或 create_testcase_group 返回值；服务端会校验，无效时不创建并返回 available_groups 供选择或新建)
可选：note(备注)、precondition(前置条件)、workitem_ids(关联工作项ID列表)、case_type(用例类型，默认 function)、default_priority(优先级，默认 P1)

【steps 步骤格式】每步含 step(操作描述) 和 result(预期结果)，sortno 按列表顺序自动编号，无需传入。
  示例：steps=[{"step": "进入登录页", "result": "登录页正常显示"}, {"step": "输入正确账号密码并提交", "result": "登录成功跳转首页"}]

【取值说明】
  default_priority: P0(最高) / P1(高) / P2(中) / P3(低) / P4(最低)
  case_type:       function(功能测试，默认)

【note / precondition 富文本格式】
  支持 HTML 片段（如 <p>段落</p><ol><li>条目1</li></ol>）；传入纯文本时自动包装为 <p> 段落，换行不渲染，多行内容建议用 <ol><li> 列表。

返回创建后的用例关键信息（含 case_id、case_key、状态、关联工作项等）。""")
async def create_testcase(
    ctx: Context,
    case_title: str,
    steps: List[Step],
    group_id: str,
    note: str = "",
    precondition: str = "",
    workitem_ids: Optional[List[str]] = None,
    case_type: str = "function",
    default_priority: str = "P1",
) -> Dict[str, Any]:
    """创建测试用例"""
    logger.info(f"开始创建测试用例: {case_title}, group_id={group_id}")
    try:
        client = await get_client(ctx)
        # 分组校验：拦截未查询分组直创、ID 臆造/记错的情况，返回可选分组引导选择或新建
        group_map = await client.get_testcase_group_map()
        if group_id not in group_map:
            logger.warning(f"分组ID无效: {group_id}，可用分组: {list(group_map)}")
            return {
                "error": f"分组ID无效: {group_id}。请从 available_groups 中选择分组后重试，或调用 create_testcase_group 新建分组",
                "available_groups": [
                    {"group_id": gid, "group_name": name} for gid, name in group_map.items()
                ],
            }
        r = await client.create_testcase(
            case_title, group_id, dump_steps(steps),
            note, precondition, workitem_ids, case_type, default_priority,
        )
        logger.info(f"测试用例创建成功: {r.get('case_id')}")
        return r
    except ValueError as e:
        # 参数校验错误：返回明确的校验提示，便于调用方修正
        logger.warning(f"创建测试用例参数校验失败: {str(e)}")
        return {"error": f"参数校验失败: {str(e)}"}
    except Exception as e:
        logger.error(f"创建测试用例失败: {str(e)}")
        return {"error": f"创建测试用例失败: {str(e)}"}


@mcp.tool(structured_output=False, description="""查询项目下的测试用例列表，返回用例详情（操作步骤已解析为结构化列表，便于阅读）。

可选：group_id(分组ID筛选，为空查全项目)、page_index(页码，从0开始)、page_size(每页数量，默认200)
返回 {"total": 总数, "cases": [用例列表]}。""")
async def get_testcase_list(
    ctx: Context,
    group_id: str = "",
    page_index: int = 0,
    page_size: int = 200,
) -> Dict[str, Any]:
    """查询测试用例列表"""
    logger.info(f"开始查询测试用例列表, group_id={group_id}, page_index={page_index}, page_size={page_size}")
    try:
        client = await get_client(ctx)
        data = await client.query_testcase_list(group_id, page_index, page_size)
        cases: List[Dict[str, Any]] = []
        for c in data.get("data", []):
            cases.append({
                "caseId": c.get("caseId"),
                "caseKey": c.get("caseKey"),
                "caseTitle": c.get("caseTitle"),
                "caseType": c.get("caseType"),
                "caseStatus": c.get("caseStatus"),
                "defaultPriority": c.get("defaultPriority"),
                "defaultAssignee": c.get("defaultAssignee"),
                "groupId": c.get("groupId"),
                "createTime": c.get("createTime"),
                "note": c.get("note"),
                "precondition": c.get("precondition"),
                "operationStep": parse_operation_step(c.get("operationStep")),
            })
        logger.info(f"成功获取 {len(cases)} 个测试用例（共 {data.get('total')} 条）")
        return {"total": data.get("total"), "cases": cases}
    except Exception as e:
        logger.error(f"获取测试用例列表失败: {str(e)}")
        return {"error": f"获取测试用例列表失败: {str(e)}"}
