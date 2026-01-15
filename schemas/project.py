"""
Project schemas for API requests and responses
"""

import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, root_validator
from cec_docifycode_common.models.project import Project, DefaultDirectoryPaths
from app.utils.constants import BaseSpecification
from config_gateway import Config

logger = logging.getLogger(__name__)


# ===================== Schema definitions for Create/Update Project API =====================
class GitSpecSchema(BaseModel):
    """Git specification schema for create/update project API"""

    repository_url: Optional[str] = Field(..., description="Git repository URL")
    branch_name: Optional[str] = Field(..., description="Git branch name")
    user_name: Optional[str] = Field(None, description="Git account username")
    token_password: Optional[str] = Field(None, description="Git token or password")
    repo_provider: Optional[str] = Field(
        None, description="Repository provider (github, gitbucket)"
    )

    class Config:
        pass


class AISpecSchema(BaseModel):
    """AI programming specification schema for create/update project API"""

    user_name: str = Field(..., description="AI programming account username")
    token_password: str = Field(
        ..., description="AI programming account token or password"
    )

    class Config:
        pass


class DirectorySpecSchema(BaseModel):
    """Directory specification schema for create/update project API"""

    rd: Optional[str] = Field(
        None, description="Directory containing Requirement Document"
    )
    bd: Optional[str] = Field(
        None, description="Directory containing Basic Design Document"
    )
    pd: Optional[str] = Field(
        None, description="Directory containing Detailed Design Document"
    )
    src: Optional[str] = Field(None, description="Directory containing Source Code")
    utd: Optional[str] = Field(
        None, description="Directory containing Unit Test Design"
    )
    utc: Optional[str] = Field(None, description="Directory containing Unit Test Code")

    class Config:
        pass


class JiraSpecSchema(BaseModel):
    """JIRA configuration schema for create/update project API"""

    url: Optional[str] = Field(None, description="URL to access JIRA")
    project_name: Optional[str] = Field(
        None, description="JIRA project name corresponding to the project"
    )
    user_name: Optional[str] = Field(
        None, description="Username account used to access JIRA"
    )
    password: Optional[str] = Field(None, description="Password/Token to access JIRA")

    class Config:
        pass


class RedmineSpecSchema(BaseModel):
    """Redmine configuration schema for create/update project API"""

    url: Optional[str] = Field(None, description="URL to access Redmine")
    project_name: Optional[str] = Field(
        None, description="Redmine project name corresponding to the project"
    )
    user_name: Optional[str] = Field(
        None, description="Username account used to access Redmine"
    )
    password: Optional[str] = Field(
        None, description="Password/Token to access Redmine"
    )

    class Config:
        pass


class GitInfo(BaseModel):
    """Git information schema"""

    repository: Optional[str] = None
    branch: Optional[str] = None
    commit_hash: Optional[str] = None

    class Config:
        pass


class ProjectCreate(BaseModel):
    """Schema for creating a project"""

    project_name: str = Field(..., min_length=1, max_length=Config.MAX_LENGTH_NAME)
    description: str = Field(..., max_length=Config.MAX_LENGTH_DESCRIPTION)
    language: str = Field(..., pattern="^(Java|Python|C#)$")
    framework: Optional[str] = None
    git: Optional[GitInfo] = None

    class Config:
        pass


class ProjectUpdate(BaseModel):
    """Schema for updating a project"""

    name: Optional[str] = Field(None, min_length=1, max_length=Config.MAX_LENGTH_NAME)
    description: Optional[str] = Field(None, max_length=Config.MAX_LENGTH_DESCRIPTION)
    programming_language: Optional[str] = Field(None, pattern="^(Java|Python|C#)$")
    framework_test: Optional[str] = None
    git_spec: Optional[GitSpecSchema] = None
    ai_spec: Optional[AISpecSchema] = None
    directory_spec: Optional[DirectorySpecSchema] = None
    share: Optional[List[str]] = None

    class Config:
        pass


