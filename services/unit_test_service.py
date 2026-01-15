"""
Unit Test Service
Handles unit test design (UTD) and unit test code (UTC) operations
"""

import json
import logging
from collections import defaultdict
from typing import Dict, List, Any, Optional, Tuple
from fastapi import HTTPException, status
from dataclasses import asdict

from app.services.activity_log_service import activity_log_service
from app.services.download_service import download_service
from app.services.project_service import ProjectService
from app.services.git_services.git_push import (
    push_files_to_git,
    check_conflicts_before_push,
)
from app.services.git_services.git_service import normalize_target_directory
from app.utils.constants import PermissionLevel
from app.utils.helpers import get_current_utc_time
from app.utils.http_helpers import raise_http_error
from app.utils.constants import SyncStatus
from app.schemas.base import ConflictResponse, ConflictFileInfo
from app.schemas.unit_test import (
    UNIT_TEST_COLLECTION_MAP,
    UNIT_TEST_DESIGN_COLLECTION,
    UNIT_TEST_CODE_COLLECTION,
)

from app.services.logs_service import save_exception_log_sync, LogLevel
from app.services.code_analysis.decision_table_generator import (
    generate_decision_table_and_test_pattern,
)

logger = logging.getLogger(__name__)

# Constants
FOLDER_TYPE_MAP = {
    "utc": "utc",
    "utd": "utd",
}


