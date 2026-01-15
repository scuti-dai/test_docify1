"""
Detail Design Schemas
Pydantic models for detail design API requests and responses
"""

import logging
from typing import List, Optional, Literal, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime

logger = logging.getLogger(__name__)


# #region DetailDesignDocument Schemas
class BDInfoSchema(BaseModel):
    """Basic Design information schema"""

    id: str = Field(..., description="Basic design ID")
    file_name: str = Field(..., description="File name")
    class_name: Optional[str] = Field(
        None, description="Class name that created detail design"
    )
    method_name: Optional[str] = Field(
        None, description="Method name that created detail design"
    )


class SourceCodeInfoSchema(BaseModel):
    """Source code information schema"""

    id: str = Field(..., description="Source code ID")
    file_name: str = Field(..., description="File name")
    class_id: Optional[str] = Field(
        None, description="Class ID that created detail design"
    )
    method_id: Optional[str] = Field(
        None, description="Method ID that created detail design"
    )


class DetailDesignDocumentItem(BaseModel):
    """Detail Design Document item response schema"""

    id: str = Field(..., description="Detail design document ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    bd_info: Optional[BDInfoSchema] = Field(
        None, description="Basic Design information"
    )
    source_code: Optional[SourceCodeInfoSchema] = Field(
        None, description="Source code information"
    )
    collection_name: str = Field(
        default="detail_design_document", description="Collection name"
    )
    created_at: Optional[datetime] = Field(
        None, description="Created timestamp (ISO 8601)"
    )
    updated_at: Optional[datetime] = Field(
        None, description="Updated timestamp (ISO 8601)"
    )
    deleted_at: Optional[datetime] = Field(
        None, description="Deleted timestamp (ISO 8601)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": "0123456789abcdef01234567",
                "project_id": "0123456789abcdef01234567",
                "bd_info": {
                    "id": "0123456789abcdef01234567",
                    "file_name": "basic_design.md",
                    "class_name": "UserService",
                    "method_name": "createUser",
                },
                "source_code": {
                    "id": "0123456789abcdef01234567",
                    "file_name": "UserService.java",
                    "class_id": "0123456789abcdef01234567",
                    "method_id": "0123456789abcdef01234567",
                },
                "collection_name": "detail_design_document",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "deleted_at": None,
            }
        }


# #endregion


# #region DetailDesign Schemas


class DetailDesignListItem(BaseModel):
    """Detail design list item response schema (without content) - Legacy format"""

    id: str = Field(..., description="Detail design ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    type: str = Field(..., description="Detail design type (CD, MD, ID, FS, AC)")
    file_name: str = Field(..., description="File name")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    status: Optional[str] = Field(
        None, description="File status: 'Git' if has commit_id, 'Local' otherwise"
    )
    file_type: Optional[str] = Field(None, description="File type")
    created_at: Optional[datetime] = Field(
        None, description="Created timestamp (ISO 8601)"
    )
    updated_at: Optional[datetime] = Field(
        None, description="Updated timestamp (ISO 8601)"
    )
    deleted_at: Optional[datetime] = Field(
        None, description="Deleted timestamp (ISO 8601)"
    )


class DetailDesignListItemData(BaseModel):
    """Detail design list item data schema - New format"""

    id: str = Field(..., description="Detail design ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    contents: dict = Field(..., description="Detail design contents (Object)")
    status: str = Field(..., description="File status")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    created_at: str = Field(..., description="Created timestamp (ISO 8601 format)")
    updated_at: str = Field(..., description="Updated timestamp (ISO 8601 format)")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "0123456789abcdef01234567",
                "project_id": "0123456789abcdef01234567",
                "contents": {"content": "public class UserService { ... }"},
                "status": "Git",
                "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                "sync_status": "synced",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
            }
        }


