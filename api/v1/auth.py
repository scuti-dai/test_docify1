"""
Authentication API endpoints
"""

import logging
from fastapi import APIRouter, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    RefreshResponse,
)
from app.services.auth_service import AuthService
from app.utils.http_helpers import raise_http_error, get_auth_error_status
from fastapi import status

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
async def login(request: Request, login_data: LoginRequest):
    """Login user and return JWT token"""
    logger.info(f"[login] Login attempt for user: {login_data.email}")
    try:
        auth_service = AuthService()
        result = await auth_service.login(login_data)
        return result
    except ValueError as e:
        error_message = f"[login] Authentication failed: {e}"
        logger.warning(error_message)
        save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

        raise_http_error(status.HTTP_401_UNAUTHORIZED)
    except Exception as e:
        error_message = f"[login] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/refresh", response_model=RefreshResponse)
@limiter.limit("10/minute")
async def refresh_token(request: Request, refresh_data: RefreshRequest):
    """Refresh access token with enhanced security validation"""
    logger.info("[refresh_token] Token refresh request")

    try:
        auth_service = AuthService()
        result = await auth_service.refresh_token(refresh_data.refresh_token)

        logger.info("[refresh_token] Token refreshed successfully")
        return result

    except ValueError as e:
        error_msg = str(e)
        logger.warning(f"[refresh_token] Validation error: {error_msg}")

        # Map error message to status code and error key
        error_status, error_key = get_auth_error_status(error_msg)
        raise_http_error(error_status, error_key=error_key)

    except Exception as e:
        error_message = f"[refresh_token] Unexpected error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
