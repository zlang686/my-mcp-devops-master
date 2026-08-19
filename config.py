"""Configuration management for DevOps MCP service.

服务端固定配置：目前仅 DevOps 后端地址（产品化决策——后端地址属于部署配置，
不由 MCP 客户端指定）。本地开发读 .env（python-dotenv），产品部署注入进程
环境变量；已存在的进程变量优先，load_dotenv 不会覆盖。

历史上本模块还承载 username/password 与 4 个 ID 默认值（登录换 token 机制），
该机制已被 per-request afc_token header 取代，相关字段已删除。
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration for DevOps MCP service."""

    base_url: str

    @classmethod
    def from_env(cls) -> "Config":
        """Load base_url from DEVOPS_BASE_URL (env var or .env)."""
        load_dotenv()

        base_url = os.getenv("DEVOPS_BASE_URL", "").strip().rstrip("/")

        if not base_url:
            raise ValueError(
                "DEVOPS_BASE_URL 未配置：服务端必须固定 DevOps 后端地址"
                "（一实例一后端），请在环境变量或 .env 中设置。"
            )

        return cls(base_url=base_url)