class DetailDesignItem(BaseModel):
    """Detail design item response schema (with content for detail view)"""

    id: str = Field(..., description="Detail design ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    type: str = Field(..., description="Detail design type (CD, MD, ID, FS, AC)")
    file_name: str = Field(..., description="File name")
    content: Optional[str] = Field(None, description="Detail design content")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    status: Optional[str] = Field(
        None, description="File status: 'Git' if has commit_id, 'Local' otherwise"
    )
    file_type: Optional[str] = Field(None, description="File type")
    images: Optional[str] = Field(
        None,
        description="Base64-encoded PNG image generated from PlantUML (for activity_diagram)",
    )
    created_at: Optional[datetime] = Field(
        None, description="Created timestamp (ISO 8601)"
    )
    updated_at: Optional[datetime] = Field(
        None, description="Updated timestamp (ISO 8601)"
    )
    deleted_at: Optional[datetime] = Field(
        None, description="Deleted timestamp (ISO 8601)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "id": "0123456789abcdef01234567",
                "project_id": "0123456789abcdef01234567",
                "type": "CD",
                "file_name": "UserService.java",
                "content": "public class UserService { ... }",
                "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                "sync_status": "synced",
                "status": "Git",
                "file_type": "detail_design",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "deleted_at": None,
            }
        }


# #endregion


class DetailDesignListData(BaseModel):
    """Detail design list data schema"""

    detail_design_list: List[DetailDesignListItemData] = Field(
        default_factory=list, description="List of detail designs"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "detail_design_list": [
                    {
                        "id": "0123456789abcdef01234567",
                        "project_id": "0123456789abcdef01234567",
                        "contents": {"content": "public class UserService { ... }"},
                        "status": "Git",
                        "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                        "sync_status": "synced",
                        "created_at": "2024-01-01T00:00:00Z",
                        "updated_at": "2024-01-01T00:00:00Z",
                    }
                ]
            }
        }


class DetailDesignListResponse(BaseModel):
    """Detail design list response schema"""

    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: DetailDesignListData = Field(..., description="Response data")

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "Success",
                "data": {
                    "detail_design_list": [
                        {
                            "id": "0123456789abcdef01234567",
                            "project_id": "0123456789abcdef01234567",
                            "type": "CD",
                            "file_name": "UserService.java",
                            "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                            "sync_status": "synced",
                            "status": "Git",
                            "collection_name": "detail_design",
                            "created_at": "2024-01-01T00:00:00Z",
                            "updated_at": "2024-01-01T00:00:00Z",
                        }
                    ]
                },
            }
        }


class DetailDesignDetailRequest(BaseModel):
    """Schema for getting detail design detail with key name"""

    key_name: str = Field(..., description="Key name of file json")

    class Config:
        json_schema_extra = {"example": {"key_name": "class_design"}}


class DetailDesignDetailData(BaseModel):
    """Detail design detail data schema"""

    id: str = Field(..., description="Detail design ID (UUID7)")
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


class DetailDesignDetailResponse(BaseModel):
    """Detail design detail response schema"""

    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: DetailDesignDetailData = Field(..., description="Detail design data")

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "Success",
                "data": {
                    "id": "0123456789abcdef01234567",
                    "project_id": "0123456789abcdef01234567",
                    "contents": {"content": "public class UserService { ... }"},
                    "status": "Git",
                    "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                    "sync_status": "synced",
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-01T00:00:00Z",
                },
            }
        }


class DetailDesignDownloadFileRequest(BaseModel):
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
            "example": {"id": "file123", "key_names": ["class_design", "method_design"]}
        }


class DetailDesignPushRequest(BaseModel):
    """Schema for pushing detail designs to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of detail design IDs to push"
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
                "commit_message": "Update detail designs",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


class DetailDesignUpdateContentRequest(BaseModel):
    """Schema for updating detail design content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "# Detail Design\n\n## Overview\nThis document describes..."
            }
        }


class GenerateDetailDesignRequest(BaseModel):
    """Schema for generating detail designs"""

    input_source: Literal["basic_design", "source_code"] = Field(
        ...,
        description="Source type: 'basic_design' for basic design documents or 'source_code' for source code files",
    )
    user_name: Optional[str] = Field(None, description="Git username")
    token_password: Optional[str] = Field(None, description="Git token or password")

    class Config:
        json_schema_extra = {
            "example": {
                "input_source": "basic_design",
            }
        }
