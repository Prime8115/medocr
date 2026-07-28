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

    model_config = {"from_attributes": True}
