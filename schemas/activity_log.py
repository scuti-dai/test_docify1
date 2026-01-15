"""
Schema definitions for activity logging service.
"""

import logging
from typing import Dict, Any, List, Optional

from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


class ActivityLogPayload(BaseModel):
    """Structured payload for recording activity events."""

    activity_type: str = Field(
        ..., min_length=1, description="Activity type identifier"
    )
    email: str = Field(..., min_length=3, description="User email address")
    user_id: str = Field(..., min_length=1, description="User unique identifier")
    project_id: str = Field(..., min_length=1, description="Project unique identifier")
    file_ids: Optional[List[str]] = Field(
        default=None, description="List of related file identifiers"
    )
    file_type: Optional[str] = Field(
        default=None, description="Type of files involved in the activity"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Additional metadata for the activity"
    )

    @validator("file_ids", each_item=True)
    def validate_file_ids(cls, value: str) -> str:
        """Ensure file identifiers are valid non-empty strings."""
        logger.info("[validate_file_ids] Start - value validation")
        try:
            if not value:
                logger.error("[validate_file_ids] Empty file identifier detected")
                raise ValueError("INVALID_FILE_ID")
            logger.info("[validate_file_ids] Success - valid file identifier")
            return value
        except Exception as e:
            logger.error(f"[validate_file_ids] Error: {e}")
            raise
