"""
Source Code Schemas
Pydantic models for source code API requests and responses
"""

from typing import Optional, List, Literal, Dict, Any
from pydantic import BaseModel, Field, model_validator, validator
from datetime import datetime


# #region Folder Schemas
class FolderBase(BaseModel):
    """Base folder schema"""

    folder_id: str = Field(..., description="Folder ID")
    folder_name: str = Field(..., description="Folder name")
    parent_folder_id: Optional[str] = Field(None, description="Parent folder ID")


class FolderResponse(FolderBase):
    """Folder response schema"""

    class Config:
        json_schema_extra = {
            "example": {
                "folder_id": "folder_001",
                "folder_name": "src",
                "parent_folder_id": None,
            }
        }


class FoldersListResponse(BaseModel):
    """Response schema for folder list"""

    data: List[FolderResponse]
    total: int = Field(..., description="Total number of folders")

    class Config:
        json_schema_extra = {
            "example": {
                "data": [
                    {
                        "folder_id": "folder_001",
                        "folder_name": "src",
                        "parent_folder_id": None,
                    }
                ],
                "total": 1,
            }
        }


# #endregion


# #region File Schemas
class SourceFileBase(BaseModel):
    """Base source file schema"""

    id: str = Field(..., description="Source code ID")
    project_id: str = Field(..., description="Project ID")
    file_path: str = Field(..., description="Full file path")
    file_name: str = Field(..., description="File name")
    folder_id: Optional[str] = Field(None, description="Folder ID")
    commit_id: Optional[str] = Field(None, description="Git commit ID")
    sync_status: Optional[str] = Field(
        None,
        description=(
            "Synchronization status: "
            "'push' (uploaded, not on git), "
            "'pull' (new commit not pulled), "
            "'pull_push' (changes on both sides), "
            "'delete_push' (deleted locally, not on git), "
            "'delete_pull' (deleted on git, not locally), "
            "'synced' (no changes)"
        ),
    )


class SourceFileResponse(SourceFileBase):
    """Source file response schema"""

    detail_design_document_id: List[str] = Field(
        default_factory=list, description="Detail design document IDs"
    )
    unit_test_id: List[str] = Field(default_factory=list, description="Unit test IDs")
    issue_id: List[str] = Field(default_factory=list, description="Issue IDs")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last updated timestamp")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "source_001",
                "project_id": "project_123",
                "file_path": "/src/components/Button.js",
                "file_name": "Button.js",
                "folder_id": "folder_123",
                "commit_id": "a1b2c3d4e5f6",
                "sync_status": "synced",
                "detail_design_document_id": ["doc_123"],
                "unit_test_id": ["test_123"],
                "issue_id": ["issue_123"],
                "created_at": "2025-11-10T09:00:00",
                "updated_at": "2025-11-11T09:00:00",
            }
        }


class SourceFilesListResponse(BaseModel):
    """Response schema for source files list"""

    data: List[SourceFileResponse]
    total: int = Field(..., description="Total number of files")
    folder_path: str = Field(..., description="Current folder path")

    class Config:
        json_schema_extra = {
            "example": {
                "data": [
                    {
                        "id": "source_001",
                        "project_id": "project_123",
                        "file_path": "/src/components/Button.js",
                        "file_name": "Button.js",
                        "folder_id": "folder_123",
                        "commit_id": "a1b2c3d",
                        "sync_status": "synced",
                        "detail_design_document_id": [],
                        "unit_test_id": [],
                        "issue_id": [],
                        "created_at": "2025-11-10T09:00:00",
                        "updated_at": "2025-11-03T10:30:00",
                    }
                ],
                "total": 1,
                "folder_path": "/src/components",
            }
        }


# #endregion


# #region Unified Source Code Response
class SourceCodeData(BaseModel):
    """Combined data for folders and sources"""

    folders: List[FolderResponse] = Field(
        default_factory=list, description="List of folders"
    )
    sources: List[SourceFileResponse] = Field(
        default_factory=list, description="List of source files"
    )


class SourceCodeListResponse(BaseModel):
    """Unified response schema for source code (folders + files)"""

    statusCode: int = Field(200, description="HTTP status code")
    message: str = Field("Success", description="Response message")
    data: SourceCodeData

    class Config:
        json_schema_extra = {
            "example": {
                "statusCode": 200,
                "message": "Success",
                "data": {
                    "folders": [
                        {
                            "folder_id": "folder_001",
                            "folder_name": "src",
                            "parent_folder_id": None,
                        }
                    ],
                    "sources": [
                        {
                            "id": "source_001",
                            "project_id": "project_123",
                            "file_path": "/src/components/Button.js",
                            "file_name": "Button.js",
                            "folder_id": "folder_123",
                            "commit_id": "a1b2c3d4e5f6",
                            "sync_status": "synced",
                            "detail_design_document_id": ["doc_123"],
                            "unit_test_id": ["test_123"],
                            "issue_id": ["issue_123"],
                            "created_at": "2025-11-10T09:00:00Z",
                            "updated_at": "2025-11-11T09:00:00Z",
                        }
                    ],
                },
            }
        }


