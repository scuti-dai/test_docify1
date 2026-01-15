"""
Issue Service
Handles issue business logic
"""

import logging
from typing import Dict, Any, Optional
from uuid6 import uuid7
from fastapi import HTTPException, status

from app.services.project_service import ProjectService
from app.services.source_code_service import source_code_service
from app.services.git_services.git_issue_service import create_git_issue
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import get_current_utc_time
from app.utils.constants import PermissionLevel

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


class IssueService:
    """Service for issue operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(IssueService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not IssueService._initialized:
            self.project_service = ProjectService()
            IssueService._initialized = True
            logger.info("[IssueService] Singleton instance initialized")
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[IssueService] Repository updated via set_repository")

    # region Public Functions

    async def create_issue(
        self,
        project_id: str,
        user_id: str,
        user_role: str,
        title: str,
        description: str,
        source_code_id: str,
        user_name: Optional[str] = None,
        token_password: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new issue"""
        logger.info(
            f"[create_issue] Start - project_id={project_id}, user_id={user_id}, source_code_id={source_code_id}"
        )

        if (
            not project_id
            or not user_id
            or not title
            or not description
            or not source_code_id
        ):
            logger.warning("[create_issue] Missing required input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if len(description) > 1000:
            logger.warning("[create_issue] Description exceeds max length")
            raise_http_error(
                status.HTTP_400_BAD_REQUEST, error_key="DESCRIPTION_TOO_LONG"
            )

        try:
            # Check user has access to project
            await self._validate_user_project_access(user_id, user_role, project_id)

            # Check source_code exists and belongs to project
            await self._validate_source_code(project_id, source_code_id)

            # Get project data to extract Git info
            project_data = await self.project_service.get_detail_project(project_id)
            if not project_data:
                logger.warning(
                    f"[create_issue] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            git_info = project_data.get("git") or {}
            ai_programming_info = project_data.get("ai_programming") or {}
            repository_url = git_info.get("repository")
            repo_provider = git_info.get("repo_provider")
            branch = git_info.get("branch")
            assignee = ai_programming_info.get("user_name")

            # Prepare content with basebranch prefix
            content_with_branch = description
            if branch:
                content_with_branch = f"basebranch: {branch}\n\n{description}"

            # Generate UUID7 for issue ID
            issue_id = str(uuid7())

            # Get current UTC timestamp
            current_time = get_current_utc_time()

            # Create issue document
            issue_doc = {
                "id": issue_id,
                "source_code_id": source_code_id,
                "project_id": project_id,
                "issue_info": {
                    "issue_title": title,
                    "issue_content": description,
                },
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            # Insert issue into database using repository
            is_inserted = await self.repository.insert_one(issue_doc)

            if is_inserted:
                logger.info(f"[create_issue] Success - issue_id={issue_id}")

                # Create issue on Git if credentials and repository info are available
                if user_name and token_password and repository_url and repo_provider:
                    try:
                        git_issue_result = await create_git_issue(
                            repository_url=repository_url,
                            repo_provider=repo_provider,
                            title=title,
                            body=content_with_branch,
                            user_name=user_name,
                            token_password=token_password,
                            assignee=assignee,
                            labels=["Docifycode"],
                        )
                        if git_issue_result:
                            logger.info(
                                f"[create_issue] Git issue created - issue_number={git_issue_result.get('number')}"
                            )
                        else:
                            logger.warning("[create_issue] Failed to create Git issue")
                    except Exception as git_error:
                        # Log error but don't fail the whole operation
                        logger.error(
                            f"[create_issue] Error creating Git issue: {git_error}"
                        )
                        save_exception_log_sync(
                            git_error,
                            f"[create_issue] Git issue creation error: {git_error}",
                            __name__,
                        )
                else:
                    logger.info(
                        "[create_issue] Skipping Git issue creation - missing credentials or repository info"
                    )

                return {
                    "id": issue_id,
                    "project_id": project_id,
                    "source_code_id": source_code_id,
                    "issue_info": {
                        "issue_title": title,
                        "issue_content": description,
                    },
                    "created_at": current_time,
                }
            else:
                logger.error("[create_issue] Failed to insert issue")
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[create_issue] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    # endregion

    # region Private Functions

    async def _validate_user_project_access(
        self, user_id: str, user_role: str, project_id: str
    ) -> None:
        """Validate user has access to project"""
        logger.info(
            f"[_validate_user_project_access] Start - project_id={project_id}, user_id={user_id}"
        )

        if not user_id or not project_id:
            logger.warning("[_validate_user_project_access] Missing input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Admin has access to all projects
            if user_role == PermissionLevel.ADMIN:
                logger.info("[_validate_user_project_access] Admin access granted")
                return

            # Check if user has access to project
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning(
                    f"[_validate_user_project_access] Access denied - project_id={project_id}, user_id={user_id}"
                )
                raise_http_error(status.HTTP_403_FORBIDDEN)

            logger.info(
                f"[_validate_user_project_access] Success - project_id={project_id}, user_id={user_id}"
            )

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[_validate_user_project_access] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _validate_source_code(self, project_id: str, source_code_id: str) -> None:
        """Validate source_code exists and belongs to project"""
        logger.info(
            f"[_validate_source_code] Start - project_id={project_id}, source_code_id={source_code_id}"
        )

        if not project_id or not source_code_id:
            logger.warning("[_validate_source_code] Missing input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check if source_code exists and belongs to project
            # get_source_by_id will raise HTTPException if not found
            await source_code_service.repository.get_source_by_id(
                project_id=project_id, file_id=source_code_id
            )

            logger.info(
                f"[_validate_source_code] Success - project_id={project_id}, source_code_id={source_code_id}"
            )

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[_validate_source_code] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion


# Create service instance
issue_service = IssueService()
