import asyncio
import base64
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Dict, List, Any, Optional
from mcp.server.fastmcp import Context, FastMCP
from devops_client import DevOpsClient
from pydantic import BaseModel
from pydantic import Field
from pydantic import TypeAdapter

# Header 名称定义：MCP 客户端连接时通过这些 headers 传递 DevOps 配置
_REQUIRED_HEADERS = {
    "base_url": "X-DevOps-Base-URL",
    # "username": "X-DevOps-Username",
    # "password": "X-DevOps-Password",
    "afc_token":"X-DevOps-afcToken"
}
_OPTIONAL_HEADERS = {
    "project_id": "X-DevOps-Project-ID",
    "iteration_id": "X-DevOps-Iteration-ID",
    "module_id": "X-DevOps-Module-ID",
    "version_id": "X-DevOps-Version-ID",
}

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 常量定义
MAX_PREVIEW_LINES: int = 200  # 预览文件的最大行数
MAX_PREVIEW_CHARS: int = 5000  # 预览文件的最大字符数
REQUEST_TIMEOUT: int = 60  # 请求超时时间（秒）

# 文本文件类型集合
TEXT_TYPES: set = {"txt", "log", "json", "xml", "yaml", "yml"}

# MIME类型映射
MIME_MAP: Dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "zip": "application/zip",
    "txt": "text/plain",
    "log": "text/plain",
    "json": "application/json"
}

