"""
API dependencies
"""

import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.core.security import verify_token
from jose import JWTError
from app.services.auth_service import auth_service
from app.services.user_service import user_service
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)

security = HTTPBearer()

# region Public Functions


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    """
    Get current user from JWT token
    Validates token signature, expiry, and user status
    """
    logger.info("[get_current_user] Start")
    unauthorized_status = status.HTTP_401_UNAUTHORIZED

    try:
        # INPUT VALIDATION - Validate token presence and basic JWT format
        if not credentials or not credentials.credentials:
            logger.warning("[get_current_user] Missing credentials")
            raise_http_error(unauthorized_status, error_key="TOKEN_MISSING")

        token = credentials.credentials
        if token.count(".") != 2:
            logger.warning("[get_current_user] Invalid token format")
            raise_http_error(unauthorized_status, error_key="TOKEN_INVALID_FORMAT")

        # Verify token signature and expiry
        payload = verify_token(token)
        if not payload:
            logger.warning("[get_current_user] Invalid token")
            raise_http_error(unauthorized_status, error_key="TOKEN_INVALID")

        # Extract user_id
        user_id = payload.get("sub")
        if not user_id:
            logger.warning("[get_current_user] Invalid payload")
            raise_http_error(unauthorized_status, error_key="INVALID_TOKEN_PAYLOAD")

        # Get user with role
        user = await _get_user_with_role(user_id)
        if not user:
            raise_http_error(unauthorized_status)
        
        logger.info(f"[get_current_user] Success - user_id={user_id}")
        return user
        
    except HTTPException:
        raise
    except JWTError as e:
        logger.warning(f"[get_current_user] JWT error: {e}")
        save_exception_log_sync(e, str(e), __name__)
        raise_http_error(unauthorized_status, error_key="TOKEN_INVALID")
    except Exception as e:
        # Catch-all: log and return generic authentication failure
        logger.error(f"[get_current_user] Error processing authentication")
        save_exception_log_sync(e, f"[get_current_user] Error: {e}", __name__)
        raise_http_error(unauthorized_status, error_key="AUTHENTICATION_FAILED")


# endregion

# region Private Functions


async def _get_user_with_role(user_id: str) -> dict:
    """Get user from DB and fetch role"""
    logger.debug(f"[_get_user_with_role] Start - user_id={user_id}")
    
    # Input validation
    if not user_id:
        logger.warning("[_get_user_with_role] Missing user_id")
        raise ValueError("user_id is required")
    
    try:
        # Get user from database
        user_doc = await user_service.get_user_by_id(user_id)
        if not user_doc:
            logger.warning(f"[_get_user_with_role] User not found - user_id={user_id}")
            return None
        
        # Extract user fields
        user_email = user_doc.get("email")
        user_name = user_doc.get("user_name")
        logger.debug(f"[_get_user_with_role] User found - email={user_email}")
        
        # Get user role from access rights
        from app.services.access_right_service import access_right_service
        user_role = await access_right_service.check_user_permission(user_email)
        logger.debug(f"[_get_user_with_role] Role fetched - role={user_role}")
        
        # Build complete user object
        user = {
            "user_id": user_id,
            "user_name": user_name,
            "email": user_email,
            "role": user_role,
            "group": user_doc.get("group", []),
            "personal_projects": user_doc.get("personal_projects", []),
            "created_at": user_doc.get("created_at"),
            "updated_at": user_doc.get("updated_at"),
            "deleted_at": user_doc.get("deleted_at"),
        }
        
        logger.debug(f"[_get_user_with_role] Success - user_id={user_id}, role={user_role}")
        return user
        
    except ValueError:
        raise
    except Exception as e:
        error_message = f"[_get_user_with_role] Error - user_id={user_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


# endregion
