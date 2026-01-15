"""
Download utility functions for file download operations
"""

import logging
import base64
import zipfile
import os
import tempfile
import json
from typing import Dict, Any, List, Tuple, Callable, Optional
from datetime import datetime
from uuid6 import uuid7
from io import BytesIO
from fastapi import HTTPException, status
from fastapi.responses import StreamingResponse
import mimetypes
import urllib.parse
from app.core.database import get_database
from cec_docifycode_common.models.activity import Activity
from app.utils.constants import ContentType
from app.utils.http_helpers import raise_http_error
from app.services.project_service import ProjectService

logger = logging.getLogger(__name__)
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
FALLBACK_DOWNLOADS_DIR = os.path.join(tempfile.gettempdir(), "cec-docifycode-downloads")


# region Public Functions


def detect_mime_type(file_name: str, default: str = "application/octet-stream") -> str:
    """Detect MIME type based on file name."""
    if not file_name:
        logger.warning("[detect_mime_type] Missing file_name parameter")
        return default

    try:
        mime_type, _ = mimetypes.guess_type(file_name)
        return mime_type or default
    except Exception as e:
        logger.error(f"[detect_mime_type] Error: {e}")
        return default


def cleanup_download_file(file_path: Optional[str]) -> None:
    """Remove a generated download file if it exists."""
    if not file_path:
        return

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.error(
            f"[cleanup_download_file] Error - file_path={file_path}, error={e}"
        )


def encode_filename_for_header(file_name: str) -> str:
    """Encode filename for Content-Disposition header (RFC 5987)."""
    if not file_name:
        return 'filename="download"'

    try:
        # If all characters are ASCII, use filename only
        if all(ord(c) < 128 for c in file_name):
            return f'filename="{file_name}"'

        # Has non-ASCII characters: use both fallback and encoded
        encoded_utf8 = urllib.parse.quote(file_name)
        # Create ASCII fallback (replace non-ASCII characters with _)
        ascii_fallback = "".join(c if ord(c) < 128 else "_" for c in file_name)
        return f"filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_utf8}"

    except Exception as e:
        logger.error(f"[encode_filename_for_header] Error: {e}")
        return 'filename="download"'


def create_file_generator(file_path: str):
    """Generator that reads file and cleans up after."""
    try:
        with open(file_path, "rb") as file_handle:
            while True:
                chunk = file_handle.read(8192)
                if not chunk:
                    break
                yield chunk
    finally:
        cleanup_download_file(file_path)


def build_download_file_response(result: Dict[str, Any]) -> StreamingResponse:
    """Construct StreamingResponse with file content and cleanup handler."""
    if not result:
        logger.warning("[build_download_file_response] Missing result parameter")
        raise ValueError("INVALID_DOWNLOAD_RESULT")

    try:
        file_path = result.get("file_path")
        file_name = result.get("file_name")
        media_type = result.get("media_type", "application/octet-stream")

        if not file_path or not file_name:
            logger.warning(
                "[build_download_file_response] Missing file_path or file_name in result"
            )
            raise ValueError("INVALID_DOWNLOAD_RESULT")

        encoded_filename = encode_filename_for_header(file_name)
        content_disposition = f"attachment; {encoded_filename}"

        response = StreamingResponse(
            create_file_generator(file_path),
            media_type=media_type,
            headers={"Content-Disposition": content_disposition},
        )

        return response

    except Exception as e:
        logger.error(f"[build_download_file_response] Error: {e}")
        raise


