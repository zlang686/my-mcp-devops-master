import httpx
from typing import Optional, Any

REQUEST_TIMEOUT = 60

workitem_type={
    "故事":{
        "workitemTypeId":"2",
        "workitemTypeName":"user-story"
    },
    "任务":{
        "workitemTypeId":"3",
        "workitemTypeName":"task"
    },
    "BUG":{
        "workitemTypeId":"4",
        "workitemTypeName":"bug"
    },
    "风险":{
        "workitemTypeId":"5",
        "workitemTypeName":"risk"
    }
}

class DevOpsClient:

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url
        self.username = username
        self.password = password
        self._token: Optional[str] = None
        self._user_info: Optional[UserInfo] = None

    def headers(self):
        h = {}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def get(self, url: str):
        """发送GET请求

        Args:
            url: 请求url

        Returns:
            响应对象

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._token:
            await self.login()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.get(url, headers=self.headers())
            r.raise_for_status()
            return r

    async def post(self, url: str, data: dict):
        """发送POST请求

        Args:
            url: 请求url
            data: 请求体数据

        Returns:
            响应对象

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._token:
            await self.login()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.post(url, headers=self.headers(), json=data)
            r.raise_for_status()
            return r

    async def put(self, url: str, data: dict):
        """发送PUT请求

        Args:
            url: 请求url
            data: 请求体数据

        Returns:
            响应对象

        Raises:
            httpx.HTTPError: If request fails
        """
        if not self._token:
            await self.login()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.put(url, headers=self.headers(), json=data)
            r.raise_for_status()
            return r

    async def get_project(self) -> list[dict[str, Any]]:
        """获取当前用户的所有项目

        Returns:
            项目列表

        """
        url = f"{self.base_url}/api/devops/pm/projects"
        r = await self.request(url)
        return r.json()

    async def query_workitem_list(self,project_id:str,workitem_status:str,offset:int,limit:int) -> list[dict[str, Any]]:
        """获取项目下的工作项列表

        Args:
            project_id: 项目id
            workitem_status: 工作项状态 open=待解决 ,in-progress=处理中,reopened=重新打开
            offset: 偏移量
            limit: 限制数量

        Returns:
            工作项列表

        """
        payload = {"includeProgress":False,
        "notInIteration":None,
        "iterationId":None,
        "moduleId":None,
        "notInModule":None,
        "workitemStatus":workitem_status,
        "workitemKey":None,
        "workitemTitle":None,
        "workitemTitleInFilter":None,
        "labelName":None,
        "stakeholder":self.username,
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
        "projectId":project_id,
        "queryInXmind":False,
        "workitemTypeId":"3,4,5",
        "noDueDate":None,
        "params":{"offset":offset,"limit":limit}
        }
        url = f"{self.base_url}/api/devops/pm/workitems/actions/query"
        r = await self.request(url)
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

    async def create_workitem(self,title:str,description:str,priority:str,workitem_type:str):
        """创建工作项

        Args:
            title: 工作项标题
            description: 工作项描述
            priority: 优先级
            workitem_type: 工作项类型 3=任务,4=缺陷,5=需求

        Returns:
            工作项详情

        """
        payload={
            "effectVersionIds":"ESB-381",
            "assignee":self._user_info.userName,
            "assigneeEmpName":self._user_info.empName,
            "assigneeStatus":"on",
            "iterationId":"ESB-201",
            "iterationName":"Sprint1",
            "moduleId":"10817",
            "moduleName":"esb-governor-server",
            "projectCode":"ESB",
            "projectId":"341",
            "projectName":"ESB",
            "title":title,
            "versionId":"ESB-381",
            "versionName":"iPaaS920",
            "workitemTypeId":"2",
            "workitemTypeName":"user-story",
            "description":description,
            "priority":priority,
            "workitemTypeId":workitem_type[workitem_type]["workitemTypeId"],
            "workitemTypeName":workitem_type[workitem_type]["workitemTypeName"]
        }
        url = f"{self.base_url}/api/devops/pm/workitems"
        r = await self.post(url,payload)
        return r.json()

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

    async def chenage_workitem_status(self,workitem_id:str,workitem_status:str):
        """变更工作项状态

        Args:
            workitem_id: 工作项id
            workitem_status: 工作项状态 open=待解决 ,in-progress=处理中,reopened=重新打开

        Returns:
            工作项状态变更

        """
        payload={"workitem":{"workitemId":workitem_id,"workitemStatus":workitem_status}}
        url = f"{self.base_url}/api/devops/pm/workitems/{workitem_id}"
        r = await self.post(url,payload)
        return r.json()

    async def login(self) -> dict[str, Any]:
        """登录devops平台并获取token

        Returns:
            Login response with token and user info

        Raises:
            httpx.HTTPError: If login fails
        """
        url = f"{self.base_url}/api/devops/uc/users/login"
        payload = {"userName": self.username, "password": self.password}
        headers={"Authorization":""}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload,headers=headers)
            response.raise_for_status()
            data = response.json()

        if "token" in data:
            self._token = data["token"]
            self._user_info = UserInfo(data["empId"],data["empName"],data["nickName"],data["userName"])
        return data

class UserInfo:
    def __init__(self, emp_id: str,emp_name: str,nick_name:str,user_name:str):
        self.empId = emp_id
        self.empName = emp_name
        self.nickName = nick_name
        self.userName = user_name
    
       