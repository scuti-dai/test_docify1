"""
Basic Design API endpoints
"""

import logging
from typing import List
from dataclasses import asdict
from datetime import datetime
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Path,
    Request,
    UploadFile,
    File,
    Form,
    Body,
    status,
)
from app.services.detail_design_service import detail_design_service
from app.services.requirement_document_service import requirement_document_service
from cec_docifycode_common.models.enums import GenerationTarget
from app.services.mqtt_service import mqtt_service
from app.utils.http_helpers import raise_http_error
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.basic_design import (
    BasicDesignDeleteRequest,
    BasicDesignDownloadFileRequest,
    BasicDesignPushRequest,
    BasicDesignUpdateContentRequest,
    BasicDesignDetailRequest,
    BasicDesignDetailResponse,
    BasicDesignDetailData,
    GenerateBasicDesignRequest,
)
from app.schemas.base import BaseResponse, DeleteResponse, ConflictResponse
from app.schemas.git import GitRestoreRequest
from app.api.deps import get_current_user
from app.utils.constants import PermissionLevel
from app.utils.constants import TASK_REGISTRATION_TOPIC, ContentType
from app.utils.helpers import validate_file_size_and_type, is_valid_file_extension
from app.utils.download_utils import build_download_file_response

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("/{project_id}/basic-design", response_model=BaseResponse)
@limiter.limit("200/minute")
async def get_list_file_basic_design(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    current_user=Depends(get_current_user),
):
    """Get list of basic design files for a project"""
    logger.info(f"[get_list_file_basic_design] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_list_file_basic_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        # Use service to get basic design files list
        from app.services.basic_design_service import basic_design_service

        user_role = current_user.get("role", PermissionLevel.USER.value)
        files = await basic_design_service.get_basic_design_files_list(
            project_id=project_id,
            user_id=current_user["user_id"],
            user_role=user_role,
        )

        logger.info(f"[get_list_file_basic_design] Success - found {len(files)} files")
        return BaseResponse(statusCode=200, message="Success", data={"files": files})

    except ValueError as e:
        error_message = f"[get_list_file_basic_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[get_list_file_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/basic-design/download")
async def download_file_basic_design(
    project_id: str = Path(..., description="Project ID"),
    download_request: BasicDesignDownloadFileRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Download basic design files for the current user."""
    logger.info(
        f"[download_file_basic_design] Start - project_id={project_id}, file_id={download_request.id}, key_names={download_request.key_names}, user_id={current_user.get('user_id')}"
    )

    try:
        if not project_id or not download_request.id or not download_request.key_names:
            logger.warning("[download_file_basic_design] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        from app.services.basic_design_service import basic_design_service

        result = await basic_design_service.download_basic_designs(
            project_id=project_id,
            file_id=download_request.id,
            key_names=download_request.key_names,
            user_id=user_id,
            user_role=user_role,
        )

        result_dict = asdict(result)
        response = build_download_file_response(result_dict)
        logger.info("[download_file_basic_design] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_file_basic_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_file_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/basic-design/{file_id}", response_model=BasicDesignDetailResponse
)
@limiter.limit("200/minute")
async def get_basic_design_detail(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Basic design file ID"),
    detail_request: BasicDesignDetailRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Get detail of a specific basic design file"""

    logger.info(
        f"[get_basic_design_detail] Start - project_id={project_id}, file_id={file_id}, key_name={detail_request.key_name}"
    )

    if not project_id or not file_id:
        logger.warning("[get_basic_design_detail] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    if not detail_request.key_name:
        logger.warning("[get_basic_design_detail] Missing key_name")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.basic_design_service import basic_design_service

        user_role = current_user.get("role", PermissionLevel.USER.value)
        detail = await basic_design_service.get_basic_design_detail(
            project_id=project_id,
            file_id=file_id,
            user_id=current_user["user_id"],
            user_role=user_role,
            key_name=detail_request.key_name,
        )

        # Get contents and extract contents_key
        contents = detail.get("contents", {})
        if not isinstance(contents, dict):
            logger.warning(
                f"[get_basic_design_detail] Contents is not a dict: {type(contents)}"
            )
            contents = {}

        contents_key = contents.get(detail_request.key_name)
        if contents_key is None:
            logger.warning(
                f"[get_basic_design_detail] Key '{detail_request.key_name}' not found in contents"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND, "KEY_NOT_FOUND")

        # Format datetime to ISO 8601 string
        created_at = detail.get("created_at")
        updated_at = detail.get("updated_at")

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

        response_data = BasicDesignDetailData(
            id=detail.get("id", ""),
            project_id=project_id,
            commit_id=(
                detail.get("git", {}).get("commit_id")
                if isinstance(detail.get("git"), dict)
                else detail.get("commit_id")
            ),
            sync_status=(
                detail.get("git", {}).get("sync_status")
                if isinstance(detail.get("git"), dict)
                else detail.get("sync_status")
            ),
            contents_key=contents_key,
            created_at=created_at_str,
            updated_at=updated_at_str,
        )

        response = BasicDesignDetailResponse(
            statusCode=200,
            message="Success",
            data=response_data,
        )

        logger.info(
            f"[get_basic_design_detail] Success - project_id={project_id}, file_id={file_id}"
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_basic_design_detail] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put("/{project_id}/basic-design/{file_id}", response_model=BaseResponse)
async def update_basic_design_content(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Basic design file ID"),
    update_request: BasicDesignUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Update basic design content"""
    logger.info(
        f"[update_basic_design_content] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning("[update_basic_design_content] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.basic_design_service import basic_design_service
        from app.services.project_service import project_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER.value)

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[update_basic_design_content] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Use service to update basic design content
        result = await basic_design_service.update_basic_design_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info(f"[update_basic_design_content] Success - file_id={file_id}")
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_basic_design_content] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/basic-design", response_model=BaseResponse)
@limiter.limit("10/minute")
async def upload_file_basic_design(
    project_id: str = Path(..., description="Project ID"),
    file: UploadFile = File(..., description="Basic design document file"),
    user_id: str = Form(..., description="User ID"),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Upload file basic design"""
    logger.info(
        f"[upload_file_basic_design] Start - project_id={project_id}, filename={file.filename}"
    )

    if not project_id or not file.filename:
        logger.warning("[upload_file_basic_design] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        # Validate file type
        file_extension = None
        if file.filename:
            file_extension = "." + file.filename.split(".")[-1].lower()

        if not file_extension or not is_valid_file_extension(file_extension):
            logger.warning(
                f"[upload_file_basic_design] Invalid file extension: {file_extension}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        # Validate file size based on file type
        file_size = await validate_file_size_and_type(file, file_extension)

        logger.info(
            f"[upload_file_basic_design] File size validation passed - size={file_size} bytes"
        )

        # Read file content for processing
        file_content = await file.read()

        # Determine content type based on file extension
        content_type = (
            ContentType.MARKDOWN if file_extension == ".md" else ContentType.IMAGE
        )

        # Use service to create basic design
        from app.services.basic_design_service import basic_design_service

        user_role = current_user.get("role", PermissionLevel.USER.value)
        result = await basic_design_service.create_basic_design_from_file(
            project_id=project_id,
            file_name=file.filename,
            file_content=file_content,
            content_type=content_type,
            file_size=file_size,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info(f"[upload_file_basic_design] Success - file_id={result['file_id']}")
        return BaseResponse(
            statusCode=200,
            message="Basic design file uploaded successfully",
            data=result,
        )

    except ValueError as e:
        error_message = f"[upload_file_basic_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        error_message = f"[upload_file_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete("/{project_id}/basic-design", response_model=DeleteResponse)
@limiter.limit("10/minute")
async def delete_file_basic_design(
    project_id: str = Path(..., description="Project ID"),
    delete_request: BasicDesignDeleteRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Delete basic design files"""
    logger.info(
        f"[delete_file_basic_design] Start - project_id={project_id}, basic_design_ids={delete_request.basic_design_id}"
    )

    if not project_id:
        logger.warning("[delete_file_basic_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        # Use service to delete basic designs
        from app.services.basic_design_service import basic_design_service

        user_role = current_user.get("role", PermissionLevel.USER.value)
        result = await basic_design_service.delete_multiple_basic_designs(
            basic_design_ids=delete_request.basic_design_id,
            user_id=current_user["user_id"],
            project_id=project_id,
            user_role=user_role,
        )

        logger.info(
            f"[delete_file_basic_design] Success - deleted: {len(result['deleted_ids'])}, failed: {len(result['failed_ids'])}"
        )
        return DeleteResponse(
            statusCode=200,
            message="Basic design files deleted successfully",
            deleted_ids=result["deleted_ids"],
            failed_ids=result["failed_ids"],
        )

    except ValueError as e:
        error_message = f"[delete_file_basic_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[delete_file_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/generate/basic-design", response_model=BaseResponse)
@limiter.limit("10/minute")
async def generate_basic_design(
    project_id: str = Path(..., description="Project ID"),
    request: Request = None,
    body: GenerateBasicDesignRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Generate basic design from requirement documents or detail design documents"""

    input_source = body.input_source

    if not project_id:
        logger.warning("[generate_basic_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        logger.info(f"[generate_basic_design] Input source: {body}")

        if input_source not in ["requirement", "detail_design"]:
            logger.warning(
                f"[generate_basic_design] Invalid input_source: {input_source}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        service = (
            detail_design_service
            if input_source == "detail_design"
            else requirement_document_service
        )

        if await service.get_active_document_count_by_project_id(project_id) == 0:
            raise_http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                error_key="NO_DATA_TO_GENERATE",
            )
        generation_target = (
            GenerationTarget.REQUIREMENT_TO_BASIC_DESIGN
            if input_source == "requirement"
            else GenerationTarget.DETAILED_DESIGN_TO_BASIC_DESIGN
        )
        client = mqtt_service.get_client()
        message = {
            "project_id": project_id,
            "generation_target": generation_target,
        }

        success = client.publish(TASK_REGISTRATION_TOPIC, message, qos=2, retain=True)
        logger.info(
            f"[generate_basic_design] Successfully published to topic '{TASK_REGISTRATION_TOPIC}'"
        )

        if not success:
            raise_http_error(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_key="MQTT_NOT_STARTED",
            )

        return BaseResponse(
            statusCode=200, message="Basic design generated successfully"
        )
    except HTTPException:
        raise
    except ValueError as e:
        error_message = f"[generate_basic_design] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[generate_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/basic-design/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_basic_design(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: BasicDesignPushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push basic designs to Git repository"""
    logger.info(
        f"[push_git_basic_design] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_basic_design] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        # Use service to push basic designs to Git
        from app.services.basic_design_service import basic_design_service

        result = await basic_design_service.push_git_basic_designs(
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
        error_message = f"[push_git_basic_design] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/basic-design/{file_id}/restore",
    response_model=BaseResponse,
)
@limiter.limit("10/minute")
async def restore_basic_design(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Basic design file ID"),
    restore_request: GitRestoreRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Restore a basic design document marked as delete_push"""
    logger.info(
        "[restore_basic_design] Start - project_id=%s, file_id=%s",
        project_id,
        file_id,
    )

    if not project_id or not file_id:
        logger.warning("[restore_basic_design] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.basic_design_service import basic_design_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER.value)

        result = await basic_design_service.restore_deleted_basic_design(
            project_id=project_id,
            file_id=file_id,
            user_id=user_id,
            user_role=user_role,
            user_name=restore_request.user_name,
            token_password=restore_request.token_password,
        )

        return BaseResponse(
            statusCode=200,
            message="File restored successfully",
            data=result,
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[restore_basic_design] Error - project_id={project_id}, file_id={file_id}, error={e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# region Private Functions


def _validate_download_request_input(
    project_id: str,
    file_ids: List[str],
) -> None:
    """Validate download request inputs."""
    logger.info("[_validate_download_request_input] Start")

    try:
        if not project_id:
            logger.warning("[_validate_download_request_input] Missing project_id")
            raise ValueError("INVALID_PROJECT_ID")

        if not file_ids:
            logger.warning("[_validate_download_request_input] Missing file_ids")
            raise ValueError("INVALID_FILE_IDS")

        logger.info("[_validate_download_request_input] Success")

    except Exception as e:
        error_message = f"[_validate_download_request_input] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


# endregion
