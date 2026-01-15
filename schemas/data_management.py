"""
Data Management schemas for API requests and responses
"""

import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FolderInfo(BaseModel):
    """Folder information schema"""

    folder_id: str = Field(..., description="Folder ID")
    folder_name: str = Field(..., description="Folder name")
    parent_folder_id: Optional[str] = Field(None, description="Parent folder ID")

    class Config:
        pass


class DataManagementCreate(BaseModel):
    """Schema for creating data management"""

    id: str = Field(..., description="UUID7, unique identifier")
    project_id: str = Field(..., description="Reference to project.project_id")
    type: str = Field(..., description="Type: comment, requirement document, etc.")
    folders: List[FolderInfo] = Field(
        default_factory=list, description="List of folders"
    )

    class Config:
        pass


class DataManagementUpdate(BaseModel):
    """Schema for updating data management"""

    type: Optional[str] = None
    folders: Optional[List[FolderInfo]] = None

    class Config:
        pass


class DataManagementResponse(BaseModel):
    """Schema for data management response"""

    id: str
    project_id: str
    type: str
    folders: List[Dict[str, Any]]
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    class Config:
        pass
