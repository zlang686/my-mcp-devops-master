# devops-mcp-master：streamable-http MCP 服务器（http://<host>:8000/mcp）
# 构建上下文需含 pyproject.toml + uv.lock（--frozen 按 lock 精确安装）
FROM python:3.13-slim

# uv 固定 0.11.14，与本地生成 uv.lock 的版本一致，避免 lock 兼容性漂移
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /usr/local/bin/

WORKDIR /app

# 禁止 uv 自行下载 Python，强制用镜像内 3.13
ENV UV_PYTHON_DOWNLOADS=never

# 依赖独立层：源码改动不触发依赖重装
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 再拷源码（COPY 顺序即缓存失效顺序）
COPY main.py server.py permissions.py config.py devops_client.py ./
COPY tools/ tools/

# venv 前置到 PATH，CMD 用裸 python（启动时不二次解析依赖）
# 容器内必须绑 0.0.0.0，宿主机 -p 端口映射才能转发进来
ENV PATH="/app/.venv/bin:$PATH" \
    MCP_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "main.py"]
