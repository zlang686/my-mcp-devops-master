"""附件域工具：文本附件预览、片段读取、资源 URI、图片附件读取。"""
import base64
import json
import logging
from typing import Any, Dict, List

import mcp_types
from mcp.server.mcpserver import Context
from mcp.server.mcpserver import Image

from server import get_client, mcp

logger = logging.getLogger(__name__)

# 常量定义
MAX_PREVIEW_LINES: int = 200  # 预览文件的最大行数
MAX_PREVIEW_CHARS: int = 5000  # 预览文件的最大字符数

# 文本文件类型集合
TEXT_TYPES: set = {"txt", "log", "json", "xml", "yaml", "yml"}

# 图片文件类型集合（对齐多模态模型支持的图片格式；bmp 等不支持，不收）
IMAGE_TYPES: set = {"png", "jpg", "jpeg", "gif", "webp"}

# 图片大小上限：超过拒绝返回不压缩（5MB base64 后约 6.7MB，接近模型单图上限）
MAX_IMAGE_BYTES: int = 5 * 1024 * 1024

# MIME类型映射
MIME_MAP: Dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
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


@mcp.tool(structured_output=False, description="预览文本类型附件（如日志、txt、json文件）的内容，返回文件的前200行或5000字符的预览")
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


@mcp.tool(structured_output=False, description="读取文本类型附件的指定片段，支持设置偏移量和长度。仅支持文本类型文件（txt/log/json/xml/yaml/yml），非文本类型将被拒绝")
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


# @mcp.tool(structured_output=False, description="返回附件资源的URI和MIME类型，用于直接访问附件")
# async def get_attachment_resource(file_url: str, file_type: str) -> Dict[str, List[Dict[str, Any]]]:
#     """返回附件资源URI和MIME类型"""
#     logger.info(f"开始获取资源: {file_url}, 类型: {file_type}")
#     mime = MIME_MAP.get(file_type.lower(), "application/octet-stream")
#     return {
#         "contents": [{
#             "type": "resource",
#             "resource": {
#                 "uri": file_url,
#                 "mimeType": mime
#             }
#         }]
#     }


# structured_output 必须为 False：返回注解含 SDK 的 Image 类（无 __get_pydantic_core_schema__），
# 开 True 时 func_metadata 会为 `Image | dict` 建 pydantic 输出模型，注册即抛
# PydanticSchemaGenerationError；且错误分支 {"error": ...} 无 structuredContent，
# 严格客户端会以 -32600 拒收（见 CLAUDE.md Known Gotchas）。
@mcp.tool(structured_output=False, description="下载图片类型附件并以图片内容返回，多模态客户端可直接查看。仅支持 png/jpg/jpeg/gif/webp 且不超过 5MB；file_url 与 file_type 取自 get_workitem_details 返回的 attachments 数组（fileUrl/fileType 字段）")
async def get_attachment_image(ctx: Context, file_url: str, file_type: str) -> Image | dict:
    """下载图片附件，返回 Image 对象；失败返回 {"error": ...}"""
    logger.info(f"开始下载图片附件: {file_url}, 类型: {file_type}")
    try:
        if file_type.lower() not in IMAGE_TYPES:
            logger.info(f"非图片类型: {file_type}，拒绝下载")
            return {
                "error": f"仅支持图片类型附件（png/jpg/jpeg/gif/webp），当前 file_type={file_type}",
                "supported_types": sorted(IMAGE_TYPES),
            }
        if file_type.lower() == "jpg":
            file_type="jpeg"

        client = await get_client(ctx)
        data = await client.download_binary(file_url)

        if len(data) > MAX_IMAGE_BYTES:
            logger.info(f"图片过大: {len(data)} 字节，超过上限 {MAX_IMAGE_BYTES}")
            return {
                "error": f"图片过大（{len(data)} 字节），超过上限 5MB，请手动下载查看",
                "file_size": len(data),
                "max_bytes": MAX_IMAGE_BYTES,
            }

        # mime = MIME_MAP.get(file_type.lower(), "application/octet-stream")
        # b64 = base64.b64encode(data).decode("ascii")
        logger.info(f"图片附件下载成功: {len(data)} 字节, type={file_type}")
        # SDK func_metadata._convert_to_content 对 Image 实例返回单个 ImageContent
        # （func_metadata.py:557-558）；mime = image/{format}，jpg 已归一为 jpeg
        return Image(data=data, format=file_type)
    except Exception as e:
        logger.error(f"下载图片附件失败: {str(e)}")
        return {"error": f"下载图片附件失败: {str(e)}"}
