"""Telegram account + login flow schemas."""
from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    phone: str = Field(pattern=r"^\+?\d{6,15}$", description="International format, e.g. +491234567890")
    api_id: int = Field(gt=0, description="From https://my.telegram.org API development tools")
    api_hash: str = Field(min_length=8, max_length=128)


class CodeSubmit(BaseModel):
    code: str = Field(min_length=1, max_length=16, description="OTP code sent by Telegram")


class TwoFASubmit(BaseModel):
    password: str = Field(min_length=1, max_length=256, description="2FA (cloud password)")


class AccountPublic(BaseModel):
    id: int
    phone: str
    status: str
    last_error: str | None = None
    last_checked_at: datetime | None = None
    created_at: datetime


class AccountStatusReport(BaseModel):
    id: int
    status: str
    user: dict | None = None