class UnitTestService:
    """Service for unit test management operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None, source_code_repository=None):
        if cls._instance is None:
            cls._instance = super(UnitTestService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None, source_code_repository=None):
        if not UnitTestService._initialized:
            self.project_service = ProjectService()
            UnitTestService._initialized = True
            logger.info("[UnitTestService] Singleton instance initialized")

        # Always set repositories if provided (allows updating after initialization)
        if repository is not None:
            self.unit_test_repository = repository
        if source_code_repository is not None:
            self.repository = source_code_repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.unit_test_repository = repository
        logger.info("[UnitTestService] Repository updated via set_repository")

    # #region Public Methods

    async def get_project_unit_test_data(
        self, project_id: str, unit_test_type: str
    ) -> Dict[str, Any]:
        """Get folders + unit test documents from unit_test collection"""
        logger.info(
            "[get_project_unit_test_data] Start - project_id=%s, unit_test_type=%s",
            project_id,
            unit_test_type,
        )

        if not project_id or not unit_test_type:
            logger.warning(
                "[get_project_unit_test_data] Missing project_id or unit_test_type"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if unit_test_type not in UNIT_TEST_COLLECTION_MAP:
            logger.warning(
                "[get_project_unit_test_data] Unsupported unit_test_type=%s",
                unit_test_type,
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            folder_type = self._resolve_folder_type(unit_test_type)
            folders = await self.repository.get_folders(project_id, folder_type)
            collection_name = UNIT_TEST_COLLECTION_MAP[unit_test_type]
            raw_documents = await self.repository.get_unit_tests_by_collection(
                project_id=project_id, collection_name=collection_name
            )
            mapped_documents = [
                self._map_unit_test_tree_fields(doc) for doc in raw_documents
            ]
            payload = self._compose_project_payload(
                data_type=unit_test_type, folders=folders, items=mapped_documents
            )
            logger.info(
                "[get_project_unit_test_data] Success - folder_count=%s, item_count=%s",
                len(folders),
                len(mapped_documents),
            )
            return payload
        except ValueError as e:
            error_message = "[get_project_unit_test_data] Validation error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise
        except Exception as e:
            error_message = "[get_project_unit_test_data] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_active_document_count_by_project_id(
        self, project_id: str, collection_name: str
    ) -> int:
        """Get count of active (non-deleted) unit tests by project_id"""
        if not project_id:
            return 0

        try:
            filter_query = {
                "project_id": project_id,
                "deleted_at": None,
                "collection_name": collection_name,
            }
            return await self.unit_test_repository.count_documents(filter_query)
        except Exception as e:
            logger.error(f"[get_active_document_count_by_project_id] Error: {e}")
            return 0

    async def get_unit_test_design_detail(
        self, project_id: str, file_id: str, user_id: str, user_role: str
    ) -> Dict[str, Any]:
        """Get source file with associated unit test design files"""
        logger.info(
            "[get_unit_test_design_detail] Start - project_id=%s, file_id=%s, user_id=%s",
            project_id,
            file_id,
            user_id,
        )

        if not project_id or not file_id or not user_id:
            logger.warning("[get_unit_test_design_detail] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }

            unit_test_document = await self.unit_test_repository.find_one(query_params)
            if not unit_test_document:
                logger.warning(
                    f"[get_unit_test_design_detail] Unit test document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            source_code_id = unit_test_document.get("source_code_id")

            source_document = await self.repository.get_source_by_id(
                project_id=project_id, file_id=source_code_id
            )

            unit_test_designs = (
                await self.unit_test_repository.get_unit_test_by_source_code_id(
                    project_id=project_id,
                    source_code_id=source_code_id,
                    collection_name=UNIT_TEST_DESIGN_COLLECTION,
                )
            )

            # Map unit test design fields (async)
            unit_test_designs_mapped = []
            for document in unit_test_designs:
                mapped_doc = await self._map_unit_test_design_fields(document)
                unit_test_designs_mapped.append(mapped_doc)

            response_payload = {
                "file": self._compose_source_file_summary(source_document),
                "unit_test_designs": unit_test_designs_mapped,
            }

            logger.info(
                "[get_unit_test_design_detail] Success - project_id=%s, file_id=%s, design_count=%s",
                project_id,
                file_id,
                len(response_payload.get("unit_test_designs", [])),
            )
            return response_payload
        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[get_unit_test_design_detail] Error - project_id=%s, file_id=%s: %s"
                % (
                    project_id,
                    file_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_unit_test_code_detail(
        self, project_id: str, file_id: str, user_id: str, user_role: str
    ) -> Dict[str, Any]:
        """Get source file with associated unit test code files"""
        logger.info(
            "[get_unit_test_code_detail] Start - project_id=%s, file_id=%s, user_id=%s",
            project_id,
            file_id,
            user_id,
        )

        if not project_id or not file_id or not user_id:
            logger.warning("[get_unit_test_code_detail] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }

            unit_test_document = await self.unit_test_repository.find_one(query_params)
            if not unit_test_document:
                logger.warning(
                    f"[get_unit_test_design_detail] Unit test document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            source_code_id = unit_test_document.get("source_code_id")

            source_document = await self.repository.get_source_by_id(
                project_id=project_id, file_id=source_code_id
            )

            unit_test_codes = (
                await self.unit_test_repository.get_unit_test_by_source_code_id(
                    project_id=project_id,
                    source_code_id=source_code_id,
                    collection_name=UNIT_TEST_CODE_COLLECTION,
                )
            )

            response_payload = {
                "file": self._compose_source_file_summary(source_document),
                "unit_test_codes": [
                    self._map_unit_test_code_fields(document)
                    for document in unit_test_codes
                ],
            }

            logger.info(
                "[get_unit_test_code_detail] Success - project_id=%s, file_id=%s, code_count=%s",
                project_id,
                file_id,
                len(response_payload.get("unit_test_codes", [])),
            )
            return response_payload
        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[get_unit_test_code_detail] Error - project_id=%s, file_id=%s: %s"
                % (
                    project_id,
                    file_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def download_unit_test_documents(
        self,
        project_id: str,
        file_ids: Optional[List[str]],
        folder_ids: Optional[List[str]],
        user_id: str,
        user_role: str,
        unit_test_type: str,
    ) -> Dict[str, Any]:
        logger.info(
            "[download_unit_test_documents] Start - project_id=%s, unit_test_type=%s",
            project_id,
            unit_test_type,
        )

        if not project_id or not user_id or not unit_test_type:
            logger.warning("[download_unit_test_documents] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if unit_test_type not in UNIT_TEST_COLLECTION_MAP:
            logger.warning(
                "[download_unit_test_documents] Unsupported unit_test_type=%s",
                unit_test_type,
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            result = await self._download_documents(
                project_id=project_id,
                file_ids=file_ids,
                folder_ids=folder_ids,
                user_id=user_id,
                user_role=user_role,
                data_type=unit_test_type,
            )
            logger.info(
                "[download_unit_test_documents] Success - project_id=%s, unit_test_type=%s",
                project_id,
                unit_test_type,
            )
            return result
        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[download_unit_test_documents] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_unit_test_design_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
        user_role: str = PermissionLevel.USER,
    ) -> Dict[str, Any]:
        """Update unit test design content with sync_status logic"""
        logger.info(
            f"[update_unit_test_design_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning(
                "[update_unit_test_design_content] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.unit_test_repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[update_unit_test_design_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            current_sync_status = document.get("sync_status") or ""

            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            current_time = get_current_utc_time()
            update_data = {
                "unit_test_design_json": content,
                "sync_status": new_sync_status,
                "updated_at": current_time,
            }

            is_updated = await self.unit_test_repository.update_document(
                document_id=file_id,
                project_id=project_id,
                update_data=update_data,
            )

            if not is_updated:
                logger.warning(
                    f"[update_unit_test_design_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            logger.info(
                f"[update_unit_test_design_content] Success - file_id={file_id}, sync_status={new_sync_status}"
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
            error_message = f"[update_unit_test_design_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_unit_test_code_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
        user_role: str = PermissionLevel.USER,
    ) -> Dict[str, Any]:
        """Update unit test code content with sync_status logic"""
        logger.info(
            f"[update_unit_test_code_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning(
                "[update_unit_test_code_content] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.unit_test_repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[update_unit_test_code_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            current_sync_status = document.get("sync_status") or ""

            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            current_time = get_current_utc_time()
            update_data = {
                "ut_code_content": content,
                "sync_status": new_sync_status,
                "updated_at": current_time,
            }

            is_updated = await self.unit_test_repository.update_document(
                document_id=file_id,
                project_id=project_id,
                update_data=update_data,
            )

            if not is_updated:
                logger.warning(
                    f"[update_unit_test_code_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            logger.info(
                f"[update_unit_test_code_content] Success - file_id={file_id}, sync_status={new_sync_status}"
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
            error_message = f"[update_unit_test_code_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_git_unit_test_spec(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """Push unit test spec files to Git repository"""
        logger.info(
            f"[push_git_unit_test_spec] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning("[push_git_unit_test_spec] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning("[push_git_unit_test_spec] User lacks project access")
                raise_http_error(status.HTTP_403_FORBIDDEN)

            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_unit_test_spec] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_unit_test_spec] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.utd:
                logger.warning(
                    "[push_git_unit_test_spec] Project does not have UTD directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="UTD_DIRECTORY_NOT_CONFIGURED",
                )

            target_directory = directory.utd.strip("/")

            # Normalize target_directory when src is "/"
            src_path = directory.src if directory else None
            target_directory = normalize_target_directory(target_directory, src_path)

            documents = await self.unit_test_repository.get_unit_test_designs_by_ids(
                project_id, file_ids
            )
            if not documents:
                logger.warning(
                    "[push_git_unit_test_spec] No documents found for provided file_ids"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_FILES_TO_PUSH"
                )

            valid_sync_statuses = [
                SyncStatus.PUSH,
                SyncStatus.PULL_PUSH,
                SyncStatus.DELETE_PUSH,
                "",
            ]
            filtered_documents = [
                doc
                for doc in documents
                if (doc.get("sync_status") or "") in valid_sync_statuses
            ]

            if not filtered_documents:
                logger.warning(
                    "[push_git_unit_test_spec] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            files_to_push = []
            files_to_delete = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status")
                logger.info(
                    f"[push_git_unit_test_spec] Processing doc - sync_status={sync_status}, file_path={doc.get('file_path')}, file_name={doc.get('file_name')}"
                )
                if sync_status == SyncStatus.DELETE_PUSH:
                    file_path = doc.get("file_path") or doc.get("file_name")
                    if file_path:
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

                file_path = doc.get("file_path") or doc.get("file_name")
                content = doc.get("unit_test_design_json") or doc.get("content", "")
                if file_path and content:
                    normalized_file_path = file_path.replace("\\", "/").strip("/")
                    target_dir_normalized = target_directory.strip("/")
                    if normalized_file_path.startswith(target_dir_normalized + "/"):
                        relative_path = normalized_file_path[
                            len(target_dir_normalized) + 1 :
                        ]
                    else:
                        relative_path = normalized_file_path

                    if isinstance(content, str):
                        content_bytes = content.encode("utf-8")
                    else:
                        content_bytes = content
                    files_to_push.append(
                        {"file_name": relative_path, "content": content_bytes}
                    )
                else:
                    logger.warning(
                        f"[push_git_unit_test_spec] Skipping file - file_path={file_path}, has_content={bool(content)}"
                    )

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_unit_test_spec] No valid files to push or delete"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            collection_name = UNIT_TEST_DESIGN_COLLECTION
            files_with_commit_id = []
            for doc in filtered_documents:
                if (
                    doc.get("commit_id")
                    and doc.get("commit_id").strip()
                    and doc.get("commit_id") != "null"
                    and (doc.get("sync_status") or "") != SyncStatus.DELETE_PUSH
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
                        f"[push_git_unit_test_spec] Conflicts detected - count={len(conflict_files)}"
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

            current_time = get_current_utc_time()
            pushed_file_ids = [doc.get("id") for doc in filtered_documents]

            delete_push_file_ids = [
                doc.get("id")
                for doc in filtered_documents
                if doc.get("sync_status") == SyncStatus.DELETE_PUSH
            ]
            other_file_ids = [
                doc_id
                for doc_id in pushed_file_ids
                if doc_id not in delete_push_file_ids
            ]

            if delete_push_file_ids:
                await self.unit_test_repository.update_documents(
                    document_ids=delete_push_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                        "deleted_at": current_time,
                    },
                    filter_additional={"collection_name": UNIT_TEST_DESIGN_COLLECTION},
                )

            if other_file_ids:
                await self.unit_test_repository.update_documents(
                    document_ids=other_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                    },
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_unit_test_spec] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

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
                error_message = f"[push_git_unit_test_spec] Failed to update project commit_ids: {e}"
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
            error_message = f"[push_git_unit_test_spec] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_git_unit_test_code(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """Push unit test code files to Git repository"""
        logger.info(
            f"[push_git_unit_test_code] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning("[push_git_unit_test_code] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning("[push_git_unit_test_code] User lacks project access")
                raise_http_error(status.HTTP_403_FORBIDDEN)

            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_unit_test_code] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_unit_test_code] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.utc:
                logger.warning(
                    "[push_git_unit_test_code] Project does not have UTC directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="UTC_DIRECTORY_NOT_CONFIGURED",
                )

            target_directory = directory.utc.strip("/")

            # Normalize target_directory when src is "/"
            src_path = directory.src if directory else None
            target_directory = normalize_target_directory(target_directory, src_path)

            documents = await self.unit_test_repository.get_unit_test_codes_by_ids(
                project_id, file_ids
            )
            if not documents:
                logger.warning(
                    "[push_git_unit_test_code] No documents found for provided file_ids"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_FILES_TO_PUSH"
                )

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
                    "[push_git_unit_test_code] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            files_to_push = []
            files_to_delete = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status")
                logger.info(
                    f"[push_git_unit_test_code] Processing doc - sync_status={sync_status}, file_path={doc.get('file_path')}, file_name={doc.get('file_name')}"
                )
                if sync_status == SyncStatus.DELETE_PUSH:
                    file_path = doc.get("file_path") or doc.get("file_name")
                    if file_path:
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

                file_path = doc.get("file_path") or doc.get("file_name")
                content = doc.get("ut_code_content") or doc.get("content", "")
                if file_path and content:
                    normalized_file_path = file_path.replace("\\", "/").strip("/")
                    target_dir_normalized = target_directory.strip("/")
                    if normalized_file_path.startswith(target_dir_normalized + "/"):
                        relative_path = normalized_file_path[
                            len(target_dir_normalized) + 1 :
                        ]
                    else:
                        relative_path = normalized_file_path

                    if isinstance(content, str):
                        content_bytes = content.encode("utf-8")
                    else:
                        content_bytes = content
                    files_to_push.append(
                        {"file_name": relative_path, "content": content_bytes}
                    )
                else:
                    logger.warning(
                        f"[push_git_unit_test_code] Skipping file - file_path={file_path}, has_content={bool(content)}"
                    )

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_unit_test_code] No valid files to push or delete"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            collection_name = UNIT_TEST_CODE_COLLECTION
            files_with_commit_id = []
            for doc in filtered_documents:
                if (
                    doc.get("commit_id")
                    and doc.get("commit_id").strip()
                    and doc.get("commit_id") != "null"
                    and (doc.get("sync_status") or "") != SyncStatus.DELETE_PUSH
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
                        f"[push_git_unit_test_code] Conflicts detected - count={len(conflict_files)}"
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

            current_time = get_current_utc_time()
            pushed_file_ids = [doc.get("id") for doc in filtered_documents]

            delete_push_file_ids = [
                doc.get("id")
                for doc in filtered_documents
                if doc.get("sync_status") == SyncStatus.DELETE_PUSH
            ]
            other_file_ids = [
                doc_id
                for doc_id in pushed_file_ids
                if doc_id not in delete_push_file_ids
            ]

            if delete_push_file_ids:
                await self.unit_test_repository.update_documents(
                    document_ids=delete_push_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                        "deleted_at": current_time,
                    },
                    filter_additional={"collection_name": UNIT_TEST_CODE_COLLECTION},
                )

            if other_file_ids:
                await self.unit_test_repository.update_documents(
                    document_ids=other_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                    },
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_unit_test_code] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

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
                error_message = f"[push_git_unit_test_code] Failed to update project commit_ids: {e}"
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
            error_message = f"[push_git_unit_test_code] Error: {e}"
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
            if data_type == "utd":
                key = "utd_list"
            elif data_type == "utc":
                key = "utc_list"
            else:
                key = "utd_list"
            logger.info("[_resolve_payload_key] Success - key=%s", key)
            return key
        except Exception as e:
            error_message = "[_resolve_payload_key] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_unit_test_tree_fields(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize unit test document for tree listing"""
        # logger.info(
        #     "[_map_unit_test_tree_fields] Start - document_keys=%s",
        #     list(document.keys()) if document else None,
        # )
        if not document:
            logger.warning("[_map_unit_test_tree_fields] Missing document input")
            return {}

        try:
            resolved_id = (
                document.get("id")
                or document.get("_id")
                or document.get("unit_test_id")
                or ""
            )
            mapped = {
                "id": resolved_id,
                "project_id": document.get("project_id", ""),
                "file_name": document.get("file_name"),
                "folder_id": document.get("folder_id"),
                "class_id": document.get("class_id"),
                "method_id": document.get("method_id"),
                "commit_id": document.get("commit_id"),
                "sync_status": document.get("sync_status"),
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
            }
            logger.info(
                "[_map_unit_test_tree_fields] Success - unit_test_id=%s", resolved_id
            )
            return mapped
        except Exception as e:
            error_message = "[_map_unit_test_tree_fields] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _resolve_folder_type(self, data_type: str) -> str:
        """Determine which folder tree to use for requested data type"""
        logger.info(
            "[_resolve_folder_type] Start - data_type=%s",
            data_type,
        )

        if not data_type:
            logger.warning("[_resolve_folder_type] Missing data_type input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            resolved_type = FOLDER_TYPE_MAP.get(data_type, data_type)
            logger.info(
                "[_resolve_folder_type] Success - resolved_type=%s", resolved_type
            )
            return resolved_type
        except Exception as e:
            error_message = "[_resolve_folder_type] Error: %s" % (e,)
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

    async def update_unit_test(
        self,
        unit_test_id: str,
        project_id: str,
        decision_table: str,
        test_pattern: str,
    ) -> bool:
        if not unit_test_id or not decision_table or not test_pattern:
            logger.warning(
                "[_update_decision_table_and_test_pattern] Missing required parameters"
            )
            return False

        try:
            logger.info(
                f"[_update_decision_table_and_test_pattern] Start - unit_test_id={unit_test_id}"
            )

            current_time = get_current_utc_time()
            update_data = {
                "decision_table": decision_table,
                "test_pattern": test_pattern,
                "updated_at": current_time,
            }

            is_updated = await self.unit_test_repository.update_document(
                document_id=unit_test_id,
                project_id=project_id,
                update_data=update_data,
            )

            if is_updated:
                logger.info(
                    f"[_update_decision_table_and_test_pattern] Success - unit_test_id={unit_test_id}"
                )
            else:
                logger.warning(
                    f"[_update_decision_table_and_test_pattern] Failed to update - unit_test_id={unit_test_id}"
                )

            return is_updated
        except Exception as e:
            error_message = f"[_update_decision_table_and_test_pattern] Error - unit_test_id={unit_test_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            return False

    async def _map_unit_test_design_fields(
        self, document: Dict[str, Any]
    ) -> Dict[str, Any]:
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

            unit_test_design_json = document.get("unit_test_design_json")

            # Check if decision_table exists, if not generate it
            decision_table = document.get("decision_table")
            test_pattern = document.get("test_pattern")

            if not decision_table and unit_test_design_json:
                logger.info(
                    "[_map_unit_test_design_fields] decision_table not found, generating from unit_test_design_json"
                )
                decision_table, test_pattern = (
                    await generate_decision_table_and_test_pattern(
                        unit_test_design_json, document.get("project_id"), unit_test_id
                    )
                )

            mapped = {
                "unit_test_id": unit_test_id,
                "project_id": document.get("project_id", ""),
                "file_name": document.get("file_name"),
                "source_code_id": document.get("source_code_id", ""),
                "class_id": document.get("class_id"),
                "method_id": document.get("method_id"),
                "unit_test_design_json": unit_test_design_json,
                "decision_table": decision_table,
                "test_pattern": test_pattern,
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
        is_unit_test_type = data_type in UNIT_TEST_COLLECTION_MAP
        unit_test_collection_name = (
            UNIT_TEST_COLLECTION_MAP.get(data_type) if is_unit_test_type else None
        )

        if file_ids:
            if is_unit_test_type and unit_test_collection_name:
                files_by_ids = await self.repository.fetch_unit_test_files_by_ids(
                    project_id=project_id,
                    file_ids=file_ids,
                    collection_name=unit_test_collection_name,
                )
            else:
                files_by_ids = await self.repository.fetch_source_files_by_ids(
                    project_id=project_id, file_ids=file_ids
                )
            all_documents.extend(files_by_ids)
            logger.debug(
                "[_download_documents] Retrieved files by IDs - "
                f"count={len(files_by_ids)}, ids={file_ids}"
            )

        if folder_ids:
            if is_unit_test_type and unit_test_collection_name:
                files_by_folders = (
                    await self.repository.fetch_unit_test_files_by_folders(
                        project_id=project_id,
                        folder_ids=folder_ids,
                        collection_name=unit_test_collection_name,
                        data_type=data_type,
                        limit=50,
                    )
                )
            else:
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

        type_mapping = {
            "utd": ("unit test specification", "utd"),
            "utc": ("unit test code", "utc"),
        }
        file_type, file_suffix = type_mapping.get(data_type, ("unit test", "ut"))

        folder_hierarchy = await self._build_folder_hierarchy(
            project_id=project_id, type=data_type, selected_folder_ids=folder_ids or []
        )

        def extract_with_folder_structure(doc: Dict[str, Any]) -> Tuple[str, bytes]:
            return self._extract_unit_test_content_with_folders(
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

        document_id_resolver = (
            self.repository.extract_unit_test_document_id
            if is_unit_test_type
            else self.repository.extract_source_code_id
        )
        document_normalizer = (
            self.repository.ensure_unit_test_document_id
            if is_unit_test_type
            else self.repository.ensure_source_document_id
        )

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
            error_message = "[_extract_project_name] Error: %s" % e
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
            type: Type of data (utd, utc)
            selected_folder_ids: List of selected folder IDs (root folders)

        Returns:
            Dict with folder_id -> folder_path mapping and folder info
        """
        logger.info(
            f"[_build_folder_hierarchy] Start - project_id={project_id}, type={type}, selected_folder_ids={selected_folder_ids}"
        )

        try:
            all_folders = await self.repository.get_folders(
                project_id=project_id, data_type=type
            )

            if not all_folders:
                logger.warning("[_build_folder_hierarchy] No folders found")
                return {"folder_map": {}, "folder_paths": {}}

            folder_map = {folder.get("folder_id"): folder for folder in all_folders}

            children_map: Dict[str, List[str]] = {}
            for folder in all_folders:
                parent_id = folder.get("parent_folder_id")
                folder_id = folder.get("folder_id")
                if parent_id:
                    if parent_id not in children_map:
                        children_map[parent_id] = []
                    children_map[parent_id].append(folder_id)

            all_selected_folder_ids = set(selected_folder_ids)
            queue = list(selected_folder_ids)
            while queue:
                current_folder_id = queue.pop(0)
                if current_folder_id in children_map:
                    for child_folder_id in children_map[current_folder_id]:
                        if child_folder_id not in all_selected_folder_ids:
                            all_selected_folder_ids.add(child_folder_id)
                            queue.append(child_folder_id)

            root_selected_folders = set(selected_folder_ids)
            for folder_id in selected_folder_ids:
                current_id = folder_id
                while current_id:
                    folder_info = folder_map.get(current_id)
                    if not folder_info:
                        break
                    parent_id = folder_info.get("parent_folder_id")
                    if parent_id and parent_id in selected_folder_ids:
                        root_selected_folders.discard(folder_id)
                        break
                    current_id = parent_id

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

                if folder_id in root_selected_folders or not parent_id:
                    path = folder_name
                else:
                    if parent_id in all_selected_folder_ids:
                        visited.add(folder_id)
                        parent_path = build_path(parent_id, visited)
                        visited.remove(folder_id)

                        if parent_path:
                            path = f"{parent_path}/{folder_name}"
                        else:
                            path = folder_name
                    else:
                        path = folder_name

                folder_paths[folder_id] = path
                return path

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

    def _extract_unit_test_content_with_folders(
        self,
        doc: Dict[str, Any],
        folder_hierarchy: Dict[str, Any],
        data_type: str = "utd",
    ) -> Tuple[str, bytes]:
        """
        Extract file path and content using folder hierarchy

        Args:
            doc: Unit test document
            folder_hierarchy: Folder hierarchy map from _build_folder_hierarchy
            data_type: Type of unit test (utd or utc)

        Returns:
            Tuple of (zip_entry_path, file_data)
        """
        logger.debug("[_extract_unit_test_content_with_folders] Extracting content")

        try:
            file_name = doc.get("file_name", "")
            folder_id = doc.get("folder_id")
            folder_paths = folder_hierarchy.get("folder_paths", {})

            if folder_id and folder_id in folder_paths:
                folder_path = folder_paths[folder_id]
                zip_entry_path = (
                    f"{folder_path}/{file_name}" if folder_path else file_name
                )
            else:
                zip_entry_path = file_name

            if data_type == "utd":
                source_content = doc.get("unit_test_design_json", "")
            elif data_type == "utc":
                source_content = (
                    doc.get("ut_code_content") or doc.get("test_code") or ""
                )
            else:
                source_content = ""
            file_data = source_content.encode("utf-8")

            logger.debug(
                f"[_extract_unit_test_content_with_folders] zip_entry_path={zip_entry_path}, folder_id={folder_id}"
            )
            return zip_entry_path, file_data

        except Exception as e:
            error_message = f"[_extract_unit_test_content_with_folders] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # #endregion


# Global service instance
unit_test_service = UnitTestService()
