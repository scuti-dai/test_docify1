"""
Basic Design schemas for API requests and responses
"""

import logging
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class BasicDesignCreate(BaseModel):
    """Schema for creating a basic design"""

    basic_design_id: str = Field(..., description="UUID7, unique identifier")
    project_id: str = Field(..., description="Reference to project.project_id")
    user_id: str = Field(..., description="Reference to user.user_id")
    username: str = Field(..., description="Username")
    title: str = Field(..., description="Design title")
    content: str = Field(..., description="Design content")
    version: str = Field(..., description="Design version")

    class Config:
        pass


class BasicDesignUpdate(BaseModel):
    """Schema for updating a basic design"""

    title: Optional[str] = None
    content: Optional[str] = None
    version: Optional[str] = None

    class Config:
        pass


class BasicDesignResponse(BaseModel):
    """Schema for basic design response"""

    basic_design_id: str
    project_id: str
    user_id: str
    username: str
    title: str
    content: str
    version: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    class Config:
        pass


class BasicDesignContent(BaseModel):
    """Schema representing content stored inside a basic design document"""

    type: str = Field("", description="Content type e.g. markdown or image")
    content: str = Field("", description="Raw content or base64 encoded data")


class BasicDesignDetailResponse(BaseModel):
    """Schema for detailed basic design response"""

    id: str = Field(..., description="Basic design identifier")
    project_id: str = Field(..., description="Associated project identifier")
    commit_id: str = Field("", description="Git commit id if synced")
    sync_status: str = Field("", description="Sync status with Git repository")
    file_name: str = Field(..., description="File name")
    bd_content: BasicDesignContent = Field(..., description="Stored content payload")
    created_at: str = Field(..., description="Creation timestamp (ISO 8601)")
    updated_at: str = Field(..., description="Last update timestamp (ISO 8601)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "bd_123",
                "project_id": "proj_456",
                "commit_id": "abc123",
                "sync_status": "synced",
                "file_name": "screen-flow.md",
                "bd_content": {"type": "markdown", "content": "# Screen Flow"},
                "created_at": "2024-01-10T10:00:00Z",
                "updated_at": "2024-01-12T15:30:00Z",
            }
        }


class BasicDesignDeleteRequest(BaseModel):
    """Schema for deleting basic designs"""

    basic_design_id: List[str] = Field(
        ..., min_items=1, description="List of basic design IDs to delete"
    )

    @validator("basic_design_id")
    def validate_basic_design_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one basic design ID is required")
        return v

    class Config:
        json_schema_extra = {"example": {"basic_design_id": ["bd_123", "bd_456"]}}


class BasicDesignDownloadFileRequest(BaseModel):
    """Schema for a single file download request"""

    id: str = Field(..., description="ID of the file")
    key_names: List[str] = Field(
        ..., min_items=1, description="List of data keys from JSON contents to download"
    )

    @validator("key_names")
    def validate_key_names(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one key name is required")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "id": "file123",
                "key_names": ["use_case_diagram", "system_overview"],
            }
        }


class BasicDesignPushRequest(BaseModel):
    """Schema for pushing basic designs to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of basic design IDs to push"
    )
    commit_message: str = Field(..., description="Commit message for Git")
    user_name: str = Field(..., description="Git username")
    token_password: Optional[str] = Field(None, description="Git token or password")

    @validator("id")
    def validate_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one file ID is required")
        return v

    @validator("commit_message")
    def validate_commit_message(cls, v):
        if not v or not v.strip():
            raise ValueError("Commit message is required")
        return v.strip()

    class Config:
        json_schema_extra = {
            "example": {
                "id": ["file123", "file456"],
                "commit_message": "Update basic designs",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


class BasicDesignUpdateContentRequest(BaseModel):
    """Schema for updating basic design content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "# Basic Design\n\n## Overview\nThis document describes..."
            }
        }


class BasicDesignDetailRequest(BaseModel):
    """Schema for getting basic design detail with key name"""

    key_name: str = Field(..., description="Key name of file json")

    class Config:
        json_schema_extra = {"example": {"key_name": "class_design"}}


class BasicDesignDetailData(BaseModel):
    """Basic design detail data schema"""

    id: str = Field(..., description="Basic design ID")
    project_id: str = Field(..., description="Project ID")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    contents_key: Any = Field(..., description="Value of key_name in contents")
    created_at: str = Field(..., description="Created timestamp (ISO 8601 format)")
    updated_at: str = Field(..., description="Updated timestamp (ISO 8601 format)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "0123456789abcdef01234567",
                "project_id": "0123456789abcdef01234567",
                "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                "sync_status": "synced",
                "contents_key": {"content": "public class UserService { ... }"},
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        }


class BasicDesignDetailResponse(BaseModel):
    """Basic design detail response schema"""

    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: BasicDesignDetailData = Field(..., description="Basic design data")

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "Success",
                "data": {
                    "id": "0123456789abcdef01234567",
                    "project_id": "0123456789abcdef01234567",
                    "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                    "sync_status": "synced",
                    "contents_key": {"content": "public class UserService { ... }"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                },
            }
        }


class GenerateBasicDesignRequest(BaseModel):
    """Schema for generating basic designs"""

    input_source: Literal["requirement", "detail_design"] = Field(
        ...,
        description="Source type: 'requirement' for requirement documents or 'detail_design' for detail design documents",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "input_source": "requirement",
            }
        }
