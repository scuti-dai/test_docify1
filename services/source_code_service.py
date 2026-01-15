"""
Source Code Service
Handles source code and folder structure data retrieval from MongoDB
"""

import logging
import os
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple, Set
from fastapi import HTTPException, status

from app.services.activity_log_service import activity_log_service
from app.services.code_analysis.decision_table_generator import (
    generate_decision_table_and_test_pattern,
)
from app.services.download_service import download_service
from app.services.project_service import ProjectService
from app.services.git_services.git_push import (
    push_files_to_git,
    check_conflicts_before_push,
)
from app.services.detail_design_service import detail_design_service
from app.utils.constants import PermissionLevel

from app.utils.helpers import (
    get_current_utc_time,
    decrypt_ai_programming_credential,
)
from app.utils.http_helpers import raise_http_error
from app.utils.constants import SyncStatus
from app.schemas.base import ConflictResponse, ConflictFileInfo
from app.schemas.unit_test import (
    UNIT_TEST_DESIGN_COLLECTION,
    UNIT_TEST_CODE_COLLECTION,
)

from fastapi import HTTPException

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


class SourceCodeService:
    """Service for source code management operations - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(
        cls, repository=None, detail_design_repository=None, unit_test_repository=None
    ):
        if cls._instance is None:
            cls._instance = super(SourceCodeService, cls).__new__(cls)
        return cls._instance

    def __init__(
        self, repository=None, detail_design_repository=None, unit_test_repository=None
    ):
        if not SourceCodeService._initialized:
            self.project_service = ProjectService()
            SourceCodeService._initialized = True
            logger.info("[SourceCodeService] Singleton instance initialized")

        # Always set repositories if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository
        if detail_design_repository is not None:
            self.detail_design_repository = detail_design_repository
        if unit_test_repository is not None:
            self.unit_test_repository = unit_test_repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[SourceCodeService] Repository updated via set_repository")

    # #region Public Methods

    async def get_project_source_code_data(self, project_id: str) -> Dict[str, Any]:
        """Get source code files with detail_design from DB"""
        logger.info(
            "[get_project_source_code_data] Start - project_id=%s",
            project_id,
        )

        if not project_id:
            logger.warning("[get_project_source_code_data] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Get folders
            folders = await self.repository.get_folders(project_id, "source_code")

            # Get all source code files
            raw_sources = await self.repository.get_sources(project_id)

            # Get detail_design document for this project
            detail_design_documents = (
                await self.detail_design_repository.get_detail_designs_by_project(
                    project_id
                )
            )

            # Get contents from detail_design document (use first one if exists)
            detail_design_contents = {}
            if detail_design_documents and len(detail_design_documents) > 0:
                detail_design_contents = (
                    detail_design_documents[0].get("contents") or {}
                )

            # Get all class_ids and method_ids from sources
            all_class_ids = []
            all_method_ids = []
            for source in raw_sources:
                for class_item in source.get("classes", []):
                    class_id = class_item.get("class_id")
                    if class_id:
                        all_class_ids.append(class_id)
                    for method_item in class_item.get("methods", []):
                        method_id = method_item.get("method_id")
                        if method_id:
                            all_method_ids.append(method_id)
                for global_method in source.get("global_methods", []):
                    method_id = global_method.get("method_id")
                    if method_id:
                        all_method_ids.append(method_id)

            # Get unit tests
            unit_test_documents = await self.repository.get_unit_tests_by_refs(
                project_id=project_id,
                class_ids=list(set(all_class_ids)),
                method_ids=list(set(all_method_ids)),
            )
            unit_test_map = await self._group_unit_tests(unit_test_documents)

            # Build response for each source file
            sources_payload = []
            for source_doc in raw_sources:
                source_payload = self._compose_source_detail_payload(
                    source_document=source_doc,
                    detail_design_contents=detail_design_contents,
                    unit_test_map=unit_test_map,
                )
                sources_payload.append(source_payload)

            logger.info(
                "[get_project_source_code_data] Success - folder_count=%s, source_count=%s",
                len(folders),
                len(sources_payload),
            )
            return {"folders": folders, "sources": sources_payload}
        except ValueError as e:
            error_message = "[get_project_source_code_data] Validation error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise
        except Exception as e:
            error_message = "[get_project_source_code_data] Error: %s" % (e,)
            logger.error(error_message)
            raise

    async def get_active_document_count_by_project_id(self, project_id: str) -> int:
        """Get count of active (non-deleted) documents by project_id"""
        if not project_id:
            return 0

        try:
            filter_query = {"project_id": project_id, "deleted_at": None}
            return await self.repository.count_documents(filter_query)
        except Exception as e:
            logger.error(f"[get_active_document_count_by_project_id] Error: {e}")
            return 0

    async def get_source_detail(
        self, project_id: str, file_id: str, user_id: str, user_role: str
    ) -> Dict[str, Any]:
        """Fetch a single source file with detail design and unit test info"""
        logger.info(
            "[get_source_detail] Start - project_id=%s, file_id=%s, user_id=%s",
            project_id,
            file_id,
            user_id,
        )

        if not project_id or not file_id or not user_id:
            logger.warning("[get_source_detail] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            source_document = await self.repository.get_source_by_id(
                project_id=project_id, file_id=file_id
            )

            # Get detail_design document for this project
            detail_design_documents = (
                await self.detail_design_repository.get_detail_designs_by_project(
                    project_id
                )
            )

            # Get contents from detail_design document (use first one if exists)
            detail_design_contents = {}
            if detail_design_documents and len(detail_design_documents) > 0:
                detail_design_contents = (
                    detail_design_documents[0].get("contents") or {}
                )
            processed_contents = (
                detail_design_service.convert_detail_design_to_markdown(
                    detail_design_contents
                )
            )
            detail_design_contents_payload = {
                key: (value["rendered_markdown"] if isinstance(value, dict) else "")
                for key, value in processed_contents.items()
            }
            class_ids = [
                class_item.get("class_id")
                for class_item in source_document.get("classes", [])
                if class_item.get("class_id")
            ]
            method_ids = self._collect_method_ids(source_document)

            unit_test_documents = await self.repository.get_unit_tests_by_refs(
                project_id=project_id, class_ids=class_ids, method_ids=method_ids
            )
            unit_test_map = await self._group_unit_tests(unit_test_documents)

            response_payload = self._compose_source_detail_payload(
                source_document=source_document,
                detail_design_contents=detail_design_contents_payload,
                unit_test_map=unit_test_map,
            )

            logger.info(
                "[get_source_detail] Success - project_id=%s, file_id=%s",
                project_id,
                file_id,
            )
            return response_payload

        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[get_source_detail] Error - project_id=%s, file_id=%s: %s"
                % (
                    project_id,
                    file_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def download_source_documents(
        self,
        project_id: str,
        file_ids: Optional[List[str]],
        folder_ids: Optional[List[str]],
        user_id: str,
        user_role: str,
    ) -> Dict[str, Any]:
        logger.info(
            "[download_source_documents] Start - project_id=%s, user_id=%s",
            project_id,
            user_id,
        )

        if not project_id or not user_id:
            logger.warning("[download_source_documents] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            result = await self._download_documents(
                project_id=project_id,
                file_ids=file_ids,
                folder_ids=folder_ids,
                user_id=user_id,
                user_role=user_role,
                data_type="source_code",
            )
            logger.info(
                "[download_source_documents] Success - project_id=%s",
                project_id,
            )
            return result
        except HTTPException:
            raise
        except Exception as e:
            error_message = "[download_source_documents] Error - project_id=%s: %s" % (
                project_id,
                e,
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _download_documents(
        self,
        project_id: str,
        file_ids: Optional[List[str]],
        folder_ids: Optional[List[str]],
        user_id: str,
        user_role: str,
        data_type: str,
    ) -> Dict[str, Any]:
        logger.info(
            "[_download_documents] Start - project_id=%s, data_type=%s",
            project_id,
            data_type,
        )

        project = await download_service.validate_project_download(
            project_id=project_id,
            user_id=user_id,
            user_role=user_role,
            project_service=self.project_service,
        )

        all_documents = []

        if file_ids:
            files_by_ids = await self.repository.fetch_source_files_by_ids(
                project_id=project_id, file_ids=file_ids
            )
            all_documents.extend(files_by_ids)
            logger.debug(
                "[_download_documents] Retrieved files by IDs - "
                f"count={len(files_by_ids)}, ids={file_ids}"
            )

        if folder_ids:
            files_by_folders = await self.repository.fetch_source_files_by_folders(
                project_id=project_id,
                folder_ids=folder_ids,
                limit=50,
                type=data_type,
            )
            all_documents.extend(files_by_folders)
            logger.debug(
                "[_download_documents] Retrieved files by folders - "
                f"count={len(files_by_folders)}, folder_ids={folder_ids}, data_type={data_type}"
            )

        logger.debug(
            "[_download_documents] Total documents before deduplication - "
            f"count={len(all_documents)}"
        )

        seen_ids = set()
        unique_documents = []
        for doc in all_documents:
            doc_id = doc.get("id")
            if doc_id and doc_id not in seen_ids:
                seen_ids.add(doc_id)
                unique_documents.append(doc)
            elif not doc_id:
                logger.warning(
                    "[_download_documents] Document missing id - "
                    f"document_keys={list(doc.keys())}"
                )

        if not unique_documents:
            logger.warning("[_download_documents] No files found to download")
            raise_http_error(status.HTTP_404_NOT_FOUND)

        document_count = len(unique_documents)
        logger.info(
            "[_download_documents] Retrieved documents - project_id=%s, document_count=%s, data_type=%s",
            project_id,
            document_count,
            data_type,
        )

        file_type, file_suffix = ("source code", "source_code")

        folder_hierarchy = await self._build_folder_hierarchy(
            project_id=project_id, type=data_type, selected_folder_ids=folder_ids or []
        )

        def extract_with_folder_structure(doc: Dict[str, Any]) -> Tuple[str, bytes]:
            return self._extract_source_code_content_with_folders(
                doc=doc, folder_hierarchy=folder_hierarchy, data_type=data_type
            )

        created_at = get_current_utc_time()
        project_name = self._extract_project_name(project)

        context = download_service.create_download_context(
            project_id=project_id,
            project_name=project_name,
            project=project,
            user_id=user_id,
            created_at=created_at,
            file_type=file_type,
            file_suffix=file_suffix,
        )

        document_id_resolver = self.repository.extract_source_code_id
        document_normalizer = self.repository.ensure_source_document_id

        resolvers = download_service.create_resolver_config(
            document_id_resolver=document_id_resolver,
            document_normalizer=document_normalizer,
            content_extractor=extract_with_folder_structure,
        )

        handlers = download_service.create_file_handlers()

        result = await download_service.build_download_payload(
            documents=unique_documents,
            context=context,
            resolvers=resolvers,
            handlers=handlers,
            activity_logger=activity_log_service.create_activity_log,
        )

        logger.info("[_download_documents] Success - download payload prepared")
        return result

    async def update_source_code_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
        user_role: str = PermissionLevel.USER,
    ) -> Dict[str, Any]:
        """Update source code content with re-parsing of classes and global_methods"""
        logger.info(
            f"[update_source_code_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning("[update_source_code_content] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            # Find the document
            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[update_source_code_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get file_name to detect language
            file_name = document.get("file_name", "")
            language = self._detect_language(file_name)

            # Parse code to extract classes and global_methods
            parsed_data = self.parse_source_code(content, language)
            classes = parsed_data.get("classes", [])
            global_methods = parsed_data.get("global_methods", [])

            logger.info(
                f"[update_source_code_content] Parsed - class_count={len(classes)}, method_count={len(global_methods)}"
            )

            # Get current sync_status
            current_sync_status = document.get("sync_status") or ""

            # Determine new sync_status
            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            # Prepare update data with parsed code structure
            current_time = get_current_utc_time()
            update_data = {
                "source_code": content,
                "classes": classes,
                "global_methods": global_methods,
                "sync_status": new_sync_status,
                "updated_at": current_time,
            }

            # Update document
            is_updated = await self.repository.update_document(
                document_id=file_id,
                project_id=project_id,
                update_data=update_data,
            )

            if not is_updated:
                logger.warning(
                    f"[update_source_code_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            logger.info(
                f"[update_source_code_content] Success - file_id={file_id}, sync_status={new_sync_status}, classes={len(classes)}, methods={len(global_methods)}"
            )

            return {
                "project_id": project_id,
                "file_id": file_id,
                "updated_at": current_time,
                "content": content,
            }

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[update_source_code_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # #endregion

    # #region Private Methods

    def _compose_project_payload(
        self, data_type: str, folders: List[Dict[str, Any]], items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Compose payload with correct list key for the given data type"""
        logger.info(
            "[_compose_project_payload] Start - data_type=%s, folder_count=%s, item_count=%s",
            data_type,
            len(folders or []),
            len(items or []),
        )
        try:
            payload_key = self._resolve_payload_key(data_type)
            payload = {"folders": folders or []}
            payload[payload_key] = items or []
            logger.info(
                "[_compose_project_payload] Success - payload_key=%s", payload_key
            )
            return payload
        except Exception as e:
            error_message = "[_compose_project_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _resolve_payload_key(self, data_type: str) -> str:
        """Determine response list key based on data type"""
        logger.info("[_resolve_payload_key] Start - data_type=%s", data_type)
        try:
            key = "sources"
            logger.info("[_resolve_payload_key] Success - key=%s", key)
            return key
        except Exception as e:
            error_message = "[_resolve_payload_key] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _validate_user_authorization(
        self, user_role: str, user_id: str, project_id: str
    ) -> None:
        """Ensure the requester can access the project"""
        logger.info(
            "[_validate_user_authorization] Start - project_id=%s, user_id=%s",
            project_id,
            user_id,
        )

        if not user_id or not project_id:
            logger.warning(
                "[_validate_user_authorization] Missing required identifiers"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            normalized_role = user_role or PermissionLevel.USER
            if normalized_role == PermissionLevel.ADMIN:
                logger.info("[_validate_user_authorization] Admin access granted")
                return

            has_access = await self.project_service.check_user_project_access(
                user_id=user_id, project_id=project_id
            )
            if not has_access:
                logger.warning(
                    "[_validate_user_authorization] Access denied - project_id=%s, user_id=%s",
                    project_id,
                    user_id,
                )
                raise_http_error(status.HTTP_403_FORBIDDEN)

            logger.info(
                "[_validate_user_authorization] Access granted - project_id=%s, user_id=%s",
                project_id,
                user_id,
            )
        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[_validate_user_authorization] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _build_detail_design_map(
        self, project_id: str, documents: List[Dict[str, Any]]
    ) -> Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]]:
        """Build mapping between class/method and detail design bundles"""
        logger.info(
            "[_build_detail_design_map] Start - project_id=%s, document_count=%s",
            project_id,
            len(documents or []),
        )

        try:
            if not documents:
                logger.info("[_build_detail_design_map] No documents provided")
                return {}

            document_ids = self._collect_detail_document_ids(documents)

            if not document_ids:
                logger.info("[_build_detail_design_map] No valid document ids found")
                return {}

            assets = (
                await self.detail_design_repository.get_detail_designs_by_document_ids(
                    project_id=project_id, document_ids=document_ids
                )
            )
            asset_map = self._map_detail_design_assets(assets)

            detail_map = self._assemble_detail_design_map(documents, asset_map)

            logger.info(
                "[_build_detail_design_map] Success - buckets=%s", len(detail_map)
            )
            return detail_map
        except Exception as e:
            error_message = "[_build_detail_design_map] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _collect_detail_document_ids(
        self, documents: List[Dict[str, Any]]
    ) -> List[str]:
        """Collect unique detail design document identifiers"""
        logger.info(
            "[_collect_detail_document_ids] Start - document_count=%s",
            len(documents or []),
        )
        try:
            if not documents:
                return []
            identifiers: List[str] = []
            for document in documents:
                document_id = self._resolve_detail_design_document_id(document)
                if document_id:
                    identifiers.append(document_id)
            unique_identifiers = list(dict.fromkeys(identifiers))
            logger.info(
                "[_collect_detail_document_ids] Success - unique_count=%s",
                len(unique_identifiers),
            )
            return unique_identifiers
        except Exception as e:
            error_message = "[_collect_detail_document_ids] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _assemble_detail_design_map(
        self,
        documents: List[Dict[str, Any]],
        asset_map: Dict[str, Dict[str, Any]],
    ) -> Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]]:
        """Assemble detail design entries grouped by class and method"""
        logger.info(
            "[_assemble_detail_design_map] Start - document_count=%s",
            len(documents or []),
        )
        try:
            detail_map: Dict[
                Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]
            ] = defaultdict(list)

            for document in documents:
                doc_id = self._resolve_detail_design_document_id(document)
                if not doc_id:
                    continue
                class_id = document.get("class_id") or None
                method_id = document.get("method_id")
                entry = self._build_detail_design_entry(
                    asset_bundle=asset_map.get(doc_id, {})
                )
                if entry:
                    detail_map[(class_id, method_id)].append(entry)

            logger.info(
                "[_assemble_detail_design_map] Success - buckets=%s", len(detail_map)
            )
            return detail_map
        except Exception as e:
            error_message = "[_assemble_detail_design_map] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _resolve_detail_design_document_id(self, document: Dict[str, Any]) -> str:
        """Resolve detail design document identifier"""
        logger.info(
            "[_resolve_detail_design_document_id] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )
        try:
            if not document:
                return ""
            doc_id = (
                document.get("detail_design_document_id")
                or document.get("id")
                or document.get("_id")
            )
            resolved = str(doc_id) if doc_id else ""
            logger.info(
                "[_resolve_detail_design_document_id] Success - document_id=%s",
                resolved,
            )
            return resolved
        except Exception as e:
            error_message = "[_resolve_detail_design_document_id] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_detail_design_assets(
        self, documents: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """Group detail design assets by document id"""
        logger.info(
            "[_map_detail_design_assets] Start - document_count=%s",
            len(documents or []),
        )
        try:
            asset_map: Dict[str, Dict[str, Any]] = defaultdict(dict)
            if not documents:
                return asset_map

            for document in documents:
                document_id = (
                    document.get("detail_design_document_id")
                    or document.get("id")
                    or document.get("_id")
                )
                if not document_id:
                    continue
                file_type = document.get("file_type")
                content = document.get("content")
                entry_id = str(document.get("id") or document.get("_id") or document_id)

                if file_type == "description_json":
                    asset_map[document_id]["description_group"] = {
                        "description_id": entry_id,
                        "description_json": content,
                        "description": content,
                    }
                elif file_type == "activity_diagram":
                    asset_map[document_id]["activity_diagram_group"] = {
                        "activity_diagram_id": entry_id,
                        "activity_diagram": content,
                    }
                elif file_type == "detail_design":
                    asset_map[document_id]["detailed_design_group"] = {
                        "detailed_design_id": entry_id,
                        "detailed_design": content,
                    }
                elif file_type == "interface_design":
                    asset_map[document_id]["interface_design_group"] = {
                        "interface_design_id": entry_id,
                        "interface_design": content,
                    }

            logger.info(
                "[_map_detail_design_assets] Success - grouped=%s", len(asset_map)
            )
            return asset_map
        except Exception as e:
            error_message = "[_map_detail_design_assets] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_detail_design_entry(
        self, asset_bundle: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a single detail design entry"""
        logger.info("[_build_detail_design_entry] Start")
        try:
            if not asset_bundle:
                logger.info("[_build_detail_design_entry] Empty asset bundle")
                return {}
            entry = {
                "description_group": asset_bundle.get("description_group"),
                "detailed_design_group": asset_bundle.get("detailed_design_group"),
                "interface_design_group": asset_bundle.get("interface_design_group"),
            }
            logger.info("[_build_detail_design_entry] Success")
            return entry
        except Exception as e:
            error_message = "[_build_detail_design_entry] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _collect_method_ids(self, source_document: Dict[str, Any]) -> List[str]:
        """Collect unique method ids from classes and global methods"""
        logger.info("[_collect_method_ids] Start")
        try:
            if not source_document:
                return []
            method_ids: List[str] = []
            for class_item in source_document.get("classes", []):
                for method_item in class_item.get("methods", []):
                    method_id = method_item.get("method_id")
                    if method_id:
                        method_ids.append(method_id)
            for global_method in source_document.get("global_methods", []):
                method_id = global_method.get("method_id")
                if method_id:
                    method_ids.append(method_id)
            unique_ids = list(dict.fromkeys(method_ids))
            logger.info(
                "[_collect_method_ids] Success - unique_method_count=%s",
                len(unique_ids),
            )
            return unique_ids
        except Exception as e:
            error_message = "[_collect_method_ids] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _group_unit_tests(
        self, unit_tests: List[Dict[str, Any]]
    ) -> Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]]:
        """Group unit tests by class and method"""
        logger.info(
            "[_group_unit_tests] Start - document_count=%s", len(unit_tests or [])
        )
        try:
            buckets: Dict[
                Tuple[Optional[str], Optional[str]], Dict[str, Dict[str, Any]]
            ] = defaultdict(dict)
            if not unit_tests:
                return {}

            for document in unit_tests:
                method_id = document.get("method_id")
                if not method_id:
                    continue
                class_id = document.get("class_id")
                normalized_doc = {**document, "class_id": class_id}
                entry_key = self._resolve_unit_test_entry_key(normalized_doc)
                bucket_key = (class_id, method_id)
                existing_entry = buckets[bucket_key].get(entry_key)
                buckets[bucket_key][entry_key] = await self._build_unit_test_entry(
                    normalized_doc, existing_entry
                )

            grouped: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]] = {
                bucket_key: list(entries.values())
                for bucket_key, entries in buckets.items()
            }
            logger.info("[_group_unit_tests] Success - buckets=%s", len(grouped))
            return grouped
        except Exception as e:
            error_message = "[_group_unit_tests] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _build_unit_test_entry(
        self, document: Dict[str, Any], existing_entry: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Build unit test payload entry"""
        logger.info("[_build_unit_test_entry] Start")
        try:
            design_id = document.get("id") or document.get("unit_test_id")
            collection_name = document.get("collection_name")
            design_group: Optional[Dict[str, Any]] = None
            code_group: Optional[Dict[str, Any]] = None

            if collection_name == UNIT_TEST_DESIGN_COLLECTION:
                unit_test_design_json = document.get("unit_test_design_json")
                decision_table = document.get("decision_table")
                test_pattern = document.get("test_pattern")
                if not decision_table and unit_test_design_json:
                    (
                        decision_table,
                        test_pattern,
                    ) = await generate_decision_table_and_test_pattern(
                        unit_test_design_json, document.get("project_id"), design_id
                    )
                design_group = {
                    "unit_test_design_id": design_id,
                    "unit_test_design_json": unit_test_design_json,
                    "decision_table": decision_table,
                    "test_pattern": test_pattern,
                }
            else:  # collection_name == UNIT_TEST_CODE_COLLECTION:
                code_group = {
                    "ut_code_id": document.get("unit_test_id") or design_id,
                    "ut_code_content": document.get("test_code")
                    or document.get("ut_code_content"),
                }

            entry = existing_entry or {
                "unit_test_design_group": None,
                "ut_code_group": None,
            }
            if design_group:
                entry["unit_test_design_group"] = design_group
            if code_group:
                entry["ut_code_group"] = code_group
            logger.info("[_build_unit_test_entry] Success")
            return entry
        except Exception as e:
            error_message = "[_build_unit_test_entry] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _resolve_unit_test_entries(
        self,
        unit_test_map: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]],
        class_id: Optional[str],
        method_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Resolve unit test entries for a method with fallback keys"""
        logger.info(
            "[_resolve_unit_test_entries] Start - class_id=%s, method_id=%s",
            class_id,
            method_id,
        )
        if not method_id:
            logger.warning("[_resolve_unit_test_entries] Missing method_id input")
            return []
        try:
            normalized_class_id = (
                class_id.strip() if isinstance(class_id, str) else class_id
            )
            candidate_class_ids: List[Optional[str]] = []
            if normalized_class_id:
                candidate_class_ids = [class_id, normalized_class_id]
            else:
                candidate_class_ids = [None, ""]

            seen: Set[Tuple[Optional[str], Optional[str]]] = set()
            for candidate in candidate_class_ids:
                key = (candidate, method_id)
                if key in seen:
                    continue
                seen.add(key)
                entries = unit_test_map.get(key)
                if entries:
                    logger.info(
                        "[_resolve_unit_test_entries] Success - matched_key=%s", key
                    )
                    return entries

            logger.info("[_resolve_unit_test_entries] No entries found")
            return []
        except Exception as e:
            error_message = "[_resolve_unit_test_entries] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _resolve_unit_test_entry_key(self, document: Dict[str, Any]) -> str:
        """Determine grouping key for unit test entries"""
        logger.info("[_resolve_unit_test_entry_key] Start")
        if not document:
            logger.warning("[_resolve_unit_test_entry_key] Missing document input")
            return ""
        try:
            file_name = document.get("file_name") or ""
            base_name = file_name.rsplit(".", 1)[0] if file_name else ""
            if not base_name:
                base_name = document.get("id") or document.get("unit_test_id") or ""
            if not base_name:
                base_name = f"{document.get('method_id', '')}_{document.get('collection_name', '')}"
            logger.info("[_resolve_unit_test_entry_key] Success - key=%s", base_name)
            return base_name
        except Exception as e:
            error_message = "[_resolve_unit_test_entry_key] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _compose_source_detail_payload(
        self,
        source_document: Dict[str, Any],
        detail_design_contents: Dict[str, Any],
        unit_test_map: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Compose final response payload"""
        logger.info("[_compose_source_detail_payload] Start")
        try:
            if not source_document:
                raise ValueError("Source document is required")

            classes_payload = [
                self._build_class_payload(
                    class_document, detail_design_contents, unit_test_map
                )
                for class_document in source_document.get("classes", [])
            ]
            global_methods_payload = [
                self._build_global_method_payload(global_method, unit_test_map)
                for global_method in source_document.get("global_methods", [])
            ]

            payload = {
                "id": source_document.get("id"),
                "project_id": source_document.get("project_id"),
                "file_path": source_document.get("file_path", ""),
                "file_name": source_document.get("file_name", ""),
                "folder_id": source_document.get("folder_id"),
                "source_code": source_document.get("source_code"),
                "classes": classes_payload,
                "global_methods": global_methods_payload,
                "commit_id": source_document.get("commit_id"),
                "sync_status": source_document.get("sync_status"),
                "detail_design_document_id": source_document.get(
                    "detail_design_document_ids", []
                ),
                "unit_test_id": source_document.get("unit_test_ids", []),
                "issue_id": source_document.get("issue_ids", []),
                "created_at": source_document.get("created_at"),
                "updated_at": source_document.get("updated_at"),
            }

            logger.info("[_compose_source_detail_payload] Success")
            return payload
        except Exception as e:
            error_message = "[_compose_source_detail_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _compose_source_file_summary(
        self, source_document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Compose simplified source file payload for UTD response"""
        logger.info("[_compose_source_file_summary] Start")
        try:
            if not source_document:
                raise ValueError("Source document is required")

            classes_payload = [
                {
                    "class_id": class_document.get("class_id", ""),
                    "class_name": class_document.get("class_name", ""),
                    "class_content": class_document.get("class_content", ""),
                    "methods": [
                        {
                            "method_id": method_document.get("method_id", ""),
                            "method_name": method_document.get("method_name", ""),
                            "method_content": method_document.get("method_content", ""),
                        }
                        for method_document in class_document.get("methods", [])
                    ],
                }
                for class_document in source_document.get("classes", [])
            ]

            global_methods_payload = [
                {
                    "method_id": method_document.get("method_id", ""),
                    "method_name": method_document.get("method_name", ""),
                    "method_content": method_document.get("method_content", ""),
                }
                for method_document in source_document.get("global_methods", [])
            ]

            payload = {
                "id": source_document.get("id"),
                "project_id": source_document.get("project_id"),
                "file_path": source_document.get("file_path", ""),
                "file_name": source_document.get("file_name", ""),
                "folder_id": source_document.get("folder_id"),
                "source_code": source_document.get("source_code"),
                "classes": classes_payload,
                "global_methods": global_methods_payload,
            }

            logger.info("[_compose_source_file_summary] Success")
            return payload
        except Exception as e:
            error_message = "[_compose_source_file_summary] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_class_payload(
        self,
        class_document: Dict[str, Any],
        detail_design_contents: Dict[str, Any],
        unit_test_map: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Build class level payload"""
        logger.info("[_build_class_payload] Start")
        try:
            class_id = class_document.get("class_id")
            methods_payload = [
                self._build_method_payload(method_document, class_id, unit_test_map)
                for method_document in class_document.get("methods", [])
            ]
            payload = {
                "class_id": class_id or "",
                "class_name": class_document.get("class_name", ""),
                "class_content": class_document.get("class_content", ""),
                "detail_design": detail_design_contents,  # Assign same contents to all classes
                "methods": methods_payload,
            }
            logger.info("[_build_class_payload] Success - class_id=%s", class_id)
            return payload
        except Exception as e:
            error_message = "[_build_class_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_method_payload(
        self,
        method_document: Dict[str, Any],
        class_id: Optional[str],
        unit_test_map: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Build method payload for class or global methods"""
        logger.info("[_build_method_payload] Start")
        try:
            method_id = method_document.get("method_id")
            unit_test_entries = self._resolve_unit_test_entries(
                unit_test_map=unit_test_map,
                class_id=class_id,
                method_id=method_id,
            )
            payload = {
                "method_id": method_id or "",
                "method_name": method_document.get("method_name", ""),
                "method_content": method_document.get("method_content", ""),
                "unit_test": unit_test_entries,
            }
            logger.info(
                "[_build_method_payload] Success - class_id=%s, method_id=%s",
                class_id,
                method_id,
            )
            return payload
        except Exception as e:
            error_message = "[_build_method_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_global_method_payload(
        self,
        method_document: Dict[str, Any],
        unit_test_map: Dict[Tuple[Optional[str], Optional[str]], List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Build payload for global methods"""
        logger.info("[_build_global_method_payload] Start")
        try:
            method_id = method_document.get("method_id")
            base_payload = self._build_method_payload(
                method_document, None, unit_test_map
            )
            logger.info(
                "[_build_global_method_payload] Success - method_id=%s", method_id
            )
            return base_payload
        except Exception as e:
            error_message = "[_build_global_method_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_unit_test_design_fields(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Map unit test design document fields to API response format"""
        logger.info(
            "[_map_unit_test_design_fields] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )
        if not document:
            logger.warning("[_map_unit_test_design_fields] Missing document input")
            return {}

        try:
            unit_test_id = (
                document.get("unit_test_id")
                or document.get("id")
                or document.get("_id")
            )
            mapped = {
                "unit_test_id": unit_test_id,
                "project_id": document.get("project_id", ""),
                "file_name": document.get("file_name"),
                "source_code_id": document.get("source_code_id", ""),
                "class_id": document.get("class_id"),
                "method_id": document.get("method_id"),
                "unit_test_design_json": document.get("unit_test_design_json")
                or document.get("sample_table"),
                "decision_table": document.get("decision_table"),
                "test_pattern": document.get("test_pattern")
                or document.get("sample_table"),
                "commit_id": document.get("commit_id"),
                "sync_status": document.get("sync_status"),
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
            }
            logger.info(
                "[_map_unit_test_design_fields] Success - unit_test_id=%s", unit_test_id
            )
            return mapped
        except Exception as e:
            error_message = "[_map_unit_test_design_fields] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_unit_test_code_fields(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Map unit test code document fields to API response format"""
        logger.info(
            "[_map_unit_test_code_fields] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )
        if not document:
            logger.warning("[_map_unit_test_code_fields] Missing document input")
            return {}

        try:
            unit_test_id = (
                document.get("unit_test_id")
                or document.get("id")
                or document.get("_id")
            )
            mapped = {
                "unit_test_id": unit_test_id,
                "project_id": document.get("project_id", ""),
                "file_name": document.get("file_name"),
                "source_code_id": document.get("source_code_id", ""),
                "class_id": document.get("class_id"),
                "method_id": document.get("method_id"),
                "ut_code_content": document.get("ut_code_content")
                or document.get("test_code"),
                "commit_id": document.get("commit_id"),
                "sync_status": document.get("sync_status"),
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
            }
            logger.info(
                "[_map_unit_test_code_fields] Success - unit_test_id=%s", unit_test_id
            )
            return mapped
        except Exception as e:
            error_message = "[_map_unit_test_code_fields] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _extract_project_name(self, project: Dict[str, Any]) -> str:
        """Extract project name from project dict or object"""
        logger.debug("[_extract_project_name] Extracting project name")

        try:
            if isinstance(project, dict):
                project_name = (
                    project.get("project_name")
                    or project.get("name")
                    or project.get("setting_item", {}).get("project_name")
                    or "project"
                )
            else:
                setting_item = getattr(project, "setting_item", None)
                if setting_item:
                    project_name = getattr(setting_item, "project_name", "project")
                else:
                    project_name = getattr(project, "project_name", "project")

            logger.debug("[_extract_project_name] Extracted: %s", project_name)
            return project_name

        except Exception as e:
            error_message = "[_extract_project_name] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return "project"

    async def _build_folder_hierarchy(
        self, project_id: str, type: str, selected_folder_ids: List[str]
    ) -> Dict[str, Any]:
        """
        Build folder hierarchy map for path reconstruction

        Args:
            project_id: Project ID
            type: Type of data (source_code)
            selected_folder_ids: List of selected folder IDs (root folders)

        Returns:
            Dict with folder_id -> folder_path mapping and folder info
        """
        logger.info(
            f"[_build_folder_hierarchy] Start - project_id={project_id}, type={type}, selected_folder_ids={selected_folder_ids}"
        )

        try:
            # Get all folders
            all_folders = await self.repository.get_folders(
                project_id=project_id, data_type=type
            )

            if not all_folders:
                logger.warning("[_build_folder_hierarchy] No folders found")
                return {"folder_map": {}, "folder_paths": {}}

            # Build folder info map
            folder_map = {folder.get("folder_id"): folder for folder in all_folders}

            # Build parent-child relationship
            children_map: Dict[str, List[str]] = {}
            for folder in all_folders:
                parent_id = folder.get("parent_folder_id")
                folder_id = folder.get("folder_id")
                if parent_id:
                    if parent_id not in children_map:
                        children_map[parent_id] = []
                    children_map[parent_id].append(folder_id)

            # Get all selected folder IDs including subfolders
            all_selected_folder_ids = set(selected_folder_ids)
            queue = list(selected_folder_ids)
            while queue:
                current_folder_id = queue.pop(0)
                if current_folder_id in children_map:
                    for child_folder_id in children_map[current_folder_id]:
                        if child_folder_id not in all_selected_folder_ids:
                            all_selected_folder_ids.add(child_folder_id)
                            queue.append(child_folder_id)

            # Find root selected folders (not subfolders of other selected folders)
            root_selected_folders = set(selected_folder_ids)
            for folder_id in selected_folder_ids:
                # Check if this folder is a subfolder of another selected folder
                current_id = folder_id
                while current_id:
                    folder_info = folder_map.get(current_id)
                    if not folder_info:
                        break
                    parent_id = folder_info.get("parent_folder_id")
                    if parent_id and parent_id in selected_folder_ids:
                        # This folder is a subfolder of another selected folder
                        root_selected_folders.discard(folder_id)
                        break
                    current_id = parent_id

            # Build folder paths for selected folders only
            folder_paths: Dict[str, str] = {}

            def build_path(folder_id: str, visited: set) -> str:
                """Recursively build folder path from nearest root selected folder"""
                if folder_id in visited:
                    logger.warning(
                        f"[_build_folder_hierarchy] Circular reference detected for folder_id={folder_id}"
                    )
                    return folder_map.get(folder_id, {}).get("folder_name", folder_id)

                if folder_id in folder_paths:
                    return folder_paths[folder_id]

                folder_info = folder_map.get(folder_id)
                if not folder_info:
                    return ""

                folder_name = folder_info.get("folder_name", folder_id)
                parent_id = folder_info.get("parent_folder_id")

                # If this is a root selected folder, path is just folder name
                if folder_id in root_selected_folders or not parent_id:
                    path = folder_name
                else:
                    # Check if parent is in selected folders
                    if parent_id in all_selected_folder_ids:
                        # Build parent path recursively
                        visited.add(folder_id)
                        parent_path = build_path(parent_id, visited)
                        visited.remove(folder_id)

                        if parent_path:
                            path = f"{parent_path}/{folder_name}"
                        else:
                            path = folder_name
                    else:
                        # Parent is not selected, use folder name only
                        path = folder_name

                folder_paths[folder_id] = path
                return path

            # Build paths for all selected folders and their subfolders
            for folder_id in all_selected_folder_ids:
                if folder_id not in folder_paths:
                    build_path(folder_id, set())

            logger.info(
                f"[_build_folder_hierarchy] Success - folder_paths_count={len(folder_paths)}"
            )
            return {"folder_map": folder_map, "folder_paths": folder_paths}

        except Exception as e:
            error_message = f"[_build_folder_hierarchy] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return {"folder_map": {}, "folder_paths": {}}

    def _extract_source_code_content_with_folders(
        self,
        doc: Dict[str, Any],
        folder_hierarchy: Dict[str, Any],
        data_type: str = "source_code",
    ) -> Tuple[str, bytes]:
        """
        Extract file path and content using folder hierarchy

        Args:
            doc: Source code document
            folder_hierarchy: Folder hierarchy map from _build_folder_hierarchy

        Returns:
            Tuple of (zip_entry_path, file_data)
        """
        logger.debug("[_extract_source_code_content_with_folders] Extracting content")

        try:
            file_name = doc.get("file_name", "")
            folder_id = doc.get("folder_id")
            folder_paths = folder_hierarchy.get("folder_paths", {})

            # Build ZIP entry path based on folder hierarchy
            if folder_id and folder_id in folder_paths:
                folder_path = folder_paths[folder_id]
                zip_entry_path = (
                    f"{folder_path}/{file_name}" if folder_path else file_name
                )
            else:
                # Fallback: use file_name only if no folder_id or folder not in hierarchy
                zip_entry_path = file_name

            # Extract source content and encode to bytes
            source_content = doc.get("source_content", "")
            file_data = source_content.encode("utf-8")

            logger.debug(
                f"[_extract_source_code_content_with_folders] zip_entry_path={zip_entry_path}, folder_id={folder_id}"
            )
            return zip_entry_path, file_data

        except Exception as e:
            error_message = f"[_extract_source_code_content_with_folders] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_source_fields(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map database fields to API response format

        Args:
            doc: Source code document from MongoDB

        Returns:
            Mapped dictionary with API field names
        """
        try:
            # Derive file_name if not present
            file_path = doc.get("file_path", "")
            file_name_value = doc.get("file_name")

            source_code_id = self.repository.extract_source_code_id(doc)
            if source_code_id:
                doc["id"] = source_code_id

            if not file_name_value and file_path:
                file_name_value = file_path.split("/")[-1]

            mapped = {
                "id": doc.get("id") or doc.get("source_code_id", ""),
                "project_id": doc.get("project_id", ""),
                "file_path": file_path,
                "file_name": file_name_value or "",
                "folder_id": doc.get("folder_id"),
                "commit_id": doc.get("commit_id"),
                "sync_status": doc.get("sync_status"),
                "detail_design_document_id": doc.get("detail_design_document_ids", []),
                "unit_test_id": doc.get("unit_test_ids", []),
                "issue_id": doc.get("issue_ids", []),
                "created_at": doc.get("created_at"),
                "updated_at": doc.get("updated_at"),
            }

            # logger.debug(
            #     f"[_map_source_fields] Success - id={source_code_id}, "
            #     f"project_id={mapped.get('project_id')}"
            # )
            return mapped

        except Exception as e:
            error_message = f"[_map_source_fields] Error mapping fields: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def create_source_code_document(
        self,
        project_id: str,
        file_path: str,
        file_name: str,
        source_code: Optional[str] = None,
        commit_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        classes: Optional[List[Dict[str, Any]]] = None,
        global_methods: Optional[List[Dict[str, Any]]] = None,
        sync_status: Optional[str] = SyncStatus.SYNCED,
    ) -> Dict[str, Any]:
        """
        Create a new source code document

        Args:
            project_id: Project ID
            file_path: File path relative to repository root
            file_name: File name
            source_code: Source code content (optional, can be None for media files)
            commit_id: Git commit ID (optional)
            folder_id: Folder ID (optional)
            sync_status: Sync status (optional, default: "synced")

        Returns:
            Dict with created document info

        Raises:
            ValueError: If required parameters are missing
            Exception: If database operation fails
        """
        logger.info(
            f"[create_source_code_document] Start - project_id={project_id}, file_path={file_path}, source_code_is_none={source_code is None}"
        )

        if not project_id or not file_path:
            logger.warning("[create_source_code_document] Missing required parameters")
            raise ValueError("Missing required parameters: project_id or file_path")

        try:
            from uuid6 import uuid7
            from app.utils.helpers import get_current_utc_time

            current_time = get_current_utc_time()

            # Generate source code ID
            source_code_id = str(uuid7())

            # Prepare classes and methods
            classes_list = classes or []
            global_methods_list = global_methods or []

            # Normalize source_code: use empty string if None (for media files)
            normalized_source_code = source_code if source_code is not None else ""
            if not normalized_source_code:
                logger.info(
                    f"[create_source_code_document] Creating document with empty source_code (media file) - file_path={file_path}, file_name={file_name}"
                )

            # Create source code document
            source_code_doc = {
                "id": source_code_id,
                "project_id": project_id,
                "file_path": file_path,
                "file_name": file_name,
                "folder_id": folder_id,
                "source_code": normalized_source_code,
                "classes": classes_list,
                "global_methods": global_methods_list,
                "commit_id": commit_id,
                "sync_status": sync_status,
                "detail_design_document_ids": [],
                "unit_test_ids": [],
                "issue_ids": [],
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            # Insert into database using repository
            is_inserted = await self.repository.insert_source_code_document(
                source_code_doc
            )

            if is_inserted:
                logger.info(
                    f"[create_source_code_document] Document created successfully - id={source_code_id}"
                )
                return {
                    "id": source_code_id,
                    "project_id": project_id,
                    "file_path": file_path,
                    "file_name": file_name,
                    "created_at": current_time,
                }
            else:
                logger.error("[create_source_code_document] Failed to insert document")
                raise ValueError("Failed to create source code document")

        except ValueError as ve:
            error_message = f"[create_source_code_document] Validation error: {ve}"
            logger.error(error_message)
            save_exception_log_sync(ve, error_message, __name__)

            raise
        except Exception as e:
            error_message = f"[create_source_code_document] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_source_code_folder_id(
        self, source_code_id: str, folder_id: Optional[str]
    ) -> None:
        """
        Update folder_id for a source code document

        Args:
            source_code_id: Source code ID
            folder_id: Folder ID to set (can be None)

        Raises:
            ValueError: If source_code_id is missing
            Exception: If database operation fails
        """
        logger.info(
            f"[update_source_code_folder_id] Start - id={source_code_id}, folder_id={folder_id}"
        )

        if not source_code_id:
            logger.warning("[update_source_code_folder_id] Missing id")
            raise ValueError("Missing required parameter: id")

        try:
            modified_count = await self.repository.update_source_code_folder_id(
                source_code_id=source_code_id, folder_id=folder_id
            )

            if modified_count > 0:
                logger.info(
                    f"[update_source_code_folder_id] Updated folder_id successfully - id={source_code_id}"
                )
            else:
                logger.warning(
                    f"[update_source_code_folder_id] No document was updated - id={source_code_id}"
                )

        except Exception as e:
            error_message = f"[update_source_code_folder_id] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def batch_update_source_code_folder_ids(
        self, source_code_ids: List[str], folder_id: Optional[str]
    ) -> None:
        """
        Batch update folder_id for multiple source code documents

        Args:
            source_code_ids: List of source code IDs
            folder_id: Folder ID to set (can be None)

        Raises:
            ValueError: If source_code_ids is empty
            Exception: If database operation fails
        """
        logger.info(
            f"[batch_update_source_code_folder_ids] Start - count={len(source_code_ids) if source_code_ids else 0}, folder_id={folder_id}"
        )

        if not source_code_ids:
            logger.warning(
                "[batch_update_source_code_folder_ids] Empty source_code_ids list"
            )
            return

        try:
            modified_count = await self.repository.batch_update_source_code_folder_ids(
                source_code_ids=source_code_ids, folder_id=folder_id
            )

            logger.info(
                f"[batch_update_source_code_folder_ids] Updated {modified_count} documents"
            )

        except Exception as e:
            error_message = f"[batch_update_source_code_folder_ids] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_git_source_code(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """Push source code files to Git repository"""
        logger.info(
            f"[push_git_source_code] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning("[push_git_source_code] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user access
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning("[push_git_source_code] User lacks project access")
                raise_http_error(status.HTTP_403_FORBIDDEN)

            # Get project to retrieve Git info and directory
            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_source_code] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get Git repository info
            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_source_code] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            # Get directory structure
            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.src:
                logger.warning(
                    "[push_git_source_code] Project does not have SRC directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="SRC_DIRECTORY_NOT_CONFIGURED",
                )

            target_directory = directory.src.strip("/")

            # Note: When src is "/", target_directory will be "" (root)
            # Files will be pushed to root, which is handled correctly by push_files_to_git

            # Get documents by IDs
            documents = await self.repository.get_sources_by_ids(project_id, file_ids)
            if not documents:
                logger.warning(
                    "[push_git_source_code] No documents found for provided file_ids"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_FILES_TO_PUSH"
                )

            # Filter documents with valid sync_status
            valid_sync_statuses = [
                SyncStatus.PUSH,
                SyncStatus.PULL_PUSH,
                SyncStatus.DELETE_PUSH,
                "",
                None,
            ]
            filtered_documents = [
                doc
                for doc in documents
                if doc.get("sync_status", "") in valid_sync_statuses
            ]

            if not filtered_documents:
                logger.warning(
                    "[push_git_source_code] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            # Build files to push and delete
            files_to_push = []
            files_to_delete = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status")
                logger.info(
                    f"[push_git_source_code] Processing doc - sync_status={sync_status}, file_path={doc.get('file_path')}, file_name={doc.get('file_name')}"
                )
                if sync_status == SyncStatus.DELETE_PUSH:
                    file_path = doc.get("file_path") or doc.get("file_name")
                    if file_path:
                        # Strip target_directory prefix from file_path if present
                        normalized_file_path = file_path.replace("\\", "/").strip("/")
                        target_dir_normalized = target_directory.strip("/")
                        if normalized_file_path.startswith(target_dir_normalized + "/"):
                            relative_path = normalized_file_path[
                                len(target_dir_normalized) + 1 :
                            ]
                        else:
                            relative_path = normalized_file_path
                        files_to_delete.append(relative_path)
                    continue

                # Build git payload - source code uses "source_code" field, not "content"
                file_path = doc.get("file_path") or doc.get("file_name")
                content = doc.get("source_code") or doc.get("content", "")
                if file_path and content:
                    # Strip target_directory prefix from file_path if present
                    # file_path might be full path like "SRC/config/config.json"
                    # but target_directory is already "SRC", so we need relative path
                    normalized_file_path = file_path.replace("\\", "/").strip("/")
                    target_dir_normalized = target_directory.strip("/")
                    if normalized_file_path.startswith(target_dir_normalized + "/"):
                        # Remove target_directory prefix
                        relative_path = normalized_file_path[
                            len(target_dir_normalized) + 1 :
                        ]
                    else:
                        # Use file_path as is (might be relative already)
                        relative_path = normalized_file_path

                    # Convert content to bytes if it's a string
                    if isinstance(content, str):
                        content_bytes = content.encode("utf-8")
                    else:
                        content_bytes = content
                    files_to_push.append(
                        {"file_name": relative_path, "content": content_bytes}
                    )
                else:
                    logger.warning(
                        f"[push_git_source_code] Skipping file - file_path={file_path}, has_content={bool(content)}"
                    )

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_source_code] No valid files to push or delete after processing"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            # Check for conflicts before pushing (only for files that have commit_id)
            # Files with sync_status = None are new files, no need to check conflicts
            collection_name = getattr(self.repository, "collection_name", None)
            files_with_commit_id = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status")
                # Skip conflict check for files with sync_status = None (new files)
                if sync_status is None:
                    logger.info(
                        f"[push_git_source_code] Skipping conflict check for new file (sync_status=None) - file_path={doc.get('file_path')}"
                    )
                    continue

                if (
                    doc.get("commit_id")
                    and doc.get("commit_id").strip()
                    and doc.get("commit_id") != "null"
                    and (sync_status or "") != SyncStatus.DELETE_PUSH
                ):
                    file_path = doc.get("file_path") or doc.get("file_name")
                    files_with_commit_id.append(
                        {
                            "file_name": doc.get("file_name", ""),
                            "file_path": file_path,
                            "local_commit_id": doc.get("commit_id") or "",
                            "file_id": doc.get("id"),
                            "project_id": project_id,
                            "collection_name": collection_name,
                        }
                    )

            if files_with_commit_id:
                conflict_files = await check_conflicts_before_push(
                    repository_url=repository_url,
                    branch_name=branch_name,
                    user_name=user_name,
                    token_password=token_password,
                    files_with_commit_id=files_with_commit_id,
                    target_directory=target_directory,
                )

                if conflict_files:
                    logger.warning(
                        f"[push_git_source_code] Conflicts detected - count={len(conflict_files)}"
                    )
                    conflict_response = ConflictResponse(
                        statusCode=409,
                        message="Conflict detected. Some files could not be merged automatically.",
                        conflict_files=[
                            ConflictFileInfo(**cf) for cf in conflict_files
                        ],
                    )
                    raise HTTPException(
                        status_code=409,
                        detail=conflict_response.dict(),
                    )

            # Push files to Git
            push_result = await push_files_to_git(
                repository_url=repository_url,
                branch_name=branch_name,
                user_name=user_name,
                token_password=token_password,
                files=files_to_push,
                commit_message=commit_message,
                target_directory=target_directory,
                files_to_delete=files_to_delete if files_to_delete else None,
            )

            if isinstance(push_result, dict):
                commit_id = push_result.get("commit_id")
            else:
                commit_id = push_result

            # Update sync_status and commit_id for pushed files
            current_time = get_current_utc_time()
            pushed_file_ids = [doc.get("id") for doc in filtered_documents]

            # Separate files that were delete_push
            delete_push_file_ids = []
            for doc in filtered_documents:
                if doc.get("sync_status") == SyncStatus.DELETE_PUSH:
                    doc_id = doc.get("id")
                    if doc_id:
                        delete_push_file_ids.append(doc_id)

            other_file_ids = [
                doc_id
                for doc_id in pushed_file_ids
                if doc_id not in delete_push_file_ids
            ]

            # Update delete_push files
            if delete_push_file_ids:
                await self.repository.update_documents(
                    document_ids=delete_push_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                        "deleted_at": current_time,
                    },
                )

            # Update other files
            if other_file_ids:
                await self.repository.update_documents(
                    document_ids=other_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                    },
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_source_code] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

            # Update project commit_ids
            try:
                await self.project_service.add_commit_id_to_project(
                    project=project,
                    new_commit_id=push_result,
                    sync_status=(
                        project.setting_item.git.sync_status
                        if project.setting_item and project.setting_item.git
                        else None
                    ),
                )
            except Exception as e:
                error_message = (
                    f"[push_git_source_code] Failed to update project commit_ids: {e}"
                )
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

            return {
                "commit_id": commit_id,
                "pushed_count": total_updated,
                "file_ids": pushed_file_ids,
            }

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[push_git_source_code] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_all_source_code_mock_to_git(
        self,
        project_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Push all source code files from app directory to Git repository in src directory"""
        logger.info(
            f"[push_all_source_code_mock_to_git] Start - project_id={project_id}, user_id={user_id}"
        )

        if not project_id or not user_id:
            logger.warning(
                "[push_all_source_code_mock_to_git] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Get project to retrieve Git info and credentials
            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_all_source_code_mock_to_git] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get Git repository info
            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_all_source_code_mock_to_git] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            # Get directory structure
            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.src:
                logger.warning(
                    "[push_all_source_code_mock_to_git] Project does not have SRC directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="SRC_DIRECTORY_NOT_CONFIGURED",
                )

            target_directory = directory.src.strip("/")

            # Get Git credentials from ai_programming in setting_item
            ai_prog = (
                project.setting_item.ai_programming if project.setting_item else None
            )
            if not ai_prog:
                logger.warning(
                    "[push_all_source_code_mock_to_git] Project does not have ai_programming configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="AI_PROGRAMMING_NOT_CONFIGURED",
                )

            user_name = getattr(ai_prog, "user_name", None)
            encrypted_token = getattr(ai_prog, "token", None)

            # Decrypt token before using
            token_password = (
                decrypt_ai_programming_credential(encrypted_token)
                if encrypted_token
                else None
            )

            if not user_name:
                logger.warning(
                    "[push_all_source_code_mock_to_git] user_name not found in ai_programming"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="GIT_USER_NAME_NOT_CONFIGURED",
                )

            if not token_password:
                logger.warning(
                    "[push_all_source_code_mock_to_git] token not found in ai_programming"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="GIT_TOKEN_NOT_CONFIGURED",
                )

            # Get app directory path (relative to project root)
            BASE_DIR = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..")
            )
            app_dir = os.path.join(BASE_DIR, "app")

            if not os.path.exists(app_dir) or not os.path.isdir(app_dir):
                logger.warning(
                    f"[push_all_source_code_mock_to_git] App directory not found: {app_dir}"
                )
                raise_http_error(
                    status.HTTP_404_NOT_FOUND, error_key="APP_DIRECTORY_NOT_FOUND"
                )

            # Read all files from app directory
            files_to_push = []
            for root, dirs, filenames in os.walk(app_dir):
                # Skip __pycache__ and .pyc files
                dirs[:] = [d for d in dirs if d != "__pycache__"]
                filenames = [f for f in filenames if not f.endswith(".pyc")]

                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    # Get relative path from app directory
                    relative_path = os.path.relpath(file_path, app_dir).replace(
                        "\\", "/"
                    )

                    # Read file content
                    try:
                        with open(file_path, "rb") as f:
                            content = f.read()
                        files_to_push.append(
                            {"file_name": relative_path, "content": content}
                        )
                        logger.debug(
                            f"[push_all_source_code_mock_to_git] Found file: {relative_path}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[push_all_source_code_mock_to_git] Failed to read file: {file_path}, error: {e}"
                        )
                        continue

            if not files_to_push:
                logger.warning(
                    "[push_all_source_code_mock_to_git] No files found in app directory"
                )
                return {
                    "commit_id": None,
                    "pushed_count": 0,
                    "file_ids": [],
                    "message": "No files found in app directory",
                }

            logger.info(
                f"[push_all_source_code_mock_to_git] Found {len(files_to_push)} files to push - project_id={project_id}"
            )

            # Create commit message
            commit_message = f"Automatically push all mock data folder app source code files after generate source code"

            # Push files to git using push_files_to_git
            from app.services.git_services.git_push import push_files_to_git

            commit_id = await push_files_to_git(
                repository_url=repository_url,
                branch_name=branch_name,
                user_name=user_name,
                token_password=token_password,
                files=files_to_push,
                commit_message=commit_message,
                target_directory=target_directory,
            )

            logger.info(
                f"[push_all_source_code_mock_to_git] Success - commit_id={commit_id}, pushed_count={len(files_to_push)}"
            )

            return {
                "commit_id": commit_id,
                "pushed_count": len(files_to_push),
                "file_ids": [],
                "message": f"Successfully pushed {len(files_to_push)} files from app directory",
            }

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[push_all_source_code_mock_to_git] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # #endregion

    # #region Private Helper Methods - Code Parsing

    def _detect_language(self, file_name: str) -> str:
        """Detect programming language from file extension"""
        if not file_name:
            return "Java"  # Default

        file_name_lower = file_name.lower()
        if file_name_lower.endswith(".java"):
            return "Java"
        elif file_name_lower.endswith(".cs"):
            return "Csharp"
        elif file_name_lower.endswith(".py"):
            return "Python"
        else:
            return "Java"  # Default fallback

    def _get_file_extension(self, language: str) -> str:
        """Get file extension for given language"""
        match language.lower():
            case "java":
                return "java"
            case "csharp" | "c#":
                return "cs"
            case "python":
                return "py"
            case _:
                return "java"  # Default

    def parse_source_code(self, content: str, language: str = "Java") -> Dict[str, Any]:
        """
        Parse source code to extract classes and global methods

        Args:
            content: Source code content
            language: Programming language (Java, Csharp, Python)

        Returns:
            Dict with 'classes' and 'global_methods' lists
        """
        logger.info(
            f"[_parse_source_code] Start - language={language}, content_length={len(content)}"
        )

        try:
            if not content or not content.strip():
                logger.warning("[_parse_source_code] Empty content provided")
                return {"classes": [], "global_methods": []}

            # Import FileData for parsing
            from app.services.data_structures.file_data import FileData

            # Create a temporary file path based on language
            temp_file_path = f"temp_file.{self._get_file_extension(language)}"

            # Parse code using FileData
            file_data = FileData(temp_file_path, content)
            code_functions = file_data.get_code_functions_dict()
            code_classes = file_data.get_code_classes_dict()

            # Group methods by class
            methods_by_class: Dict[str, List[Dict[str, Any]]] = {}
            for func in code_functions:
                class_id = func.get("class_id") or "root"
                if class_id not in methods_by_class:
                    methods_by_class[class_id] = []
                methods_by_class[class_id].append(
                    {
                        "method_id": func["id"],
                        "method_name": func["name"],
                        "method_content": func["code"],
                    }
                )

            # Build classes list
            parsed_classes = []
            for cls in code_classes:
                class_id = cls["id"]
                parsed_classes.append(
                    {
                        "class_id": class_id,
                        "class_name": cls["name"],
                        "class_content": cls["code"],
                        "methods": methods_by_class.get(class_id, []),
                    }
                )

            # Build global methods list
            parsed_methods = methods_by_class.get("root", [])

            logger.info(
                f"[_parse_source_code] Success - classes={len(parsed_classes)}, methods={len(parsed_methods)}"
            )

            return {
                "classes": parsed_classes,
                "global_methods": parsed_methods,
            }

        except Exception as e:
            error_message = f"[_parse_source_code] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            # Return empty structure on parse error
            return {"classes": [], "global_methods": []}

    # #endregion


source_code_service = SourceCodeService()
