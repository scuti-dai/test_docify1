"""
Requirement Document API endpoints
"""

import logging
from typing import Any, Dict, List
from dataclasses import asdict
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
from app.utils.http_helpers import raise_http_error
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.requirement_document import (
    RequirementDocumentDeleteRequest,
    RequirementDocumentDownloadRequest,
    RequirementDocumentPushRequest,
    RequirementDocumentUpdateContentRequest,
)
from app.schemas.base import BaseResponse, DeleteResponse, ConflictResponse
from app.schemas.git import GitRestoreRequest
from app.api.deps import get_current_user
from app.services.requirement_document_service import requirement_document_service
from app.utils.download_utils import build_download_file_response
from app.utils.helpers import is_filename_length_valid, validate_file_size_and_type, is_valid_file_extension
from app.utils.constants import PermissionLevel

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("/{project_id}/requirement-documents", response_model=BaseResponse)
@limiter.limit("200/minute")
async def get_list_file_requirement(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    current_user=Depends(get_current_user),
):
    """Get list of requirement documents for a project"""
    logger.info(f"[get_list_file_requirement] Fetching files for project: {project_id}")

    try:
        user_id = current_user["user_id"]

        # Use service to get requirement documents with user validation
        files = await requirement_document_service.get_requirement_documents_list(
            project_id=project_id, user_id=user_id
        )

        return BaseResponse(statusCode=200, message="Success", data={"files": files})

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_list_file_requirement] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get(
    "/{project_id}/requirement-documents/{file_id}", response_model=BaseResponse
)
@limiter.limit("200/minute")
async def get_requirement_document_detail(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Requirement document file ID"),
    current_user=Depends(get_current_user),
):
    """Get requirement document detail by ID"""
    logger.info(
        f"[get_requirement_document_detail] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning(
            "[get_requirement_document_detail] Missing project_id or file_id"
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        # Use service to get requirement document detail with authorization
        document_detail = (
            await requirement_document_service.get_requirement_document_detail(
                project_id=project_id,
                file_id=file_id,
                user_id=user_id,
                user_role=user_role,
            )
        )

        logger.info(f"[get_requirement_document_detail] Success - file_id={file_id}")
        return BaseResponse(statusCode=200, message="Success", data=document_detail)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_requirement_document_detail] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put(
    "/{project_id}/requirement-documents/{file_id}", response_model=BaseResponse
)
async def update_requirement_document_content(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Requirement document file ID"),
    update_request: RequirementDocumentUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Update requirement document content"""
    logger.info(
        f"[update_requirement_document_content] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning(
            "[update_requirement_document_content] Missing project_id or file_id"
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.project_service import project_service

        user_id = current_user["user_id"]

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[update_requirement_document_content] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Use service to update requirement document content
        result = await requirement_document_service.update_requirement_document_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
        )

        logger.info(
            f"[update_requirement_document_content] Success - file_id={file_id}"
        )
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_requirement_document_content] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/requirement-documents", response_model=BaseResponse)
@limiter.limit("10/minute")
async def upload_file_requirement(
    project_id: str = Path(..., description="Project ID"),
    file: UploadFile = File(..., description="Requirement document file"),
    user_id: str = Form(..., description="User ID"),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Upload file requirement documents"""
    logger.info(f"[upload_file_requirement] Uploading file for project: {project_id}")

    try:
        # Validate file type
        file_extension = None
        if file.filename:
            file_extension = "." + file.filename.split(".")[-1].lower()

                
        if not is_filename_length_valid(file.filename):
            logger.warning(
                f"[_validate_upload_files] Filename too long: {file.filename}"
            )   
            raise_http_error(status.HTTP_400_BAD_REQUEST,
                error_key="FILE_NAME_TOO_LONG") 

        if not file_extension or not is_valid_file_extension(file_extension):
            logger.warning(
                f"[upload_file_requirement] Invalid file extension: {file_extension}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        # Validate file size based on file type
        file_size = await validate_file_size_and_type(file, file_extension)

        logger.info(
            f"[upload_file_requirement] File size validation passed - size={file_size} bytes"
        )

        # Read file content for processing
        file_content = await file.read()

        # Use service to create requirement document with user validation
        result = await requirement_document_service.create_requirement_document(
            project_id=project_id,
            file_name=file.filename,
            file_content=file_content,
            content_type=file.content_type,
            user_id=user_id,
        )

        return BaseResponse(
            statusCode=200, message="File uploaded successfully", data=result
        )
    except HTTPException:
        raise
    
    except ValueError as e:
        error_message = f"[upload_file_requirement] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        error_message = f"[upload_file_requirement] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete("/{project_id}/requirement-documents", response_model=DeleteResponse)
@limiter.limit("10/minute")
async def delete_file_requirement(
    project_id: str = Path(..., description="Project ID"),
    delete_request: RequirementDocumentDeleteRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Delete requirement documents"""
    logger.info(
        f"[delete_file_requirement] Start - project_id={project_id}, requirement_ids={delete_request.requirement_id}"
    )

    if not project_id:
        logger.warning("[delete_file_requirement] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        # Use service to delete requirement documents
        result = (
            await requirement_document_service.delete_multiple_requirement_documents(
                document_ids=delete_request.requirement_id,
                user_id=current_user["user_id"],
                project_id=project_id,
            )
        )

        logger.info(
            f"[delete_file_requirement] Success - deleted: {len(result['deleted_ids'])}, failed: {len(result['failed_ids'])}"
        )
        return DeleteResponse(
            statusCode=200,
            message="Requirement documents deleted successfully",
            deleted_ids=result["deleted_ids"],
            failed_ids=result["failed_ids"],
        )

    except ValueError as e:
        error_message = f"[delete_file_requirement] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[delete_file_requirement] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/requirement-documents/download")
async def download_file_requirement(
    project_id: str = Path(..., description="Project ID"),
    download_request: RequirementDocumentDownloadRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Download requirement documents for the current user."""
    logger.info(
        f"[download_file_requirement] Start - project_id={project_id}, file_ids={download_request.file_id}, user_id={current_user.get('user_id')}"
    )

    try:
        _validate_download_request_input(project_id, download_request.file_id)
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        result = await requirement_document_service.download_requirement_documents(
            project_id=project_id,
            file_ids=download_request.file_id,
            user_id=user_id,
            user_role=user_role,
        )

        # Convert DownloadResult dataclass to dict
        result_dict = (
            asdict(result) if hasattr(result, "__dataclass_fields__") else result
        )

        logger.info(
            f"[download_file_requirement] Download payload ready - file_name={result_dict.get('file_name')}, media_type={result_dict.get('media_type')}"
        )

        response = build_download_file_response(result_dict)
        logger.info("[download_file_requirement] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_file_requirement] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_file_requirement] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# region Private Functions


@router.post(
    "/{project_id}/requirement-documents/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_requirement_document(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: RequirementDocumentPushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push requirement documents to Git repository"""
    logger.info(
        f"[push_git_requirement_document] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_requirement_document] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        # Use service to push requirement documents to Git
        result = await requirement_document_service.push_git_requirement_documents(
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
        error_message = f"[push_git_requirement_document] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/requirement-documents/{file_id}/restore",
    response_model=BaseResponse,
)
@limiter.limit("10/minute")
async def restore_requirement_document(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Requirement document file ID"),
    restore_request: GitRestoreRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Restore a requirement document that is marked as delete_push"""
    logger.info(
        "[restore_requirement_document] Start - project_id=%s, file_id=%s",
        project_id,
        file_id,
    )

    if not project_id or not file_id:
        logger.warning("[restore_requirement_document] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        result = (
            await requirement_document_service.restore_deleted_requirement_document(
                project_id=project_id,
                file_id=file_id,
                user_id=user_id,
                user_name=restore_request.user_name,
                token_password=restore_request.token_password,
            )
        )

        return BaseResponse(
            statusCode=200,
            message="File restored successfully",
            data=result,
        )

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[restore_requirement_document] Error - project_id={project_id}, file_id={file_id}: {e}"
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
