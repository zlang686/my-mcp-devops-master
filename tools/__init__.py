"""业务域工具包：导入各域模块以触发 @mcp.tool 注册（模块导入即注册）。"""
from . import attachments, testcases, workitems  # noqa: F401
