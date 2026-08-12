import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, List, Any
from mcp.server.fastmcp import Context, FastMCP
from devops_client import DevOpsClient
from pydantic import BaseModel
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
# @mcp.tool(description="获取当前用户的所有项目列表，返回项目ID、项目代码和项目名称")
# async def get_project() -> List[Dict[str, Any]]:
#     """获取当前用户的所有项目"""
#     logger.info("开始获取当前用户的所有项目")
#     try:
#         data = await client.get_project()
#         print("===",data)
#         projects: List[Dict[str, Any]] = []
#         for project in data["data"]:
#             projects.append({
#                 "projectId":project.get("projectId"),
#                 "projectCode":project.get("projectCode"),
#                 "projectName":project.get("projectName")
#             })
#         logger.info(f"成功获取 {len(projects)} 个项目")
#         return projects
#     except Exception as e:
#         logger.error(f"获取项目列表失败: {str(e)}")
#         return [{"error": f"获取项目列表失败: {str(e)}"}]

@mcp.tool(description="查询指定项目下的工作项列表，支持按状态、工作项key筛选，返回工作项详细信息")
async def get_workitem_list(ctx: Context, project_id: str,workitem_key:str, workitem_status: str, offset: int, limit: int) -> List[Dict[str, Any]]:
    """查询项目下的当前用户相关的工作项"""
    logger.info(f"开始查询项目 {project_id} 下的工作项，状态: {workitem_status}, 偏移: {offset}, 限制: {limit}")
    try:
        client = await get_client(ctx)
        data = await client.query_workitem_list(project_id,workitem_key, workitem_status, offset, limit)
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
                "workitemType":workitem_type.get(workitem.get("workitemTypeId"), {}).get("workitemType", "unknown"),
                "workitemTypeName":workitem_type.get(workitem.get("workitemTypeId"), {}).get("workitemTypeName", "未知")
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

@mcp.tool(description="变更指定工作项的状态，需要提供工作项ID和新的状态")
async def change_workitem_status(ctx: Context, workitem_id: str, workitem_status: str) -> Dict[str, Any]:
    """变更工作项状态"""
    logger.info(f"开始变更工作项 {workitem_id} 的状态为 {workitem_status}")
    try:
        client = await get_client(ctx)
        r = await client.change_workitem_status(workitem_id, workitem_status)
        logger.info("工作项状态变更成功")
        return r.json()
    except Exception as e:
        logger.error(f"变更工作项状态失败: {str(e)}")
        return {"error": f"变更工作项状态失败: {str(e)}"}


@mcp.tool(description="获取指定工作项的详细信息，包括标题、状态、优先级、负责人、描述和附件元数据")
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


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
