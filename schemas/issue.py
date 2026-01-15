"""
Issue schemas for API requests and responses
"""

import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class IssueCreateRequest(BaseModel):
    """Schema for creating an issue"""

    title: str = Field(..., min_length=1, description="Issue title")
    description: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Detailed description of the issue",
    )
    source_code_id: str = Field(
        ..., description="ID of the source code file that creates the issue"
    )
    user_name: Optional[str] = Field(
        None, description="Git username for creating issue on Git"
    )
    token_password: Optional[str] = Field(
        None, description="Git token/password for creating issue on Git"
    )

    class Config:
        pass


# Alias for backward compatibility
IssueCreate = IssueCreateRequest


class IssueUpdate(BaseModel):
    """Schema for updating an issue"""

    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None

    class Config:
        pass


class IssueResponse(BaseModel):
    """Schema for issue response"""

    issue_id: str
    project_id: str
    user_id: str
    username: str
    title: str
    description: str
    status: str
    priority: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    class Config:
        pass
