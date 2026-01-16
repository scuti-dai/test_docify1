"""
Project API endpoints
"""

import logging
import json
from typing import Any, List, Optional, Dict
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
    Body,
    Request,
    UploadFile,
    File,
    Form,
    Path,
)
from slowapi import Limiter
from slowapi.util import get_remote_address
from app.services.git_services.git_validate import (
    validate_git_repository,
    validate_git_url,
)
from app.services.git_services.git_service import get_default_branch, get_list_branches
from app.services.git_services.git_clone import clone_code_from_git
from app.services.git_services.git_check_changes import check_repo_changes
from app.services.git_services.git_pull import pull_data_from_git
from app.services.git_services.git_change_branch import change_git_branch
from app.services.git_services.git_change_folder_name import rename_folder_and_push
from app.core.database import app_db
from cec_docifycode_common.models.detail_design import (
    DETAIL_DESIGN_TYPE_CD,
    DETAIL_DESIGN_TYPE_MD,
    DETAIL_DESIGN_TYPE_ID,
    DETAIL_DESIGN_TYPE_FS,
    DETAIL_DESIGN_TYPE_AC,
    FILE_NAME_DETAIL_DESIGN,
)
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from app.utils.constants import SyncStatus, GitType, ProjectEventType
from cec_docifycode_common.models.project import DefaultDirectoryPaths
from app.schemas.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
    ProjectsDeleteRequest,
    ProjectsBulkDeleteResponse,
    ProjectCreateRequestSchema,
    ProjectCreateResponseSchema,
    DirectorySpecSchema,
    ConflictDownloadRequest,
)
from app.services.requirement_document_service import requirement_document_service
from app.schemas.base import BaseResponse
from app.api.deps import get_current_user
from app.services.project_service import project_service
from app.services.user_service import user_service
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import (
    is_filename_length_valid,
    is_valid_file_extension,
    validate_file_size_and_type,
)
from app.utils.constants import BaseSpecification
from app.core.error_messages import get_error_response
from cec_docifycode_common.models.unit_test import UnitTestCode, UnitTestDesign
from app.services.conflict_download_service import conflict_download_service
from app.utils.download_utils import build_download_file_response

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("", response_model=BaseResponse)
async def get_list_projects(
    user_id: str = Query(..., description="User ID to filter projects for")
):
    """Get list of projects for a specific user via query (?user_id=...)."""
    logger.info(f"[get_list_projects] Start - user_id={user_id}")

    if not user_id:
        logger.warning("[get_list_projects] Missing user_id input")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        projects = await project_service.get_projects_for_user(user_id)
        logger.info(
            f"[get_list_projects] Success - user_id={user_id}, count={len(projects)}"
        )
        return BaseResponse(
            statusCode=200, message="Success", data={"projects": projects}
        )
    except Exception as e:
        error_message = f"[get_list_projects] Error - user_id={user_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{project_id}", response_model=BaseResponse)
