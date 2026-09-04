# DevOps MCP Server

把 DevOps 平台（工作项 / 测试用例管理系统）封装为 [MCP（Model Context Protocol）](https://modelcontextprotocol.io) 服务器，供 Claude Code 等支持 MCP 的 LLM 客户端调用，实现对话式的工作项查询/创建、评论、状态流转、附件查看与测试用例管理。

- 传输方式：Streamable HTTP（`http://127.0.0.1:8000/mcp`）
- 技术栈：Python 3.13 + [uv](https://docs.astral.sh/uv/) + `mcp[cli]` v2 + httpx + pydantic
- 设计文档：`docs/superpowers/specs/`，实施计划：`docs/superpowers/plans/`

## 工具清单（14 个）

### 工作项（tools/workitems.py）

| 工具 | 说明 |
|---|---|
| `get_workitem_list` | 查询工作项列表，支持按 key / 状态 / 类型筛选 |
| `get_workitem_details` | 按 id 获取工作项详情（标题、状态、优先级、负责人、附件等） |
| `create_workitem` | 创建故事 / 任务 / bug / 风险（类型映射与优先级换算在服务端完成） |
| `add_workitem_comment` | 为指定工作项添加评论 |
| `update_workitem_description` | 修改指定工作项的描述（纯文本自动包 `<p>`） |
| `get_next_workitem_status_list` | 查询工作项当前状态可流转的下一状态列表（后端动态获取，支持平台可配置流程） |
| `change_workitem_status` | 变更工作项状态（目标状态需传 `get_next_workitem_status_list` 返回的英文值） |

### 附件（tools/attachments.py）

| 工具 | 说明 |
|---|---|
| `get_attachment_preview` | 预览文本附件（txt/log/json/xml/yaml/yml）前 200 行或 5000 字符 |
| `get_attachment_chunk` | 按偏移量/长度读取文本附件片段 |
| `get_attachment_image` | 下载图片附件（png/jpg/jpeg/gif/webp，≤5MB），以 `ImageContent` 返回给多模态客户端 |

### 测试用例（tools/testcases.py）

| 工具 | 说明 |
|---|---|
| `get_testcase_groups` | 查询测试用例分组树 |
| `create_testcase_group` | 创建用例分组（创建用例前可先用它建目录） |
| `get_testcase_list` | 查询用例列表，操作步骤反解析为结构化列表 |
| `create_testcase` | 在指定分组下创建用例（服务端校验分组有效性，无效时返回可选分组引导） |

> 服务端还会做：P0–P4 ↔ highest/…/lowest 优先级换算（仅工作项）、工时小时→秒换算、富文本字段规范化（`to_rich_text`）。

## 快速开始

```bash
# 1. 安装依赖（Python 3.13，由 .python-version 锁定）
uv sync

# 2. 配置后端地址（唯一的服务端配置项）
cp .env.example .env
# 编辑 .env：DEVOPS_BASE_URL=http://<devops-host>[:port][/path]

# 3. 启动（默认 127.0.0.1:8000，路径 /mcp）
uv run python main.py
```

`DEVOPS_BASE_URL` 缺失时进程拒绝启动（fail-fast）。本地开发读 `.env`，部署时读进程环境变量（后者优先）。`MCP_HOST` / `MCP_PORT` 可覆盖默认监听地址（Docker 内已自动设 `MCP_HOST=0.0.0.0`）。

## MCP 客户端接入

凭据通过 HTTP 请求头按请求注入（**不在服务端配置**）：

| Header | 必填 | 说明 |
|---|---|---|
| `X-DevOps-afcToken` | ✅ | 用户登录态 token |
| `X-DevOps-Project-ID` | ✅ | 项目 ID（权限接口需要） |
| `X-DevOps-Iteration-ID` / `X-DevOps-Module-ID` / `X-DevOps-Version-ID` | 可选 | 迭代 / 模块 / 版本上下文 |

Claude Code 接入示例（先启动服务）：

```bash
claude mcp add --transport http devops http://127.0.0.1:8000/mcp \
  --header "X-DevOps-afcToken: <你的token>" \
  --header "X-DevOps-Project-ID: <项目ID>"
```

## 权限模型

每个工具调用先经过权限中间件（fail-closed）：

1. 从请求头取 `X-DevOps-Project-ID`，缺失即拒绝；
2. 用当前用户 `empId` 调 DevOps 权限接口获取权限码（按凭据缓存）；
3. 工具 → 权限码映射见 `permissions.py::TOOL_PERMISSIONS`，无对应权限码则拒绝（返回 `is_error=true` 的 JSON，含 `required_permission`）；
4. 后端不可达 / 权限接口失败同样拒绝；未映射的工具默认放行。

## Docker 部署

```bash
docker build -t devops-mcp:0.1.0 .
docker run -d --name devops-mcp \
  -p 8000:8000 \
  -e DEVOPS_BASE_URL=http://<devops-host>[:port][/path] \
  devops-mcp:0.1.0
```

`.env` 不会进入镜像（见 `.dockerignore`），后端地址一律通过 `-e` 注入。完整 runbook（save/scp/load、升级）见 `docs/superpowers/plans/2026-08-24-docker-deployment.md`。

## 架构

```
main.py            入口：日志配置、导入 tools 包、mcp.run()
server.py          共享 MCPServer 实例 + ClientRegistry（凭据键控 LRU 缓存，
                   全进程共享一个 httpx 连接池与并发闸门）+ get_client(ctx) 门面
permissions.py     TOOL_PERMISSIONS 映射 + fail-closed 权限中间件
devops_client.py   DevOps HTTP API 封装（认证、权限缓存、类型/优先级/工时换算）
tools/             工具适配层：workitems / attachments / testcases，import 即注册
config.py          Config 数据类，从 .env / 进程环境加载 DEVOPS_BASE_URL
```

分层原则：`tools/*.py` 是薄适配层（调 `DevOpsClient` → 重塑响应为最小字段集 → 异常统一转 `{"error": ...}` JSON，不抛出）；HTTP 细节全部收敛在 `devops_client.py`。多个用户共享一个连接池，凭据按请求头注入，后端并发始终 ≤ `MAX_CONCURRENT_REQUESTS`。

## 已知注意事项

- **`get_attachment_image` 的 `structured_output` 必须为 `False`**：返回注解含 SDK 的 `Image` 类（无 pydantic schema），开 `True` 注册即抛 `PydanticSchemaGenerationError`；且错误分支无 structuredContent 会被严格客户端以 -32600 拒收。
- **工具不得返回裸 list**：SDK 会把 list 拍平成多个 TextContent 块，部分客户端只读首块（实测丢过数据）。列表工具一律包成 `{"total", ..., "items"/"groups"/"cases"}` dict。
- 工具层错误是正常（非 isError）结果；只有权限中间件拒绝才置 `is_error=true`。
- 状态流转词表按类型区分（如「处理中」：bug/task/risk 为 `in-progress`，story 为 `developing`）；流转列表以后端动态接口为准，勿在前端硬编码。
- **部署环境若开着系统代理**（Windows 注册表代理 / 环境变量代理）：httpx 默认 `trust_env=True` 会把内网请求发给本地代理导致 502。内网部署建议给服务进程设 `NO_PROXY` 或改为直连。
- 附件 `fileUrl` 为后端文件仓库直链（Nexus）；`get_attachment_image` 的 `file_url`/`file_type` 取自 `get_workitem_details` 返回的 `attachments` 数组。
