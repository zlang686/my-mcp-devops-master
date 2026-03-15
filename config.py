"""Configuration management for DevOps MCP service."""

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass
class Config:
    """Configuration for DevOps MCP service."""

    base_url: str
    username: str
    password: str

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
