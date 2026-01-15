"""
Comment API endpoints
"""

import logging
from fastapi import (
    APIRouter,
    Depends,
    Query,
    Path,
    Request,
    Body,
    status,
)
from app.utils.http_helpers import raise_http_error
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.comment import CommentCreate
from app.schemas.base import BaseResponse
from app.api.deps import get_current_user
from app.services.comment_service import comment_service

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/{project_id}/comments", response_model=BaseResponse)
@limiter.limit("100/minute")
async def add_comment(
    project_id: str = Path(..., description="Project ID"),
    comment_data: CommentCreate = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Create new comment for a project"""
    logger.info(f"[add_comment] Creating comment for project: {project_id}")
    try:
        # Use comment service to create comment
        comment_data_result = await comment_service.create_comment(
            project_id=project_id,
            user_id=comment_data.user_id,
            content=comment_data.content,
        )

        return BaseResponse(
            statusCode=200,
            message="Comment created successfully",
            data=comment_data_result,
        )

    except ValueError as e:
        if "User not found" in str(e):
            raise_http_error(status.HTTP_404_NOT_FOUND)
        elif "Permission denied" in str(e):
            raise_http_error(status.HTTP_403_FORBIDDEN)
        else:
            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        error_message = f"[add_comment] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{project_id}/comments", response_model=BaseResponse)
@limiter.limit("100/minute")
async def get_list_comments(
    project_id: str = Path(..., description="Project ID"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Get comments for a project with pagination"""
    logger.info(f"[get_list_comments] Fetching comments for project: {project_id}")
    try:
        # Use comment service to get comments
        result = await comment_service.get_comments(
            project_id=project_id, page=page, per_page=per_page
        )

        return BaseResponse(statusCode=200, message="Success", data=result)

    except Exception as e:
        error_message = f"[get_list_comments] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
