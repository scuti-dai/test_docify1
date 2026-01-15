"""
Housekeeping API endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.base import BaseResponse
from app.api.deps import get_current_user
from app.utils.constants import PermissionLevel
from app.utils.http_helpers import raise_http_error
from app.services.housekeeping_service import housekeeping_service

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.delete("/cleanup", response_model=BaseResponse)
@limiter.limit("100/hour")
async def cleanup_housekeeping(
    days: int = Query(
        ...,
        description="Number of days (delete records with deleted_at < NOW() - days)",
        ge=1,
    ),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Clean up records that have been marked as deleted for more than specified days"""
    logger.info(
        f"[cleanup_housekeeping] Start - days={days}, user_id={current_user.get('user_id')}"
    )

    try:
        # Check if user has admin privileges
        user_role = current_user.get("role", "")
        if user_role != PermissionLevel.ADMIN:
            logger.warning(
                f"[cleanup_housekeeping] Non-admin user attempted cleanup: {current_user.get('user_id')}"
            )
            raise_http_error(status.HTTP_403_FORBIDDEN, error_key="ADMIN_ROLE_REQUIRED")

        # Perform cleanup
        result = await housekeeping_service.cleanup_deleted_records(days)

        logger.info(
            f"[cleanup_housekeeping] Success - total_deleted={result.get('total_deleted', 0)}"
        )
        return BaseResponse(
            statusCode=200, message="Housekeeping completed successfully"
        )

    except ValueError as e:
        error_message = f"[cleanup_housekeeping] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[cleanup_housekeeping] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
