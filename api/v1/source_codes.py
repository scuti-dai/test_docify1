"""
Source Code Routes
API endpoints for source code management
"""

import logging
from dataclasses import asdict
import os
from fastapi import APIRouter, HTTPException, status, Path, Body, Request, Depends
from cec_docifycode_common.models.enums import GenerationTarget
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.source_code import (
    FolderResponse,
    SourceFileResponse,
    SourceCodeListResponse,
    SourceCodeData,
    SourceCodeDownloadRequest,
    SourceDetailResponse,
    SourceCodeUpdateContentRequest,
    SourceCodePushRequest,
    GenerateSourceCodeRequest,
)
from app.schemas.base import BaseResponse, ConflictResponse
from app.services.project_service import project_service
from app.services.git_services.git_service import github_files_exist
from app.services.source_code_service import source_code_service
from app.services.mqtt_service import mqtt_service
from app.api.deps import get_current_user
from app.utils.constants import (
    DEFAULT_DIRECTORY_BD_PD,
    GENERATE_SOURCE_CODE_TOPIC,
    TASK_REGISTRATION_TOPIC,
)
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from cec_docifycode_common.models.detail_design import FILE_NAME_DETAIL_DESIGN
from config_gateway import Config
from app.utils.download_utils import build_download_file_response
from app.utils.http_helpers import raise_http_error
from app.utils.constants import PermissionLevel

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# #region Get Source Code Data (Unified - Folders + Files)
@router.get(
    "/{project_id}/source",
    response_model=SourceCodeListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get source code data (folders and files)",
    description="Retrieve both folder structure and source files for a project in one call",
)
async def get_source_code_data(project_id: str):
    """
    Get unified source code data (folders + files)

    Args:
        project_id: Project ID

    Returns:
        SourceCodeListResponse with folders and sources
    """
    logger.info(f"[get_source_code_data] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_source_code_data] Missing project_id")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_INPUT: project_id is required",
        )

    try:
        data = await source_code_service.get_project_source_code_data(project_id)

        # Build response
        response_data = SourceCodeData(
            folders=[FolderResponse(**folder) for folder in data.get("folders", [])],
            sources=[
                SourceFileResponse(**source) for source in data.get("sources", [])
            ],
        )

        response = SourceCodeListResponse(
            statusCode=200, message="Success", data=response_data
        )

        logger.info(
            f"[get_source_code_data] Success - folders={len(data.get('folders', []))}, "
            f"sources={len(data.get('sources', []))}"
        )
        return response

    except ValueError as ve:
        logger.error(
            f"[get_source_code_data] Validation error - project_id={project_id}: {ve}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        error_message = f"[get_source_code_data] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get source code data: {str(e)}",
        )


