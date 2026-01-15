"""
Assets API endpoints - Serve images from database for markdown rendering
"""

import base64
import logging
from urllib.parse import unquote
from fastapi import APIRouter, Depends, Query, Path, status
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.core.database import app_db
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()

# #region Constants
MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}

COLLECTION_MAP = {
    "BD": "basic_design",
    "RD": "requirement_document",
    "PD": "detail_design",
    "SRC": "source_code",
}

# 1x1 transparent PNG placeholder
PLACEHOLDER_IMAGE_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
# #endregion


# #region Public Functions
@router.get("/{project_id}/assets/image")
async def get_image_asset(
    project_id: str = Path(..., description="Project ID"),
    file_path: str = Query(..., description="File path relative to repo root"),
    current_user=Depends(get_current_user),
):
    """
    Get image asset from database by file_path for markdown rendering.

    Supports:
    - Multiple image formats (png, jpg, jpeg, gif, svg, webp, bmp)
    - URL-encoded paths with spaces
    - Case-insensitive collection prefix (BD, bd, Bd)
    - Cross-collection image references
    """
    if not project_id or not file_path:
        logger.warning("[get_image_asset] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        decoded_path = unquote(file_path)
        normalized_path = _normalize_file_path(decoded_path)
        mime_type = _get_mime_type(normalized_path)
        collection_name = _get_collection_from_path(normalized_path)

        image_content = await _find_image_in_db(
            project_id=project_id,
            file_path=normalized_path,
            collection_name=collection_name,
        )

        if image_content:
            return _build_image_response(image_content, mime_type)

        logger.warning(
            "[get_image_asset] Image not found - file_path=%s",
            normalized_path,
        )
        return _build_placeholder_response()

    except Exception as e:
        error_message = "[get_image_asset] Error - file_path=%s: %s", file_path, e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return _build_placeholder_response()


# #endregion


# #region Private Functions
def _normalize_file_path(file_path: str) -> str:
    """
    Normalize file path by stripping leading "/" and "./"
    """
    if not file_path:
        return ""

    try:
        normalized = file_path.strip()
        while normalized.startswith("/") or normalized.startswith("./"):
            if normalized.startswith("/"):
                normalized = normalized[1:]
            elif normalized.startswith("./"):
                normalized = normalized[2:]
        return normalized
    except Exception as e:
        error_message = "[_normalize_file_path] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _get_mime_type(file_path: str) -> str:
    """
    Get MIME type from file extension
    """
    if not file_path:
        return "application/octet-stream"

    try:
        extension = ""
        if "." in file_path:
            extension = "." + file_path.rsplit(".", 1)[-1].lower()
        return MIME_TYPES.get(extension, "application/octet-stream")
    except Exception as e:
        error_message = "[_get_mime_type] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _get_collection_from_path(file_path: str) -> str:
    """
    Determine collection name from file path prefix (case-insensitive)
    """
    if not file_path:
        return ""

    try:
        parts = file_path.split("/")
        if not parts:
            return ""

        prefix = parts[0].upper()
        return COLLECTION_MAP.get(prefix, "")
    except Exception as e:
        error_message = "[_get_collection_from_path] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _find_image_in_db(
    project_id: str,
    file_path: str,
    collection_name: str,
) -> str:
    """
    Find image content in database by project_id and file_path
    Returns base64-encoded image content or empty string if not found
    """
    if not project_id or not file_path:
        return ""

    try:
        # Try to find by file_path first
        result = await _query_by_file_path(project_id, file_path, collection_name)
        if result:
            return result

        # Fallback: try to find by file_name only
        file_name = file_path.split("/")[-1] if "/" in file_path else file_path
        result = await _query_by_file_name(project_id, file_name, collection_name)
        if result:
            return result

        # Try all collections if specific collection didn't have the image
        if collection_name:
            result = await _query_all_collections(project_id, file_path, file_name)
            if result:
                return result

        return ""
    except Exception as e:
        error_message = "[_find_image_in_db] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


async def _query_by_file_path(
    project_id: str,
    file_path: str,
    collection_name: str,
) -> str:
    """Query image by exact file_path match"""
    if not collection_name:
        return ""

    try:
        query = {
            "project_id": project_id,
            "file_path": file_path,
            "deleted_at": None,
        }

        # Also try case-insensitive match for file_path
        query_lower = {
            "project_id": project_id,
            "file_path": {"$regex": f"^{file_path}$", "$options": "i"},
            "deleted_at": None,
        }

        if collection_name == "basic_design":
            repo = app_db.basic_design_repository
            doc = await repo.find_one(query)
            if not doc:
                doc = await repo.find_one(query_lower)
            if doc:
                return _extract_image_content(doc, "basic_design")

        elif collection_name == "requirement_document":
            # For requirement_document: query by file_name (not file_path)
            # Parse file_path to get file_name: "Docifycode/RD/test.md" -> "test.md"
            import os

            file_name = os.path.basename(file_path)
            query_rd = {
                "project_id": project_id,
                "file_name": file_name,
                "deleted_at": None,
            }
            repo = app_db.requirement_document_repository
            doc = await repo.find_one(query_rd)
            if doc:
                return _extract_image_content(doc, "requirement_document")

        elif collection_name == "detail_design":
            repo = app_db.detail_design_repository
            doc = await repo.find_one(query)
            if not doc:
                doc = await repo.find_one(query_lower)
            if doc:
                return _extract_image_content(doc, "detail_design")

        elif collection_name == "source_code":
            repo = app_db.source_code_repository
            doc = await repo.find_one(query)
            if not doc:
                doc = await repo.find_one(query_lower)
            if doc:
                return _extract_image_content(doc, "source_code")

        return ""
    except Exception as e:
        error_message = "[_query_by_file_path] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


