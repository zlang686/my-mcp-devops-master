# Docker 部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 devops-mcp-master 打包为 Docker 镜像，可部署到 Linux 服务器（客户端直连 `http://IP:8000/mcp`）。

**Architecture:** 唯一代码改动是 `main.py` 的 host/port 环境变量化（默认值不变）；新增 `Dockerfile`（python:3.13-slim + uv 0.11.14，依赖层/源码层分离）与 `.dockerignore`（重点是 `.env` 真实凭据绝不进镜像）；`DEVOPS_BASE_URL` 运行时 `-e` 注入。

**Tech Stack:** Docker、uv（与本地 0.11.14 同版）、python:3.13-slim。

**Spec:** `docs/superpowers/specs/2026-08-24-docker-deployment-design.md`

**验证约定（重要）：** 本项目**无测试套件**（CLAUDE.md 明确约定，不得发明测试命令）。验证手段 = `py_compile` + 实跑冒烟（起进程/起容器后 curl 探活）+ `docker logs`。

**执行环境注意：** 命令按 Git Bash（Windows）编写；提交一律 pathspec 风格（`git commit -m ... -- <paths>`），避免带入用户暂存文件。

---

### Task 1: main.py host/port 环境变量化

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 修改 main.py**

在 import 区加 `import os`，`mcp.run` 的 host/port 改为环境变量读取，默认值保持不变。完整目标代码：

```python
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
```

- [ ] **Step 2: 语法检查**

```bash
uv run python -m py_compile main.py
```
Expected: 无输出，退出码 0。

- [ ] **Step 3: 本地默认行为冒烟（不设环境变量）**

```bash
uv run python main.py > /tmp/mcp-smoke.log 2>&1 &
sleep 4
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/mcp
WIN_PID=$(netstat -ano | grep ':8000.*LISTENING' | awk '{print $NF}' | head -1)
[ -n "$WIN_PID" ] && taskkill //F //T //PID $WIN_PID   # MSYS $! 是 bash 内部 PID，须用 netstat 查真实 Windows PID 杀树
```
Expected: curl 输出 4xx（400/405/406 之一——裸 GET 打 streamable-http 端点的正常拒绝码）；`000` = 服务没起来，查 `/tmp/mcp-smoke.log`。若端口被占，先用同样的 netstat+taskkill 清掉上一次冒烟的孤儿 python.exe。
（前提：本地 8000 端口空闲；被占用则先停掉占用进程。`.env` 已提供 `DEVOPS_BASE_URL`，启动不依赖后端可达。）

- [ ] **Step 4: 提交**

```bash
git add main.py
git commit -m "feat: main.py host/port 支持 MCP_HOST/MCP_PORT 环境变量覆盖（默认 127.0.0.1:8000 不变）" -- main.py
```

---

### Task 2: 新增 .dockerignore + Dockerfile 并构建

**Files:**
- Create: `.dockerignore`
- Create: `Dockerfile`

- [ ] **Step 1: 前置检查本地 Docker**

```bash
docker --version && docker info --format '{{.OSType}}'
```
Expected: 版本号 + `linux`（Docker Desktop 构建的 就是 Linux 镜像，可直接 load 到 Linux 服务器）。失败则先装 Docker Desktop，后续任务暂停。

- [ ] **Step 2: 写 .dockerignore**

```
# 真实凭据绝不进镜像（服务器侧配置走 docker run -e）
.env
.git
.gitignore
.venv
.idea
.claude
__pycache__
*.pyc
docs
CLAUDE.md
*.tar
```

- [ ] **Step 3: 写 Dockerfile**

```dockerfile
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
```

- [ ] **Step 4: 构建镜像**

```bash
docker build -t devops-mcp:0.1.0 .
```
Expected: 构建成功无报错（首次拉基础镜像较慢属正常）。

- [ ] **Step 5: 提交**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: 新增 Dockerfile 与 .dockerignore（python:3.13-slim + uv 0.11.14，.env 不进镜像）" -- Dockerfile .dockerignore
```

---

### Task 3: 本地容器端到端冒烟（纯验证，无提交）

**Files:** 无改动。

- [ ] **Step 1: 起容器（宿主 8001 避免与本地开发 8000 冲突）**

```bash
docker run -d --name devops-mcp-smoke -p 8001:8000 \
  -e DEVOPS_BASE_URL=http://host.docker.internal:14080 \
  devops-mcp:0.1.0
```
Expected: 输出容器 ID。（冒烟只验证启动与端口映射，启动阶段不连后端，`DEVOPS_BASE_URL` 只要非空即可；`host.docker.internal` 指向宿主机——Docker Desktop for Windows 可靠，若将来在 Linux 宿主上重跑需加 `--add-host=host.docker.internal:host-gateway`。）

- [ ] **Step 2: 探活**

```bash
sleep 5
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/mcp
```
Expected: 4xx（405/406）。`000` = 失败。

- [ ] **Step 3: 看日志并清理**

```bash
docker logs devops-mcp-smoke
docker rm -f devops-mcp-smoke
```
Expected: 日志含服务启动信息、无 Traceback。**若日志出现 `ValueError`（缺 DEVOPS_BASE_URL）说明 -e 注入没生效，回查 Step 1。**

---

### Task 4: CLAUDE.md 同步

**Files:**
- Modify: `CLAUDE.md`（Transport 章节）

- [ ] **Step 1: 更新 Transport 段**

在该章节"Server runs with `transport="streamable-http"` at `127.0.0.1:8000`"处补充：默认 `127.0.0.1:8000`，可用 `MCP_HOST`/`MCP_PORT` 环境变量覆盖（Docker 部署绑 `0.0.0.0`）；根目录 `Dockerfile`/`.dockerignore` 用于构建部署镜像，`DEVOPS_BASE_URL` 运行时 `-e` 注入。

- [ ] **Step 2: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md Transport 章节同步 MCP_HOST/MCP_PORT 与 Docker 部署说明" -- CLAUDE.md
```

---

### Task 5: 镜像导出与服务器部署（导出在本地执行；scp/服务器命令由用户执行）

**Files:** 无代码改动；产物 `devops-mcp.tar`（不入库，加进 .gitignore 不必要——`.dockerignore` 已挡构建上下文，git 侧它本来就是未跟踪文件，注意**别裸 `git add .`**）。

- [ ] **Step 1: 导出镜像**

```bash
docker save -o devops-mcp.tar devops-mcp:0.1.0
```
Expected: 当前目录生成 `devops-mcp.tar`（约 150-250MB）。

- [ ] **Step 2: 交付用户执行（服务器侧命令清单，照 spec runbook）**

```bash
# 传输（替换 user@<服务器> 与后端地址）
ssh user@<服务器> "mkdir -p /opt/devops-mcp"   # scp 目标目录需已存在
scp devops-mcp.tar user@<服务器>:/opt/devops-mcp/
ssh user@<服务器> "docker load -i /opt/devops-mcp/devops-mcp.tar"

# 服务器启动
docker run -d --name devops-mcp --restart unless-stopped \
  -p 8000:8000 -e DEVOPS_BASE_URL=http://<后端地址> devops-mcp:0.1.0

# 验证
docker logs devops-mcp
curl http://<服务器IP>:8000/mcp
# Electron 客户端 MCP 地址改为 http://<服务器IP>:8000/mcp，实测工具调用
```

- [ ] **Step 3: 用户确认服务器部署成功后，在本地清理冒烟产物**

```bash
rm -f devops-mcp.tar
```
