# 写操作工具响应精简 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `create_workitem` / `update_workitem_description` / `change_workitem_status` 三个写工具的响应从全量工作项 JSON（8~9KB）精简为每操作最小确认形状（9/4/5 字段）。

**Architecture:** 全部重塑发生在 `devops_client.py` 各方法内联小 dict（与 create 现有 client 层精简惯例一致），`tools/workitems.py` 只改 description 文案与复制错文案，错误契约 `{"error": ...}` 不动。附带修复：WIP 新工具 `update_workitem_description` 缺失的 `TOOL_PERMISSIONS` 映射（**spec 偏差**，理由见 Task 5），以及 CLAUDE.md 过时工具数。

**Tech Stack:** Python 3.13 + uv；无测试套件，验证用 `py_compile` + `mcp.list_tools()` 数量核对 + 真实后端冒烟。

**Spec:** `docs/superpowers/specs/2026-08-31-write-tool-response-trim-design.md`

**工作区注意（重要）：** 不建 worktree——工作区有未提交 WIP（`update_workitem_description` 的 client 方法与工具），本计划在其之上修改，worktree 从 HEAD 检出会丢失 WIP。全程在当前分支 `devops-mcp2.0` 工作区操作。提交一律 pathspec（`git commit -m ... -- <path>`），避免带入用户暂存的 `.idea/` 等文件；首次提交 devops_client.py / tools/workitems.py 会自然包含该 WIP，提交信息如实注明。

**字段映射（三个任务共用，snake_case ← 后端 camelCase，均 `.get()` 取值）：**
`workitem_id`←`workitemId`、`workitem_key`←`workitemKey`、`workitem_type_name`←`workitemTypeName`、`workitem_status`←`workitemStatus`、`workitem_status_name`←`workitemStatusName`、`assignee_emp_name`←`assigneeEmpName`、`create_time`←`createTime`、`update_time`←`updateTime`；`title`/`priority` 同名直传。

---

### Task 1: devops_client.py — create_workitem 缩减为 9 字段

**Files:**
- Modify: `devops_client.py:306-307`（docstring Returns）
- Modify: `devops_client.py:353-378`（result dict）

- [ ] **Step 1: 更新 docstring Returns 行**

把（约 306-307 行）：

```python
        Returns:
            工作项关键信息 dict（workitem_id、workitem_key、状态、负责人等）
```

改为：

```python
        Returns:
            最小确认 dict（9 字段）：workitem_id、workitem_key、title、workitem_type_name、
            workitem_status、workitem_status_name、priority、assignee_emp_name、create_time
```

- [ ] **Step 2: 缩减 result dict**

把（353-378 行）现有 24 字段的 `result = {...}` 整块替换为：

```python
        result = {
            "workitem_id": data.get("workitemId"),
            "workitem_key": data.get("workitemKey"),
            "title": data.get("title"),
            "workitem_type_name": data.get("workitemTypeName"),
            "workitem_status": data.get("workitemStatus"),
            "workitem_status_name": data.get("workitemStatusName"),
            "priority": data.get("priority"),
            "assignee_emp_name": data.get("assigneeEmpName"),
            "create_time": data.get("createTime"),
        }
```

其余代码（校验、workitem 构造、POST）不动。

- [ ] **Step 3: 语法检查**

Run: `uv run python -m py_compile devops_client.py`
Expected: 无输出（退出码 0）

### Task 2: devops_client.py — change_workitem_status 重塑 5 字段

**Files:**
- Modify: `devops_client.py:436-450`（整个方法）

- [ ] **Step 1: 替换整个方法（修 docstring 错文案 + 重塑返回）**

把现有方法（docstring 误写"变更工作项状态an"、裸 `return r.json()`）替换为：

```python
    async def change_workitem_status(self,workitem_id:str,workitem_status:str):
        """变更工作项状态

        Args:
            workitem_id: 工作项id
            workitem_status: 工作项状态，例如：open=待解决 ,in-progress=处理中,reopened=重新打开

        Returns:
            最小确认 dict（5 字段）：workitem_id、workitem_key、workitem_status、
            workitem_status_name、update_time
        """
        payload={"workitem":{"workitemId":workitem_id,"workitemStatus":workitem_status}}
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}"
        r = await self.put(url,payload)
        data = r.json()
        return {
            "workitem_id": data.get("workitemId"),
            "workitem_key": data.get("workitemKey"),
            "workitem_status": data.get("workitemStatus"),
            "workitem_status_name": data.get("workitemStatusName"),
            "update_time": data.get("updateTime"),
        }
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m py_compile devops_client.py`
Expected: 无输出

