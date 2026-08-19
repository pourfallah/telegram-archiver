"""Dashboard authentication schemas."""
from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserSummary"


class UserSummary(BaseModel):
    id: int
    email: str
    is_admin: bool


LoginResponse.model_rebuild()
