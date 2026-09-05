# 会话级 DevOps 项目动态切换 — 设计文档

日期：2026-09-05
分支：devops-mcp2.0
状态：已实施（含真实后端全链路冒烟，10/10 断言通过）

## 背景与问题

一个代码库可能对应多个 DevOps 项目（开发项目 + 维护项目，如 ESB 341 与 ESB维护 1468）。
MCP 客户端（Claude Code）在 server 配置里用 `X-DevOps-Project-ID` header 固定注入项目，
服务端 `ClientRegistry` 按凭据 5 元组缓存 client、权限中间件按该 projectId 拉权限码
——**一个 server 配置 = 一个项目**，会话中途无法切换。

## 设计决策（用户确认）

1. **会话内切默认项目**：`switch_project` 工具切换后，该会话后续调用默认走新项目。
2. **header 模式完全向后兼容**：没切换过 = 与现状逐字节一致；切换过 = 覆盖值优先；
   会话结束/重连 = 自动回落 header 模式，无需任何客户端配置改动。
3. **切换范围 = 项目 + 可选上下文**：三个可选 ID（iteration/module/version）默认重置
   为空（旧项目的值对新项目无意义；空串是既有受支持状态；同时保护 create_workitem
   的 `affectVersionIds` 不挂旧项目版本）。
4. 附带 `list_projects` 工具暴露既有零调用方法 `get_project()`。

## 核心机制

- **会话身份 = `mcp-session-id` HTTP header**：工具层 `ctx.headers` 与中间件层
  `ctx.request.headers` 是同一个 Starlette Request 的 header（SDK 源码核实：
  `ctx.headers = getattr(request_context.request, "headers", None)`）。
- **单点解析**：`ClientRegistry.get(headers, override=None)` 内部自动
  `sessions.resolve(headers)`；命中覆盖则整体替换凭据 kwargs 的 4 个项目字段
  （在必填校验之后——header 仍是必填基线），5 元组 key 随之区分 → 目标项目获得
  独立 client 实例与独立权限缓存。仅 `switch_project` 试构造时显式传 `override`。
  由此 **permissions.py / get_client / 既有 14 个工具零代码改动**，权限按"生效项目"
  校验由构造保证（middleware 与 handler 同 key → 同一 client 实例）。
- **安全边界**：覆盖只碰 project/iteration/module/version 四字段，`afc_token` 与
  `base_url` 永不可被覆盖（切项目 ≠ 切身份/切后端）。
- SDK 内建中间件（RequestStateBoundary、OTel）在用户中间件外层、不构造 client，
  无泄漏路径；`Connection.state` 当前版本不暴露给 `ServerRequestContext`（不可用），
  故自建有界 LRU store。

## 组件

| 文件 | 内容 |
|---|---|
| `session_context.py`（新） | `SessionContext` NamedTuple + `SessionContextStore`（OrderedDict LRU，maxsize=256 与 ClientRegistry 对齐）+ `session_id_from_headers()` + 单例 `sessions` |
| `server.py` | `ClientRegistry.get` 加 `override` 参数 + 内部自动解析；shutdown lifespan 清空 store |
| `tools/projects.py`（新） | `list_projects` / `switch_project` |
| `permissions.py` | 零代码改动；docstring/映射表注释补"按生效项目校验"与有意不映射说明 |
| `tools/__init__.py` | 注册 projects 域 |

### switch_project 流程（fail-closed）

1. 无 `ctx.headers` → error；无 `mcp-session-id` → error（stateless/stdio 不支持）。
2. `project_id == ""` → 清除覆盖，回落 header 默认，返回 `{"status": "reset", ...}`。
3. 试构造目标 client（`_registry.get(headers, override=target)`）——verify 失败不入
   注册表缓存、不写会话覆盖（现状不变）。
