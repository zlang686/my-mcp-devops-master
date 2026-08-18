import asyncio
import httpx
from typing import Optional, Any
import logging
REQUEST_TIMEOUT = 60

# 对 DevOps API 的最大并发请求数。
# 背景：MCP 客户端可能一次并发发起几十个工具调用（如批量创建工作项），
# SDK 层会全部转成对后端的并发 HTTP 请求，易把后端打满导致请求排队超时
# （客户端默认 60s 超时报 -32001）。此闸门使多余请求在本端排队而非压向后端。
MAX_CONCURRENT_REQUESTS = 5

workitem_type_map={
    "story":{
        "workitemTypeId":"2",
        "workitemTypeName":"user-story"
    },
    "task":{
        "workitemTypeId":"3",
        "workitemTypeName":"task"
    },
    "bug":{
        "workitemTypeId":"4",
        "workitemTypeName":"bug"
    },
    "risk":{
        "workitemTypeId":"5",
        "workitemTypeName":"risk"
    }
}

# 工作项类型ID → 类型名 反向映射（workitem_type_map 的逆向，用于从详情响应的 typeId 反查类型名）
WORKITEM_TYPE_ID_TO_NAME = {
    v["workitemTypeId"]: k for k, v in workitem_type_map.items()
}

# 工作项状态转换规则表：{类型名: {当前状态: [允许的目标状态]}}
# 键名与 workitem_type_map 的键一致（story/task/bug/risk）
# 规则源自 DevOps 平台定义
STATUS_TRANSITIONS = {
    "bug": {
        "closed": ["reopened"],
        "in-progress": ["closed", "open", "to-be-tested"],
        "open": ["closed", "in-progress", "to-be-tested"],
        "reopened": ["closed", "in-progress"],
        "testing": ["closed", "reopened", "verified"],
        "to-be-tested": ["closed", "reopened", "testing"],
        "verified": ["closed"],
    },
    "risk": {
        "closed": ["reopened"],
        "in-progress": ["closed", "resolved"],
        "open": ["closed", "in-progress", "resolved"],
        "reopened": ["closed", "in-progress", "resolved"],
        "resolved": ["closed", "reopened"],
    },
    "story": {
        "developing": ["open", "to-be-tested"],
        "open": ["developing"],
        "testing": ["open", "verified"],
        "to-be-tested": ["open", "testing"],
        "verified": ["open", "released"],
    },
    "task": {
        "done": ["in-progress", "to-do"],
        "in-progress": ["done", "to-do"],
        "to-do": ["done", "in-progress"],
    },
}

def priority_convert(priority: str) -> str:
    """将优先级转换为DevOps优先级"""
    priority_map = {
        "P0":"highest",
        "P1":"high",
        "P2":"medium",
        "P3":"low",
        "P4":"lowest"
    }
    return priority_map.get(priority, "1")
def man_hour_convert(man_hour:int) -> int:
    """将工时转换为DevOps工时"""
    return man_hour*3600

def to_rich_text(text: str) -> str:
    """将文本转为 DevOps 富文本字段格式。

    空值返回空串；疑似 HTML（首尾为尖括号）原样透传；纯文本包装为 <p> 段落。
    """
    if not text:
        return ""
    stripped = text.strip()
    if stripped.startswith("<") and stripped.endswith(">"):
        return text
    return f"<p>{text}</p>"
# highest 最高
# high 高
# medium 中等
# low 低
# lowest 最低

# 日志配置统一在入口 main.py 完成（logging.basicConfig），此处仅获取 logger
logger = logging.getLogger(__name__)

