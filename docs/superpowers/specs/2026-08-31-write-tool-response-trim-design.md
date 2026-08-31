# 写操作工具响应精简设计（create_workitem / update_workitem_description / change_workitem_status）

- 日期：2026-08-31
- 分支：devops-mcp2.0
- 状态：设计已获用户确认，待实施

## 背景与动机

`change_workitem_status` 与 `update_workitem_description`（后者为工作区未提交的 WIP）目前把后端 PUT/POST 返回的**完整工作项 JSON**（实测约 8~9KB）原样透传给 MCP 客户端。完整响应包含：

- 20+ 个恒为 null 的噪声字段（`ext1`~`ext11`、`envType`、`bugReason` 等）
- 完整嵌套对象 `project`（含 `projectTemplateConfigModel`、公告、extend）、`iteration`、`version`、`module`——而这些的扁平摘要（`iterationName`/`versionName`/`moduleName`）本就在顶层
- `workitemComments` 全量评论正文、`fieldChangeRecords` 全量变更记录——随工作项生命周期**无界增长**

问题：

1. **上下文成本**：MCP 工具结果全部进入 LLM 上下文，单次约 2.5~3K tokens，大半是噪声。
2. **零信息增益回显**：`update_workitem_description` 把客户端刚发送的 description（可达数 KB HTML）原样回显。
3. **违反项目惯例**：CLAUDE.md 明确 "only expose the fields a client needs — existing tools deliberately drop most raw fields"。`get_workitem_list` 精简到 15 字段、`get_workitem_details` 7 字段、`create_workitem` 已有 23 字段精简（`devops_client.py` create_workitem 的 result dict）。两个裸透传的写工具是例外。
4. **职责重叠**：写操作应"确认结果"；看全量详情是读操作（`get_workitem_details` / `get_workitem_list`）的职责。

用户已确认：**客户端不依赖写操作返回的完整详情，确认成功即可**。

## 方案选择

采用**方案 B：每操作最小确认**（用户选定）。

备选方案（已否决）：

- **A 统一摘要**：抽共享 `_summarize_workitem`，三个写工具返回同一份摘要。否决原因：状态变更场景下多余字段无收益；用户偏好绝对最小 token。
- **C 重塑上移工具层**：client 层裸返，tools 层统一重塑。否决原因：需把 create 已提交的重塑从 client 层搬走，纯搬家重构；项目现状本就两种惯例并存（list 在工具层、create 在 client 层），搬移无实际收益。

## 返回形状

| 工具 | 返回字段 |
|---|---|
| `create_workitem` | `workitem_id`, `workitem_key`, `title`, `workitem_type_name`, `workitem_status`, `workitem_status_name`, `priority`, `assignee_emp_name`, `create_time`（9 字段） |
| `update_workitem_description` | `workitem_id`, `workitem_key`, `title`, `update_time`（4 字段） |
| `change_workitem_status` | `workitem_id`, `workitem_key`, `workitem_status`, `workitem_status_name`, `update_time`（5 字段） |

设计依据：

- **键名沿用 snake_case**：与 `create_workitem` 已提交的精简形状一致；不趁机改 camelCase——那会重命名既有键，破坏面更大。
- **create 保留最多**：新工作项对 agent 是全未知的，需要身份 + 初始状态。砍掉的 14 个字段（`iteration_id`/`version_id`/`module_id`/`project_*`/`parent_workitem_*`/`due_time`/`time_estimate`/`assignee`/`workitem_type_id` 等）要么由服务端 header 决定、要么是 agent 刚传入的回显，无信息增益。
- **两个更新类工具都带 `workitem_key`**：agent 向用户汇报用人类可读标识（如 IPAAS-283），而非它自己传入的 `workitem_id`。
- **`update_time`**：后端回显的 updateTime，是"写入已落地"的证据。

字段映射（snake_case ← 后端 camelCase）：`workitem_id`←`workitemId`、`workitem_key`←`workitemKey`、`workitem_type_name`←`workitemTypeName`、`workitem_status`←`workitemStatus`、`workitem_status_name`←`workitemStatusName`、`assignee_emp_name`←`assigneeEmpName`、`create_time`←`createTime`、`update_time`←`updateTime`。

**破坏性变更**：`create_workitem` 返回从 23 字段缩减为 9 字段（已提交行为的变化）。用户已确认无客户端依赖被砍字段。

## 实现要点

### devops_client.py

1. `create_workitem`：现有 result dict（353-378 行区域）缩减为 9 字段。
2. `update_workitem_description`：`r.json()` 后重塑为 4 字段 dict；**顺带修正 docstring**（现误写为"变更工作项状态an"）。
3. `change_workitem_status`：`r.json()` 后重塑为 5 字段；同样修正误写 docstring。
4. 不抽共享 helper：三个形状各异，内联小 dict 与 create 现有风格一致。

### tools/workitems.py

1. 三个工具的 `description` 各加一句返回字段说明（`update_workitem_description` 现有 description 完全未提返回形状）。
2. 修正 `update_workitem_description` 工具的复制错文案：函数 docstring"添加工作项评论"→"修改工作项内容描述"；except 分支日志与错误文案"添加评论失败"→同步修正。
3. `change_workitem_status` 的 description 补充返回形状（新状态 + updateTime 确认变更生效）。

### 不变的契约

- 错误契约不动：失败返回 `{"error": ...}` JSON 文本，非 isError。
- 工具注册方式、`structured_output=False`、权限映射（TOOL_PERMISSIONS）均不动。
- 读侧工具（`get_workitem_details` / `get_workitem_list`）不动。

## 验证

```bash
# 语法检查
uv run python -m py_compile main.py server.py permissions.py config.py devops_client.py tools/workitems.py tools/attachments.py tools/testcases.py

# 工具注册数量仍为 13
uv run python -c "import asyncio, tools, main; print(len(asyncio.run(main.mcp.list_tools())))"
```

真实后端冒烟（终验）：对测试工作项执行改描述 / 改状态，确认返回为精简形状且字段值正确（token 可从 `~/.claude.json` 的 mcpServers headers 取，注意会过期；或由用户在其 Electron 客户端实测）。

## 明确不做

- 不改读侧两个工具的返回形状
- 不抽共享重塑 helper
- 不动错误契约 / 权限 / 注册机制
- 不处理 `add_workitem_comment` 的返回（评论接口返回的是评论对象而非完整工作项，不在本次范围；如需另行精简另开任务）
