"""
Database logging utility functions
Simple functions to save logs to database from exception handlers
"""

import logging
from datetime import datetime
from typing import Optional

from app.core.database import app_db

logger = logging.getLogger(__name__)


class LogLevel:
    """Log level constants"""

    EMERGENCY = "Emergency"
    ALERT = "Alert"
    CRITICAL = "Critical"
    ERROR = "Error"
    WARNING = "Warning"
    NOTICE = "Notice"
    INFO = "Info"
    DEBUG = "Debug"


class LogService:
    """Service for log operations - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(LogService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not LogService._initialized:
            LogService._initialized = True
            logger.info("[LogService] Singleton instance initialized")
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[LogService] Repository updated via set_repository")


async def save_exception_log(
    exception: Exception,
    level: str = LogLevel.ERROR,
    log_type: str = "exception",
) -> Optional[str]:
    """
    Save exception to database log

    Args:
        exception: The exception object
        level: Log level (Emergency/Alert/Critical/Error/Warning/Notice/Info/Debug)
        log_type: Type of log (e.g., "exception", "api_error", "database_error")
    Returns:
        Log ID if successful, None otherwise

    Example:
        try:
            # some code
        except Exception as e:
            await save_exception_log(
                e,
                level=LogLevel.ERROR,
                log_type="api_error",
            )
            raise
    """
    try:
        # Get current date
        current_date = datetime.utcnow().strftime("%Y-%m-%d")

        # Build detail object - simple, just the message
        detail = {
            "message": str(exception),
        }

        # Save to database
        log_repository = app_db.log_repository
        log_id = await log_repository.create_log(
            date=current_date, log_type=log_type, level=level, detail=detail
        )

        return log_id

    except Exception as e:
        # Don't call save_exception_log here to avoid infinite recursion
        # Just log to console/file instead
        logger.error(f"[save_exception_log] Failed to save log: {e}")
        return None


async def save_info_log(
    message: str,
    log_type: str = "info",
) -> Optional[str]:
    """
    Save informational log to database

    Args:
        message: Log message
        log_type: Type of log

    Returns:
        Log ID if successful, None otherwise

    Example:
        await save_info_log(
            "User logged in successfully",
            log_type="authentication",
        )
    """
    try:
        current_date = datetime.utcnow().strftime("%Y-%m-%d")

        detail = {
            "message": message,
        }

        log_repository = app_db.log_repository
        log_id = await log_repository.create_log(
            date=current_date, log_type=log_type, level=LogLevel.INFO, detail=detail
        )

        return log_id

    except Exception as e:
        # Don't call save_exception_log here to avoid infinite recursion
        # Just log to console/file instead
        logger.error(f"[save_info_log] Failed to save log: {e}")
        return None


# Synchronous wrapper for use in non-async contexts (if needed)
def save_exception_log_sync(
    exception: Exception,
    error_message: str = "",
    logger_name: str = "",
    level: str = LogLevel.ERROR,
    log_type: str = "exception",
):
    """
    Synchronous wrapper for save_exception_log

    Args:
        exception: The exception object
        error_message: Error message from logger.error (e.g., "[function_name] Error: {e}")
        logger_name: Logger name from logging.getLogger(__name__)
        level: Log level
        log_type: Type of log

    Example:
        logger = logging.getLogger(__name__)
        try:
            # some code
        except Exception as e:
            error_msg = f"[create_project] Error: {e}"
            logger.error(error_msg)
            save_exception_log_sync(e, error_msg, __name__)
            raise
    """
    import asyncio

    try:
        # Get current date
        current_date = datetime.utcnow().strftime("%Y-%m-%d")

        # Build detail with rule requested
        detail = {
            "message": error_message if error_message else str(exception),
        }

        if logger_name:
            detail["logger_name"] = logger_name
        
        log_repository = app_db.log_repository
        try:
            # Fire-and-forget: create task in async context (non-blocking)
            asyncio.create_task(
                log_repository.create_log(
                    date=current_date, log_type=log_type, level=level, detail=detail
                )
            )
        except RuntimeError:
            # No running loop: fallback to run_until_complete in sync context
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                log_repository.create_log(
                    date=current_date, log_type=log_type, level=level, detail=detail
                )
            )
    except Exception as e:
        # Don't call save_exception_log_sync here to avoid infinite recursion
        # Just log to console/file instead
        logger.error(f"[save_exception_log_sync] Error: {e}")