class DevOpsClient:

    def __init__(
        self,
        base_url: str,
        afc_token: str,
        project_id: str,
        iteration_id: str,
        module_id: str,
        version_id: str,
        http_client: Optional[httpx.AsyncClient] = None,
        semaphore: Optional[asyncio.Semaphore] = None,
    ):
        self.base_url = base_url
        self.afc_token=afc_token
        self.project_id = project_id
        self.iteration_id = iteration_id
        self.module_id = module_id
        self.version_id = version_id
        # HTTP 连接池与并发闸门支持注入共享实例（多凭据共用一个池，连接数与用户数解耦）；
        # 未注入时懒创建自有实例（独立使用场景），aclose 仅关闭自有实例
        self._http = http_client
        self._owns_http = http_client is None
        # 出站请求并发闸门（见 MAX_CONCURRENT_REQUESTS 注释）；注入共享闸门时
        # 限流语义升级为“全进程对后端总并发 ≤ MAX_CONCURRENT_REQUESTS”
        self._semaphore = semaphore if semaphore is not None else asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        # 由 verify_token() 填充
        self._user_info = None
        # 权限码缓存（get_permissions 双检锁填充；None 表示未拉取）
        self._permissions: frozenset[str] | None = None
        self._perm_lock = asyncio.Lock()


    def headers(self) -> dict[str, str]:
        """构造认证请求头。afc_token 缺失时显式报错，避免静默发出无认证请求。"""
        if not self.afc_token:
            raise ValueError("afc_token 未配置，无法发起认证请求")
        return {
            "Authorization": f"afc-token:{self.afc_token}",
            "Content-Type": "application/json",
        }

    def _http_client(self) -> httpx.AsyncClient:
        """长生命周期 HTTP 客户端（连接池复用），首次调用时懒创建。"""
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        """关闭底层 HTTP 连接池；共享注入的池不归本实例所有，不在此关闭。"""
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
    ) -> httpx.Response:
        """统一发送 HTTP 请求并校验状态码。

        Raises:
            ValueError: afc_token 未配置
            RuntimeError: HTTP 状态码非 2xx（附带响应体摘要，便于定位后端错误）
            httpx.HTTPError: 网络层错误（超时、连接失败等）
        """
        async with self._semaphore:
            r = await self._http_client().request(
                method, url, headers=self.headers(), params=params, json=json_body,
            )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:300]
            raise RuntimeError(
                f"DevOps API 错误 HTTP {e.response.status_code} {method} {url}: {body}"
            ) from e
        return r

    async def get(self, url: str, params: Optional[dict] = None) -> httpx.Response:
        """发送GET请求

        Args:
            url: 请求url
            params: 查询参数字典，None 表示无

        Returns:
            响应对象
        """
        return await self._request("GET", url, params=params)

    async def post(self, url: str, data: dict) -> httpx.Response:
        """发送POST请求

        Args:
            url: 请求url
            data: 请求体数据

        Returns:
            响应对象
        """
        return await self._request("POST", url, json_body=data)

    async def put(self, url: str, data: dict) -> httpx.Response:
        """发送PUT请求

        Args:
            url: 请求url
            data: 请求体数据

        Returns:
            响应对象
        """
        return await self._request("PUT", url, json_body=data)

    async def get_project(self) -> dict[str, Any]:
        """获取当前用户的所有项目

        Returns:
            项目列表

        """
        url = f"{self.base_url}/api/devops/pm/projects/actions/querybyuser"
        r = await self.get(url)
        return r.json()

    async def query_workitem_list(self,workitem_key:str,workitem_status:str,workitem_type_id:str,offset:int,limit:int) -> dict[str, Any]:
        """获取项目下的工作项列表

        Args:
            workitem_status: 工作项状态 open=待解决 ,in-progress=处理中,reopened=重新打开
            workitem_type_id: 工作项类型id
            offset: 偏移量
            limit: 限制数量

        Returns:
            工作项列表

        """
        logger.info(f"开始查询工作项列表，workitem_key={workitem_key},workitem_status={workitem_status},workitem_type_id={workitem_type_id},offset={offset},limit={limit}")
        logger.info(f"username={self._user_info.userName},project_id={self.project_id}")
        payload = {"includeProgress":False,
        "notInIteration":None,
        "iterationId":None,
        "moduleId":None,
        "notInModule":None,
        "workitemStatus":workitem_status,
        "workitemKey":workitem_key,
        "workitemTitle":None,
        "workitemTitleInFilter":None,
        "labelName":None,
        "stakeholder":self._user_info.userName,
        "stakeholderAction":"assign",
        "orderBys":None,
        "versionId":None,
        "taskType":None,
        "notInVersion":None,
        "beginDueDate":None,
        "endDueDate":None,
        "beginCreateTime":None,
        "endCreateTime":None,
        "includeChildCount":True,
        "includeRefProject":False,
        "otherConditions":[],
        "projectId":self.project_id,
        "queryInXmind":False,
        "workitemTypeId":workitem_type_id,
        "noDueDate":None,
        "params":{"offset":offset,"limit":limit}
        }
        url = f"{self.base_url}/api/devops/pm/workitems/actions/query"
        r = await self.post(url,data=payload)
        return r.json()

    async def get_workitem_details(self, workitem_id: str):
        """根据工作项id获取工作项详情

        Args:
            workitem_id: 工作项id

        Returns:
            工作项详情

        """
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}/details"
        r = await self.get(url)
        return r.json()

    async def download_text(self, url: str):
        """下载文本文件

        Args:
            url: 文本文件url

        Returns:
            文本文件内容

        """
        r = await self.get(url)
        r.encoding = "utf-8"
        return r.text

    async def create_workitem(
        self,
        title: str,
        description: str,
        priority: str,
        workitem_type: str,
        man_hour: int,
        parent_workitem_id: Optional[str] = None,
        due_time: Optional[str] = None,
    ) -> dict[str, Any]:
        """创建工作项

        登录由调用方（main.py 的 get_client）保证已完成，self._user_info 可用。

        Args:
            title: 工作项标题
            description: 工作项描述，HTML 富文本格式（如 <p>...</p><ul><li>...</li></ul>）；为空则置空；纯文本（无标签）自动包装为 <p> 段落
            priority: 优先级，可选值 highest/high/medium/low/lowest
            workitem_type: 工作项类型，可选值 story(故事)/task(任务)/bug/risk(风险)
            man_hour: 评估工时（小时），内部会转换为秒
            parent_workitem_id: 父工作项ID，为空表示根工作项
            due_time: 截止日期，格式 YYYY-MM-DD，可选

        Returns:
            工作项关键信息 dict（workitem_id、workitem_key、状态、负责人等）

        Raises:
            ValueError: workitem_type 或 priority 非法时抛出
        """
        # 校验工作项类型
        type_info = workitem_type_map.get(workitem_type)
        if type_info is None:
            raise ValueError(
                f"非法的 workitem_type: {workitem_type}，可选值: story/task/bug/risk"
            )
        # 校验优先级
        valid_priorities = {"highest", "high", "medium", "low", "lowest"}
        if priority not in valid_priorities:
            raise ValueError(
                f"非法的 priority: {priority}，可选值: highest/high/medium/low/lowest"
            )

        # 构造描述 HTML：空值不包装；疑似已含 HTML 标签则原样透传；否则包装 <p>
        desc_html = to_rich_text(description)

        workitem = {
            "assignee": self._user_info.userName,
            "resolver": self._user_info.userName,
            "iterationId": self.iteration_id,
            "moduleId": self.module_id,
            "projectId": self.project_id,
            "title": title,
            "versionId": self.version_id,
            "affectVersionIds": self.version_id,
            "timeEstimate": man_hour_convert(man_hour),
            "description": desc_html,
            "priority": priority,
            "workitemType": {
                "workitemTypeId": type_info["workitemTypeId"],
            },
        }
        if due_time:
            workitem["dueTime"] = due_time
        if parent_workitem_id:
            workitem["parentWorkitemId"] = parent_workitem_id
            workitem["parentWorkitem"] = {"workitemId": parent_workitem_id}

        url = f"{self.base_url}/api/devops/pm/workitems"
        r = await self.post(url, {"workitem": workitem})
        data = r.json()
        result = {
            "workitem_id": data.get("workitemId"),
            "workitem_key": data.get("workitemKey"),
            "title": data.get("title"),
            "workitem_type_id": data.get("workitemTypeId"),
            "workitem_type_name": data.get("workitemTypeName"),
            "workitem_status": data.get("workitemStatus"),
            "workitem_status_name": data.get("workitemStatusName"),
            "priority": data.get("priority"),
            "assignee": data.get("assignee"),
            "assignee_emp_name": data.get("assigneeEmpName"),
            "project_id": data.get("projectId"),
            "project_code": data.get("projectCode"),
            "project_name": data.get("projectName"),
            "parent_workitem_id": data.get("parentWorkitemId"),
            "parent_workitem_key": data.get("parentWorkitemKey"),
            "iteration_id": data.get("iterationId"),
            "iteration_name": data.get("iterationName"),
            "version_id": data.get("versionId"),
            "version_name": data.get("versionName"),
            "module_id": data.get("moduleId"),
            "module_name": data.get("moduleName"),
            "create_time": data.get("createTime"),
            "due_time": data.get("dueTime"),
            "time_estimate": data.get("timeEstimate"),
        }
        return result


    async def add_workitem_comment(self,project_id:str,workitem_id:str,comment:str):
        """添加工作项评论

        Args:
            project_id: 项目id
            workitem_id: 工作项id
            comment: 评论内容

        Returns:
            评论详情

        """
        payload={
            "projectId":project_id,
            "comment":comment
        }
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}/workitem-comments"
        r = await self.post(url,payload)
        return r.json()

    async def change_workitem_status(self,workitem_id:str,workitem_status:str):
        """变更工作项状态an

        Args:
            workitem_id: 工作项id
            workitem_status: 工作项状态，例如：open=待解决 ,in-progress=处理中,reopened=重新打开

        Returns:
            工作项状态变更

        """
        payload={"workitem":{"workitemId":workitem_id,"workitemStatus":workitem_status}}
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}"
        r = await self.put(url,payload)
        return r.json()

    async def validate_status_transition(self, workitem_id: str, new_status: str) -> dict:
        """校验工作项状态转换是否合法。

        查询工作项真实详情，提取类型和当前状态，查 STATUS_TRANSITIONS 规则表
        判断 new_status 是否为当前状态下允许的目标状态。

        Args:
            workitem_id: 工作项id
            new_status: 目标状态

        Returns:
            {
                "valid": bool,                  # 转换是否合法
                "workitem_type": str | None,    # 类型名，如 "bug"；未知类型时为 None
                "current_status": str,          # 真实当前状态
                "allowed_statuses": list[str],  # 当前状态下允许的目标状态列表
            }

        Raises:
            异常向上抛出（HTTP 错误、字段缺失、工作项不存在等），
            由 main.py 工具的外层 try/except 统一捕获。
        """
        details = await self.get_workitem_details(workitem_id)
        # 强转 str：workitem_type_map 的 typeId 为字符串，但 JSON 响应可能返回整数
        type_id = str(details.get("workitemTypeId", ""))
        current_status = details.get("workitemStatus", details.get("status", ""))

        workitem_type = WORKITEM_TYPE_ID_TO_NAME.get(type_id)
        # 未知类型：不允许转换
        if workitem_type is None:
            return {
                "valid": False,
                "workitem_type": None,
                "current_status": current_status,
                "allowed_statuses": [],
            }

        transitions = STATUS_TRANSITIONS.get(workitem_type, {})
        # 终态/未知当前状态：transitions.get 返回 []，valid 自然为 False
        allowed_statuses = transitions.get(current_status, [])
        return {
            "valid": new_status in allowed_statuses,
            "workitem_type": workitem_type,
            "current_status": current_status,
            "allowed_statuses": allowed_statuses,
        }

    async def create_testcase_group(self, group_name: str, parent_group_id: Optional[str] = None) -> dict[str, Any]:
        """创建测试用例分组

        Args:
            group_name: 分组名称
            parent_group_id: 父分组ID，为空表示根分组

        Returns:
            分组关键信息 dict
        """
        payload = {
            "group": {
                "projectId": self.project_id,
                "groupName": group_name,
                "parentGroupId": parent_group_id,
                "groupType": "testcase",
                "groupScope": "PROJECT",
            }
        }
        url = f"{self.base_url}/api/devops/pcm/groups"
        r = await self.post(url, payload)
        data = r.json()
        return {
            "group_id": data.get("groupId"),
            "group_name": data.get("groupName"),
            "parent_group_id": data.get("parentGroupId"),
            "group_type": data.get("groupType"),
            "group_scope": data.get("groupScope"),
            "create_user": data.get("createUser"),
            "create_time": data.get("createTime"),
        }

    async def get_testcase_groups(self) -> list[dict[str, Any]]:
        """查询项目下的测试用例分组列表

        Returns:
            分组列表（后端返回裸 JSON 列表，含 hasChild/childGroupIds/caseCount 等字段）
        """
        url = f"{self.base_url}/api/devops/pcm/groups"
        r = await self.get(url, params={"projectId": self.project_id, "groupType": "testcase"})
        return r.json()

    async def get_testcase_group_map(self) -> dict[str, str]:
        """查询分组列表并转为 {groupId(str): groupName} 映射，供创建用例前的分组校验使用。"""
        groups = await self.get_testcase_groups()
        return {str(g.get("groupId")): g.get("groupName") for g in groups}

    async def create_testcase(
        self,
        case_title: str,
        group_id: str,
        operation_step: str,
        note: str = "",
        precondition: str = "",
        workitem_ids: Optional[list[str]] = None,
        case_type: str = "function",
        default_priority: str = "P1",
    ) -> dict[str, Any]:
        """创建测试用例

        登录由调用方（main.py 的 get_client）保证已完成，self._user_info 可用。

        Args:
            case_title: 用例标题
            group_id: 所属测试用例分组ID
            operation_step: 操作步骤，已序列化的 JSON 字符串，格式 [{"sortno":1,"step":"...","result":"..."}]
            note: 备注（富文本；纯文本自动包 <p>）
            precondition: 前置条件（富文本；纯文本自动包 <p>）
            workitem_ids: 关联工作项ID列表，为空表示不关联
            case_type: 用例类型，默认 function(功能测试)
            default_priority: 优先级，可选值 P0/P1/P2/P3/P4

        Returns:
            用例关键信息 dict

        Raises:
            ValueError: default_priority 非法时抛出
        """
        valid_priorities = {"P0", "P1", "P2", "P3", "P4"}
        if default_priority not in valid_priorities:
            raise ValueError(
                f"非法的 default_priority: {default_priority}，可选值: P0/P1/P2/P3/P4"
            )

        testcase = {
            "projectId": self.project_id,
            "caseTitle": case_title,
            "groupId": group_id,
            "note": to_rich_text(note),
            "precondition": to_rich_text(precondition),
            "operationStep": operation_step,
            "caseType": case_type,
            "estimatedTime": None,
            "caseKey": None,
            "defaultAssignee": self._user_info.userName,
            "defaultPriority": default_priority,
            "workitems": [{"workitemId": wid} for wid in (workitem_ids or [])],
        }
        url = f"{self.base_url}/api/devops/pm/testcases"
        r = await self.post(url, {"testCase": testcase})
        data = r.json()
        return {
            "case_id": data.get("caseId"),
            "case_key": data.get("caseKey"),
            "case_title": data.get("caseTitle"),
            "case_type": data.get("caseType"),
            "case_status": data.get("caseStatus"),
            "group_id": data.get("groupId"),
            "default_priority": data.get("defaultPriority"),
            "default_assignee": data.get("defaultAssignee"),
            "sortno": data.get("sortno"),
            "create_time": data.get("createTime"),
            "workitem_ids": [w.get("workitemId") for w in data.get("workitems", [])],
        }

    async def query_testcase_list(self, group_id: str = "", page_index: int = 0, page_size: int = 200) -> dict[str, Any]:
        """查询项目下的测试用例列表

        Args:
            group_id: 分组ID筛选，为空查全项目
            page_index: 页码，从0开始
            page_size: 每页数量

        Returns:
            分页结果 {"total": n, "data": [...], ...}
        """
        params: dict[str, Any] = {
            "projectId": self.project_id,
            "pageIndex": page_index,
            "pageSize": page_size,
        }
        if group_id:
            params["groupId"] = group_id
        url = f"{self.base_url}/api/devops/pm/testcases"
        r = await self.get(url, params=params)
        return r.json()



    async def get_permissions(self) -> frozenset[str]:
        """获取当前用户在当前项目下的权限码集合（双检锁缓存，仅拉取一次）。

        调用 GET /api/devops/uc/permissions/employees?empId=&projectId=，
        依赖 verify_token() 已填充的 _user_info.empId。

        Returns:
            权限码字符串的不可变集合

        Raises:
            RuntimeError: 权限接口返回格式异常（非字符串列表）
        """
        if self._permissions is not None:
            return self._permissions
        async with self._perm_lock:
            if self._permissions is not None:
                return self._permissions
            r = await self.get(
                f"{self.base_url}/api/devops/uc/permissions/employees",
                params={"empId": self._user_info.empId, "projectId": self.project_id},
            )
            data = r.json()
            if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
                raise RuntimeError(f"权限接口返回格式异常: {type(data).__name__}")
            self._permissions = frozenset(data)
            return self._permissions

    async def verify_token(self) -> dict[str, Any]:
        """校验 afc_token 有效性并加载当前用户信息（缓存到 self._user_info）。

        原名 login，实际并不换取新 token，故更名以匹配真实语义。

        Returns:
            当前用户信息

        Raises:
            ValueError: afc_token 未配置
            RuntimeError / httpx.HTTPError: 请求失败或 token 无效
        """
        url = f"{self.base_url}/api/devops/uc/users/current-user"
        # 注意：日志中不输出 Authorization 头，避免 token 泄漏
        logger.info(f"开始校验token，url={url}")
        r = await self.get(url)
        data = r.json()

        if data:
            # empId 语义（2026-08-18 实测）：权限接口 /uc/permissions/employees 需要
            # 员工编号，对应 employee 子对象的 empId；顶层 data["id"] 是账号 ID，
            # 用它查询会报 EMPLOYEE_NOT_EXISTED。
            self._user_info = UserInfo(
                data["employee"]["empId"],
                data["employee"]["empName"],
                data["userName"],
                data["userName"],
            )
        return data

class UserInfo:
    def __init__(self, emp_id: str,emp_name: str,nick_name:str,user_name:str):
        self.empId = emp_id
        self.empName = emp_name
        self.nickName = nick_name
        self.userName = user_name
    
       