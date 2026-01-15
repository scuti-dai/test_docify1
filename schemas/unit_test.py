"""
Unit Test schemas for API requests and responses
"""

import logging
from typing import Optional, Dict, Literal
from pydantic import BaseModel, Field
from datetime import datetime

# Import collection constants from Common
from cec_docifycode_common.utils.collection_constants import (
    UNIT_TEST_DESIGN_COLLECTION,
    UNIT_TEST_CODE_COLLECTION,
    UNIT_TEST_COLLECTION_MAP,
)

logger = logging.getLogger(__name__)


# #region UnitTestDesign Schemas
class UnitTestDesignItem(BaseModel):
    """Unit Test Design item response schema"""

    id: str = Field(..., description="Unit test design ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    source_code_id: str = Field(..., description="Source code ID")
    class_id: str = Field(..., description="Class ID")
    method_id: str = Field(..., description="Method ID")
    file_name: str = Field(..., description="File name")
    unit_test_design_json: Optional[str] = Field(
        None, description="Unit test design JSON content"
    )
    decision_table: Optional[str] = Field(None, description="Decision table")
    test_pattern: Optional[str] = Field(None, description="Test pattern")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    collection_name: str = Field(
        default=UNIT_TEST_DESIGN_COLLECTION, description="Collection name"
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
                "source_code_id": "0123456789abcdef01234567",
                "class_id": "class_123",
                "method_id": "method_123",
                "file_name": "UserServiceTest.json",
                "unit_test_design_json": '{"test_cases": [...]}',
                "decision_table": "| Input | Expected Output |\n|-------|----------------|\n| valid | success |",
                "test_pattern": "| Test Case | Input | Expected |\n|-----------|-------|----------|\n| TC1 | user1 | success |",
                "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                "sync_status": "synced",
                "collection_name": "unit_test_design",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "deleted_at": None,
            }
        }


# #endregion


# #region UnitTestCode Schemas
class UnitTestCodeItem(BaseModel):
    """Unit Test Code item response schema"""

    id: str = Field(..., description="Unit test code ID (UUID7)")
    project_id: str = Field(..., description="Project ID")
    source_code_id: str = Field(..., description="Source code ID")
    class_id: str = Field(..., description="Class ID")
    method_id: str = Field(..., description="Method ID")
    file_name: str = Field(..., description="File name")
    decision_table: Optional[str] = Field(None, description="Decision table")
    test_pattern: Optional[str] = Field(None, description="Test pattern")
    commit_id: Optional[str] = Field(None, description="Commit ID")
    sync_status: Optional[str] = Field(None, description="Sync status")
    collection_name: str = Field(
        default=UNIT_TEST_CODE_COLLECTION, description="Collection name"
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
                "source_code_id": "0123456789abcdef01234567",
                "class_id": "class_123",
                "method_id": "method_123",
                "file_name": "UserServiceTest.java",
                "decision_table": "| Input | Expected Output |\n|-------|----------------|\n| valid | success |",
                "test_pattern": "| Test Case | Input | Expected |\n|-----------|-------|----------|\n| TC1 | user1 | success |",
                "commit_id": "a1b2c3d4e5f6g7h8i9j0k1l2",
                "sync_status": "synced",
                "collection_name": "unit_test_code",
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "deleted_at": None,
            }
        }


# #endregion


# #region Legacy Schemas (for backward compatibility)
class UnitTestCreate(BaseModel):
    """Schema for creating a unit test (Legacy - for backward compatibility)"""

    unit_test_id: str = Field(..., description="UUID7, unique identifier")
    project_id: str = Field(..., description="Reference to project.project_id")
    user_id: str = Field(..., description="Reference to user.user_id")
    username: str = Field(..., description="Username")
    test_name: str = Field(..., description="Test name")
    test_content: str = Field(..., description="Test content")
    framework: str = Field(..., description="Testing framework")

    class Config:
        pass


class UnitTestUpdate(BaseModel):
    """Schema for updating a unit test (Legacy - for backward compatibility)"""

    test_name: Optional[str] = None
    test_content: Optional[str] = None
    framework: Optional[str] = None

    class Config:
        pass


class UnitTestResponse(BaseModel):
    """Schema for unit test response (Legacy - for backward compatibility)"""

    unit_test_id: str
    project_id: str
    user_id: str
    username: str
    test_name: str
    test_content: str
    framework: str
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    class Config:
        pass


# #endregion


# #region Generate Unit Test Schema
class GenerateUnitTestRequest(BaseModel):
    """Schema for generating unit tests"""

    input_source: Literal["detail_design", "source_code"] = Field(
        ...,
        description="Source type: 'detail_design' for detail design documents or 'source_code' for source code files",
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


class GenerateUnitTestCodeRequest(BaseModel):
    """Schema for generating unit test code"""

    input_source: Literal["unit_test_design"] = Field(
        ...,
        description="Source type: 'unit_test_design' for unit test design documents",
    )

    class Config:
        json_schema_extra = {
            "example": {
                "input_source": "unit_test_design",
            }
        }


# #endregion
