"""附件域工具：文本附件预览、片段读取、资源 URI。"""
import logging
from typing import Annotated, Any, Dict, List

from mcp.server.fastmcp import Context
from pydantic import Field

from server import get_client, mcp

logger = logging.getLogger(__name__)

# 常量定义
MAX_PREVIEW_LINES: int = 200  # 预览文件的最大行数
MAX_PREVIEW_CHARS: int = 5000  # 预览文件的最大字符数

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


def is_text_file(file_type: str) -> bool:
    return file_type.lower() in TEXT_TYPES


def preview_text(text: str) -> str:
    """截断预览：超限时截取，且仅在真的发生截断时追加标记。"""
    truncated = len(text) > MAX_PREVIEW_CHARS
    if truncated:
        text = text[:MAX_PREVIEW_CHARS]

    lines = text.splitlines()
    if len(lines) > MAX_PREVIEW_LINES:
        lines = lines[:MAX_PREVIEW_LINES]
        truncated = True

    preview = "\n".join(lines)
    if truncated:
        preview += "\n\n(truncated preview)"
    return preview


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


@mcp.tool(description="读取文本类型附件的指定片段，支持设置偏移量和长度。仅支持文本类型文件（txt/log/json/xml/yaml/yml），非文本类型将被拒绝")
async def get_attachment_chunk(ctx: Context, file_url: str, file_type: str, offset: int = 0, length: int = 4000) -> Dict[str, List[Dict[str, Any]]]:
    """读取文本类型附件的指定片段"""
    logger.info(f"开始读取文件片段: {file_url}, 类型: {file_type}, 偏移: {offset}, 长度: {length}")
    try:
        if not is_text_file(file_type):
            logger.info("非文本文件，无法读取片段")
            return {
                "contents": [{
                    "type": "text",
                    "text": "Chunk reading only available for text files"
                }]
            }

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


@mcp.tool(description="返回附件资源的URI和MIME类型，用于直接访问附件")
async def get_attachment_resource(file_url: str, file_type: str) -> Dict[str, List[Dict[str, Any]]]:
    """返回附件资源URI和MIME类型"""
    logger.info(f"开始获取资源: {file_url}, 类型: {file_type}")
    mime = MIME_MAP.get(file_type.lower(), "application/octet-stream")
    return {
        "contents": [{
            "type": "resource",
            "resource": {
                "uri": file_url,
                "mimeType": mime
            }
        }]
    }