class UnitTestFileBase(BaseModel):
    """Base schema for unit test entries displayed in folder tree"""

    id: str = Field(..., description="Unit test document ID")
    project_id: str = Field(..., description="Project ID")
    file_name: str = Field(..., description="File name")
    folder_id: Optional[str] = Field(None, description="Folder ID")
    class_id: Optional[str] = Field(None, description="Class ID")
    method_id: Optional[str] = Field(None, description="Method ID")
    commit_id: Optional[str] = Field(None, description="Git commit ID")
    sync_status: Optional[str] = Field(None, description="Synchronization status")
    created_at: Optional[datetime] = Field(None, description="Created at timestamp")
    updated_at: Optional[datetime] = Field(None, description="Updated at timestamp")


class UnitTestDesignListData(BaseModel):
    """Folders + unit test design files"""

    folders: List[FolderResponse] = Field(
        default_factory=list, description="Folder list for UTD"
    )
    utd_list: List[UnitTestFileBase] = Field(
        default_factory=list, description="Unit test design files"
    )


class UnitTestCodeListData(BaseModel):
    """Folders + unit test code files"""

    folders: List[FolderResponse] = Field(
        default_factory=list, description="Folder list for UTC"
    )
    utc_list: List[UnitTestFileBase] = Field(
        default_factory=list, description="Unit test code files"
    )


class UnitTestDesignListResponse(BaseModel):
    """Response for unit test design folder tree"""

    statusCode: int = Field(200, description="HTTP status code")
    message: str = Field("Success", description="Response message")
    data: UnitTestDesignListData


class UnitTestCodeListResponse(BaseModel):
    """Response for unit test code folder tree"""

    statusCode: int = Field(200, description="HTTP status code")
    message: str = Field("Success", description="Response message")
    data: UnitTestCodeListData


# #endregion


# #region File Detail Schema (for future use)
class SourceFileDetail(SourceFileResponse):
    """Detailed source file schema with content"""

    content: Optional[str] = Field(None, description="File content")
    language: Optional[str] = Field(None, description="Programming language")
    lines_count: Optional[int] = Field(None, description="Number of lines")

    class Config:
        json_schema_extra = {
            "example": {
                "id": "file_001",
                "name": "Button.js",
                "path": "/src/components/Button.js",
                "folder_path": "/src/components",
                "commit_id": "a1b2c3d",
                "size": 2048,
                "content": "import React from 'react';...",
                "language": "javascript",
                "lines_count": 50,
                "updated_at": "2025-11-03T10:30:00",
            }
        }


# #endregion


# #region Source Detail Schemas
class DescriptionGroup(BaseModel):
    description_id: Optional[str] = Field(None, description="Description document ID")
    description_json: Optional[str] = Field(
        None, description="Description data in JSON format"
    )
    description: Optional[str] = Field(
        None, description="Description content in plain text"
    )


class ActivityDiagramGroup(BaseModel):
    activity_diagram_id: Optional[str] = Field(
        None, description="Activity diagram document ID"
    )
    activity_diagram: Optional[str] = Field(
        None, description="Activity diagram data (image / JSON)"
    )
    activity_diagram_image: Optional[str] = Field(
        None, description="Activity diagram image (base64 encoded)"
    )


class DetailedDesignGroup(BaseModel):
    detailed_design_id: Optional[str] = Field(
        None, description="Detailed design document ID"
    )
    detailed_design: Optional[str] = Field(None, description="Detailed design content")


class InterfaceDesignGroup(BaseModel):
    interface_design_id: Optional[str] = Field(
        None, description="Interface design document ID"
    )
    interface_design: Optional[str] = Field(
        None, description="Interface design content"
    )


class DetailDesignEntry(BaseModel):
    description_group: Optional[DescriptionGroup] = Field(
        None, description="Description group data"
    )
    activity_diagram_group: Optional[ActivityDiagramGroup] = Field(
        None, description="Activity diagram group data"
    )
    detailed_design_group: Optional[DetailedDesignGroup] = Field(
        None, description="Detailed design group data"
    )
    interface_design_group: Optional[InterfaceDesignGroup] = Field(
        None, description="Interface design group data"
    )