@limiter.limit("200/minute")
async def get_project(
    request: Request, project_id: str, current_user=Depends(get_current_user)
):
    """Get project by ID"""
    logger.info(f"[get_project] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_project] Missing project_id input")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        project_data = await project_service.get_detail_project(project_id)
        if not project_data:
            logger.warning(f"[get_project] Project not found - project_id={project_id}")
            raise_http_error(status.HTTP_404_NOT_FOUND)

        logger.info(f"[get_project] Success - project_id={project_id}")
        return BaseResponse(statusCode=200, message="Success", data=project_data)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_project] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/download/conflict",
)
@limiter.limit("30/minute")
async def download_conflict_files(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    payload: ConflictDownloadRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Download conflicted files as a ZIP archive"""
    logger.info(f"[download_conflict_files] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[download_conflict_files] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    if not payload or not payload.conflict_files:
        logger.warning("[download_conflict_files] No conflict files provided")
        raise_http_error(status.HTTP_400_BAD_REQUEST)
    user_id = current_user["user_id"]
    # Check if user has access to project
    has_access = await project_service.check_user_project_access(user_id, project_id)
    if not has_access:
        logger.warning(
            f"[get_list_branch] Access denied - user_id={user_id}, project_id={project_id}"
        )
        raise_http_error(status.HTTP_403_FORBIDDEN, error_key="FORBIDDEN")

    try:
        download_result = await conflict_download_service.generate_conflict_zip(
            project_id=project_id,
            user_id=user_id,
            conflict_files=payload.conflict_files,
        )
        logger.info(f"[download_conflict_files] Success - project_id={project_id}")
        return build_download_file_response(download_result)
    except HTTPException:
        raise
    except Exception as e:
        error_message = (
            f"[download_conflict_files] Error - project_id={project_id}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "",
    response_model=ProjectCreateResponseSchema,
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "project_data": {
                                "type": "string",
                                "description": "JSON string containing project data",
                            },
                            "upload_files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                                "description": "Array of files to upload (can be single file or multiple files)",
                            },
                        },
                        "required": ["project_data"],
                    },
                }
            }
        }
    },
)
@limiter.limit("10/minute")
async def create_project(
    request: Request,
    current_user=Depends(get_current_user),
):
    """Create new project with multipart/form-data - chỉ validate request"""
    logger.info(f"[create_project] Start - user_id={current_user.get('user_id')}")

    try:
        # Parse multipart form data manually to handle both single and multiple files
        form = await request.form()
        git_type = None
        # Get project_data
        project_data = form.get("project_data")
        if not project_data:
            logger.warning("[create_project] Missing project_data")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        # Parse JSON project_data
        try:
            if isinstance(project_data, str):
                project_data_dict = json.loads(project_data)
            else:
                # Handle form data object
                project_data_dict = json.loads(project_data.value)
        except (json.JSONDecodeError, AttributeError) as e:
            logger.error(f"[create_project] Invalid JSON in project_data: {e}")
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
            )

        # Validate project data using schema
        try:
            project_request = ProjectCreateRequestSchema(**project_data_dict)
        except Exception as e:
            error_message = f"[create_project] Validation error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
            )

        # Set default directory_spec (always set from defaults)
        project_request.directory_spec = DirectorySpecSchema(
            **{
                "rd": DefaultDirectoryPaths.RD,
                "bd": DefaultDirectoryPaths.BD,
                "pd": DefaultDirectoryPaths.PD,
                "src": DefaultDirectoryPaths.SRC,
                "utd": DefaultDirectoryPaths.UTD,
                "utc": DefaultDirectoryPaths.UTC,
            }
        )

        # Get upload_files
        upload_files = await _extract_upload_files(form)

        # Check if project name already exists
        if await project_service.check_project_name_exists(project_request.name):
            raise_http_error(
                status.HTTP_409_CONFLICT, error_key="PROJECT_NAME_ALREADY_EXISTS"
            )

        # Validate git_spec for SOURCE_CODE base_specification
        if project_request.base_specification == BaseSpecification.SOURCE_CODE:
            if (
                not project_request.git_spec
                or not project_request.git_spec.repository_url
                or not project_request.git_spec.user_name
                or not project_request.git_spec.token_password
            ):
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="GIT_INVALID_URL",
                )

            # If git_specification is empty, use directory_spec.src
            if (
                not project_request.git_specification
                or not project_request.git_specification.strip()
            ):
                if project_request.directory_spec.src:
                    project_request.git_specification = (
                        project_request.directory_spec.src
                    )
                else:
                    logger.warning(
                        "[create_project] Both git_specification and directory_spec.src are empty for SOURCE_CODE"
                    )
            else:
                # If git_specification is different from directory_spec.src, update directory_spec.src
                if (
                    project_request.directory_spec.src
                    != project_request.git_specification
                ):
                    project_request.directory_spec.src = (
                        project_request.git_specification
                    )

        logger.info(f"[create_project] Found {len(upload_files)} file(s) to validate")
        # Validate files if base_specification is "requirement"
        if (
            project_request.base_specification == BaseSpecification.REQUIREMENT
            and upload_files
        ):
            await _validate_upload_files(upload_files)

        # Add creator email to share list if not present
        creator_email = current_user.get("email")
        if creator_email:
            if project_request.share is None:
                project_request.share = []
            if creator_email not in project_request.share:
                project_request.share.append(creator_email)

        # Create project in database
        user_id = current_user.get("user_id")
        git_type = None

        # Validate Git repository only for SOURCE_CODE base_specification
        if project_request.git_spec and project_request.git_spec.repository_url:
            if await project_service.check_repository_exists(
                project_request.git_spec.repository_url
            ):
                raise_http_error(
                    status.HTTP_409_CONFLICT, error_key="REPOSITORY_ALREADY_EXISTS"
                )

            git_type = await validate_git_repository(
                repository_url=project_request.git_spec.repository_url,
                branch_name=project_request.git_spec.branch_name,
                user_name=project_request.git_spec.user_name,
                token_password=project_request.git_spec.token_password,
            )
            logger.info(
                f"[create_project] Git repository validated - git_type={git_type}"
            )

            if (
                project_request.ai_spec
                and project_request.ai_spec.user_name
                and project_request.ai_spec.token_password
            ):
                await validate_git_repository(
                    repository_url=project_request.git_spec.repository_url,
                    branch_name=project_request.git_spec.branch_name,
                    user_name=project_request.ai_spec.user_name,
                    token_password=project_request.ai_spec.token_password,
                    is_ai_programming=True,
                )

            # Set repo_provider from git_type if not provided in request
            if git_type:
                project_request.git_spec.repo_provider = git_type
                logger.info(
                    f"[create_project] Set repo_provider from git_type - repo_provider={git_type}"
                )

            # If branch_name is empty, get default branch and update
            if (
                not project_request.git_spec.branch_name
                or not project_request.git_spec.branch_name.strip()
            ):

                default_branch = await get_default_branch(
                    repository_url=project_request.git_spec.repository_url,
                    user_name=project_request.git_spec.user_name,
                    token_password=project_request.git_spec.token_password,
                    git_type=git_type,
                )
                project_request.git_spec.branch_name = default_branch

        try:
            project = await project_service.create_project(project_request, user_id)
            logger.info(
                f"[create_project] Project created successfully - project_id={project.id}"
            )
        except Exception as e:
            error_message = f"[create_project] Error creating project: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        share_emails = project_request.share or []

        try:
            # Add project_id to creator's and shared users' personal_projects
            await _add_project_to_users(
                creator_id=user_id,
                project_id=project.id,
                share_emails=share_emails,
            )

            # Process files if base_specification is "requirement"
            if (
                project_request.base_specification == BaseSpecification.REQUIREMENT
                and upload_files
            ):
                await _process_requirement_files(upload_files, project.id, user_id)

            # Clone and process source code if base_specification is "source_code"
            if project_request.git_spec.repository_url:
                await clone_code_from_git(
                    repository_url=project_request.git_spec.repository_url,
                    branch_name=project_request.git_spec.branch_name,
                    user_name=project_request.git_spec.user_name,
                    token_password=project_request.git_spec.token_password,
                    git_type=git_type,
                    project_id=project.id,
                    source_code_path=project_request.git_specification,
                    programming_language=project_request.programming_language,
                )
        except HTTPException as http_error:
            logger.error(
                f"[create_project] Post-create HTTP error - project_id={project.id}: {http_error}"
            )
            await _rollback_project_creation(project.id, user_id, share_emails)
            raise
        except Exception as e:
            error_message = f"[create_project] Post-create processing failed - project_id={project.id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            await _rollback_project_creation(project.id, user_id, share_emails)
            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Publish MQTT event for project creation
        try:
            project_service._publish_project_event(
                project_id=project.id,
                event_type=ProjectEventType.NEW,
                project=project,
            )
        except Exception as e:
            logger.warning(
                f"[create_project] Failed to publish MQTT event - project_id={project.id}: {e}"
            )
            # Don't fail the request if MQTT publish fails

        logger.info("[create_project] Request processed successfully")
        return ProjectCreateResponseSchema(
            statusCode=201,
            message="Project created successfully",
            data={
                "id": str(project.id),
                "project_name": project.setting_item.project_name,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[create_project] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put("/{project_id}", response_model=BaseResponse)
@limiter.limit("20/minute")
async def update_project(
    request: Request,
    project_id: str,
    project_data: ProjectUpdate,
    current_user=Depends(get_current_user),
):
    """Update project"""
    logger.info(
        f"[update_project] Start - project_id={project_id}, user_id={current_user.get('user_id')}"
    )

    if not project_id:
        logger.warning("[update_project] Missing project_id input")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        # Check if user has access to this project
        has_access = await project_service.check_user_project_access(
            user_id, project_id
        )
        if not has_access:
            raise HTTPException(status_code=403, detail=get_error_response(403).dict())

        # Get existing project to compare share list
        existing_project = await project_service.get_project_by_id(project_id)
        if not existing_project:
            logger.warning(
                f"[update_project] Project not found - project_id={project_id}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND)

        # Extract setting_item to avoid deep property chains
        existing_setting_item = (
            existing_project.setting_item if existing_project.setting_item else None
        )

        # Check if project name already exists (if being updated and name changed)
        current_name = (
            existing_setting_item.project_name if existing_setting_item else None
        )
        if project_data.name and project_data.name != current_name:
            if await project_service.check_project_name_exists(project_data.name):
                raise_http_error(
                    status.HTTP_409_CONFLICT, error_key="PROJECT_NAME_ALREADY_EXISTS"
                )
        if not project_data.share:
            raise_http_error(
                status.HTTP_400_BAD_REQUEST, error_key="LAST_USER_CANNOT_LEAVE_PROJECT"
            )
        # Validate and process git information if provided and existing values are empty
        git_type = None
        existing_git = (
            existing_setting_item.git
            if existing_setting_item and existing_setting_item.git
            else None
        )
        existing_repository = (existing_git.repository if existing_git else None) or ""

        # Only validate and update git if repository_url is provided AND existing value is empty
        if (
            project_data.git_spec
            and project_data.git_spec.repository_url
            and not existing_repository.strip()
        ):
            logger.info(
                f"[update_project] Validating git repository - repository_url={project_data.git_spec.repository_url}"
            )

            # Validate required git fields
            if (
                not project_data.git_spec.user_name
                or not project_data.git_spec.token_password
            ):
                logger.warning(
                    "[update_project] Missing git credentials (user_name or token_password)"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="GIT_INVALID_URL",
                )

            if await project_service.check_repository_exists(
                project_data.git_spec.repository_url, project_id
            ):
                raise_http_error(
                    status.HTTP_409_CONFLICT, error_key="REPOSITORY_ALREADY_EXISTS"
                )

            # Validate Git repository
            git_type = await validate_git_repository(
                repository_url=project_data.git_spec.repository_url,
                branch_name=project_data.git_spec.branch_name,
                user_name=project_data.git_spec.user_name,
                token_password=project_data.git_spec.token_password,
            )
            logger.info(
                f"[update_project] Git repository validated - git_type={git_type}"
            )

            # Set repo_provider from git_type if not provided in request
            if git_type:
                project_data.git_spec.repo_provider = git_type
                logger.info(
                    f"[update_project] Set repo_provider from git_type - repo_provider={git_type}"
                )

            # If branch_name is empty, get default branch and update
            if (
                not project_data.git_spec.branch_name
                or not project_data.git_spec.branch_name.strip()
            ):
                default_branch = await get_default_branch(
                    repository_url=project_data.git_spec.repository_url,
                    user_name=project_data.git_spec.user_name,
                    token_password=project_data.git_spec.token_password,
                    git_type=git_type,
                )
                project_data.git_spec.branch_name = default_branch
                logger.info(
                    f"[update_project] Updated branch_name to default - branch={default_branch}"
                )
        elif (
            project_data.git_spec
            and project_data.git_spec.repository_url
            and existing_repository.strip()
        ):
            logger.info(
                f"[update_project] Skipping git update - existing repository already has value: {existing_repository}"
            )

        repository_url = (
            project_data.git_spec.repository_url
            if project_data.git_spec and project_data.git_spec.repository_url
            else (existing_git.repository if existing_git else None)
        )

        branch_name = (
            project_data.git_spec.branch_name
            if project_data.git_spec and project_data.git_spec.branch_name
            else (existing_git.branch if existing_git else None)
        )

        if (
            project_data.ai_spec
            and project_data.ai_spec.user_name
            and project_data.ai_spec.token_password
            and repository_url
        ):
            # user_name, token_password = project_service._extract_git_credentials(
            #     existing_project
            # )

            # credentials_changed = (
            #     user_name != project_data.ai_spec.user_name
            #     or token_password != project_data.ai_spec.token_password
            # )
            await validate_git_repository(
                repository_url=repository_url,
                branch_name=branch_name,
                user_name=project_data.ai_spec.user_name,
                token_password=project_data.ai_spec.token_password,
                is_ai_programming=True,
            )

        # # Check for directory changes
        # existing_directory = (
        #     existing_setting_item.directory
        #     if existing_setting_item and existing_setting_item.directory
        #     else None
        # )
        # directory_changes = {}

        # if project_data.directory_spec and existing_directory:
        #     existing_dir_dict = (
        #         existing_directory.dict()
        #         if hasattr(existing_directory, "dict")
        #         else existing_directory
        #     )
        #     new_dir_dict = project_data.directory_spec.dict(exclude_none=True)

        #     # Directory keys mapping
        #     dir_keys = [
        #         "rd",
        #         "bd",
        #         "pd",
        #         "cd",
        #         "md",
        #         "id",
        #         "fs",
        #         "ac",
        #         "src",
        #         "utd",
        #         "utc",
        #     ]

        #     for key in dir_keys:
        #         old_value = (
        #             (existing_dir_dict.get(key) or "").strip()
        #             if existing_dir_dict
        #             else ""
        #         )
        #         new_value = (
        #             (new_dir_dict.get(key) or "").strip() if new_dir_dict else ""
        #         )

        #         if old_value and new_value and old_value != new_value:
        #             directory_changes[key] = {"old": old_value, "new": new_value}
        #             logger.info(
        #                 f"[update_project] Directory change detected - key={key}, old={old_value}, new={new_value}"
        #             )

        old_share_list = set(existing_project.share or [])
        new_share_list = (
            set(project_data.share or [])
            if project_data.share is not None
            else old_share_list
        )

        # Find users to remove (in old but not in new)
        users_to_remove = old_share_list - new_share_list
        # Find users to add (in new but not in old)
        users_to_add = new_share_list - old_share_list

        # Update project
        project = await project_service.update_project(
            project_id, project_data, user_id
        )

        # Update personal_projects based on share list changes
        await _update_share_list_users(users_to_remove, users_to_add, project_id)

        # # Handle directory changes: pull with force_override and rename folders in git
        # if directory_changes and existing_git and existing_git.repository:
        #     logger.info(
        #         f"[update_project] Processing directory changes - project_id={project_id}, changes_count={len(directory_changes)}"
        #     )

        #     # Get git credentials from git_spec in request
        #     if not project_data.git_spec or not project_data.git_spec.user_name:
        #         logger.warning(
        #             f"[update_project] Missing git credentials in git_spec for directory rename - project_id={project_id}"
        #         )
        #     else:
        #         user_name = project_data.git_spec.user_name
        #         token_password = (
        #             project_data.git_spec.token_password
        #             if project_data.git_spec.token_password
        #             else None
        #         )
        #         repository_url = project_data.git_spec.repository_url
        #         branch_name = project_data.git_spec.branch_name

        #         # Pull data from git with force_override=true
        #         try:
        #             logger.info(
        #                 f"[update_project] Pulling data from git with force_override - project_id={project_id}"
        #             )

        #             # Get directory paths for pull
        #             updated_project = await project_service.get_project_by_id(
        #                 project_id
        #             )
        #             updated_setting_item = (
        #                 updated_project.setting_item
        #                 if updated_project.setting_item
        #                 else None
        #             )
        #             updated_directory = (
        #                 updated_setting_item.directory
        #                 if updated_setting_item and updated_setting_item.directory
        #                 else None
        #             )

        #             directory_paths = {}
        #             if updated_directory:
        #                 dir_dict = (
        #                     updated_directory.dict()
        #                     if hasattr(updated_directory, "dict")
        #                     else updated_directory
        #                 )
        #                 directory_paths = {
        #                     "rd": dir_dict.get("rd") or "",
        #                     "bd": dir_dict.get("bd") or "",
        #                     "pd": dir_dict.get("pd") or "",
        #                     "cd": dir_dict.get("cd") or "",
        #                     "md": dir_dict.get("md") or "",
        #                     "id": dir_dict.get("id") or "",
        #                     "fs": dir_dict.get("fs") or "",
        #                     "ac": dir_dict.get("ac") or "",
        #                     "src": dir_dict.get("src") or "",
        #                     "utd": dir_dict.get("utd") or "",
        #                     "utc": dir_dict.get("utc") or "",
        #                 }

        #             # Get programming language
        #             programming_language = (
        #                 updated_setting_item.language
        #                 if updated_setting_item and updated_setting_item.language
        #                 else None
        #             )

        #             # Get commit IDs
        #             db_commit_ids = (
        #                 existing_git.commit_id
        #                 if isinstance(existing_git.commit_id, list)
        #                 else []
        #             )

        #             # Pull with force_override
        #             pull_result = await pull_data_from_git(
        #                 project_id=project_id,
        #                 repository_url=repository_url,
        #                 branch_name=branch_name,
        #                 user_name=user_name,
        #                 token_password=token_password,
        #                 db_commit_ids=db_commit_ids,
        #                 directory_paths=directory_paths,
        #                 programming_language=programming_language,
        #                 force_override=True,
        #             )

        #             # Rename folders in git and update DB (all at once)
        #             # Get PD directory for cd, md, id, fs, ac (they are subdirectories of PD)
        #             pd_old = (
        #                 existing_dir_dict.get("pd") or "" if existing_dir_dict else ""
        #             ).strip()
        #             pd_new = (
        #                 new_dir_dict.get("pd") or "" if new_dir_dict else ""
        #             ).strip()

        #             # If PD itself changed, use new PD value for subdirectories
        #             if "pd" in directory_changes:
        #                 pd_for_subdirs = pd_new
        #             else:
        #                 pd_for_subdirs = pd_old or pd_new

        #             # Prepare directory_changes with full paths for rename_multiple_folders_and_push
        #             from app.services.git_services.git_change_folder_name import (
        #                 rename_multiple_folders_and_push,
        #             )

        #             prepared_directory_changes = {}
        #             for dir_key, change_info in directory_changes.items():
        #                 try:
        #                     # For cd, md, id, fs, ac, combine with PD directory
        #                     if dir_key in ["cd", "md", "id", "fs", "ac"]:
        #                         if not pd_for_subdirs:
        #                             logger.warning(
        #                                 f"[update_project] PD directory not found for {dir_key} - skipping"
        #                             )
        #                             continue

        #                         old_folder_name = (
        #                             f"{pd_for_subdirs}/{change_info['old']}"
        #                         )
        #                         new_folder_name = (
        #                             f"{pd_for_subdirs}/{change_info['new']}"
        #                         )
        #                         logger.info(
        #                             f"[update_project] Processing subdirectory - key={dir_key}, old_path={old_folder_name}, new_path={new_folder_name}"
        #                         )
        #                     else:
        #                         old_folder_name = change_info["old"]
        #                         new_folder_name = change_info["new"]

        #                     prepared_directory_changes[dir_key] = {
        #                         "old": old_folder_name,
        #                         "new": new_folder_name,
        #                     }
        #                 except Exception as e:
        #                     error_message = f"[update_project] Error preparing folder rename for {dir_key}: {e}"
        #                     logger.error(error_message)
        #                     save_exception_log_sync(e, error_message, __name__)
        #                     # Continue with other directory changes
        #                     continue

        #             # Rename all folders at once, push once, update all DB documents
        #             if prepared_directory_changes:
        #                 try:
        #                     commit_id = await rename_multiple_folders_and_push(
        #                         project_id=project_id,
        #                         directory_changes=prepared_directory_changes,
        #                         repository_url=repository_url,
        #                         branch_name=branch_name,
        #                         user_name=user_name,
        #                         token_password=token_password,
        #                     )
        #                     logger.info(
        #                         f"[update_project] All folders renamed - project_id={project_id}, commit_id={commit_id}, changes_count={len(prepared_directory_changes)}"
        #                     )
        #                 except Exception as e:
        #                     error_message = (
        #                         f"[update_project] Error renaming multiple folders: {e}"
        #                     )
        #                     logger.error(error_message)
        #                     save_exception_log_sync(e, error_message, __name__)
        #                     # Don't fail the update if folder rename fails

        #         except Exception as e:
        #             error_message = (
        #                 f"[update_project] Error processing directory changes: {e}"
        #             )
        #             logger.error(error_message)
        #             save_exception_log_sync(e, error_message, __name__)
        #             # Don't fail the update if directory rename fails

        # Extract setting_item to avoid deep property chains
        project_setting_item = project.setting_item if project.setting_item else None
        project_name = project_setting_item.project_name if project_setting_item else ""
        description = project_setting_item.description if project_setting_item else ""

        # Publish MQTT event for project update
        try:
            project_service._publish_project_event(
                project_id=project_id,
                event_type=ProjectEventType.EDIT,
                project=project,
            )
        except Exception as e:
            logger.warning(
                f"[update_project] Failed to publish MQTT event - project_id={project_id}: {e}"
            )
            # Don't fail the request if MQTT publish fails

        logger.info(f"[update_project] Success - project_id={project_id}")
        return BaseResponse(
            statusCode=200,
            message="Success",
            data={
                "id": project.id,
                "project_name": project_name,
                "description": description,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
            },
        )
    except ValueError as ve:
        error_msg = str(ve)
        if error_msg == "PROJECT_NOT_FOUND":
            raise_http_error(status.HTTP_404_NOT_FOUND)
        elif error_msg == "PROJECT_NAME_ALREADY_EXISTS":
            raise_http_error(
                status.HTTP_409_CONFLICT, error_key="PROJECT_NAME_ALREADY_EXISTS"
            )
        raise_http_error(status.HTTP_422_UNPROCESSABLE_ENTITY)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_project] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/git/list-branch", response_model=BaseResponse)
@limiter.limit("100/minute")
async def get_list_branch(
    request: Request,
    project_id: str,
    user_name: str = Body(...),
    token_password: Optional[str] = Body(None),
    current_user=Depends(get_current_user),
):
    """Get list of branches from Git repository"""
    logger.info(f"[get_list_branch] Start - project_id={project_id}")

    if not project_id or not user_name:
        logger.warning("[get_list_branch] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user.get("user_id")

        # Check if user has access to project
        has_access = await project_service.check_user_project_access(
            user_id, project_id
        )
        if not has_access:
            logger.warning(
                f"[get_list_branch] Access denied - user_id={user_id}, project_id={project_id}"
            )
            raise_http_error(status.HTTP_403_FORBIDDEN, error_key="FORBIDDEN")

        # Get project to retrieve repository URL
        project = await project_service.get_detail_project(project_id)
        if not project:
            logger.warning(
                f"[get_list_branch] Project not found - project_id={project_id}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, error_key="PROJECT_NOT_FOUND")

        # Get repository URL from project
        repository_url = None
        if project.get("git") and project["git"].get("repository"):
            repository_url = project["git"]["repository"]
        else:
            logger.warning(
                f"[get_list_branch] Project does not have repository URL - project_id={project_id}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        # Determine Git type
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[get_list_branch] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        # Get list of branches
        branches = await get_list_branches(
            repository_url=repository_url,
            user_name=user_name,
            token_password=token_password,
            git_type=git_type,
        )

        logger.info(
            f"[get_list_branch] Success - project_id={project_id}, branch_count={len(branches)}"
        )
        return BaseResponse(
            statusCode=200,
            message="Success",
            data={"branches": branches},
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_list_branch] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/git/check-changes", response_model=BaseResponse)
@limiter.limit("100/minute")
async def check_repo_changes_api(
    request: Request,
    project_id: str,
    user_name: str = Body(...),
    token_password: Optional[str] = Body(None),
    current_user=Depends(get_current_user),
):
    """Check for new commits and file changes in Git repository"""
    logger.info(f"[check_repo_changes_api] Start - project_id={project_id}")

    if not project_id or not user_name:
        logger.warning("[check_repo_changes_api] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user.get("user_id")

        # Check if user has access to project
        has_access = await project_service.check_user_project_access(
            user_id, project_id
        )
        if not has_access:
            logger.warning(
                f"[check_repo_changes_api] Access denied - user_id={user_id}, project_id={project_id}"
            )
            raise_http_error(status.HTTP_403_FORBIDDEN, error_key="FORBIDDEN")

        # Get project to retrieve repository information
        project = await project_service.get_project_by_id(project_id)
        if not project:
            logger.warning(
                f"[check_repo_changes_api] Project not found - project_id={project_id}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, error_key="PROJECT_NOT_FOUND")

        # Extract git information
        setting_item = project.setting_item if project.setting_item else None
        git_info = setting_item.git if setting_item and setting_item.git else None
        directory_info = (
            setting_item.directory if setting_item and setting_item.directory else None
        )

        if not git_info or not git_info.repository:
            logger.warning(
                f"[check_repo_changes_api] Project does not have repository URL - project_id={project_id}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        repository_url = git_info.repository
        branch_name = git_info.branch or "main"
        db_commit_ids = (
            git_info.commit_id if isinstance(git_info.commit_id, list) else []
        )

        # Build directory paths dictionary - only rd, bd, pd, src, utd, utc
        directory_paths = {}
        if directory_info:
            directory_paths = {
                "rd": directory_info.rd or "",
                "bd": directory_info.bd or "",
                "pd": directory_info.pd or "",
                "src": directory_info.src or "",
                "utd": directory_info.utd or "",
                "utc": directory_info.utc or "",
            }

        # Check for changes
        # Note: _get_directory_key_for_file in git_check_changes.py handles DOCIFYCODE_DIRECTORY_NAME automatically
        # It detects if file path starts with DOCIFYCODE_DIRECTORY_NAME and skips it when comparing
        # For bd: only check FILE_NAME_BASIC_DESIGN in bd directory
        # For pd: only check FILE_NAME_DETAIL_DESIGN in pd directory
        changes_result = await check_repo_changes(
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
            db_commit_ids=db_commit_ids,
            directory_paths=directory_paths,
        )

        # Update sync_status for changed files
        updated_count = await _update_sync_status_for_changes(
            project_id=project_id,
            changed_files=changes_result.get("changed_files", []),
        )

        # Update project sync_status to "pull" if there are changes
        if changes_result.get("new_commits"):
            await project_service.update_project_sync_status(
                project_id=project_id,
                sync_status=SyncStatus.PULL,
            )

        logger.info(
            f"[check_repo_changes_api] Success - project_id={project_id}, updated_files={updated_count}"
        )
        return BaseResponse(
            statusCode=200,
            message="Success",
            data={
                "new_commits_count": len(changes_result.get("new_commits", [])),
                "changed_files_count": len(changes_result.get("changed_files", [])),
                "updated_files_count": updated_count,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[check_repo_changes_api] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/git/pull", response_model=BaseResponse)
@limiter.limit("100/minute")
async def pull_data_api(
    request: Request,
    project_id: str,
    user_name: str = Body(...),
    token_password: Optional[str] = Body(None),
    force_override: bool = Body(False),
    current_user=Depends(get_current_user),
):
    """Pull latest changes from Git repository and update database"""
    logger.info(
        f"[pull_data_api] Start - project_id={project_id}, force_override={force_override}"
    )

    if not project_id or not user_name:
        logger.warning("[pull_data_api] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user.get("user_id")

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[pull_data_api] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Check if user has access to project
        has_access = await project_service.check_user_project_access(
            user_id, project_id
        )
        if not has_access:
            logger.warning(
                f"[pull_data_api] Access denied - user_id={user_id}, project_id={project_id}"
            )
            raise_http_error(status.HTTP_403_FORBIDDEN, error_key="FORBIDDEN")

        # Get project to retrieve repository information
        project = await project_service.get_project_by_id(project_id)
        if not project:
            logger.warning(
                f"[pull_data_api] Project not found - project_id={project_id}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, error_key="PROJECT_NOT_FOUND")

        # Extract git information
        setting_item = project.setting_item if project.setting_item else None
        git_info = setting_item.git if setting_item and setting_item.git else None
        directory_info = (
            setting_item.directory if setting_item and setting_item.directory else None
        )

        if not git_info or not git_info.repository:
            logger.warning(
                f"[pull_data_api] Project does not have repository URL - project_id={project_id}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        repository_url = git_info.repository
        branch_name = git_info.branch or "main"
        db_commit_ids = (
            git_info.commit_id if isinstance(git_info.commit_id, list) else []
        )

        # Get programming language
        programming_language = (
            setting_item.language if setting_item and setting_item.language else None
        )

        # Build directory paths dictionary
        # Build directory paths dictionary - only rd, bd, pd, src, utd, utc
        directory_paths = {}
        if directory_info:
            directory_paths = {
                "rd": directory_info.rd or "",
                "bd": directory_info.bd or "",
                "pd": directory_info.pd or "",
                "src": directory_info.src or "",
                "utd": directory_info.utd or "",
                "utc": directory_info.utc or "",
            }

        # Pull data from git
        pull_result = await pull_data_from_git(
            project_id=project_id,
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
            db_commit_ids=db_commit_ids,
            directory_paths=directory_paths,
            programming_language=programming_language,
            force_override=force_override,
        )

        # Check for conflicts
        conflict_files = pull_result.get("conflict_files", [])
        if conflict_files and not force_override:
            # Return 409 Conflict response
            from fastapi.responses import JSONResponse

            logger.warning(
                f"[pull_data_api] Conflict detected - project_id={project_id}, conflict_files_count={len(conflict_files)}"
            )
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={
                    "statusCode": 409,
                    "message": "Conflict detected. Some files could not be merged automatically.",
                    "conflict_files": conflict_files,
                },
            )

        # Update project commit_ids and sync_status
        repo_commit_ids = pull_result.get("repo_commit_ids", [])
        if repo_commit_ids:
            await project_service.update_project_commit_ids(
                project_id=project_id,
                new_commit_ids=repo_commit_ids,
            )
            await project_service.update_project_sync_status(
                project_id=project_id,
                sync_status=SyncStatus.SYNCED,
            )

        # Create activity log
        from app.services.activity_log_service import activity_log_service

        await activity_log_service.create_activity_log(
            project_id=project_id,
            user_id=user_id,
            activity_type="GIT_PULL",
        )

        logger.info(
            f"[pull_data_api] Success - project_id={project_id}, updated_files={pull_result.get('updated_files', 0)}"
        )
        return BaseResponse(
            statusCode=200,
            message="Success",
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[pull_data_api] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/git/change-branch", response_model=BaseResponse)
@limiter.limit("100/minute")
async def change_branch_api(
    request: Request,
    project_id: str,
    branch_name: str = Body(...),
    force: bool = Body(False),
    user_name: str = Body(...),
    token_password: Optional[str] = Body(None),
    current_user=Depends(get_current_user),
):
    """Change Git branch for a project"""
    logger.info(
        f"[change_branch_api] Start - project_id={project_id}, branch_name={branch_name}, force={force}"
    )

    if not project_id or not branch_name or not user_name:
        logger.warning("[change_branch_api] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user.get("user_id")

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[change_branch_api] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Check if user has access to project
        has_access = await project_service.check_user_project_access(
            user_id, project_id
        )
        if not has_access:
            logger.warning(
                f"[change_branch_api] Access denied - user_id={user_id}, project_id={project_id}"
            )
            raise_http_error(status.HTTP_403_FORBIDDEN, error_key="FORBIDDEN")

        # Get project to retrieve repository information
        project = await project_service.get_project_by_id(project_id)
        if not project:
            logger.warning(
                f"[change_branch_api] Project not found - project_id={project_id}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, error_key="PROJECT_NOT_FOUND")

        # Extract git information
        setting_item = project.setting_item if project.setting_item else None
        git_info = setting_item.git if setting_item and setting_item.git else None

        if not git_info or not git_info.repository:
            logger.warning(
                f"[change_branch_api] Project does not have repository URL - project_id={project_id}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        repository_url = git_info.repository

        # Get programming language and source code path
        programming_language = (
            setting_item.language if setting_item and setting_item.language else None
        )
        directory_info = (
            setting_item.directory if setting_item and setting_item.directory else None
        )
        source_code_path = (
            directory_info.src if directory_info and directory_info.src else None
        )

        # Change branch
        change_result = await change_git_branch(
            project_id=project_id,
            repository_url=repository_url,
            new_branch=branch_name,
            user_name=user_name,
            token_password=token_password,
            force=force,
            source_code_path=source_code_path,
            programming_language=programming_language,
        )

        # Check if has_local_changes is True (user needs to confirm)
        has_local_changes = change_result.get("has_local_changes", False)
        if has_local_changes:
            logger.info(
                f"[change_branch_api] Local changes detected - project_id={project_id}"
            )
            return BaseResponse(
                statusCode=200,
                message=change_result.get("message", "Local changes detected"),
                data={"has_local_changes": True},
            )

        # Get commit IDs from result
        commit_ids = change_result.get("commit_ids", [])

        # Update project branch, sync_status, and commit_ids in database
        await project_service.update_project_branch_with_commits(
            project_id=project_id,
            branch_name=branch_name,
            commit_ids=commit_ids,
        )

        # Create activity log
        from app.services.activity_log_service import activity_log_service

        await activity_log_service.create_activity_log(
            project_id=project_id,
            user_id=user_id,
            activity_type="GIT_CHANGE_BRANCH",
        )

        logger.info(
            f"[change_branch_api] Success - project_id={project_id}, new_branch={branch_name}"
        )
        return BaseResponse(
            statusCode=200,
            message=change_result.get("message", "Branch changed successfully"),
            data={"has_local_changes": False},
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[change_branch_api] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


async def _update_sync_status_for_changes(
    project_id: str, changed_files: List[Dict[str, Any]]
) -> int:
    """
    Update sync_status for changed files based on their current status

    Args:
        project_id: Project ID
        changed_files: List of changed file information

    Returns:
        Number of files updated
    """
    logger.info(
        f"[_update_sync_status_for_changes] Start - project_id={project_id}, changed_files_count={len(changed_files)}"
    )

    if not changed_files:
        logger.info("[_update_sync_status_for_changes] No files to update")
        return 0

    updated_count = 0

    try:
        # Group files by directory key
        files_by_directory = {}
        for file_info in changed_files:
            directory_key = file_info.get("directory_key")
            if not directory_key:
                continue

            if directory_key not in files_by_directory:
                files_by_directory[directory_key] = []
            files_by_directory[directory_key].append(file_info)

        # Update files for each directory
        for directory_key, files in files_by_directory.items():
            try:
                count = await _update_files_for_directory(
                    project_id=project_id,
                    directory_key=directory_key,
                    files=files,
                )
                updated_count += count
            except Exception as e:
                error_message = f"[_update_sync_status_for_changes] Error updating directory {directory_key}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)
                continue

        logger.info(
            f"[_update_sync_status_for_changes] Success - updated_count={updated_count}"
        )
        return updated_count

    except Exception as e:
        error_message = f"[_update_sync_status_for_changes] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _update_files_for_directory(
    project_id: str, directory_key: str, files: List[Dict[str, Any]]
) -> int:
    """
    Update sync_status for files in a specific directory
    """
    logger.info(
        f"[_update_files_for_directory] Start - project_id={project_id}, directory_key={directory_key}, files_count={len(files)}"
    )

    updated_count = 0

    try:
        # Map directory keys to repositories
        # Only rd, bd, pd, src, utd, utc are supported
        requirement_repo = app_db.requirement_document_repository
        repository_map = {
            "rd": (requirement_repo, "file_name"),
            "bd": (app_db.basic_design_repository, "project_id"),
            "pd": (app_db.detail_design_repository, "project_id"),
            "src": (app_db.source_code_repository, "file_path"),
            "utd": (app_db.unit_test_repository, "file_path"),
            "utc": (app_db.unit_test_repository, "file_path"),
        }

        if directory_key not in repository_map:
            logger.warning(
                f"[_update_files_for_directory] Unknown directory_key: {directory_key}"
            )
            return 0

        repository, query_field = repository_map[directory_key]

        # For each file, find it in DB and update sync_status
        for file_info in files:
            file_path = file_info.get("file_path")
            commit_id = file_info.get("commit_id")
            change_type = file_info.get("change_type", "modified")

            if not file_path:
                continue

            try:
                # Find file in database based on directory_key
                file_document = await _find_file_by_path(
                    repository=repository,
                    project_id=project_id,
                    file_path=file_path,
                    directory_key=directory_key,
                )

                if not file_document:
                    logger.warning(
                        f"[_update_files_for_directory] File not found in DB - file_path={file_path}"
                    )
                    continue

                # Get current sync_status
                current_sync_status = file_document.get("sync_status")
                document_id = file_document.get("id") or file_document.get("_id")

                if not document_id:
                    logger.warning(
                        f"[_update_files_for_directory] File document has no ID - file_path={file_path}"
                    )
                    continue

                # Determine new sync_status based on current status and change type
                if change_type == "deleted":
                    new_sync_status = SyncStatus.DELETE_PULL
                elif current_sync_status == SyncStatus.SYNCED:
                    new_sync_status = SyncStatus.PULL
                elif current_sync_status == SyncStatus.PUSH:
                    new_sync_status = SyncStatus.PULL_PUSH
                elif current_sync_status == SyncStatus.PULL_PUSH:
                    # Keep PULL_PUSH if already PULL_PUSH (has both local and git changes)
                    new_sync_status = SyncStatus.PULL_PUSH
                else:
                    # For other statuses, set to pull
                    new_sync_status = SyncStatus.PULL

                # Update sync_status only (not commit_id)
                is_updated = await _update_file_sync_status(
                    repository=repository,
                    document_id=document_id,
                    project_id=project_id,
                    sync_status=new_sync_status,
                )

                if is_updated:
                    logger.info(
                        f"[_update_files_for_directory] Updated file_path={file_path}, commit_id={commit_id}, sync_status={new_sync_status}"
                    )
                    updated_count += 1
                else:
                    logger.warning(
                        f"[_update_files_for_directory] Failed to update file_path={file_path}"
                    )

            except Exception as e:
                error_message = f"[_update_files_for_directory] Error updating file {file_path}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)
                continue

        logger.info(
            f"[_update_files_for_directory] Success - updated_count={updated_count}"
        )
        return updated_count

    except Exception as e:
        error_message = f"[_update_files_for_directory] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _find_file_by_path(
    repository: Any,
    project_id: str,
    file_path: str,
    directory_key: str,
) -> Optional[Dict[str, Any]]:
    """
    Find file in database by file_path based on directory_key
    """
    logger.info(
        f"[_find_file_by_path] Start - project_id={project_id}, file_path={file_path}, directory_key={directory_key}"
    )

    if not repository or not project_id or not file_path:
        logger.warning("[_find_file_by_path] Missing required parameters")
        return None

    try:
        # Normalize file path
        normalized_path = file_path.replace("\\", "/").strip("/")
        path_parts = normalized_path.split("/")

        # Build query based on directory_key
        query_params = {
            "project_id": project_id,
            "$or": [
                {"deleted_at": None},
                {"deleted_at": {"$exists": False}},
            ],
        }

        # For rd: query by file_name
        if directory_key == "rd":
            # Parse file_path: "Docifycode/RD/test.md" -> file_name="test.md"
            if len(path_parts) >= 1:
                file_name = path_parts[-1]
                query_params["file_name"] = file_name
            else:
                logger.warning(
                    f"[_find_file_by_path] Invalid file_path for rd: {file_path}"
                )
                return None

        # For bd: query by project_id only (only 1 document per project)
        # File must be FILE_NAME_BASIC_DESIGN in .design_document/ directory
        elif directory_key == "bd":
            # Check if file_name is FILE_NAME_BASIC_DESIGN
            file_name = path_parts[-1] if path_parts else ""
            if file_name != FILE_NAME_BASIC_DESIGN:
                logger.warning(
                    f"[_find_file_by_path] File {file_name} is not {FILE_NAME_BASIC_DESIGN} for bd"
                )
                return None
            # Query by project_id only (only 1 document per project, not deleted)

        # For pd: query by project_id only (only 1 document per project)
        # File must be FILE_NAME_DETAIL_DESIGN in .design_document/ directory
        elif directory_key == "pd":
            # Check if file_name is FILE_NAME_DETAIL_DESIGN
            file_name = path_parts[-1] if path_parts else ""
            if file_name != FILE_NAME_DETAIL_DESIGN:
                logger.warning(
                    f"[_find_file_by_path] File {file_name} is not {FILE_NAME_DETAIL_DESIGN} for pd"
                )
                return None
            # Query by project_id only (only 1 document per project, not deleted)

        # For src: query by file_path
        # If src is "/" (root), file_path is the file name at root
        elif directory_key == "src":
            query_params["file_path"] = normalized_path

        # For utd, utc: query by collection_name and file_path
        elif directory_key in ["utd", "utc"]:
            collection_name_map = {
                "utd": UnitTestDesign.Config.collection_name,
                "utc": UnitTestCode.Config.collection_name,
            }
            collection_name = collection_name_map.get(directory_key)
            if collection_name:
                query_params["collection_name"] = collection_name
                query_params["file_path"] = normalized_path
            else:
                logger.warning(
                    f"[_find_file_by_path] Unknown directory_key for unit test: {directory_key}"
                )
                return None

        else:
            logger.warning(
                f"[_find_file_by_path] Unknown directory_key: {directory_key}"
            )
            return None

        file_document = await repository.find_one(query_params)
        if file_document:
            logger.info(
                f"[_find_file_by_path] Found file - file_path={file_path}, directory_key={directory_key}, document_id={file_document.get('id')}"
            )
        else:
            logger.warning(
                f"[_find_file_by_path] File not found - file_path={file_path}, directory_key={directory_key}"
            )

        return file_document

    except Exception as e:
        error_message = f"[_find_file_by_path] Error - file_path={file_path}, directory_key={directory_key}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        return None


async def _update_file_sync_status(
    repository: Any,
    document_id: str,
    project_id: str,
    sync_status: str,
) -> bool:
    """
    Update file sync_status only (not commit_id)
    """
    logger.info(
        f"[_update_file_sync_status] Start - document_id={document_id}, sync_status={sync_status}"
    )

    if not repository or not document_id or not project_id:
        logger.warning("[_update_file_sync_status] Missing required parameters")
        return False

    try:
        # Use update_document method - only update sync_status
        update_data = {
            "sync_status": sync_status,
        }

        is_updated = await repository.update_document(
            document_id=document_id,
            project_id=project_id,
            update_data=update_data,
        )

        if is_updated:
            logger.info(
                f"[_update_file_sync_status] Success - document_id={document_id}, sync_status={sync_status}"
            )
        else:
            logger.warning(
                f"[_update_file_sync_status] Failed to update - document_id={document_id}"
            )

        return is_updated

    except Exception as e:
        error_message = (
            f"[_update_file_sync_status] Error - document_id={document_id}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        return False


@router.delete("", response_model=ProjectsBulkDeleteResponse)
@limiter.limit("10/minute")
async def delete_projects(
    request: Request,
    body: Any = Body(..., media_type="application/json"),
    current_user=Depends(get_current_user),
):
    """Bulk delete projects that belong to the current user (by personal_projects).
    Accepts either a raw JSON array of IDs ["id1","id2"] or an object {"project_id": [..]} / {"project_ids": [..]}.
    """
    user_id = current_user["user_id"]
    logger.info(f"[delete_projects] Start - user_id={user_id}")

    # Normalize incoming body to a list of IDs
    project_ids: list[str] = []
    try:
        if isinstance(body, list):
            project_ids = [str(x) for x in body]
        elif isinstance(body, dict):
            req = ProjectsDeleteRequest(**body)
            project_ids = req.ids()
        else:
            raise ValueError("Invalid payload type")
    except Exception as e:
        error_message = f"[delete_projects] Error - user_id={user_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_message="Invalid request body. Provide an array of IDs or {project_id:[...]}.",
        )

    logger.info(f"[delete_projects] user={user_id} requested delete ids={project_ids}")

    if not project_ids:
        raise_http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            error_message="At least one project ID is required",
        )

    try:
        result = await project_service.delete_projects_bulk(
            user_id=user_id, project_ids=project_ids
        )
        deleted_ids = result.get("deleted_ids", [])
        failed_ids = result.get("failed_ids", [])

        # Publish MQTT events for deleted projects
        for project_id in deleted_ids:
            try:
                project_service._publish_project_event(
                    project_id=project_id,
                    event_type=ProjectEventType.DELETE,
                    project=None,
                )
            except Exception as e:
                logger.warning(
                    f"[delete_projects] Failed to publish MQTT event - project_id={project_id}: {e}"
                )
                # Don't fail the request if MQTT publish fails

        message = "Success"
        if failed_ids and deleted_ids:
            message = "Partially deleted"
        elif failed_ids and not deleted_ids:
            message = "No items deleted"

        logger.info(
            f"[delete_projects] Success - user_id={user_id}, deleted={len(deleted_ids)}, failed={len(failed_ids)}"
        )
        return ProjectsBulkDeleteResponse(
            statusCode=200,
            message=message,
            deleted_ids=deleted_ids,
            failed_ids=failed_ids,
        )
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[delete_projects] Error - user_id={user_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===================== Private Helper Functions =====================


async def _extract_upload_files(form) -> List[UploadFile]:
    """Extract upload files from form data"""
    logger.info("[_extract_upload_files] Start")

    if not form:
        logger.warning("[_extract_upload_files] Missing form input")
        return []

    upload_files = []
    upload_files_list = form.getlist("upload_files")

    if upload_files_list and len(upload_files_list) > 0:
        for file_item in upload_files_list:
            if isinstance(file_item, UploadFile):
                upload_files.append(file_item)
            elif hasattr(file_item, "filename") and hasattr(file_item, "read"):
                upload_files.append(file_item)

    # Try to get as single file (fallback) if no files found yet
    if not upload_files:
        upload_file_single = form.get("upload_files")
        if upload_file_single:
            if isinstance(upload_file_single, UploadFile):
                upload_files.append(upload_file_single)
            elif hasattr(upload_file_single, "filename") and hasattr(
                upload_file_single, "read"
            ):
                upload_files.append(upload_file_single)

    logger.info(f"[_extract_upload_files] Success - found {len(upload_files)} file(s)")
    return upload_files


async def _validate_upload_files(upload_files: List[UploadFile]) -> None:
    """Validate upload files"""
    logger.info(
        f"[_validate_upload_files] Start - count={len(upload_files) if upload_files else 0}"
    )

    if not upload_files:
        logger.warning("[_validate_upload_files] No files to validate")
        return

    for file in upload_files:
        if not file.filename:
            continue

        if not is_filename_length_valid(file.filename):
            logger.warning(
                f"[_validate_upload_files] Filename too long: {file.filename}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST, error_key="FILE_NAME_TOO_LONG"
            )

        file_extension = "." + file.filename.split(".")[-1].lower()
        if not is_valid_file_extension(file_extension):
            logger.warning(
                f"[_validate_upload_files] Invalid file extension: {file_extension}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await validate_file_size_and_type(file, file_extension)
            logger.info(
                f"[_validate_upload_files] File validation passed - filename={file.filename}"
            )
        except HTTPException as e:
            logger.warning(
                f"[_validate_upload_files] File validation failed - filename={file.filename}: {e.detail}"
            )
            raise

    logger.info("[_validate_upload_files] Success")


async def _add_project_to_users(
    creator_id: str,
    project_id: str,
    share_emails: List[str],
) -> None:
    """Add project_id to creator's and shared users' personal_projects"""
    logger.info(
        f"[_add_project_to_users] Start - project_id={project_id}, share_count={len(share_emails) if share_emails else 0}"
    )

    if not creator_id or not project_id:
        logger.warning("[_add_project_to_users] Missing required input")
        return

    # Add to creator's personal_projects
    try:
        await user_service.add_project_to_user(creator_id, project_id)
        logger.info(f"[_add_project_to_users] Added project to creator: {creator_id}")
    except Exception as e:
        error_message = f"[_add_project_to_users] Error adding project to creator: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

    # Add to shared users' personal_projects
    if share_emails:
        for email in share_emails:
            try:
                shared_user = await user_service.get_user_by_email(email)
                if shared_user:
                    shared_user_id = shared_user.get("user_id")
                    await user_service.add_project_to_user(shared_user_id, project_id)
                    logger.info(
                        f"[_add_project_to_users] Added project to shared user: {email}"
                    )
                else:
                    logger.warning(
                        f"[_add_project_to_users] Shared user not found: {email}"
                    )
            except Exception as e:
                error_message = f"[_add_project_to_users] Error adding project to shared user {email}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

    logger.info("[_add_project_to_users] Success")


async def _rollback_project_creation(
    project_id: str, creator_id: str, share_emails: List[str]
) -> None:
    """Rollback project data when post-creation steps fail"""
    share_emails = share_emails or []
    logger.info(
        f"[_rollback_project_creation] Start - project_id={project_id}, share_count={len(share_emails) if share_emails else 0}"
    )

    if not project_id:
        logger.warning("[_rollback_project_creation] Missing project_id input")
        return

    # Soft delete project
    try:
        await project_service.delete_project(project_id, creator_id)
        logger.info(
            f"[_rollback_project_creation] Project deleted - project_id={project_id}"
        )
    except Exception as e:
        error_message = f"[_rollback_project_creation] Error deleting project - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

    # Remove project from creator's personal_projects
    try:
        if creator_id:
            await user_service.remove_project_from_user(creator_id, project_id)
    except Exception as e:
        error_message = f"[_rollback_project_creation] Error removing project from creator - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

    # Remove project from shared users
    if share_emails:
        for email in share_emails:
            try:
                shared_user = await user_service.get_user_by_email(email)
                shared_user_id = shared_user.get("user_id") if shared_user else None
                if shared_user_id:
                    await user_service.remove_project_from_user(
                        shared_user_id, project_id
                    )
            except Exception as e:
                error_message = f"[_rollback_project_creation] Error removing project from shared user {email}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

    logger.info(f"[_rollback_project_creation] Completed - project_id={project_id}")


async def _process_requirement_files(
    upload_files: List[UploadFile], project_id: str, user_id: str
) -> None:
    """Process requirement files and create requirement documents"""
    logger.info(
        f"[_process_requirement_files] Start - project_id={project_id}, file_count={len(upload_files) if upload_files else 0}"
    )

    if not upload_files or not project_id or not user_id:
        logger.warning("[_process_requirement_files] Missing required input")
        return

    for file in upload_files:
        if not file.filename:
            continue

        try:
            file_content = await file.read()
            content_type = file.content_type or "application/octet-stream"

            await requirement_document_service.create_requirement_document(
                project_id=project_id,
                file_name=file.filename,
                file_content=file_content,
                content_type=content_type,
                user_id=user_id,
            )
            logger.info(
                f"[_process_requirement_files] File saved successfully - filename={file.filename}"
            )
        except Exception as e:
            error_message = (
                f"[_process_requirement_files] Error saving file {file.filename}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            continue

    logger.info("[_process_requirement_files] Success")


async def _update_share_list_users(
    users_to_remove: set, users_to_add: set, project_id: str
) -> None:
    """Update personal_projects based on share list changes"""
    logger.info(
        f"[_update_share_list_users] Start - project_id={project_id}, remove={len(users_to_remove) if users_to_remove else 0}, add={len(users_to_add) if users_to_add else 0}"
    )

    if not project_id:
        logger.warning("[_update_share_list_users] Missing project_id input")
        return

    # Remove project_id from users who were removed from share list
    if users_to_remove:
        for email in users_to_remove:
            try:
                shared_user = await user_service.get_user_by_email(email)
                if shared_user:
                    shared_user_id = shared_user.get("user_id")
                    await user_service.remove_project_from_user(
                        shared_user_id, project_id
                    )
                    logger.info(
                        f"[_update_share_list_users] Removed project from user: {email}"
                    )
            except Exception as e:
                error_message = f"[_update_share_list_users] Error removing project from user {email}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

    # Add project_id to users who were added to share list
    if users_to_add:
        for email in users_to_add:
            try:
                shared_user = await user_service.get_user_by_email(email)
                if shared_user:
                    shared_user_id = shared_user.get("user_id")
                    await user_service.add_project_to_user(shared_user_id, project_id)
                    logger.info(
                        f"[_update_share_list_users] Added project to user: {email}"
                    )
            except Exception as e:
                error_message = f"[_update_share_list_users] Error adding project to user {email}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

    logger.info("[_update_share_list_users] Success")
