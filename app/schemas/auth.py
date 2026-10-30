import re
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)


UserRole = Literal["Admin", "Organizer", "Customer", "Staff"]


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone_number: str | None = Field(
        default=None,
        min_length=10,
        max_length=10,
    )
    password: str = Field(min_length=8, max_length=72)
    role: Literal["Organizer", "Customer"] = "Customer"

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Full name cannot be empty.")
        return value

    @field_validator("phone_number")
    @classmethod
    def validate_phone_number(
        cls,
        value: str | None,
    ) -> str | None:
        if value is not None and not re.fullmatch(r"\d{10}", value):
            raise ValueError(
                "Phone number must contain exactly 10 digits."
            )
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value):
            raise ValueError("Password must contain at least one letter.")
        if not re.search(r"\d", value):
            raise ValueError("Password must contain at least one number.")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    phone_number: str | None = None
    role: UserRole
    is_active: bool
    organizer_approved: bool

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse