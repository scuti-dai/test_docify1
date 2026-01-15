"""
Service for downloading conflicted files as a ZIP archive
"""

import logging
import os
from io import BytesIO
from typing import Any, Dict, List, Tuple, Optional

from fastapi import HTTPException, status

from app.core.database import get_database
from cec_docifycode_common.models.basic_design import BasicDesign
from cec_docifycode_common.models.detail_design import DetailDesign
from cec_docifycode_common.models.requirement_document import RequirementDocument
from cec_docifycode_common.models.source_code import SourceCode
from cec_docifycode_common.models.unit_test import UnitTestCode, UnitTestDesign
from app.services.project_service import project_service
from app.utils.download_utils import (
    create_zip_from_documents,
    save_zip_to_disk,
    extract_requirement_content,
    extract_basic_design_content,
    extract_detail_design_content,
)
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


class ConflictDownloadService:
    """Service class for building conflict download ZIP files"""

    _instance: Optional["ConflictDownloadService"] = None
    CONFLICT_SUFFIX = "conflict"

    def __new__(cls) -> "ConflictDownloadService":
        if cls._instance is None:
            cls._instance = super(ConflictDownloadService, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._collection_extractors = {
            RequirementDocument.Config.collection_name: extract_requirement_content,
            BasicDesign.Config.collection_name: extract_basic_design_content,
            DetailDesign.Config.collection_name: extract_detail_design_content,
            SourceCode.Config.collection_name: self._extract_source_code_content,
            UnitTestDesign.Config.collection_name: self._extract_unit_test_design_content,
            UnitTestCode.Config.collection_name: self._extract_unit_test_code_content,
        }
        self._initialized = True
        logger.info("[ConflictDownloadService] Initialized")

    async def generate_conflict_zip(
        self,
        project_id: str,
        user_id: str,
        conflict_files: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        logger.info(
            "[generate_conflict_zip] Start - project_id=%s, files=%s",
            project_id,
            len(conflict_files or []),
        )
        if not project_id or not conflict_files:
            logger.warning("[generate_conflict_zip] Missing input data")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            normalized_items = [
                item.dict() if hasattr(item, "dict") else item
                for item in conflict_files
            ]
            project = await self._ensure_access(project_id, user_id)
            documents = await self._collect_file_payloads(project_id, normalized_items)
            file_name, file_path = await self._create_zip_file(
                project_name=self._extract_project_name(project),
                documents=documents,
            )
            logger.info(
                "[generate_conflict_zip] Success - project_id=%s, file=%s",
                project_id,
                file_name,
            )
            return {
                "file_name": file_name,
                "file_path": file_path,
                "media_type": "application/zip",
            }
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[generate_conflict_zip] Error - project_id=%s: %s" % (
                project_id,
                e,
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _ensure_access(self, project_id: str, user_id: str) -> Any:
        logger.info(
            "[_ensure_access] Start - project_id=%s, user_id=%s", project_id, user_id
        )
        try:
            has_access = await project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(status.HTTP_403_FORBIDDEN)

            project = await project_service.get_project_by_id(project_id)
            if not project:
                raise_http_error(status.HTTP_404_NOT_FOUND)

            logger.info("[_ensure_access] Success - project_id=%s", project_id)
            return project
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[_ensure_access] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _collect_file_payloads(
        self, project_id: str, conflict_files: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        logger.info(
            "[_collect_file_payloads] Start - project_id=%s, files=%s",
            project_id,
            len(conflict_files),
        )
        try:
            db = await get_database()
            payloads: List[Dict[str, Any]] = []
            for item in conflict_files:
                file_meta = self._normalize_conflict_item(item)
                document = await self._fetch_document(
                    db=db,
                    collection_name=file_meta["collection_name"],
                    project_id=project_id,
                    document_id=file_meta["id"],
                )
                file_bytes = self._extract_file_bytes(
                    collection_name=file_meta["collection_name"], document=document
                )
                zip_entry = self._build_zip_entry_path(file_meta, document)
                payloads.append(
                    {
                        "zip_entry_path": zip_entry,
                        "content_bytes": file_bytes,
                        "id": document.get("id", file_meta["id"]),
                    }
                )
            if not payloads:
                raise_http_error(status.HTTP_404_NOT_FOUND)
            logger.info(
                "[_collect_file_payloads] Success - project_id=%s, prepared=%s",
                project_id,
                len(payloads),
            )
            return payloads
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[_collect_file_payloads] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _fetch_document(
        self,
        db,
        collection_name: str,
        project_id: str,
        document_id: str,
    ) -> Dict[str, Any]:
        logger.info(
            "[_fetch_document] Start - collection=%s, doc_id=%s",
            collection_name,
            document_id,
        )
        try:
            # Unit test documents are stored in the shared "unit_test" collection
            # Differentiated by collection_name field
            actual_collection_name = collection_name
            if collection_name in [
                UnitTestDesign.Config.collection_name,
                UnitTestCode.Config.collection_name,
            ]:
                actual_collection_name = "unit_test"

            collection = db.get_collection(actual_collection_name)
            query = {
                "project_id": project_id,
                "id": document_id,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }

            # For unit test collections, add collection_name filter
            if actual_collection_name == "unit_test":
                query["collection_name"] = collection_name

            document = await collection.find_one(query)
            if not document:
                raise_http_error(status.HTTP_404_NOT_FOUND)
            if "_id" in document:
                document["id"] = str(document["_id"])
            logger.info("[_fetch_document] Success - document found")
            return document
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[_fetch_document] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _extract_file_bytes(
        self, collection_name: str, document: Dict[str, Any]
    ) -> bytes:
        logger.info("[_extract_file_bytes] Start - collection=%s", collection_name)
        try:
            extractor = self._collection_extractors.get(collection_name)
            if extractor:
                _, file_bytes = extractor(document)
                logger.info(
                    "[_extract_file_bytes] Success - collection=%s", collection_name
                )
                return file_bytes
            raise_http_error(status.HTTP_400_BAD_REQUEST)
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[_extract_file_bytes] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    def _build_zip_entry_path(
        self, file_meta: Dict[str, Any], document: Dict[str, Any]
    ) -> str:
        logger.info("[_build_zip_entry_path] Start")
        try:
            raw_path = (
                file_meta.get("file_path")
                or document.get("file_path")
                or file_meta.get("file_name")
                or document.get("file_name")
                or file_meta.get("id")
            )
            sanitized = raw_path.replace("\\", "/").strip("/")
            sanitized = sanitized.replace("..", "_")
            final_path = sanitized or f"{file_meta.get('id')}.txt"
            logger.info("[_build_zip_entry_path] Success - path=%s", final_path)
            return final_path
        except Exception as e:
            error_message = "[_build_zip_entry_path] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _create_zip_file(
        self, project_name: str, documents: List[Dict[str, Any]]
    ) -> Tuple[str, str]:
        logger.info(
            "[_create_zip_file] Start - project_name=%s, documents=%s",
            project_name,
            len(documents),
        )
        try:
            zip_buffer = create_zip_from_documents(
                documents, self._zip_content_extractor
            )
            temp_name, temp_path = save_zip_to_disk(
                zip_buffer,
                project_name=project_name,
                file_suffix=self.CONFLICT_SUFFIX,
            )
            final_name = f"{self._sanitize_name(project_name)}-conflict.zip"
            final_path = os.path.join(os.path.dirname(temp_path), final_name)
            os.replace(temp_path, final_path)
            logger.info(
                "[_create_zip_file] Success - file_name=%s, file_path=%s",
                final_name,
                final_path,
            )
            return final_name, final_path
        except Exception as e:
            error_message = "[_create_zip_file] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    @staticmethod
    def _zip_content_extractor(document: Dict[str, Any]) -> Tuple[str, bytes]:
        logger.info("[_zip_content_extractor] Start")
        try:
            result = (
                document.get("zip_entry_path", "unknown"),
                document.get("content_bytes", b""),
            )
            logger.info("[_zip_content_extractor] Success")
            return result
        except Exception as e:
            error_message = "[_zip_content_extractor] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @staticmethod
    def _normalize_conflict_item(item: Dict[str, Any]) -> Dict[str, Any]:
        logger.info("[_normalize_conflict_item] Start")
        try:
            normalized = {
                "id": item.get("id"),
                "file_name": item.get("file_name"),
                "file_path": item.get("file_path"),
                "collection_name": item.get("collection_name"),
            }
            if not all([normalized["id"], normalized["collection_name"]]):
                raise_http_error(status.HTTP_400_BAD_REQUEST)
            logger.info("[_normalize_conflict_item] Success - id=%s", normalized["id"])
            return normalized
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[_normalize_conflict_item] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    @staticmethod
    def _extract_project_name(project: Any) -> str:
        logger.info("[_extract_project_name] Start")
        try:
            setting_item = getattr(project, "setting_item", None)
            project_name = getattr(setting_item, "project_name", None)
            final_name = project_name or "project"
            logger.info("[_extract_project_name] Success - name=%s", final_name)
            return final_name
        except Exception as e:
            error_message = "[_extract_project_name] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return "project"

    @staticmethod
    def _sanitize_name(name: str) -> str:
        logger.info("[_sanitize_name] Start")
        try:
            safe_name = "".join(
                char if char.isalnum() or char in ("-", "_", ".", " ") else "_"
                for char in (name or "project")
            ).strip()
            final_name = safe_name or "project"
            logger.info("[_sanitize_name] Success - name=%s", final_name)
            return final_name
        except Exception as e:
            error_message = "[_sanitize_name] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return "project"

    @staticmethod
    def _extract_source_code_content(document: Dict[str, Any]) -> Tuple[str, bytes]:
        logger.info("[_extract_source_code_content] Start")
        try:
            content = (
                document.get("source_code") or document.get("source_content") or ""
            )
            result = (
                document.get("file_path") or document.get("file_name"),
                content.encode("utf-8"),
            )
            logger.info("[_extract_source_code_content] Success")
            return result
        except Exception as e:
            error_message = "[_extract_source_code_content] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @staticmethod
    def _extract_unit_test_design_content(
        document: Dict[str, Any],
    ) -> Tuple[str, bytes]:
        logger.info("[_extract_unit_test_design_content] Start")
        try:
            content = document.get("unit_test_design_json", "")
            result = (
                document.get("file_path") or document.get("file_name"),
                content.encode("utf-8"),
            )
            logger.info("[_extract_unit_test_design_content] Success")
            return result
        except Exception as e:
            error_message = "[_extract_unit_test_design_content] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @staticmethod
    def _extract_unit_test_code_content(document: Dict[str, Any]) -> Tuple[str, bytes]:
        logger.info("[_extract_unit_test_code_content] Start")
        try:
            content = document.get("ut_code_content") or document.get("test_code") or ""
            result = (
                document.get("file_path") or document.get("file_name"),
                content.encode("utf-8"),
            )
            logger.info("[_extract_unit_test_code_content] Success")
            return result
        except Exception as e:
            error_message = "[_extract_unit_test_code_content] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise


conflict_download_service = ConflictDownloadService()
