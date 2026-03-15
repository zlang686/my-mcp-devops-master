"""Configuration management for DevOps MCP service."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration for DevOps MCP service."""

    base_url: str=os.getenv("DEVOPS_BASE_URL", "")
    username: str=os.getenv("DEVOPS_USERNAME", "")
    password: str=os.getenv("DEVOPS_PASSWORD", "")
    # 服务端主机地址
    HOST:str=os.getenv("HOST","localhost")
    # 服务端端口号
    PORT:int=int(os.getenv("PORT","8000"))

    # 
    AUTH_HOST:str=os.getenv("AUTH_HOST","localhost")
    AUTH_PORT:int =int(os.getenv("AUTH_PORT","8080"))
    AUTH_REALM:str=os.getenv("AUTH_REALM","master")
    # 认证服务端地址
    OAUTH_CLIENT_ID:str=os.getenv("OAUTH_CLIENT_ID","test-client")
    OAUTH_CLIENT_SECRET: str = os.getenv("OAUTH_CLIENT_SECRET", "qj4N3o4vQ64RcRjRw2R6LXtPJVm4Dxfo")


    # Server settings
    MCP_SCOPE: str = os.getenv("MCP_SCOPE", "mcp:tools")
    OAUTH_STRICT: bool = os.getenv("OAUTH_STRICT", "false").lower() in ("true", "1", "yes")
    TRANSPORT: str = os.getenv("TRANSPORT", "streamable-http")

    @property
    def server_url(self) -> str:
        """Build the server URL."""
        return f"http://{self.HOST}:{self.PORT}"

    @property
    def auth_base_url(self) -> str:
        """Build the auth server base URL."""
        return f"http://{self.AUTH_HOST}:{self.AUTH_PORT}/realms/{self.AUTH_REALM}/"

    def validate(self) -> None:
        """Validate configuration."""
        if self.TRANSPORT not in ["sse", "streamable-http"]:
            raise ValueError(f"Invalid transport: {self.TRANSPORT}. Must be 'sse' or 'streamable-http'")

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        load_dotenv()

        base_url = os.getenv("DEVOPS_BASE_URL", "")
        username = os.getenv("DEVOPS_USERNAME", "")
        password = os.getenv("DEVOPS_PASSWORD", "")

        if not all([base_url, username, password]):
            raise ValueError(
                "Missing required environment variables. "
                "Please set DEVOPS_BASE_URL, DEVOPS_USERNAME, and DEVOPS_PASSWORD."
            )

        return cls(base_url=base_url, username=username, password=password)
