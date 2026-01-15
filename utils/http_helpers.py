"""
HTTP helper functions for API responses
"""
import logging
from typing import Optional
from fastapi import HTTPException, status
from app.schemas.base import ErrorResponse
from app.core.error_messages import get_error_response, APPLICATION_ERROR_MESSAGES

logger = logging.getLogger(__name__)

#region Public Functions

def raise_http_error(
    status_code: int,
    error_key: Optional[str] = None,
    error_message: Optional[str] = None
):
    """
    Raise HTTPException with ErrorResponse format
    
    Args:
        status_code: HTTP status code
        error_key: Key from APPLICATION_ERROR_MESSAGES (optional)
        error_message: Custom error message (optional)
    """
    logger.debug(f"[raise_http_error] Start - status_code={status_code}")
    
    # Input validation
    if not status_code:
        logger.warning("[raise_http_error] Missing status_code")
        raise ValueError("status_code is required")
    
    try:
        # Determine error message
        if error_key:
            # Get message from APPLICATION_ERROR_MESSAGES
            message = APPLICATION_ERROR_MESSAGES.get(error_key, "An error occurred")
        elif error_message:
            message = error_message
        else:
            # Use generic error response
            error_response = get_error_response(status_code)
            raise HTTPException(
                status_code=status_code,
                detail=error_response.dict()
            )
        
        # Build and raise HTTPException
        logger.debug(f"[raise_http_error] Raising {status_code} error")
        raise HTTPException(
            status_code=status_code,
            detail=ErrorResponse(
                statusCode=status_code,
                error_message=message
            ).dict()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[raise_http_error] Error: {e}")
        raise

def get_auth_error_status(error_msg: str) -> tuple[int, str]:
    """
    Map error message to appropriate status code and error key
    
    Args:
        error_msg: Error message string
        
    Returns:
        Tuple of (status_code, error_key)
    """
    logger.debug(f"[get_auth_error_status] Mapping error: {error_msg}")
    
    # Input validation
    if not error_msg:
        logger.warning("[get_auth_error_status] Missing error_msg")
        return (status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID")
    
    try:
        error_lower = error_msg.lower()
        
        if "not found" in error_lower:
            return (status.HTTP_404_NOT_FOUND, "USER_NOT_FOUND")
        elif "invalid" in error_lower:
            return (status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID")
        else:
            return (status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID")
            
    except Exception as e:
        logger.error(f"[get_auth_error_status] Error: {e}")
        return (status.HTTP_401_UNAUTHORIZED, "TOKEN_INVALID")

#endregion

