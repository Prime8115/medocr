from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    shop_name: str = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: EmailStr
    role: str
    shop_id: str
    is_platform_admin: bool = False
    # The organisation, the current workspace and the ones the user may open.
    org_id: Optional[str] = None
    org_name: Optional[str] = None
    workspace_name: Optional[str] = None
    workspaces: List[dict] = []

    model_config = {"from_attributes": True}


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)