async def validate_download_access(
    project_id: str, user_id: str, user_role: str, project_service: ProjectService
) -> Dict[str, Any]:
    """Validate user access to download files from project"""
    logger.info(
        f"[validate_download_access] Start - project_id={project_id}, user_id={user_id}"
    )

    if not project_id or not user_id:
        logger.warning("[validate_download_access] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.utils.constants import PermissionLevel

        if user_role != PermissionLevel.ADMIN:
            has_access = await project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(403)

        project = await project_service.get_project_by_id(project_id)
        if not project:
            raise_http_error(status.HTTP_404_NOT_FOUND)

        logger.info("[validate_download_access] Success")
        return project

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[validate_download_access] Error: {e}")
        raise


async def fetch_documents_by_ids(
    collection,
    project_id: str,
    file_ids: List[str],
    context_name: str = "download",
) -> List[Dict[str, Any]]:
    """Fetch documents by project_id and file_ids from collection"""
    logger.info(
        f"[fetch_documents_by_ids] Start - project_id={project_id}, file_ids={file_ids}, context={context_name}"
    )

    if not project_id or not file_ids:
        logger.warning("[fetch_documents_by_ids] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        query = {
            "id": {"$in": file_ids},
            "project_id": project_id,
            "deleted_at": None,
        }
        documents = [doc async for doc in collection.find(query)]

        if not documents:
            raise_http_error(status.HTTP_404_NOT_FOUND)

        found_ids = {doc.get("id") for doc in documents}
        missing_ids = set(file_ids) - found_ids
        if missing_ids:
            logger.warning(
                f"[fetch_documents_by_ids] Some files not found: {missing_ids}"
            )
            raise_http_error(status.HTTP_404_NOT_FOUND)

        logger.info(
            f"[fetch_documents_by_ids] Success - found {len(documents)} documents"
        )
        return documents

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[fetch_documents_by_ids] Error: {e}")
        raise


def create_zip_from_documents(
    documents: List[Dict[str, Any]],
    content_extractor: Callable[[Dict[str, Any]], Tuple[str, bytes]],
) -> BytesIO:
    """Create ZIP file from documents using content extractor function"""
    if not documents:
        logger.warning("[create_zip_from_documents] No documents provided")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for i, doc in enumerate(documents):
                try:
                    file_name, file_data = content_extractor(doc)
                    zip_file.writestr(file_name, file_data)
                except Exception as e:
                    logger.warning(
                        f"[create_zip_from_documents] Error processing document {i} (id={doc.get('id')}): {e}"
                    )
                    continue

        zip_buffer.seek(0)
        return zip_buffer

    except Exception as e:
        logger.error(f"[create_zip_from_documents] Error: {e}")
        raise


def save_zip_to_disk(
    zip_buffer: BytesIO, project_name: str, file_suffix: str
) -> Tuple[str, str]:
    """Save in-memory ZIP to disk and return (file_name, file_path)"""
    if not project_name or not file_suffix:
        logger.warning("[save_zip_to_disk] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        downloads_dir = _get_download_directory()
        safe_project_name = _sanitize_name(project_name)

        zip_file_name = f"{safe_project_name}-{file_suffix}.zip"
        zip_file_path = os.path.join(downloads_dir, zip_file_name)

        with open(zip_file_path, "wb") as f:
            f.write(zip_buffer.getvalue())

        return zip_file_name, zip_file_path

    except Exception as e:
        logger.error(f"[save_zip_to_disk] Error: {e}")
        raise


def save_single_file_to_disk(
    file_name: str, file_data: bytes, project_name: str, file_suffix: str
) -> Tuple[str, str]:
    """Save a single document to disk and return (file_name, file_path)."""
    if not file_name or file_data is None:
        logger.warning("[save_single_file_to_disk] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        downloads_dir = _get_download_directory()
        safe_file_name = _sanitize_name(os.path.basename(file_name))
        final_file_name = safe_file_name
        final_file_path = os.path.join(downloads_dir, final_file_name)

        with open(final_file_path, "wb") as file_handle:
            file_handle.write(file_data)

        return final_file_name, final_file_path

    except Exception as e:
        logger.error(f"[save_single_file_to_disk] Error: {e}")
        raise


async def create_download_activity(
    project_id: str,
    user_id: str,
    file_count: int,
    document_type: str = "document",
) -> None:
    """Create activity record for file download"""
    logger.info(
        f"[create_download_activity] Start - project_id={project_id}, user_id={user_id}, file_count={file_count}, type={document_type}"
    )

    if not project_id or not user_id:
        logger.warning("[create_download_activity] Missing required parameters")
        return

    try:
        db = await get_database()
        activity_collection = db.get_collection(Activity.Config.collection_name)

        activity_id = str(uuid7())
        current_time = datetime.utcnow().isoformat() + "Z"
        description = f"Downloaded {file_count} {document_type}(s)"

        activity_doc = {
            "activity_id": activity_id,
            "project_id": project_id,
            "user_id": user_id,
            "activity_type": "DOWNLOAD_FILE",
            "description": description,
            "created_at": current_time,
        }

        await activity_collection.insert_one(activity_doc)
        logger.info(f"[create_download_activity] Success - activity_id={activity_id}")

    except Exception as e:
        logger.error(f"[create_download_activity] Error: {e}")
        # Don't raise - activity logging failure shouldn't break the download


# =========================================================
# 🧩 Content Extractor Functions
# =========================================================


def extract_requirement_content(doc: Dict[str, Any]) -> Tuple[str, bytes]:
    """Extract file name and content from requirement document"""
    logger.debug("[extract_requirement_content] Extracting content")

    try:
        file_name = doc.get("file_name", "unknown")
        rd_content = doc.get("rd_content", {})
        content_type = rd_content.get("type", ContentType.MARKDOWN)
        content = rd_content.get("content", "")

        if content_type == ContentType.MARKDOWN:
            file_data = content.encode("utf-8")
        else:
            # Image files are stored as base64
            if not content:
                logger.warning(
                    f"[extract_requirement_content] Empty content for file: {file_name}"
                )
                file_data = b""
            else:
                try:
                    file_data = base64.b64decode(content)
                except Exception as e:
                    logger.error(
                        f"[extract_requirement_content] Base64 decode error for {file_name}: {e}"
                    )
                    raise

        return file_name, file_data

    except Exception as e:
        logger.error(f"[extract_requirement_content] Error: {e}")
        raise


def extract_basic_design_content(doc: Dict[str, Any]) -> Tuple[str, bytes]:
    """Extract file name and content from basic design document"""
    try:
        file_name = doc.get("file_name", "unknown")
        contents = doc.get("contents", {})
        content_type = contents.get("type", ContentType.MARKDOWN)
        content = contents.get("content", "")

        if content_type == ContentType.MARKDOWN:
            file_data = content.encode("utf-8")
        else:
            # Image files are stored as base64 (same as requirement_documents)
            file_data = base64.b64decode(content)

        return file_name, file_data

    except Exception as e:
        logger.error(f"[extract_basic_design_content] Error: {e}")
        raise


def extract_detail_design_content(doc: Dict[str, Any]) -> Tuple[str, bytes]:
    """Extract file name and content from detail design document"""
    try:
        file_name = doc.get("file_name", "unknown")
        content = doc.get("content", "")

        # Content is stored as string, encode to bytes
        file_data = content.encode("utf-8")

        return file_name, file_data

    except Exception as e:
        logger.error(f"[extract_detail_design_content] Error: {e}")
        raise


def extract_content_by_key(doc: Dict[str, Any]) -> Tuple[str, bytes]:
    """Extract file name and content from document by key_name.
    Works for both basic design and detail design documents.
    doc should have 'key_name' field indicating which key to extract from contents.
    Prioritizes 'content' field if available (pre-converted/virtual doc)."""
    try:
        key_name = doc.get("key_name", "")
        if not key_name:
            raise ValueError("key_name is required in document")

        content = doc.get("content", "")
        if content and isinstance(content, str):
            file_name = f"{key_name}.md"
            file_data = content.encode("utf-8")
            return file_name, file_data

        contents = doc.get("contents", {})
        if not isinstance(contents, dict):
            raise ValueError("contents must be a dictionary")

        key_data = contents.get(key_name)
        if key_data is None:
            logger.warning(
                f"[extract_content_by_key] Key '{key_name}' not found in contents"
            )
            key_data = ""

        # Convert key_data to markdown string
        if isinstance(key_data, dict):
            content = json.dumps(key_data, indent=2, ensure_ascii=False)
        elif isinstance(key_data, str):
            content = key_data
        else:
            content = json.dumps(key_data, indent=2, ensure_ascii=False)

        # File name is key_name with .md extension
        file_name = f"{key_name}.md"
        file_data = content.encode("utf-8")

        return file_name, file_data

    except Exception as e:
        logger.error(f"[extract_content_by_key] Error: {e}")
        raise


def extract_source_code_content(doc: Dict[str, Any]) -> Tuple[str, bytes]:
    """Extract file path and content from source code document"""
    try:
        # Use file_path to preserve directory structure in ZIP
        file_path = doc.get("file_path", "")
        file_name = doc.get("file_name", "")

        # Determine ZIP entry path
        if file_path:
            # Remove leading slash if present for ZIP structure
            zip_entry_path = file_path.lstrip("/")
        elif file_name:
            zip_entry_path = file_name
        else:
            zip_entry_path = "unknown"

        # Extract source content and encode to bytes
        source_content = doc.get("source_content", "")
        file_data = source_content.encode("utf-8")

        return zip_entry_path, file_data

    except Exception as e:
        logger.error(f"[extract_source_code_content] Error: {e}")
        raise


# endregion


# region Private Functions


def _sanitize_name(name: str) -> str:
    """Sanitize file or project name for safe file system usage."""
    if name is None:
        logger.warning("[_sanitize_name] Missing name parameter")
        return "unknown"

    try:
        safe_name = "".join(
            char if char.isalnum() or char in ("-", "_", ".", " ") else "_"
            for char in name
        ).strip()

        if not safe_name:
            logger.warning("[_sanitize_name] Sanitized name empty, using fallback")
            safe_name = "unknown"

        return safe_name

    except Exception as e:
        logger.error(f"[_sanitize_name] Error: {e}")
        raise


def _get_download_directory() -> str:
    """Resolve and prepare a writable downloads directory."""
    try:
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        return DOWNLOADS_DIR

    except PermissionError as primary_error:
        logger.warning(
            f"[_get_download_directory] Primary directory permission denied, switching to fallback - error={primary_error}"
        )
        try:
            os.makedirs(FALLBACK_DOWNLOADS_DIR, exist_ok=True)
            return FALLBACK_DOWNLOADS_DIR
        except Exception as e:
            logger.error(f"[_get_download_directory] Fallback directory error: {e}")
            raise

    except Exception as e:
        logger.error(f"[_get_download_directory] Error: {e}")
        raise


# endregion


# =========================================================
# 🧩 Markdown Image Extraction Functions
# =========================================================

import re


def extract_image_paths_from_markdown(content: str) -> List[str]:
    """
    Extract local image paths from markdown content.
    Supports both markdown syntax ![alt](path) and HTML <img src="path">

    Args:
        content: Markdown content string

    Returns:
        List of unique local image paths (excludes http/https URLs)
    """
    if not content:
        return []

    try:
        paths = []

        # Markdown: ![alt](path) or ![alt](path "title")
        md_regex = r'!\[[^\]]*\]\(([^)\s"]+)(?:\s+"[^"]*")?\)'
        for match in re.finditer(md_regex, content):
            paths.append(match.group(1))

        # HTML: <img src="path" ...> or <img src='path' ...>
        html_regex = r'<img[^>]+src=["\']([^"\']+)["\'][^>]*>'
        for match in re.finditer(html_regex, content, re.IGNORECASE):
            paths.append(match.group(1))

        # Filter out external URLs and deduplicate
        local_paths = []
        for path in paths:
            lower_path = path.lower().strip()
            if not (
                lower_path.startswith("http://")
                or lower_path.startswith("https://")
                or lower_path.startswith("data:")
            ):
                if path not in local_paths:
                    local_paths.append(path)

        return local_paths

    except Exception as e:
        logger.error("[extract_image_paths_from_markdown] Error: %s", e)
        return []


def resolve_image_path(current_file_path: str, relative_image_path: str) -> str:
    """
    Resolve relative image path to absolute path based on current file location.

    Args:
        current_file_path: Path of the markdown file (e.g., "RD/Screen_Flow.md")
        relative_image_path: Relative image path from markdown (e.g., "Screen_Flow.png")

    Returns:
        Resolved absolute path (e.g., "RD/Screen_Flow.png")
    """
    if not relative_image_path:
        return ""

    try:
        # Normalize: strip leading "/" or "./"
        path = relative_image_path.strip()
        while path.startswith("/") or path.startswith("./"):
            if path.startswith("/"):
                path = path[1:]
            elif path.startswith("./"):
                path = path[2:]

        # If path already has collection prefix (BD/, RD/, etc.), return as-is
        upper_path = path.upper()
        if (
            upper_path.startswith("BD/")
            or upper_path.startswith("RD/")
            or upper_path.startswith("PD/")
            or upper_path.startswith("SRC/")
        ):
            return path

        # Get directory from current file path
        if current_file_path:
            last_slash = current_file_path.rfind("/")
            current_dir = current_file_path[:last_slash] if last_slash > 0 else ""
        else:
            current_dir = ""

        if not current_dir:
            return path

        # Handle "../" by navigating up directories
        dir_parts = [p for p in current_dir.split("/") if p]
        path_parts = path.split("/")

        for part in path_parts:
            if part == "..":
                if dir_parts:
                    dir_parts.pop()
            elif part != "." and part:
                dir_parts.append(part)

        result = "/".join(dir_parts)
        return result

    except Exception as e:
        logger.error("[resolve_image_path] Error: %s", e)
        return relative_image_path


async def fetch_referenced_images(
    project_id: str,
    documents: List[Dict[str, Any]],
    collection_type: str,
) -> List[Dict[str, Any]]:
    """
    Fetch all images referenced in markdown documents.

    Args:
        project_id: Project ID
        documents: List of markdown documents
        collection_type: "basic_design" or "requirement_document"

    Returns:
        List of image documents to include in download
    """
    logger.info(
        "[fetch_referenced_images] Start - project_id=%s, doc_count=%d, type=%s",
        project_id,
        len(documents),
        collection_type,
    )

    if not documents or not project_id:
        return []

    try:
        from cec_docifycode_common.repositories.basic_design_repository import (
            BasicDesignRepository,
        )
        from cec_docifycode_common.repositories.requirement_document_repository import (
            RequirementDocumentRepository,
        )

        # Collect all referenced image paths
        all_image_paths = set()

        for doc in documents:
            # Get markdown content
            if collection_type == "basic_design":
                contents = doc.get("contents", {})
                content_type = contents.get("type", "")
                content = contents.get("content", "")
            else:  # requirement_document
                rd_content = doc.get("rd_content", {})
                content_type = rd_content.get("type", "")
                content = rd_content.get("content", "")

            # Skip non-markdown files
            if content_type != ContentType.MARKDOWN:
                continue

            # Get file_path for resolving relative paths
            file_path = doc.get("file_path", "")

            # Extract image paths from content
            image_paths = extract_image_paths_from_markdown(content)

            # Resolve each path and add to set
            for img_path in image_paths:
                resolved_path = resolve_image_path(file_path, img_path)
                if resolved_path:
                    all_image_paths.add(resolved_path)

        if not all_image_paths:
            logger.info("[fetch_referenced_images] No images referenced")
            return []

        logger.info(
            "[fetch_referenced_images] Found %d unique image paths: %s",
            len(all_image_paths),
            list(all_image_paths),
        )

        # Fetch images from database
        image_documents = []

        if collection_type == "basic_design":
            repo = BasicDesignRepository()
        else:
            repo = RequirementDocumentRepository()

        for img_path in all_image_paths:
            # Extract file_name from path
            file_name = img_path.split("/")[-1] if "/" in img_path else img_path

            # Try to find by file_path first
            query = {
                "project_id": project_id,
                "file_path": img_path,
                "deleted_at": None,
            }
            doc = await repo.find_one(query)

            # Fallback: find by file_name
            if not doc:
                query = {
                    "project_id": project_id,
                    "file_name": file_name,
                    "deleted_at": None,
                }
                doc = await repo.find_one(query)

            if doc:
                # Check if it's an image (not markdown)
                if collection_type == "basic_design":
                    contents = doc.get("contents", {})
                    if contents.get("type") == ContentType.IMAGE:
                        image_documents.append(doc)
                else:
                    rd_content = doc.get("rd_content", {})
                    if rd_content.get("type") == ContentType.IMAGE:
                        image_documents.append(doc)

        logger.info(
            "[fetch_referenced_images] Success - fetched %d images",
            len(image_documents),
        )
        return image_documents

    except Exception as e:
        logger.error("[fetch_referenced_images] Error: %s", e)
        return []
