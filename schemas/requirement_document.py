"""
Requirement Document schemas for API requests and responses
"""

import logging
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class RequirementDocumentCreate(BaseModel):
    """Schema for creating a requirement document"""

    requirement_document_id: str = Field(..., description="UUID7, unique identifier")
    project_id: str = Field(..., description="Reference to project.project_id")
    user_id: str = Field(..., description="Reference to user.user_id")
    username: str = Field(..., description="Username")
    title: str = Field(..., description="Document title")
    content: str = Field(..., description="Document content")
    version: str = Field(..., description="Document version")

    class Config:
        pass


class RequirementDocumentUpdate(BaseModel):
    """Schema for updating a requirement document"""

    title: Optional[str] = None
    content: Optional[str] = None
    version: Optional[str] = None
    commit_id: Optional[str] = Field(
        None, description="Latest commit ID of the file that was pulled"
    )
    sync_status: Optional[str] = Field(
        None,
        description="Sync status between local ↔ git (push: file uploaded not synced to git, pull: has new commits not pulled, pull_push: changes in both local and git not synced, delete_push: file deleted on docifycode not synced to git, delete_pull: file deleted on git not synced to docifycode, synced: no changes between local ↔ git)",
    )

    class Config:
        pass


class RequirementDocumentContentUpdate(BaseModel):
    """Schema for updating requirement document content"""

    rd_content: Dict[str, Any] = Field(
        ..., description="Requirement document content with type and content"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "rd_content": {
                    "type": "markdown",
                    "content": "This is the content of the requirement document.",
                }
            }
        }


class RequirementDocumentResponse(BaseModel):
    """Schema for requirement document response"""

    requirement_document_id: str
    project_id: str
    user_id: str
    username: str
    title: str
    content: str
    version: str
    commit_id: Optional[str] = None
    sync_status: Optional[str] = None
    created_at: str
    updated_at: str
    deleted_at: Optional[str] = None

    class Config:
        pass


class RequirementDocumentDeleteRequest(BaseModel):
    """Schema for deleting requirement documents"""

    requirement_id: List[str] = Field(
        ..., min_items=1, description="List of requirement document IDs to delete"
    )

    @validator("requirement_id")
    def validate_requirement_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one requirement ID is required")
        return v

    class Config:
        json_schema_extra = {
            "example": {"requirement_id": ["req_doc_123", "req_doc_456"]}
        }


class RequirementDocumentDownloadRequest(BaseModel):
    """Schema for downloading requirement documents"""

    file_id: List[str] = Field(
        ..., min_items=1, description="List of requirement document IDs to download"
    )

    @validator("file_id")
    def validate_file_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one file ID is required")
        return v

    class Config:
        json_schema_extra = {"example": {"file_id": ["file123", "file456", "file789"]}}


class RequirementDocumentPushRequest(BaseModel):
    """Schema for pushing requirement documents to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of requirement document IDs to push"
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
                "commit_message": "Update requirement documents",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


class RequirementDocumentUpdateContentRequest(BaseModel):
    """Schema for updating requirement document content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "# System Requirements\n\n## Overview\nThis document describes..."
            }
        }
