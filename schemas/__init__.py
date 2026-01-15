# Schemas package

from .base import BaseResponse, PaginationResponse
from .auth import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    LogoutResponse,
    RefreshRequest,
    RefreshResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
)
from .project import ProjectCreate, ProjectUpdate, ProjectResponse, GitInfo
from .comment import CommentCreate, CommentUpdate, CommentResponse
from .data_management import (
    DataManagementCreate,
    DataManagementUpdate,
    DataManagementResponse,
    FolderInfo,
)
from .activity import ActivityCreate, ActivityUpdate, ActivityResponse
from .log import LogCreate, LogUpdate, LogResponse
from .requirement_document import (
    RequirementDocumentCreate,
    RequirementDocumentUpdate,
    RequirementDocumentResponse,
)
from .basic_design import (
    BasicDesignCreate,
    BasicDesignUpdate,
    BasicDesignResponse,
    BasicDesignContent,
    BasicDesignDetailResponse,
)
from .issue import IssueCreate, IssueUpdate, IssueResponse
from .source_code import (
    FolderResponse,
    FoldersListResponse,
    SourceFileResponse,
    SourceFilesListResponse,
    SourceFileDetail,
    SourceCodeData,
    SourceCodeListResponse,
)
from .unit_test import UnitTestCreate, UnitTestUpdate, UnitTestResponse
from .access_right import (
    AccessGroupCreate,
    AccessGroupResponse,
    AccessGroupListResponse,
    AccessGroupCreateResponse,
    AccessUserCreate,
    AccessUserResponse,
    AccessUserListResponse,
    AccessUserCreateResponse,
)
from .local_login import LocalLoginCreate, LocalLoginResponse

__all__ = [
    # Base schemas
    "BaseResponse",
    "PaginationResponse",
    # Auth schemas
    "LoginRequest",
    "LoginResponse",
    "LogoutRequest",
    "LogoutResponse",
    "RefreshRequest",
    "RefreshResponse",
    "ChangePasswordRequest",
    "ChangePasswordResponse",
    # Group schemas
    "GroupCreate",
    "GroupUpdate",
    "GroupResponse",
    # Project schemas
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectResponse",
    "GitInfo",
    # Comment schemas
    "CommentCreate",
    "CommentUpdate",
    "CommentResponse",
    # Data Management schemas
    "DataManagementCreate",
    "DataManagementUpdate",
    "DataManagementResponse",
    "FolderInfo",
    # Activity schemas
    "ActivityCreate",
    "ActivityUpdate",
    "ActivityResponse",
    # Log schemas
    "LogCreate",
    "LogUpdate",
    "LogResponse",
    # Requirement Document schemas
    "RequirementDocumentCreate",
    "RequirementDocumentUpdate",
    "RequirementDocumentResponse",
    # Basic Design schemas
    "BasicDesignCreate",
    "BasicDesignUpdate",
    "BasicDesignResponse",
    "BasicDesignContent",
    "BasicDesignDetailResponse",
    # Issue schemas
    "IssueCreate",
    "IssueUpdate",
    "IssueResponse",
    # Source Code schemas
    "FolderResponse",
    "FoldersListResponse",
    "SourceFileResponse",
    "SourceFilesListResponse",
    "SourceFileDetail",
    "SourceCodeData",
    "SourceCodeListResponse",
    # Unit Test schemas
    "UnitTestCreate",
    "UnitTestUpdate",
    "UnitTestResponse",
    # Access Right schemas
    "AccessGroupCreate",
    "AccessGroupResponse",
    "AccessGroupListResponse",
    "AccessGroupCreateResponse",
    "AccessUserCreate",
    "AccessUserResponse",
    "AccessUserListResponse",
    "AccessUserCreateResponse",
    # Local Login schemas
    "LocalLoginCreate",
    "LocalLoginResponse",
]