async def _query_by_file_name(
    project_id: str,
    file_name: str,
    collection_name: str,
) -> str:
    """Query image by file_name only (fallback for legacy data)"""
    try:
        query = {
            "project_id": project_id,
            "file_name": file_name,
            "deleted_at": None,
        }

        if collection_name == "basic_design":
            repo = app_db.basic_design_repository
            doc = await repo.find_one(query)
            if doc:
                return _extract_image_content(doc, "basic_design")

        elif collection_name == "requirement_document":
            repo = app_db.requirement_document_repository
            doc = await repo.find_one(query)
            if doc:
                return _extract_image_content(doc, "requirement_document")

        elif collection_name == "detail_design":
            repo = app_db.detail_design_repository
            doc = await repo.find_one(query)
            if not doc:
                # Also try with file_path field for detail_design
                query_path = {
                    "project_id": project_id,
                    "file_path": {"$regex": f".*{file_name}$", "$options": "i"},
                    "deleted_at": None,
                }
                doc = await repo.find_one(query_path)
            if doc:
                return _extract_image_content(doc, "detail_design")

        elif collection_name == "source_code":
            repo = app_db.source_code_repository
            doc = await repo.find_one(query)
            if doc:
                return _extract_image_content(doc, "source_code")

        return ""
    except Exception as e:
        error_message = "[_query_by_file_name] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


async def _query_all_collections(
    project_id: str,
    file_path: str,
    file_name: str,
) -> str:
    """Try to find image in all collections"""
    try:
        for collection in [
            "basic_design",
            "requirement_document",
            "detail_design",
            "source_code",
        ]:
            result = await _query_by_file_path(project_id, file_path, collection)
            if result:
                return result
            result = await _query_by_file_name(project_id, file_name, collection)
            if result:
                return result
        return ""
    except Exception as e:
        error_message = "[_query_all_collections] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


def _extract_image_content(doc: dict, collection_type: str) -> str:
    """Extract base64 image content from document based on collection type"""
    if not doc:
        return ""

    try:
        content = ""

        if collection_type == "basic_design":
            contents = doc.get("contents", {})
            if isinstance(contents, dict) and contents.get("type") == "image":
                content = contents.get("content", "")
            else:
                return ""

        elif collection_type == "requirement_document":
            rd_content = doc.get("rd_content", {})
            if isinstance(rd_content, dict) and rd_content.get("type") == "image":
                content = rd_content.get("content", "")
            else:
                return ""

        elif collection_type == "detail_design":
            content = doc.get("content", "")
            if not content or not _is_base64_image(content):
                return ""

        elif collection_type == "source_code":
            content = doc.get("source_code", "")
            if not content or not _is_base64_image(content):
                return ""

        return content
    except Exception as e:
        error_message = "[_extract_image_content] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


def _is_base64_image(content: str) -> bool:
    """Check if content looks like base64 encoded image data"""
    if not content:
        return False

    # Base64 image data typically starts with specific patterns
    # or is a long string of base64 characters
    try:
        # Try to decode first few bytes to check if it's valid base64
        if len(content) < 100:
            return False
        base64.b64decode(content[:100])
        return True
    except Exception as e:
        error_message = "[_is_base64_image] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return False


def _build_image_response(base64_content: str, mime_type: str) -> Response:
    """Build HTTP response with decoded image content"""
    try:
        image_bytes = base64.b64decode(base64_content)
        return Response(
            content=image_bytes,
            media_type=mime_type,
            headers={
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": "inline",
            },
        )
    except Exception as e:
        error_message = "[_build_image_response] Error decoding base64: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return _build_placeholder_response()


def _build_placeholder_response() -> Response:
    """Build HTTP response with placeholder image"""
    try:
        image_bytes = base64.b64decode(PLACEHOLDER_IMAGE_BASE64)
        return Response(
            content=image_bytes,
            media_type="image/png",
            headers={
                "Cache-Control": "no-cache",
                "Content-Disposition": "inline",
            },
        )
    except Exception as e:
        error_message = "[_build_placeholder_response] Error: %s", e
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


# #endregion
