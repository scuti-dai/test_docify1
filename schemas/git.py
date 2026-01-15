"""
Git-related request/response schemas
"""

import logging
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class GitRestoreRequest(BaseModel):
    """Schema for restoring files that were marked as delete_push"""

    user_name: str = Field(..., description="Git username or account name")
    token_password: Optional[str] = Field(
        None, description="Git token/password used for authentication"
    )


class GitRestoreResponseData(BaseModel):
    """Schema describing restore result"""

    file_id: str = Field(..., description="Restored file identifier")
    sync_status: str = Field(..., description="Updated sync status after restore")