### Task 3: devops_client.py — update_workitem_description 重塑 4 字段

**Files:**
- Modify: `devops_client.py:405-419`（整个方法，WIP 未提交代码）

- [ ] **Step 1: 替换整个方法（修 docstring 错文案 + 重塑返回 + 补 to_rich_text）**

**评审补充（spec 外缺陷修复，与 Task 6 同类）：** WIP 工具 description 向 LLM 承诺"传入纯文本时自动包装为 `<p>` 段落"，但原实现裸传 description、未调 `to_rich_text`（`create_workitem` 是调用的，见 devops_client.py:326）——纯文本描述在平台上会渲染成一坨。本任务补上 `to_rich_text(description)` 使行为与已发布的工具说明一致。

把现有方法（docstring 误写"变更工作项状态an"、裸 `return r.json()`、裸传 description）替换为：

```python
    async def update_workitem_description(self,workitem_id:str,description:str):
        """修改工作项内容描述

        Args:
            workitem_id: 工作项id
            description: 工作项内容描述，HTML 富文本格式；纯文本（无标签）自动包装为 <p> 段落

        Returns:
            最小确认 dict（4 字段）：workitem_id、workitem_key、title、update_time
        """
        payload={"workitem":{"workitemId":workitem_id,"description":to_rich_text(description)}}
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}"
        r = await self.post(url,payload)
        data = r.json()
        return {
            "workitem_id": data.get("workitemId"),
            "workitem_key": data.get("workitemKey"),
            "title": data.get("title"),
            "update_time": data.get("updateTime"),
        }
```

（`to_rich_text` 是 devops_client.py 模块内已有函数，无需新增 import。）

- [ ] **Step 2: 语法检查**

Run: `uv run python -m py_compile devops_client.py`
Expected: 无输出

### Task 4: 提交 devops_client.py（含落地 WIP 方法）

- [ ] **Step 1: 提交**

```bash
git commit -m "feat: 三写工具客户端响应精简为最小确认形状（含落地 update_workitem_description 方法）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" -- devops_client.py
```

（pathspec 提交；diff 自然包含未提交的 update_workitem_description 方法本体 + 三处重塑。）

### Task 5: tools/workitems.py — 三个工具的 description 与错文案修正

**Files:**
- Modify: `tools/workitems.py:138`（create description 末句，替换）
- Modify: `tools/workitems.py:184-205`（update 工具：description 加返回说明、docstring、日志/错误文案）
- Modify: `tools/workitems.py:251-257`（change description 加返回说明）

- [ ] **Step 1: create_workitem description 末句替换**

把（138 行）：

```
返回创建后的工作项关键信息（含 workitem_key、状态、负责人等）。
```

替换为：

```
返回最小确认信息（9 字段）：workitem_id、workitem_key、title、workitem_type_name、workitem_status、workitem_status_name、priority、assignee_emp_name、create_time；需要完整详情请调用 get_workitem_details。
```

- [ ] **Step 2: update_workitem_description 工具三处修正**

a) description 末尾（`"""` 前）追加一段：

```
【返回】最小确认信息（4 字段）：workitem_id、workitem_key、title、update_time。
```

b) 函数 docstring（196 行）`"""添加工作项评论"""` 改为 `"""修改工作项内容描述"""`。**注意**：`"""添加工作项评论"""` 在 `add_workitem_comment`（171 行）有同文，编辑时必须带前面的 `async def update_workitem_description(...)` 行消歧：

```python
# old（含 def 行上下文）
async def update_workitem_description(ctx: Context, workitem_id: str, description: str) -> Dict[str, Any]:
    """添加工作项评论"""
# new
async def update_workitem_description(ctx: Context, workitem_id: str, description: str) -> Dict[str, Any]:
    """修改工作项内容描述"""
```

c) except 分支（204-205 行）两处"添加评论失败"改为"修改工作项内容描述失败"。**注意**：`add_workitem_comment` 的 except（181-182 行）完全同文，必须带前面的 `logger.info("修改工作项内容描述成功")` 行消歧：

```python
# old（含成功日志行上下文）
        logger.info("修改工作项内容描述成功")
        return data
    except Exception as e:
        logger.error(f"添加评论失败: {str(e)}")
        return {"error": f"添加评论失败: {str(e)}"}
# new
        logger.info("修改工作项内容描述成功")
        return data
    except Exception as e:
        logger.error(f"修改工作项内容描述失败: {str(e)}")
        return {"error": f"修改工作项内容描述失败: {str(e)}"}
```

- [ ] **Step 3: change_workitem_status description 加返回说明**

在 description 的编号流程（"3. 变更失败时…"）之后追加一行：

