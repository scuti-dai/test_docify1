"""
Detail Design Document API endpoints
"""

import logging
from dataclasses import asdict
from datetime import datetime
from fastapi import APIRouter, Depends, Path, Request, Body, status, HTTPException
from app.services.basic_design_service import basic_design_service
from app.services.git_services import git_validate
from app.services.source_code_service import source_code_service
from cec_docifycode_common.models.enums import GenerationTarget
from app.utils.http_helpers import raise_http_error
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.detail_design import (
    DetailDesignListResponse,
    DetailDesignListData,
    DetailDesignListItemData,
    DetailDesignDetailResponse,
    DetailDesignDetailData,
    DetailDesignDetailRequest,
    DetailDesignDownloadFileRequest,
    DetailDesignPushRequest,
    DetailDesignUpdateContentRequest,
    GenerateDetailDesignRequest,
)
from app.schemas.base import BaseResponse, ConflictResponse
from app.api.deps import get_current_user
from app.services.detail_design_service import detail_design_service
from app.services.mqtt_service import mqtt_service
from app.utils.constants import PermissionLevel
from app.utils.constants import TASK_REGISTRATION_TOPIC
from app.utils.download_utils import build_download_file_response

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get(
    "/{project_id}/detail-design",
    response_model=DetailDesignListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detail designs for a project",
    description="Retrieve all detail designs for a specific project",
)
@limiter.limit("100/minute")
async def get_detail_design_list(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    current_user=Depends(get_current_user),
):
    """
    Get detail designs for a project

    Args:
        project_id: Project ID

    Returns:
        DetailDesignListResponse with list of detail designs
    """
    logger.info(f"[get_detail_design_list] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_detail_design_list] Missing project_id input")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        detail_designs_raw = await detail_design_service.get_detail_designs_by_project(
            project_id
        )

        # Map to new response format
        detail_design_items = []
        for item in detail_designs_raw:
            contents = item.get("contents") or {}
            created_at = item.get("created_at")
            updated_at = item.get("updated_at")

            # Format datetime to ISO 8601 string
            if created_at:
                if isinstance(created_at, datetime):
                    created_at_str = created_at.isoformat()
                elif isinstance(created_at, str):
                    created_at_str = created_at
                else:
                    created_at_str = str(created_at)
            else:
                created_at_str = ""

            if updated_at:
                if isinstance(updated_at, datetime):
                    updated_at_str = updated_at.isoformat()
                elif isinstance(updated_at, str):
                    updated_at_str = updated_at
                else:
                    updated_at_str = str(updated_at)
            else:
                updated_at_str = ""

            detail_design_items.append(
                DetailDesignListItemData(
                    id=item.get("id", ""),
                    project_id=item.get("project_id", ""),
                    contents=contents,
                    status=item.get("status", ""),
                    commit_id=item.get("commit_id"),
                    sync_status=item.get("sync_status"),
                    created_at=created_at_str,
                    updated_at=updated_at_str,
                )
            )

        response_data = DetailDesignListData(detail_design_list=detail_design_items)
        response = DetailDesignListResponse(
            statusCode=200, message="Success", data=response_data
        )

        logger.info(
            f"[get_detail_design_list] Success - project_id={project_id}, count={len(detail_design_items)}"
        )
        return response
    except ValueError as ve:
        logger.error(
            f"[get_detail_design_list] Validation error - project_id={project_id}: {ve}"
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[get_detail_design_list] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/detail-design/download")
async def download_detail_designs(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    download_request: DetailDesignDownloadFileRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Download detail design files for the current user"""
    logger.info(
        f"[download_detail_designs] Start - project_id={project_id}, file_id={download_request.id}, key_names={download_request.key_names}, user_id={current_user.get('user_id')}"
    )

    try:
        if not project_id or not download_request.id or not download_request.key_names:
            logger.warning("[download_detail_designs] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        result = await detail_design_service.download_detail_designs(
            project_id=project_id,
            file_id=download_request.id,
            key_names=download_request.key_names,
            user_id=user_id,
            user_role=user_role,
        )

        result_dict = asdict(result)
        response = build_download_file_response(result_dict)
        logger.info("[download_detail_designs] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_detail_designs] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_detail_designs] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/detail-design/{file_id}",
    response_model=DetailDesignDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get detail design detail",
    description="Retrieve a specific detail design document content",
)
@limiter.limit("100/minute")
async def get_detail_design_detail(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Detail design file ID"),
    detail_request: DetailDesignDetailRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Get a single detail design document with authorization enforcement"""

    logger.info(
        "[get_detail_design_detail] Start - project_id=%s, file_id=%s, key_name=%s, user_id=%s",
        project_id,
        file_id,
        detail_request.key_name,
        current_user.get("user_id"),
    )

    if not project_id or not file_id:
        logger.warning("[get_detail_design_detail] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    if not detail_request.key_name:
        logger.warning("[get_detail_design_detail] Missing key_name")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        detail_document = await detail_design_service.get_detail_design_detail(
            project_id=project_id,
            file_id=file_id,
            user_id=user_id,
            user_role=user_role,
            key_name=detail_request.key_name,  # Pass key_name (can be None)
        )

        # Map to new response format
        contents = detail_document.get("contents") or {}
        if not isinstance(contents, dict):
            logger.warning(
                f"[get_detail_design_detail] Contents is not a dict: {type(contents)}"
            )
            contents = {}

        contents_key = contents.get(detail_request.key_name)
        if contents_key is None:
            logger.warning(
                f"[get_detail_design_detail] Key '{detail_request.key_name}' not found in contents"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, "KEY_NOT_FOUND")

        created_at = detail_document.get("created_at")
        updated_at = detail_document.get("updated_at")

        # Format datetime to ISO 8601 string
        if created_at:
            if isinstance(created_at, datetime):
                created_at_str = created_at.isoformat()
            elif isinstance(created_at, str):
                created_at_str = created_at
            else:
                created_at_str = str(created_at)
        else:
            created_at_str = ""

        if updated_at:
            if isinstance(updated_at, datetime):
                updated_at_str = updated_at.isoformat()
            elif isinstance(updated_at, str):
                updated_at_str = updated_at
            else:
                updated_at_str = str(updated_at)
        else:
            updated_at_str = ""

        response_data = DetailDesignDetailData(
            id=detail_document.get("id", ""),
            project_id=detail_document.get("project_id", ""),
            commit_id=detail_document.get("commit_id"),
            sync_status=detail_document.get("sync_status"),
            contents_key=contents_key,
            created_at=created_at_str,
            updated_at=updated_at_str,
        )

        response = DetailDesignDetailResponse(
            statusCode=200,
            message="Success",
            data=response_data,
        )

        logger.info(
            "[get_detail_design_detail] Success - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )
        return response
    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(
            "[get_detail_design_detail] Validation error - project_id=%s, file_id=%s: %s",
            project_id,
            file_id,
            ve,
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = (
            f"[get_detail_design_detail] Error - project_id=%s, file_id=%s: %s"
            % (
                project_id,
                file_id,
                e,
            )
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put(
    "/{project_id}/detail-design/{file_id}",
    response_model=BaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Update detail design content",
    description="Update the content of a specific detail design document",
)
async def update_detail_design_content(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Detail design file ID"),
    update_request: DetailDesignUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Update detail design content"""
    logger.info(
        f"[update_detail_design_content] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning("[update_detail_design_content] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.project_service import project_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[update_detail_design_content] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Use service to update detail design content
        result = await detail_design_service.update_detail_design_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info(f"[update_detail_design_content] Success - file_id={file_id}")
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_detail_design_content] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/detail-design/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_detail_design(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: DetailDesignPushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push detail designs to Git repository"""
    logger.info(
        f"[push_git_detail_design] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_detail_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        # Use service to push detail designs to Git
        result = await detail_design_service.push_git_detail_designs(
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
        # If it's a conflict exception (409), let it propagate
        # The exception handler will return the detail as JSON
        if e.status_code == 409:
            # Re-raise to let exception handler format it correctly
            raise
        raise
    except Exception as e:
        error_message = f"[push_git_detail_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/generate/detail-design", response_model=BaseResponse)
@limiter.limit("10/minute")
async def generate_detail_design(
    project_id: str = Path(..., description="Project ID"),
    generate_request: GenerateDetailDesignRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Generate detail design from basic design documents or source code files"""
    logger.info(
        f"[generate_detail_design] Start - project_id={project_id}, input_source={generate_request.input_source}"
    )

    if not project_id:
        logger.warning("[generate_detail_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        input_source = generate_request.input_source

        if input_source not in ["basic_design", "source_code"]:
            logger.warning(
                f"[generate_detail_design] Invalid input_source: {input_source}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if input_source == "source_code":
            should_update_revision = await git_validate.check_revision(
                project_id, generate_request.user_name, generate_request.token_password
            )

            if should_update_revision:
                topic_generation_target = GenerationTarget.SOURCE_CODE_TO_SUMMARY
            else:
                topic_generation_target = GenerationTarget.SOURCE_CODE_TO_DETAIL_DESIGN

        service = (
            basic_design_service
            if input_source == "basic_design"
            else source_code_service
        )
        if await service.get_active_document_count_by_project_id(project_id) == 0:
            raise_http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                error_key="NO_DATA_TO_GENERATE",
            )
        generation_target = (
            GenerationTarget.BASIC_DESIGN_TO_DETAILED_DESIGN
            if input_source == "basic_design"
            else topic_generation_target
        )
        logger.info(f"[generate_detail_design] Generation target: {generation_target}")

        client = mqtt_service.get_client()
        message = {
            "project_id": project_id,
            "generation_target": generation_target,
        }

        success = client.publish(TASK_REGISTRATION_TOPIC, message, qos=2, retain=True)

        if not success:
            raise_http_error(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_key="MQTT_NOT_STARTED",
            )

        logger.info(
            f"[generate_detail_design] Success - project_id={project_id}, input_source={input_source}"
        )
        return BaseResponse(
            statusCode=200, message="Detail design generated successfully"
        )
    except HTTPException:
        raise
    except ValueError as e:
        error_message = f"[generate_detail_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[generate_detail_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