# 工作项类型映射
workitem_type: Dict[str, Dict[str, str]] = {
    "2":{
        "workitemType":"user-story",
        "workitemTypeName":"故事"
    },
    "3":{
        "workitemTypeId":"task",
        "workitemTypeName":"任务"
    },
    "4":{
        "workitemTypeId":"bug",
        "workitemTypeName":"bug"
    },
    "5":{
        "workitemTypeId":"risk",
        "workitemTypeName":"风险"
    }
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


class Step(BaseModel):
    sortno: int
    step: str
    result: str


async def get_client(ctx: Context) -> DevOpsClient:
    """从会话上下文中获取（必要时构造）DevOpsClient。

    首次调用时从 HTTP headers 读取配置并构造客户端，缓存到 lifespan_context；
    后续调用直接复用，保留 _token 的 lazy-login 缓存。
    """
    lifespan_state = ctx.request_context.lifespan_context
    # 已构造：直接返回（同会话复用 token，避免重复登录）
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
        await client.login()
        lifespan_state["client"] = client
        return client


# 初始化 MCP 服务器
# 每个客户端连接（mcp-session-id）对应一次 lifespan 进入/退出，
# 在此处为每个会话懒构造独立的 DevOpsClient。
@asynccontextmanager
async def app_lifespan(app: FastMCP):
    yield {"client": None, "lock": asyncio.Lock()}


mcp: FastMCP = FastMCP("devops-mcp-master", lifespan=app_lifespan)

@mcp.tool(description="""查询工作项列表，支持按工作项key、状态、类型筛选，返回工作项详细信息。

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
) -> List[Dict[str, Any]]:
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
                "workitemType":workitem_type.get(workitem.get("workitemTypeId"), {}).get("workitemType", "unknown"),
                "workitemTypeName":workitem_type.get(workitem.get("workitemTypeId"), {}).get("workitemTypeName", "未知"),
                "createTime":workitem.get("createTime"),
                "dueTime":workitem.get("dueTime"),
                "timeEstimate":format_time_estimate(workitem.get("timeEstimate")),
            })
        logger.info(f"成功获取 {len(workitems)} 个工作项")
        return workitems
    except Exception as e:
        logger.error(f"获取工作项列表失败: {str(e)}")
        return [{"error": f"获取工作项列表失败: {str(e)}"}]

@mcp.tool(description="创建新的工作项，需要提供标题、描述、优先级、工作项类型、父工作项ID（可选，为空表示根工作项）、评估工时")
async def create_workitem(ctx: Context, title: str, description: str, priority: str, workitem_type: str,parent_workitem_id:str,man_hour:int) -> Dict[str, Any]:
    """创建工作项"""
    logger.info(f"开始创建工作项: {title}")
    try:
        client = await get_client(ctx)
        r = await client.create_workitem(title, description, priority, workitem_type,parent_workitem_id, man_hour)
        logger.info("工作项创建成功")
        return r
    except Exception as e:
        logger.error(f"创建工作项失败: {str(e)}")
        return {"error": f"创建工作项失败: {str(e)}"}

@mcp.tool(description="为指定工作项添加评论，需要提供项目ID、工作项ID和评论内容")
async def add_workitem_comment(ctx: Context, project_id: str, workitem_id: str, comment: str) -> Dict[str, Any]:
    """添加工作项评论"""
    logger.info(f"开始为工作项 {workitem_id} 添加评论")
    try:
        client = await get_client(ctx)
        r = await client.add_workitem_comment(project_id, workitem_id, comment)
        logger.info("评论添加成功")
        return r.json()
    except Exception as e:
        logger.error(f"添加评论失败: {str(e)}")
        return {"error": f"添加评论失败: {str(e)}"}

@mcp.tool(description="""变更指定工作项的状态，需要提供工作项ID和新的状态。

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


@mcp.tool(description="根据工作项id获取指定工作项的详细信息，包括标题、状态、优先级、负责人、描述和附件元数据")
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
            "status": data.get("status"),
            "priority": data.get("priority"),
            "assignee": data.get("assignee"),
            "description": data.get("description"),
            "attachments": attachments
        }
    except Exception as e:
        logger.error(f"获取工作项详情失败: {str(e)}")
        return {"error": f"获取工作项详情失败: {str(e)}"}



@mcp.tool(description="预览文本类型附件（如日志、txt、json文件）的内容，返回文件的前200行或5000字符的预览")
async def get_attachment_preview(ctx: Context, file_url: str, file_type: str) -> Dict[str, List[Dict[str, Any]]]:
    """预览文本类型附件（如日志、txt、json文件）"""
    logger.info(f"开始预览文件: {file_url}, 类型: {file_type}")
    try:
        if not is_text_file(file_type):
            logger.info("非文本文件，无法预览")
            return {
                "contents": [{
                    "type": "text",
                    "text": "Preview only available for text files"
                }]
            }

        client = await get_client(ctx)
        text = await client.download_text(file_url)
        preview = preview_text(text)
        logger.info("文件预览成功")
        return {
            "contents": [{
                "type": "text",
                "text": preview
            }]
        }
    except Exception as e:
        logger.error(f"预览文件失败: {str(e)}")
        return {
            "contents": [{
                "type": "text",
                "text": f"预览文件失败: {str(e)}"
            }]
        }

def is_text_file(file_type: str) -> bool:
    return file_type.lower() in TEXT_TYPES

def preview_text(text: str) -> str:

    if len(text) > MAX_PREVIEW_CHARS:
        text = text[:MAX_PREVIEW_CHARS]

    lines = text.splitlines()[:MAX_PREVIEW_LINES]

    preview = "\n".join(lines)

    return preview + "\n\n(truncated preview)"


@mcp.tool(description="读取文本类型附件的指定片段，支持设置偏移量和长度")
async def get_attachment_chunk(ctx: Context, file_url: str, offset: int = 0, length: int = 4000) -> Dict[str, List[Dict[str, Any]]]:
    """读取文本类型附件的指定片段"""
    logger.info(f"开始读取文件片段: {file_url}, 偏移: {offset}, 长度: {length}")
    try:
        client = await get_client(ctx)
        text = await client.download_text(file_url)
        chunk = text[offset: offset + length]
        logger.info("文件片段读取成功")
        return {
            "contents": [{
                "type": "text",
                "text": chunk
            }]
        }
    except Exception as e:
        logger.error(f"读取文件片段失败: {str(e)}")
        return {
            "contents": [{
                "type": "text",
                "text": f"读取文件片段失败: {str(e)}"
            }]
        }


async def create_testcase(ctx: Context,case_title: str, note: str, precondition: str, operation_step: List[Step], workitem_id: str, default_priority: str) -> Dict[str, Any]:
    """创建测试用例"""
    logger.info(f"开始创建测试用例: {case_title}")
    try:
        client = await get_client(ctx)
        data = await client.create_testcases(case_title, note, precondition, TypeAdapter(List[Step]).dump_json(operation_step).decode(), workitem_id, default_priority)
        logger.info("测试用例创建成功")
        returncase_id = data.get("id")
        return {"id": returncase_id}
    except Exception as e:
        return {"error": f"创建测试用例失败: {str(e)}"}


@mcp.tool(description="返回附件资源的URI和MIME类型，用于直接访问附件")
async def get_attachment_resource(file_url: str, file_type: str) -> Dict[str, List[Dict[str, Any]]]:
    """返回附件资源URI和MIME类型"""
    logger.info(f"开始获取资源: {file_url}, 类型: {file_type}")
    try:
        mime = MIME_MAP.get(file_type.lower(), "application/octet-stream")
        logger.info(f"资源获取成功，MIME类型: {mime}")
        return {
            "contents": [{
                "type": "resource",
                "resource": {
                    "uri": file_url,
                    "mimeType": mime
                }
            }]
        }
    except Exception as e:
        logger.error(f"获取资源失败: {str(e)}")
        return {
            "contents": [{
                "type": "text",
                "text": f"获取资源失败: {str(e)}"
            }]
        }


@mcp.tool(description="为指定工作项上传附件，需要提供工作项ID、文件名（含扩展名）和base64编码的文件内容")
async def upload_workitem_attachment(ctx: Context, workitem_id: str, file_name: str, file_content: Annotated[str, Field(description="base64编码的文件内容")]) -> Dict[str, Any]:
    """上传工作项附件"""
    logger.info(f"开始为工作项 {workitem_id} 上传附件: {file_name}")
    try:
        client = await get_client(ctx)
        # 从文件名扩展名推断 MIME 类型
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        content_type = MIME_MAP.get(ext, "application/octet-stream")
        # 解码 base64 得到文件二进制内容
        file_data = base64.b64decode(file_content)
        r = await client.upload_workitem_attachment(workitem_id, file_name, file_data, content_type)
        logger.info("附件上传成功")
        return r
    except Exception as e:
        logger.error(f"上传工作项附件失败: {str(e)}")
        return {"error": f"上传工作项附件失败: {str(e)}"}


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