class UnitTestDesignGroup(BaseModel):
    unit_test_design_id: Optional[str] = Field(
        None, description="Unit test design document ID"
    )
    unit_test_design_json: Optional[str] = Field(
        None, description="Unit test design content"
    )
    decision_table: Optional[str] = Field(
        None, description="Decision table for the test"
    )
    test_pattern: Optional[str] = Field(None, description="Test pattern description")


class UnitTestCodeGroup(BaseModel):
    ut_code_id: Optional[str] = Field(None, description="Unit test code document ID")
    ut_code_content: Optional[str] = Field(
        None, description="Unit test code implementation"
    )


class MethodUnitTest(BaseModel):
    unit_test_design_group: Optional[UnitTestDesignGroup] = Field(
        None, description="Unit test design information"
    )
    ut_code_group: Optional[UnitTestCodeGroup] = Field(
        None, description="Unit test code information"
    )


class MethodDetail(BaseModel):
    method_id: str = Field(..., description="Method ID")
    method_name: str = Field(..., description="Method name")
    method_content: str = Field(..., description="Method content")
    unit_test: List[MethodUnitTest] = Field(
        default_factory=list, description="Unit test information"
    )


class SourceClassDetail(BaseModel):
    class_id: str = Field(..., description="Class ID")
    class_name: str = Field(..., description="Class name")
    class_content: str = Field(..., description="Class content")
    detail_design: Dict[str, Any] = Field(
        default_factory=dict, description="Detail design data for the class (object)"
    )
    methods: List[MethodDetail] = Field(
        default_factory=list, description="List of methods in the class"
    )


class GlobalMethodDetail(MethodDetail):
    """Global method detail - same as MethodDetail without activity_diagram_group"""

    pass


class SourceDetailData(BaseModel):
    id: str = Field(..., description="Source code file ID")
    project_id: str = Field(..., description="Project ID")
    file_path: str = Field(..., description="Absolute file path")
    file_name: str = Field(..., description="File name")
    folder_id: Optional[str] = Field(None, description="Folder ID")
    source_code: Optional[str] = Field(None, description="Full source code content")
    classes: List[SourceClassDetail] = Field(
        default_factory=list, description="Classes within the file"
    )
    global_methods: List[GlobalMethodDetail] = Field(
        default_factory=list, description="Global methods"
    )
    commit_id: Optional[str] = Field(None, description="Git commit ID")
    sync_status: Optional[str] = Field(None, description="Synchronization status")
    detail_design_document_id: List[str] = Field(
        default_factory=list, description="Related detail design document IDs"
    )
    unit_test_id: List[str] = Field(
        default_factory=list, description="Linked unit test IDs"
    )
    issue_id: List[str] = Field(default_factory=list, description="Linked issue IDs")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last updated timestamp")


class SourceDetailResponse(BaseModel):
    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: SourceDetailData = Field(..., description="Source file detail data")


# #endregion


# #region Unit Test Design Detail Schema
class MethodSummary(BaseModel):
    method_id: str = Field(..., description="Method ID")
    method_name: str = Field(..., description="Method name")
    method_content: str = Field(..., description="Method content")


class SourceClassSummary(BaseModel):
    class_id: str = Field(..., description="Class ID")
    class_name: str = Field(..., description="Class name")
    class_content: str = Field(..., description="Class content")
    methods: List[MethodSummary] = Field(
        default_factory=list, description="Methods within the class"
    )


class SourceFileSummary(BaseModel):
    id: str = Field(..., description="Source code file ID")
    project_id: str = Field(..., description="Project ID")
    file_path: str = Field(..., description="File path")
    file_name: str = Field(..., description="File name")
    folder_id: Optional[str] = Field(None, description="Folder ID")
    source_code: Optional[str] = Field(None, description="Full source code")
    classes: List[SourceClassSummary] = Field(
        default_factory=list, description="Classes defined in the file"
    )
    global_methods: List[MethodSummary] = Field(
        default_factory=list, description="Global methods defined in the file"
    )


class UnitTestDesignItem(BaseModel):
    unit_test_id: str = Field(..., description="Unit test document ID")
    project_id: str = Field(..., description="Project ID")
    file_name: Optional[str] = Field(None, description="UTD file name")
    source_code_id: str = Field(..., description="Related source code ID")
    class_id: Optional[str] = Field(None, description="Class reference")
    method_id: Optional[str] = Field(None, description="Method reference")
    unit_test_design_json: Optional[str] = Field(
        None, description="Original unit test design JSON"
    )
    decision_table: Optional[str] = Field(None, description="Decision table data")
    test_pattern: Optional[str] = Field(None, description="Test pattern data")
    commit_id: Optional[str] = Field(None, description="Latest commit ID")
    sync_status: Optional[str] = Field(None, description="Synchronization status")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    updated_at: Optional[datetime] = Field(None, description="Updated timestamp")