```

【返回】变更成功返回最小确认信息（5 字段）：workitem_id、workitem_key、workitem_status、workitem_status_name、update_time（workitem_status 为变更后的新状态值）。
```

- [ ] **Step 4: 语法检查**

Run: `uv run python -m py_compile tools/workitems.py`
Expected: 无输出

- [ ] **Step 5: 提交**

```bash
git commit -m "feat: 注册 update_workitem_description 工具；三写工具 description 补返回形状说明并修正错文案

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" -- tools/workitems.py
```

### Task 6: permissions.py — 补 update_workitem_description 权限映射（spec 偏差）

**spec 声明了"权限映射不动"，此处偏差的理由：** WIP 工具 `update_workitem_description` 当前**未映射**——按 middleware 规则"未映射工具放行"，这个写工具正处于无权限校验直接放行状态，违背项目 fail-closed 惯例与 CLAUDE.md"新工具应刻意映射"的指引。映射码取 `project_kanban_workitem_edit`，与其同级写操作 `change_workitem_status`/`add_workitem_comment`/`get_next_workitem_status_list` 一致。

**Files:**
- Modify: `permissions.py:33`（TOOL_PERMISSIONS，在 change_workitem_status 行后插入）

- [ ] **Step 1: 插入映射条目**

在 `"change_workitem_status": "project_kanban_workitem_edit",` 之后插入：

```python
    "update_workitem_description": "project_kanban_workitem_edit",
```

- [ ] **Step 2: 语法检查 + 提交**

Run: `uv run python -m py_compile permissions.py`
Expected: 无输出

```bash
git commit -m "feat: 补 update_workitem_description 权限映射（project_kanban_workitem_edit，修复未映射放行缺口）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" -- permissions.py
```

### Task 7: 全量验证 + CLAUDE.md 工具数同步

**Files:**
- Modify: `CLAUDE.md`（三处过时工具数）

- [ ] **Step 1: 全量语法检查**

Run: `uv run python -m py_compile main.py server.py permissions.py config.py devops_client.py tools/workitems.py tools/attachments.py tools/testcases.py`
Expected: 无输出

- [ ] **Step 2: 工具注册核对**

Run: `uv run python -c "import asyncio, tools, main; print(len(asyncio.run(main.mcp.list_tools())))"`
Expected: `14`（13 既有 + update_workitem_description；get_attachment_resource 仍禁用不计）

- [ ] **Step 3: CLAUDE.md 三处工具数修正**

现状（实测）：14 注册 / 14 映射（映射已含禁用的 get_attachment_resource，未含 update_workitem_description）；Task 6 落地后为 15 映射 = 14 个已注册工具 + 1 个禁用项。改三处：

1. Common Commands 注释 `# Confirm tool registration (12 tools currently)` → `(14 tools currently)`
2. Architecture 节 `permissions.py → TOOL_PERMISSIONS mapping (13 tools)` → `(15 tools: 14 registered + disabled get_attachment_resource)`
3. Known Gotchas 节 `Tool count is 12 registered vs 13 mapped.` → `Tool count is 14 registered vs 15 mapped (mapping includes the disabled get_attachment_resource; update_workitem_description mapped in 2026-08-31).`

- [ ] **Step 4: 提交**

```bash
git commit -m "docs: CLAUDE.md 工具数同步（14 注册 / 15 映射，含 update_workitem_description）

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" -- CLAUDE.md
```

### Task 8: 真实后端冒烟（终验）

无自动化手段，二选一执行：

- [ ] **Step 1: 冒烟三工具**

优先由用户在 Electron 客户端实测；或 Claude 从 `~/.claude.json` 的 mcpServers headers 取 token（会过期，多副本逐个试，勿回显）对测试工作项执行：

1. `create_workitem`（测试项目建一个可废弃项）→ 确认返回恰为 9 字段
2. `update_workitem_description` → 确认 4 字段；**重点确认响应是否回显 `updateTime`**（状态 PUT 实测有；若此 POST 不回显则 `update_time` 为 null，可接受，记录即可）
3. `get_next_workitem_status_list` + `change_workitem_status`（用返回的合法 toStatus）→ 确认 5 字段且 `workitem_status` 为新状态；结束后把状态改回或关闭废弃项

**冒烟注意：** Task 6 落地后 `update_workitem_description` 需要持有 `project_kanban_workitem_edit` 权限码——若冒烟时该工具返回权限拒绝 JSON（`required_permission` 字段），那是中间件在正常工作，不是响应形状缺陷。

- [ ] **Step 2: 冒烟结果记录**

发现形状/字段问题 → 回到对应任务修正重验；全部通过 → 向用户汇报完成。