class ProjectStatusManage(BaseModel):
    """Project status and error tracking."""

    status: Optional[str] = Field(
        None, description="Current project status (processing, completed, error, ...)"
    )
    error_info: Optional[str] = Field(
        None, description="Detailed error information when an issue occurs"
    )


class ProjectResponse(BaseModel):
    """Schema for project response"""

    project_id: str
    project_name: str
    description: str
    language: str
    framework: Optional[str]
    git: Optional[Dict[str, Any]]
    status_manage: Optional[ProjectStatusManage]
    created_at: str
    updated_at: str
    deleted_at: Optional[str]

    @classmethod
    def from_project(cls, project: Project) -> "ProjectResponse":
        """Create ProjectResponse from Project model"""
        logger.debug(
            f"[from_project] Creating ProjectResponse from Project: {project.id}"
        )
        try:
            # Create status_manage object from project's status and error_info
            status_manage = None
            if project.status is not None or project.error_info is not None:
                status_manage = ProjectStatusManage(
                    status=project.status, error_info=project.error_info
                )

            return cls(
                project_id=str(project.id),
                project_name=project.project_name,
                description=project.description,
                language=project.language,
                framework=project.framework,
                git=project.git,
                status_manage=status_manage,
                created_at=project.created_at.isoformat(),
                updated_at=project.updated_at.isoformat(),
                deleted_at=(
                    project.deleted_at.isoformat() if project.deleted_at else None
                ),
            )
        except Exception as e:
            logger.error(f"[from_project] Error creating ProjectResponse: {e}")
            raise

    class Config:
        pass


# ===================== New comprehensive Project interface =====================
class GitRepository(BaseModel):
    """Git repository information."""

    repository: Optional[str] = Field(None, description="GIT repository URL")
    branch: Optional[str] = Field(None, description="Current GIT branch name")


class AIProgrammingCredentials(BaseModel):
    """Temporary credentials for AI programming integrations (e.g., GitBucket/GitHub)."""

    user_name: Optional[str] = Field(None, description="GitBucket username")
    password: Optional[str] = Field(None, description="GitBucket password")
    token: Optional[str] = Field(None, description="GitHub access token")


class DirectorySpecSchema(BaseModel):
    """Directory specification schema for create/update project API"""

    rd: Optional[str] = Field(
        None, description="Requirements definition documents directory"
    )
    bd: Optional[str] = Field(None, description="Basic design documents directory")
    pd: Optional[str] = Field(None, description="Program design documents directory")
    src: Optional[str] = Field(None, description="Source code directory")
    utd: Optional[str] = Field(None, description="Unit Test Design documents directory")
    utc: Optional[str] = Field(None, description="Unit Test Code directory")

    class Config:
        pass


class ProjectDirectory(BaseModel):
    """Directory structure under the project root."""

    rd: Optional[str] = Field(
        None, description="Requirements definition documents directory"
    )
    bd: Optional[str] = Field(None, description="Basic design documents directory")
    pd: Optional[str] = Field(None, description="Program design documents directory")
    source_code: Optional[str] = Field(None, description="Source code directory")
    utd: Optional[str] = Field(None, description="Unit Test Design documents directory")
    utc: Optional[str] = Field(None, description="Unit Test Code directory")


class ProjectSettingItem(BaseModel):
    """Project settings and metadata."""

    project_name: Optional[str] = Field(
        None, description="Project name or its explanation"
    )
    description: Optional[str] = Field(
        None, description="Team explanation or detailed project content"
    )
    language: Optional[str] = Field(
        None, description="Programming language (Java, Python, C#, ...)"
    )
    framework: Optional[str] = Field(
        None, description="Framework used for Unit Test (UT test)"
    )
    git: Optional[GitRepository] = Field(None, description="GIT information")
    ai_programming: Optional[AIProgrammingCredentials] = Field(
        None, description="(Temporary) AI programming credentials"
    )
    directory: Optional[ProjectDirectory] = Field(
        None, description="Directory structure under root"
    )