class UnitTestDesignDetailData(BaseModel):
    file: SourceFileSummary = Field(..., description="Source file information")
    unit_test_designs: List[UnitTestDesignItem] = Field(
        default_factory=list, description="List of unit test design files"
    )


class UnitTestDesignDetailResponse(BaseModel):
    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: UnitTestDesignDetailData = Field(..., description="Response data")


# #endregion


# #region Unit Test Code Detail Schema
class UnitTestCodeItem(BaseModel):
    unit_test_id: str = Field(..., description="Unit test code document ID")
    project_id: str = Field(..., description="Project ID")
    file_name: Optional[str] = Field(None, description="UTC file name")
    source_code_id: str = Field(..., description="Related source code ID")
    class_id: Optional[str] = Field(None, description="Class reference")
    method_id: Optional[str] = Field(None, description="Method reference")
    ut_code_content: Optional[str] = Field(None, description="Unit test code content")
    commit_id: Optional[str] = Field(None, description="Latest commit ID")
    sync_status: Optional[str] = Field(None, description="Synchronization status")
    created_at: Optional[datetime] = Field(None, description="Created timestamp")
    updated_at: Optional[datetime] = Field(None, description="Updated timestamp")


class UnitTestCodeDetailData(BaseModel):
    file: SourceFileSummary = Field(..., description="Source file information")
    unit_test_codes: List[UnitTestCodeItem] = Field(
        default_factory=list, description="List of unit test code files"
    )


class UnitTestCodeDetailResponse(BaseModel):
    statusCode: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Response message")
    data: UnitTestCodeDetailData = Field(..., description="Response data")


# #endregion


# #region Update Content Schema
class SourceCodeUpdateContentRequest(BaseModel):
    """Schema for updating source code content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {"content": "public class UserService {\n    // Updated code\n}"}
        }


# #endregion


# #region Update UTD Content Schema
class UnitTestDesignUpdateContentRequest(BaseModel):
    """Schema for updating unit test design content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {
                "content": '{"test_cases": [{"id": "TC1", "input": "user1", "expected": "success"}]}'
            }
        }


# #endregion


# #region Update UTC Content Schema
class UnitTestCodeUpdateContentRequest(BaseModel):
    """Schema for updating unit test code content"""

    content: str = Field(..., description="File content after editing")

    class Config:
        json_schema_extra = {
            "example": {
                "content": "@Test\npublic void testCreateUser() {\n    // Test code\n}"
            }
        }


# #endregion


# #region Download Schema
class SourceCodeDownloadRequest(BaseModel):
    """Schema for downloading source code files and folders"""

    file_id: Optional[List[str]] = Field(
        None, description="List of source code file IDs (source_code_id) to download"
    )
    folder_id: Optional[List[str]] = Field(
        None, description="List of folder IDs to download"
    )

    @model_validator(mode="after")
    def validate_at_least_one_provided(self):
        """Validate that at least one of file_id or folder_id is provided"""
        file_id = self.file_id
        folder_id = self.folder_id

        if not file_id and not folder_id:
            raise ValueError("At least one of file_id or folder_id must be provided")

        if file_id is not None and len(file_id) == 0:
            raise ValueError("If file_id is provided, it cannot be empty")

        if folder_id is not None and len(folder_id) == 0:
            raise ValueError("If folder_id is provided, it cannot be empty")

        return self

    class Config:
        json_schema_extra = {
            "example": {
                "file_id": ["source_001", "source_002"],
                "folder_id": ["folder_123"],
            }
        }


# #endregion


# #region Git Push Schemas
class SourceCodePushRequest(BaseModel):
    """Schema for pushing source code files to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of source code file IDs to push"
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
                "commit_message": "Update source code",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


class UnitTestSpecPushRequest(BaseModel):
    """Schema for pushing unit test spec files to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of unit test spec file IDs to push"
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
                "commit_message": "Update unit test spec",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


class UnitTestCodePushRequest(BaseModel):
    """Schema for pushing unit test code files to Git"""

    id: List[str] = Field(
        ..., min_items=1, description="List of unit test code file IDs to push"
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
                "commit_message": "Update unit test code",
                "user_name": "gituser",
                "token_password": "token123",
            }
        }


# #endregion


# #region Generate Source Code Schema
class GenerateSourceCodeRequest(BaseModel):
    """Schema for generating source code"""

    input_source: Literal["detail_design"] = Field(
        ...,
        description="Source type: 'detail_design' for detail design documents",
    )
    user_name: Optional[str] = Field(
        None, description="Git username for repository access (optional)"
    )

    token_password: Optional[str] = Field(
        None, description="GitHub Personal Access Token (PAT) (optional)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "input_source": "detail_design",
                "user_name": "cec",
                "token_password": "ghp_xxxxxx",
            }
        }


# #endregion
