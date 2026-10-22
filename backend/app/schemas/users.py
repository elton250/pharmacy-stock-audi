"""Auth and user schemas."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    detail: str
    user: "UserOut"


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    permissions: list[str] = []

    model_config = {"from_attributes": True}

    @field_validator("permissions", mode="before")
    @classmethod
    def _permission_codes(cls, value):
        return [getattr(p, "code", p) for p in value or []]


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    email: Optional[str] = None
    is_active: bool
    must_change_password: bool = False
    roles: list[RoleOut] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _new_password_differs(cls, value: str, info) -> str:
        current = info.data.get("current_password")
        if current is not None and value == current:
            raise ValueError("new password must differ from the current password")
        return value


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    email: Optional[str] = Field(default=None, max_length=255)
    role_names: list[str] = []
    must_change_password: bool = False


class UserUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, max_length=200)
    email: Optional[str] = Field(default=None, max_length=255)
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)
    role_names: Optional[list[str]] = None
