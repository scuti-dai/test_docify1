"""
Base schemas - Re-exports from cec_docifycode_common
"""

# Import all base schemas from Common package
from cec_docifycode_common.schemas.base import (
    BaseResponse,
    ErrorResponse,
    PaginationResponse,
    DeleteResponse,
    ConflictFileInfo,
    ConflictResponse,
)

__all__ = [
    "BaseResponse",
    "ErrorResponse",
    "PaginationResponse",
    "DeleteResponse",
    "ConflictFileInfo",
    "ConflictResponse",
]
