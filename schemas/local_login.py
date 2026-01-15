"""
Local Login schemas for API requests and responses
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LocalLoginCreate(BaseModel):
    """Schema for creating local login from frontend"""

    email: str = Field(..., description="Email address (login_id)")
    user_name: str = Field(..., description="User name")
    password: str = Field(..., description="Plain text password")

    class Config:
        pass


class LocalLoginResponse(BaseModel):
    """Schema for local login response"""

    login_id: str = Field(..., description="Login ID (email address)")
    user_name: str = Field(..., description="User name")
    created_at: str = Field(..., description="Creation date")
    updated_at: Optional[str] = Field(None, description="Last update date")
    deleted_at: Optional[str] = Field(None, description="Deletion date")

    class Config:
        pass
