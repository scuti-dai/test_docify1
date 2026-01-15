"""
Issue API endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Path, Request, Body, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.issue import IssueCreateRequest
from app.schemas.base import BaseResponse
from app.api.deps import get_current_user
from app.services.issue_service import issue_service
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post(
    "/{project_id}/issue",
    response_model=BaseResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("10/minute")
async def create_issue(
    project_id: str = Path(..., description="Project ID"),
    request: Request = None,
    data: IssueCreateRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Create a new issue for a project"""
    logger.info(
        f"[create_issue] Start - project_id={project_id}, user_id={current_user.get('user_id')}"
    )

    if not project_id:
        logger.warning("[create_issue] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user.get("user_id")
        user_role = current_user.get("role", "")

        # Create issue
        result = await issue_service.create_issue(
            project_id=project_id,
            user_id=user_id,
            user_role=user_role,
            title=data.title,
            description=data.description,
            source_code_id=data.source_code_id,
            user_name=data.user_name,
            token_password=data.token_password,
        )

        logger.info(f"[create_issue] Success - issue_id={result.get('id')}")
        return BaseResponse(statusCode=201, message="Issue created successfully")

    except ValueError as e:
        error_message = f"[create_issue] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[create_issue] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
