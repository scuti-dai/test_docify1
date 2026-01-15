"""
Comment schemas for API requests and responses
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class CommentCreate(BaseModel):
    """Schema for creating a comment"""

    content: str = Field(
        ..., min_length=1, max_length=2000, description="Comment content"
    )
    user_id: str = Field(..., min_length=1, description="ID of user who commented")

    class Config:
        pass


class CommentUpdate(BaseModel):
    """Schema for updating a comment"""

    content: Optional[str] = Field(None, min_length=1, max_length=2000)

    class Config:
        pass


class CommentResponse(BaseModel):
    """Schema for comment response"""

    id: str
    project_id: str
    user_id: str
    user_name: str
    content: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    class Config:
        pass