4. `get_permissions()` 失败 → 拒绝（fail-closed；成功则预热权限缓存）。
   **冒烟实测：权限接口对不存在项目返回空而非报错**，故需下一步硬校验。
5. 项目列表硬校验：`get_project()` 列表不含目标 → `{"error", "available_projects"}`；
   形状异常 → warning 放行（由 3/4 兜底）。
6. `sessions.set(sid, target)` → 返回 `{"status": "switched", "project_id",
   "project_name", "iteration_id", "module_id", "version_id", "session_id_head", "note"}`。

### list_projects 返回（实测形状固化）

`get_project()` → `GET /api/devops/pm/projects/actions/querybyuser`，返回分页 dict
`{"data": [...], "total", ...}`；项目字段：`projectId`（字符串）、`projectCode`、
`projectName`、`projectType`（D=开发 / M=维护）、`projectStatus`。工具重塑为
`{"total", "items": [{"project_id", "project_code", "project_name", "project_type",
"project_type_name", "project_status", "is_current"}]}`。

## 权限与工具映射

`list_projects` / `switch_project` **有意不进 TOOL_PERMISSIONS**（未映射=放行）：
权限码均为项目内业务权限，无对应码；前者只读且按 token 自限；后者自身三重
fail-closed 校验，且切换后的业务调用仍经权限中间件按目标项目校验。
决策注释写在 `TOOL_PERMISSIONS` 定义上方。

## 兼容性矩阵

| 场景 | 行为 |
|---|---|
| 未切换 | resolve → None → 与现状逐字节一致 |
| 切换后 | 4 字段覆盖进 key；权限/查询/创建全走目标项目 client |
| 会话结束/重连/重启/LRU 淘汰 | 覆盖消失 → 静默回落 header 模式（有日志 + note 预告） |
| 同 token 多会话 | 按 sid 各自独立 |
| stateless（2026-07-28）/stdio | 无法切换（明确报错），header 模式不受影响 |
| header 缺 Project-ID 但已切换 | 必填校验先于覆盖 → 仍报错（header 是必填基线） |

## 已知限制

- 同会话"切换与其他调用并发"存在极窄 TOCTOU（middleware 校验项目 vs handler 生效
  项目瞬时不一致）；Claude Code 顺序使用不受影响；未来可用 ContextVar 消除。
- LRU 淘汰（>256 个切换会话）静默回落 + WARNING 日志；会话结束无回调，靠 LRU 兜底。
- `get_permissions` 对不存在项目不报错（返回空）——可达性靠项目列表硬校验兜住。

## 验证记录（2026-09-05 实测）

- 语法编译全过；工具注册 16 个（含 list_projects / switch_project）。
- session store 单元冒烟：set/resolve/clear、无 sid → None、混合大小写 → 命中、
  LRU 淘汰最旧 + resolve 刷新顺序、空串 sid → None，全过。
- **SDK 全链路（真实后端，10/10 断言）**：
  - 切换前 `list_projects` is_current=341（ESB，header 默认）；
  - `switch_project("1468")` → status=switched, project_name=ESB维护；
  - 切换后 `list_projects` is_current=1468（覆盖解析端到端生效）；
  - `get_workitem_list`：切换前 `ESB-201xx`（88 条）/ 切换后 `ESBWEIHU-xxx`
    （44 条）——同一会话同一 header，数据落点切换成功；且该工具为权限映射工具，
    成功返回 = middleware 按目标项目权限校验通过；
  - 会话隔离：A 切换后新开 B 会话（同 token）仍 is_current=341；
  - 乱写 999999 → `{"error", "available_projects"}`（正常结果，符合工具层错误契约），
    且现状保持 1468（失败不变现状）；
  - `switch_project("")` 重置 → is_current 回 341。

## 工具数

注册 16 / 映射 15（14 现役映射 + 禁用的 get_attachment_resource；list_projects /
switch_project 有意不映射）。CLAUDE.md、README.md、main.py 注释已同步。
