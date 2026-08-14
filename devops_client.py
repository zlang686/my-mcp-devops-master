import httpx
from typing import Optional, Any
import logging
REQUEST_TIMEOUT = 60

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
# highest 最高
# high 高
# medium 中等
# low 低
# lowest 最低

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DevOpsClient:

    def __init__(self, base_url: str,afc_token:str, project_id: str, iteration_id: str, module_id: str, version_id: str):
        self.base_url = base_url
        self.afc_token=afc_token
        self.project_id = project_id
        self.iteration_id = iteration_id
        self.module_id = module_id
        self.version_id = version_id
        

    def headers(self):
        h = {}
        if self.afc_token:
            h["Authorization"] = f"afc-token:{self.afc_token}"
            h["Content-Type"] = "application/json"
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
        # if not self._token:
        #     await self.login()
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
        # if not self._token:
        #     await self.login()
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
        # if not self._token:
        #     await self.login()
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            r = await client.put(url, headers=self.headers(), json=data)
            r.raise_for_status()
            return r

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

    async def create_workitem(self,title:str,description:str,priority:str,workitem_type:str,parent_workitem_id:str,man_hour:int):
        """创建工作项

        Args:
            title: 工作项标题
            description: 工作项描述
            priority: 优先级
            workitem_type: 工作项类型 3=任务,4=缺陷,5=需求

        Returns:
            工作项详情

        """
        # if not self._token:
        #     await self.login()
        workitem={
            "effectVersionIds":self.version_id,
            "assignee":self._user_info.userName,
            "assigneeEmpName":self._user_info.empName,
            "assigneeStatus":"on",
            "iterationId":self.iteration_id,
            "moduleId":self.module_id,
            "projectId":self.project_id,
            "title":title,
            "versionId":self.version_id,
            "parentWorkitemId":parent_workitem_id,
            "timeEstimate":man_hour_convert(man_hour),
            "description":description,
            "priority":priority_convert(priority),
            "workitemTypeId":workitem_type_map[workitem_type]["workitemTypeId"],
            "workitemTypeName":workitem_type_map[workitem_type]["workitemTypeName"]
        }
        url = f"{self.base_url}/api/devops/pm/workitems"
        r = await self.post(url,{"workitem":workitem})
        data=r.json()
        result={
            "workitem_id":data["workitemId"],
            "parent_workitem_id":data["parentWorkitemId"],
            "project_id":data["projectId"]
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
        r = await self.post(url,payload)
        return r.json()
    
    async def create_testcases(self,case_title:str,note:str,precondition:str,operation_step:str,workitem_id:str,default_priority:str):
        # if not self._token:
        #     await self.login()
        payload={
            "testcase":{
                "caseTitle":case_title,
                "note":note,
                "precondition":precondition,
                "operationStep":operation_step,
                "workitems":[{"workitemId":workitem_id}],
                "defaultPriority":default_priority,
                "projectId":self.project_id,
                "caseType":"funcation",
                "defaultAssignee":self._user_info.userName,
                "groupId":"1"
            }
        }
        url = f"{self.base_url}/api/devops/pm/testcases"
        r = await self.post(url,payload)
        return r.json()
    
    async def login(self) -> dict[str, Any]:
        """登录devops平台并获取token

        Returns:
            Login response with token and user info

        Raises:
            httpx.HTTPError: If login fails
        """
        url = f"{self.base_url}/api/devops/uc/users/current-user"
        headers={"Authorization":f"afc-token:{self.afc_token}"}
        logger.info(f"开始校验token，url={url},headers={headers}")
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

        if data:
            self._user_info = UserInfo(data["id"],data["employee"]["empName"],data["userName"],data["userName"])
        return data

class UserInfo:
    def __init__(self, emp_id: str,emp_name: str,nick_name:str,user_name:str):
        self.empId = emp_id
        self.empName = emp_name
        self.nickName = nick_name
        self.userName = user_name
    
       