"""MCP 服务器入口：日志配置、导入工具包触发注册、启动服务。

工具实现位于 tools/{workitems,attachments,testcases}.py，
FastMCP 实例与会话管理位于 server.py。
"""
import logging

import tools  # noqa: F401  导入即注册 12 个 @mcp.tool 工具
from server import mcp

# 配置日志（入口统一配置，各模块仅 getLogger）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def main():
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
