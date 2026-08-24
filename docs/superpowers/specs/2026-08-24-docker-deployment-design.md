# Docker 部署设计（devops-mcp-master）

日期：2026-08-24
状态：已确认（用户批准）

## 背景与目标

MCP 服务器目前只在开发机本地运行（`uv run python main.py`，监听 `127.0.0.1:8000/mcp`）。
目标：打包为 Docker 镜像部署到 Linux 服务器，MCP 客户端（Electron 等）通过
`http://服务器IP:8000/mcp` 直连使用。

## 已确认的部署约束（用户选定）

| 约束 | 选择 |
|---|---|
| 服务器环境 | Linux + Docker |
| 客户端访问 | 直连 `http://IP:8000/mcp`（无反向代理、无 TLS） |
| 后端可达性 | 服务器与 DevOps 后端同内网，可直连 `DEVOPS_BASE_URL` |
| 交付方式 | 本地（Windows + Docker Desktop）构建镜像 → `docker save`/`scp`/`docker load` 上传，不走 registry |

## 设计

### 1. 代码改动（唯一一处）

`main.py` 的 `host`/`port` 从环境变量读取，默认值不变：

```python
host=os.getenv("MCP_HOST", "127.0.0.1"),
port=int(os.getenv("MCP_PORT", "8000")),
```

- 本地开发零变化（不设变量即 `127.0.0.1:8000`）。
- 容器内必须绑 `0.0.0.0`：容器内进程绑 127.0.0.1 时宿主机 `-p` 端口映射无法转发进来，
  这是 Docker 网络的硬性要求。Dockerfile 中设 `ENV MCP_HOST=0.0.0.0`。

### 2. 新增文件

**`Dockerfile`**

- 基础镜像 `python:3.13-slim`（项目 `requires-python >=3.13`）。
- uv 采用官方推荐模式并**固定版本**：`COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /usr/local/bin/`
  （与本地生成 `uv.lock` 的 uv 同版，避免 latest 大版本漂移破坏 lock 兼容性）。
- 分层：先只 COPY `pyproject.toml` + `uv.lock` 执行 `uv sync --frozen --no-dev`
  （依赖层缓存，源码改动不重装依赖），再 COPY 源码。
- `ENV PATH="/app/.venv/bin:$PATH"` 把 venv 前置；`ENV MCP_HOST=0.0.0.0`；
  `CMD ["python", "main.py"]` —— 明确选 venv-PATH + 裸 `python`（而非 `uv run`，
  后者启动时会二次解析环境），与"运行时不重装依赖"目标一致。
- 端口声明 `EXPOSE 8000`（文档性质）。

**`.dockerignore`**

排除 `.env`（真实凭据，绝不进镜像）、`.git`、`.venv`、`.idea`、`.claude`、
`docs/`、`__pycache__` 等。

### 3. 配置注入

- `DEVOPS_BASE_URL` 不打进镜像，运行时 `docker run -e DEVOPS_BASE_URL=...` 注入。
  `config.py` 现有逻辑：进程环境变量优先于 `.env`，缺失时 fail-fast 拒绝启动——符合预期。
- 镜像与后端解耦：换后端地址只需重跑容器改环境变量，不用重新构建。

### 4. 运行方式

```bash
docker run -d --name devops-mcp --restart unless-stopped \
  -p 8000:8000 \
  -e DEVOPS_BASE_URL=http://<后端地址> \
  devops-mcp:0.1.0
```

`--restart unless-stopped`：开机自启 + 异常退出自动拉起（手动 stop 后不自启）。

## 部署 runbook

```bash
# ── 本地（Windows / Docker Desktop）──
docker build -t devops-mcp:0.1.0 .
docker save -o devops-mcp.tar devops-mcp:0.1.0   # -o 写文件，Git Bash / PowerShell 通用

# ── 传输 ──
scp devops-mcp.tar user@<服务器>:/opt/devops-mcp/
ssh user@<服务器> "docker load -i /opt/devops-mcp/devops-mcp.tar"

# ── 服务器：启动 ──
docker run -d --name devops-mcp --restart unless-stopped \
  -p 8000:8000 -e DEVOPS_BASE_URL=http://<后端地址> devops-mcp:0.1.0

# ── 验证 ──
docker logs devops-mcp                    # 启动日志无报错
curl http://<服务器IP>:8000/mcp           # 探活（非 POST 请求返回 4xx/握手响应即通）
# Electron 客户端 MCP 地址改为 http://<服务器IP>:8000/mcp，headers 带法不变，实测工具调用

# ── 升级 ──
# 本地改版本号重新 build + save + scp + load 后：
docker rm -f devops-mcp && docker run -d --name devops-mcp --restart unless-stopped \
  -p 8000:8000 -e DEVOPS_BASE_URL=http://<后端地址> devops-mcp:<新版本>
```

## 注意事项

- **安全边界**：直连 IP 意味着内网可达 8000 端口的人都能尝试连接；但每次工具调用都需
  有效 `X-DevOps-afcToken`（后端校验 + 权限中间件 fail-closed），实际暴露面与 DevOps
  平台本身一致。
- **`.env` 不进镜像也不上传**：服务器侧配置全部走 `docker run -e`。
- 首次部署前置：本地安装 Docker Desktop；服务器 8000 端口未占用、防火墙放行。

## 明确不做（YAGNI）

- docker-compose / k8s 编排（单容器，`docker run` 足够）
- CI/CD 流水线、自动化部署脚本（升级就 3 条命令）
- 健康检查（healthcheck）、TLS/反向代理（客户端直连 HTTP，与现状一致）
- 多后端多实例（一实例一后端是现有架构约束）
