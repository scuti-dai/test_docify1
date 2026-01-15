"""
Authentication schemas for API requests and responses
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field
from config_gateway import Config

logger = logging.getLogger(__name__)
config = Config()


class LoginRequest(BaseModel):
    """Schema for login request"""

    if config.AUTH_TYPE == "LOCAL":
        email: str = Field(..., description="User's email address")
        password: str = Field(..., description="User's password")
    else:
        email: str = Field(..., description="User's email address")
        password: Optional[str] = Field(None, description="User's password")
        name: Optional[str] = Field(None, description="User's name")
        token: Optional[str] = Field(
            None, description="OAuth token if using third-party login"
        )
        groups: Optional[list[str]] = Field(None, description="User groups or roles")

    class Config:
        pass


class LoginResponse(BaseModel):
    """Schema for login response"""

    statusCode: int = 200
    message: str = "Login successful"
    data: dict

    class Config:
        pass


class LoginData(BaseModel):
    """Schema for login data in response"""

    user_id: str
    email: str
    user_name: str
    role: str
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int
    if config.AUTH_TYPE != "LOCAL":
        OAuth_token: Optional[str] = None
        group: Optional[list[str]] = None

    class Config:
        pass


class LogoutRequest(BaseModel):
    """Schema for logout request"""

    refresh_token: str = Field(..., description="Refresh token of the user to logout")

    class Config:
        pass


class LogoutResponse(BaseModel):
    """Schema for logout response"""

    statusCode: int = 200
    message: str = "Logout successful"

    class Config:
        pass


class RefreshRequest(BaseModel):
    """Schema for refresh token request"""

    refresh_token: str = Field(..., description="Refresh token")

    class Config:
        pass


class RefreshResponse(BaseModel):
    """Schema for refresh token response"""

    access_token: str
    token_type: str = "bearer"
    expires_in: int

    class Config:
        pass


class ChangePasswordRequest(BaseModel):
    """Schema for change password request"""

    current_password: str = Field(..., min_length=6)
    new_password: str = Field(..., min_length=6)

    class Config:
        pass


class ChangePasswordResponse(BaseModel):
    """Schema for change password response"""

    message: str = "Password changed successfully"

    class Config:
        pass