# #endregion
@router.get(
    "/{project_id}/source/{file_id}",
    response_model=SourceDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detailed source file information",
    description="Retrieve source file content with linked detail design and unit test data",
)
async def get_source_detail(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Source file ID"),
    current_user=Depends(get_current_user),
):
    """Get source file detail including design and unit test references"""
    logger.info(
        "[get_source_detail] Start - project_id=%s, file_id=%s, user_id=%s",
        project_id,
        file_id,
        current_user.get("user_id"),
    )

    if not project_id or not file_id:
        logger.warning("[get_source_detail] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        detail_data = await source_code_service.get_source_detail(
            project_id=project_id,
            file_id=file_id,
            user_id=user_id,
            user_role=user_role,
        )

        response = SourceDetailResponse(
            statusCode=200,
            message="Success",
            data=detail_data,
        )

        logger.info(
            "[get_source_detail] Success - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )
        return response
    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(
            "[get_source_detail] Validation error - project_id=%s, file_id=%s: %s",
            project_id,
            file_id,
            ve,
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_message=str(ve))
    except Exception as e:
        error_message = f"[get_source_detail] Error - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put(
    "/{project_id}/source/{file_id}",
    response_model=BaseResponse,
    summary="edit_file_source",
    description="Edit an existing source file and persist its new content.",
)
async def edit_file_source(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Source code file ID"),
    update_request: SourceCodeUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Edit source file content (edit_file_source API spec)"""
    logger.info(
        "[edit_file_source] Start - project_id=%s, file_id=%s",
        project_id,
        file_id,
    )

    if not project_id or not file_id:
        logger.warning("[edit_file_source] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.project_service import project_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[edit_file_source] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        result = await source_code_service.update_source_code_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info("[edit_file_source] Success - file_id=%s", file_id)
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[edit_file_source] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# #region Download Source Code
@router.post("/{project_id}/source/download")
async def download_source_code(
    project_id: str = Path(..., description="Project ID"),
    download_request: SourceCodeDownloadRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Download source code files and folders for the current user"""
    logger.info(
        f"[download_source_code] Start - project_id={project_id}, file_ids={download_request.file_id}, folder_ids={download_request.folder_id}, user_id={current_user.get('user_id')}"
    )

    try:
        if not project_id:
            logger.warning("[download_source_code] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        result = await source_code_service.download_source_documents(
            project_id=project_id,
            file_ids=download_request.file_id,
            folder_ids=download_request.folder_id,
            user_id=user_id,
            user_role=user_role,
        )

        # Convert DownloadResult dataclass to dict
        result_dict = (
            asdict(result) if hasattr(result, "__dataclass_fields__") else result
        )

        logger.info(
            f"[download_source_code] Download payload ready - file_name={result_dict.get('file_name')}, media_type={result_dict.get('media_type')}"
        )

        response = build_download_file_response(result_dict)
        logger.info("[download_source_code] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_source_code] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_source_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# #endregion


# #region Git Push APIs
@router.post(
    "/{project_id}/source/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_source_code(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: SourceCodePushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push source code files to Git repository"""
    logger.info(
        f"[push_git_source_code] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_source_code] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        result = await source_code_service.push_git_source_code(
            project_id=project_id,
            file_ids=push_request.id,
            commit_message=push_request.commit_message,
            user_name=push_request.user_name,
            token_password=push_request.token_password,
            user_id=user_id,
        )

        return BaseResponse(
            statusCode=200,
            message="Files pushed to Git successfully",
            data=result,
        )

    except HTTPException as e:
        if e.status_code == 409:
            raise
        raise
    except Exception as e:
        error_message = f"[push_git_source_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/generate/source-code", response_model=BaseResponse)
@limiter.limit("10/minute")
async def generate_source_code(
    project_id: str = Path(..., description="Project ID"),
    generate_request: GenerateSourceCodeRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Generate source code from detail design documents"""
    logger.info(
        f"[generate_source_code] Start - project_id={project_id}, input_source={generate_request.input_source}"
    )

    if not project_id:
        logger.warning("[generate_source_code] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        input_source = generate_request.input_source

        if input_source not in ["detail_design"]:
            logger.warning(
                f"[generate_source_code] Invalid input_source: {input_source}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        project = await project_service.get_detail_project(project_id)
        branch = project["git"]["branch"]
        repository_url = project["git"]["repository"]
        file_paths = [
            os.path.join(DEFAULT_DIRECTORY_BD_PD, FILE_NAME_BASIC_DESIGN),
            os.path.join(DEFAULT_DIRECTORY_BD_PD, FILE_NAME_DETAIL_DESIGN),
        ]
        exists = github_files_exist(
            repository_url,
            branch_name=branch,
            file_paths=file_paths,
            user_name=generate_request.user_name,
            token_password=generate_request.token_password,
        )

        if not exists:
            raise_http_error(
                status.HTTP_400_BAD_REQUEST, error_key="GIT_DESIGN_FILE_NOT_FOUND"
            )

        client = mqtt_service.get_client()
        message = {
            "project_id": project_id,
            "generation_target": GenerationTarget.DETAILED_DESIGN_TO_SOURCE_CODE,
        }

        success = client.publish(
            GENERATE_SOURCE_CODE_TOPIC, message, qos=2, retain=True
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to publish message to MQTT",
            )

        # AI programming simulator: creating mock data code on Git
        # Only push if MOCK_DATA_GENERATE flag is enabled
        if Config.MOCK_DATA_GENERATE:
            try:
                user_id = current_user.get("user_id")
                if user_id:
                    logger.info(
                        f"[generate_source_code] Pushing all source code files to git - project_id={project_id}"
                    )
                    git_result = (
                        await source_code_service.push_all_source_code_mock_to_git(
                            project_id=project_id,
                            user_id=user_id,
                        )
                    )
                    logger.info(
                        f"[generate_source_code] Git push completed - commit_id={git_result.get('commit_id')}, pushed_count={git_result.get('pushed_count')}"
                    )
                else:
                    logger.warning(
                        "[generate_source_code] User ID not found, skipping git push"
                    )
            except HTTPException as git_error:
                # Log git push error but don't fail the entire request
                logger.warning(
                    f"[generate_source_code] Git push failed - project_id={project_id}, error={git_error.detail}"
                )
            except Exception as git_error:
                # Log git push error but don't fail the entire request
                error_message = f"[generate_source_code] Git push error: {git_error}"
                logger.warning(error_message)
                save_exception_log_sync(git_error, error_message, __name__)

        logger.info(
            f"[generate_source_code] Success - project_id={project_id}, input_source={input_source}"
        )
        return BaseResponse(
            statusCode=200, message="Source code generated successfully"
        )
    except HTTPException:
        raise
    except ValueError as e:
        error_message = f"[generate_source_code] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[generate_source_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# #endregion