class ProjectInterface(BaseModel):
    """Canonical interface for the Project collection (DB document shape)."""

    id: str = Field(
        ..., description="Unique identifier (UUIDv7). Primary Key and Partition Key"
    )
    setting_item: Optional[ProjectSettingItem] = Field(
        None, description="Project settings and metadata"
    )
    status_manage: Optional[ProjectStatusManage] = Field(
        None, description="Project status management"
    )
    created_at: Optional[str] = Field(
        None, description="Record creation time (ISO 8601)"
    )
    updated_at: Optional[str] = Field(
        None, description="Record last update time (ISO 8601)"
    )
    deleted_at: Optional[str] = Field(
        None, description="Record deletion time (ISO 8601)"
    )

    class Config:
        pass


class ConflictDownloadFile(BaseModel):
    """File descriptor for conflicted downloads"""

    id: str = Field(..., description="File ID")
    file_name: str = Field(..., description="Conflict file name")
    file_path: str = Field(..., description="Full relative path of the file")
    collection_name: str = Field(..., description="Origin collection name")

    class Config:
        pass


class ConflictDownloadRequest(BaseModel):
    """Request payload for conflict file download"""

    conflict_files: List[ConflictDownloadFile] = Field(
        ..., description="List of conflict files to download"
    )

    class Config:
        pass


class ProjectsDeleteRequest(BaseModel):
    """Request body for bulk delete projects. Accepts 'project_id' or 'project_ids'."""

    project_id: Optional[List[str]] = Field(
        None, description="List of project IDs to delete"
    )
    project_ids: Optional[List[str]] = Field(None, description="Alias for project_id")

    @root_validator(pre=True)
    def coerce_ids(cls, values):
        # Allow either key; normalize into 'project_id'
        if values.get("project_id") is None and values.get("project_ids") is not None:
            values["project_id"] = values.get("project_ids")
        return values

    def ids(self) -> List[str]:
        return list(self.project_id or [])


class ProjectsBulkDeleteResponse(BaseModel):
    """Response body for bulk delete projects, matching required shape"""

    statusCode: int
    message: str
    deleted_ids: List[str] = []
    failed_ids: List[str] = []


# ===================== New Create Project API Schema =====================
class ProjectCreateRequestSchema(BaseModel):
    """Schema for create project API request (JSON part of multipart/form-data)"""

    name: str = Field(
        ..., max_length=Config.MAX_LENGTH_NAME, description="Project name"
    )
    description: str = Field(
        ..., max_length=Config.MAX_LENGTH_DESCRIPTION, description="Project description"
    )
    git_spec: GitSpecSchema = Field(..., description="Project Git information")
    ai_spec: AISpecSchema = Field(..., description="AI programming account information")
    share: Optional[List[str]] = Field(
        default_factory=list, description="List of emails with shared permissions"
    )
    programming_language: str = Field(
        ..., description="Programming language", pattern="^(Python|Java|C#)$"
    )
    framework_test: str = Field(..., description="Testing framework")
    directory_spec: Optional[DirectorySpecSchema] = Field(
        None,
        description="Project directory information (optional, will use defaults if not provided)",
    )
    base_specification: str = Field(
        ...,
        description="Project starting point",
        pattern=f"^({'|'.join(BaseSpecification.VALUES)})$",
    )
    git_specification: Optional[str] = Field(
        None,
        description="Source code information when base_specification = source_code",
    )
    jira: JiraSpecSchema = Field(
        default_factory=lambda: JiraSpecSchema(),
        description="JIRA system connection configuration information",
    )
    redmine: RedmineSpecSchema = Field(
        default_factory=lambda: RedmineSpecSchema(),
        description="Redmine system connection configuration information",
    )

    class Config:
        pass


class ProjectCreateData(BaseModel):
    """Data schema for create project response"""

    id: str = Field(..., description="Project ID")
    project_name: str = Field(..., description="Project name")

    class Config:
        pass


class ProjectCreateResponseSchema(BaseModel):
    """Response schema for create project API"""

    statusCode: int
    message: str
    data: Optional[ProjectCreateData] = Field(None, description="Project creation data")

    class Config:
        pass
