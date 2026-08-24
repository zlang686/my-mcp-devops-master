"""MCP 服务器入口：日志配置、导入工具包触发注册、启动服务。

工具实现位于 tools/{workitems,attachments,testcases}.py，
MCPServer 实例与凭据注册表位于 server.py，工具权限中间件位于 permissions.py。
"""
import logging
import os

import tools  # noqa: F401  导入即注册 13 个 @mcp.tool 工具
from server import mcp

# 配置日志（入口统一配置，各模块仅 getLogger）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def main():
    # v1 FastMCP 的 host/port/mcp_path 参数在 v2 MCPServer 中统一移到 run()，
    # 路径参数名为 streamable_http_path；显式固定保持客户端连接 URL 不变
    # （http://127.0.0.1:8000/mcp，与 v1 默认一致）。
    # host/port 支持环境变量覆盖（Docker 容器内需绑 0.0.0.0，宿主机 -p 映射才进得来），
    # 默认值保持本地开发行为零变化：MCP_HOST=127.0.0.1、MCP_PORT=8000
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8000")),
        streamable_http_path="/mcp",
    )


if __name__ == "__main__":
    main()
