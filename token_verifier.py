import logging 
from typing import Any

from mcp.server.auth.provider import AccessToken,TokenVerifier
from mcp.shared.auth_utils import check_resource_allowed,resource_url_from_server_url

logger = logging.getLogger(__name__)

class IntrospectionTokenVerifier(TokenVerifier):
    def __init__(self, introspection_endpoint:str,server_url:str,client_id:str,client_secret:str):
        self.introspection_endpoint = introspection_endpoint
        self.server_url = server_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.resource_url=resource_url_from_server_url(server_url)
    
    async def verify_token(self,token:str) -> AccessToken| None:
        import httpx

        if not self.introspection_endpoint.startswith(("https://","http://localhost","http://127.0.0.1")):
            return None
        
        timeout=httpx.Timeout(10.0,connect=5.0)
        limits=httpx.Limits(max_connections=10,max_keepalive_connections=5)

        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            verify=True,
        ) as client:
            try :
                form_data = {
                    "token":token,
                    "client_id":self.client_id,
                    "client_secret":self.client_secret,
                }

                headers={"Content-Type":"application/x-www-form-urlencoded"}

                response=await client.post(
                    self.introspection_endpoint,
                    data=form_data,
                    headers=headers,
                )

                if response.status_code!=200:
                    return None
                
                data=response.json()

                if not data.get("active",False):
                    return None

                if not self._validate_token(data):
                    return None

                return AccessToken(
                    token=token,
                    client_id=data.get("client_id","unknown"),
                    scopes=data.get("scope","").split() if data.get("scope") else [],
                    expires_at=data.get("exp"),
                    resource=data.get("aud")
                )
            except Exception as e:
                logger.error(f"验证token失败: {str(e)}")
                return None
    
    def _validate_resource(self,token_data:dict[str,Any])-> bool:
        if not self.server_url or not self.resource_url:
            return False
        aud:list[str] | str|None = token_data.get("aud")
        if isinstance(aud,str):
            return any(self._is_valid_resource(a) for a in aud)
        
        if isinstance(aud,str):
            return self._is_valid_resource(aud)
        return False
    
    def _is_valid_resource(self,resource:str) ->bool:
        return check_resource_allowed(self.resource_url,resource)

       