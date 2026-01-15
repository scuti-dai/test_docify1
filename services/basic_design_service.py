"""
Basic Design service for managing basic designs in MongoDB
"""

import asyncio
import json
import logging
import base64
from functools import partial
from typing import Dict, Any, Optional, List, Callable
from uuid6 import uuid7
from datetime import datetime
from fastapi import HTTPException, status

from app.utils.constants import ContentType, FileStatus, SyncStatus
from app.utils.helpers import get_current_utc_time
from app.services.project_service import ProjectService
from app.utils.http_helpers import raise_http_error
from app.services.download_service import download_service
from app.services.git_services.git_push import (
    push_files_to_git,
    check_conflicts_before_push,
    fetch_remote_file_content,
)
from app.services.git_services.git_service import normalize_target_directory
from app.schemas.base import ConflictResponse, ConflictFileInfo
from app.utils.download_utils import (
    create_download_activity,
    extract_basic_design_content,
    fetch_referenced_images,
    extract_detail_design_content,
)
from app.utils.constants import PermissionLevel

from app.services.logs_service import save_exception_log_sync, LogLevel

from cec_docifycode_common.models.content_models.basic_design import (
    BasicDesignActivityDiagramData,
    BasicDesignArchitectureDesignData,
    BasicDesignDataBaseDesignData,
    BasicDesignDirectoryStructureData,
    BasicDesignInterfaceDesignData,
    BasicDesignScreenDesignData,
    BasicDesignScreenTransitionDiagramData,
    BasicDesignSequenceDiagramData,
    BasicDesignSystemConfigurationData,
    BasicDesignUseCaseDiagramData,
)
from cec_docifycode_common.models.basic_design import (
    FILE_NAME_BASIC_DESIGN,
    BasicDesignKey,
)
from cec_docifycode_common.models.content_models.utils.displayable_content import (
    DisplayableContentType,
)
from app.services.detail_design_service import detail_design_service

logger = logging.getLogger(__name__)


class BasicDesignService:
    """Service for basic design operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(BasicDesignService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not BasicDesignService._initialized:
            self.project_service = ProjectService()
            BasicDesignService._initialized = True
            logger.info("[BasicDesignService] Singleton instance initialized")

        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[BasicDesignService] Repository updated via set_repository")

    async def create_basic_design_from_file(
        self,
        project_id: str,
        file_name: str,
        file_content: bytes,
        content_type: str,
        file_size: int,
        user_id: str,
        user_role: str = PermissionLevel.USER.value,
    ) -> Dict[str, Any]:
        """Create basic design from uploaded file or update existing one with same file name"""
        logger.info(
            f"[create_basic_design_from_file] Start - project_id={project_id}, file_name={file_name}, user_id={user_id}"
        )

        if not project_id or not file_name or not file_content or not user_id:
            logger.warning(
                "[create_basic_design_from_file] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
            )

            # Create contents object (same format as requirement_documents)
            if content_type == ContentType.MARKDOWN:
                # Markdown: decode to string (preserves newlines)
                markdown_content = file_content.decode("utf-8")
                contents = {"type": ContentType.MARKDOWN, "content": markdown_content}
            else:
                # Image: encode to base64 string (same as requirement_documents)
                encoded_content = base64.b64encode(file_content).decode("utf-8")
                contents = {"type": ContentType.IMAGE, "content": encoded_content}

            current_time = get_current_utc_time()

            # Check if file with same name already exists in this project
            duplicate_file_query = {
                "project_id": project_id,
                "file_name": file_name,
                "deleted_at": None,
            }
            duplicate_file = await self.repository.find_one(duplicate_file_query)

            if duplicate_file:
                # Update existing document content
                logger.info(
                    f"[create_basic_design_from_file] Updating existing document: {duplicate_file.get('id')}"
                )

                # Determine new sync_status based on current sync_status
                current_sync_status = (duplicate_file.get("sync_status") or "").lower()
                new_sync_status = None
                if current_sync_status == SyncStatus.SYNCED:
                    new_sync_status = SyncStatus.PUSH
                elif current_sync_status == SyncStatus.PULL:
                    new_sync_status = SyncStatus.PULL_PUSH
                else:
                    new_sync_status = current_sync_status

                update_data = {
                    "contents": contents,
                    "updated_at": current_time,
                    "sync_status": new_sync_status if new_sync_status else None,
                }

                is_updated = await self.repository.update_document(
                    document_id=duplicate_file["id"],
                    project_id=project_id,
                    update_data=update_data,
                )

                if is_updated:
                    logger.info(
                        f"[create_basic_design_from_file] Document updated successfully: {duplicate_file['id']}"
                    )
                    return {
                        "file_id": duplicate_file["id"],
                        "file_name": file_name,
                        "uploaded_by": user_id,
                        "project_id": project_id,
                        "created_at": duplicate_file["created_at"],
                        "updated_at": current_time,
                    }
                else:
                    raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
            else:
                # Create new document
                logger.info(f"[create_basic_design_from_file] Creating new document")

                # Generate basic design ID
                basic_design_id = str(uuid7())

                # Create basic design document
                basic_design_doc = {
                    "id": basic_design_id,
                    "project_id": project_id,
                    "file_name": file_name,
                    "commit_id": "",
                    "sync_status": "",
                    "rd_info": {
                        "id": "",  # Will be filled by service later
                        "file_name": "",
                    },
                    "contents": contents,
                    "created_at": current_time,
                    "updated_at": current_time,
                    "deleted_at": None,
                }

                # Insert into database
                is_inserted = await self.repository.insert_one(basic_design_doc)

                if is_inserted:
                    logger.info(
                        f"[create_basic_design_from_file] Document created successfully: {basic_design_id}"
                    )
                    return {
                        "file_id": basic_design_id,
                        "file_name": file_name,
                        "uploaded_by": user_id,
                        "project_id": project_id,
                        "created_at": current_time,
                        "updated_at": current_time,
                    }
                else:
                    logger.error(
                        f"[create_basic_design_from_file] Failed to insert document"
                    )
                    raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            error_message = f"[create_basic_design_from_file] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_basic_design_by_project_id(
        self, project_id: str
    ) -> List[Dict[str, Any]]:
        """Get basic designs by project_id from database"""
        logger.info(f"[get_basic_design_by_project_id] Start - project_id={project_id}")

        if not project_id:
            logger.warning("[get_basic_design_by_project_id] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            basic_designs = await self.repository.get_basic_designs_by_project(
                project_id
            )

            logger.info(
                f"[get_basic_design_by_project_id] Found {len(basic_designs)} documents"
            )
            return basic_designs

        except Exception as e:
            error_message = f"[get_basic_design_by_project_id] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

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

    async def get_basic_design_files_list(
        self, project_id: str, user_id: str, user_role: str = PermissionLevel.USER.value
    ) -> List[Dict[str, Any]]:
        """Get list of basic design files for API response"""
        logger.info(f"[get_basic_design_files_list] Start - project_id={project_id}")

        if not project_id or not user_id:
            logger.warning(
                "[get_basic_design_files_list] Missing required input - project_id or user_id"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
            )

            # Get basic designs from database
            basic_designs = await self.get_basic_design_by_project_id(project_id)

            files = []
            for basic_design in basic_designs:
                normalized_doc = self._normalize_basic_design_doc(basic_design)

                # Get contents object
                contents = normalized_doc.get("contents", {})
                if not isinstance(contents, dict):
                    contents = {}

                # Build git object
                commit_id = normalized_doc.get("commit_id", "")
                git = {
                    "commit_id": commit_id,
                    "sync_status": normalized_doc.get("sync_status", ""),
                }

                # Get status: GIT if commit_id has data, LOCAL otherwise
                status = (
                    FileStatus.GIT
                    if commit_id and commit_id.strip()
                    else FileStatus.LOCAL
                )

                files.append(
                    {
                        "id": normalized_doc.get("id"),
                        "contents": contents,
                        "status": status,
                        "git": git,
                        "created_at": normalized_doc.get("created_at"),
                        "updated_at": normalized_doc.get("updated_at"),
                    }
                )

            logger.info(f"[get_basic_design_files_list] Found {len(files)} files")
            return files

        except Exception as e:
            error_message = f"[get_basic_design_files_list] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def convert_basic_design_to_markdown(
        self,
        raw_contents: dict[str, Any],
        key_names: Optional[list[str]] = None,
        skip_render: bool = False,
    ) -> dict[str, str]:
        """
        Convert basic design contents to markdown.
        If key_names is provided, only process those keys.
        """

        if not isinstance(raw_contents, dict):
            return {}

        processed_contents: dict[str, str] = {}

        # helper
        def get_safe_content(data_model: Any) -> str:
            if not hasattr(data_model, "get_displayable_contents"):
                return ""

            displayables = data_model.get_displayable_contents()
            return displayables

        # section processors
        def process_use_case(data: Any) -> str:
            return get_safe_content(BasicDesignUseCaseDiagramData(**data))

        def process_activity(data: Any) -> str:
            return get_safe_content(BasicDesignActivityDiagramData(**data))

        def process_interface(data: Any) -> str:
            return get_safe_content(BasicDesignInterfaceDesignData(**data))

        def process_database(data: Any) -> str:
            return get_safe_content(BasicDesignDataBaseDesignData(**data))

        def process_sequence(data: Any) -> str:
            return get_safe_content(BasicDesignSequenceDiagramData(**data))

        def process_screen(data: Any) -> str:
            return get_safe_content(BasicDesignScreenDesignData(**data))

        def process_directory(data: Any) -> str:
            return get_safe_content(BasicDesignDirectoryStructureData(**data))

        def process_architecture(data: Any) -> str:
            return get_safe_content(BasicDesignArchitectureDesignData(**data))

        def process_system_config(data: Any) -> str:
            return get_safe_content(BasicDesignSystemConfigurationData(**data))

        def process_screen_transition(data: Any) -> str:
            return get_safe_content(BasicDesignScreenTransitionDiagramData(**data))

        section_processors = {
            BasicDesignKey.USE_CASE_DIAGRAM.value: process_use_case,
            BasicDesignKey.ACTIVITY_DIAGRAMS.value: process_activity,
            BasicDesignKey.INTERFACE.value: process_interface,
            BasicDesignKey.DATABASE.value: process_database,
            BasicDesignKey.SEQUENCE_DIAGRAM.value: process_sequence,
            BasicDesignKey.SCREEN.value: process_screen,
            BasicDesignKey.DIRECTORY.value: process_directory,
            BasicDesignKey.ARCHITECTURE.value: process_architecture,
            BasicDesignKey.SYSTEM_CONFIGURATION.value: process_system_config,
            BasicDesignKey.SCREEN_TRANSITION.value: process_screen_transition,
        }

        # determine keys to process
        if key_names:
            keys_to_process = [key for key in key_names if key in section_processors]
        else:
            keys_to_process = list(section_processors.keys())

        for key in keys_to_process:
            processed_contents[key] = detail_design_service.process_design_section(
                key, raw_contents, section_processors[key], skip_render
            )

        return processed_contents

    async def get_basic_design_detail(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        user_role: str,
        key_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get basic design detail with authorization enforcement
        """
        logger.info(
            "[get_basic_design_detail] Start - project_id=%s, file_id=%s, user_id=%s, key_name=%s",
            project_id,
            file_id,
            user_id,
            key_name,
        )

        try:
            # 1. Validate input
            self._validate_detail_inputs(project_id, file_id, user_id)

            # 2. Authorization
            await self._ensure_user_has_project_access(
                project_id=project_id,
                user_id=user_id,
            )

            # 3. Fetch document
            document = await self.repository.find_one(
                {
                    "id": file_id,
                    "project_id": project_id,
                    "deleted_at": None,
                }
            )

            if not document:
                logger.warning(
                    "[get_basic_design_detail] Basic design not found - file_id=%s",
                    file_id,
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            logger.info(
                "[get_basic_design_detail] Document fetched - basic_design_id=%s",
                file_id,
            )

            # 4. Build base response (metadata only)
            response = self._build_basic_design_detail_response(document)

            # 5. Convert contents → markdown (delegated)
            raw_contents = document.get("contents") or {}
            response["contents"] = self.convert_basic_design_to_markdown(
                raw_contents=raw_contents,
                key_names=[key_name],
            )

            logger.info(
                "[get_basic_design_detail] Success - basic_design_id=%s",
                response.get("id"),
            )
            return response

        except HTTPException:
            raise

        except Exception as e:
            error_message = f"[get_basic_design_detail] Error - file_id={file_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            raise

    async def delete_multiple_basic_designs(
        self,
        basic_design_ids: List[str],
        user_id: str,
        project_id: str,
        user_role: str = PermissionLevel.USER.value,
    ) -> Dict[str, List[str]]:
        """Delete multiple basic designs with access validation"""
        logger.info(
            f"[delete_multiple_basic_designs] Start - basic_design_ids={basic_design_ids}, user_id={user_id}"
        )

        if not basic_design_ids:
            logger.warning(
                "[delete_multiple_basic_designs] No basic design IDs provided"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
            )

            current_time = get_current_utc_time()

            delete_tasks = [
                self._delete_single_basic_design(
                    basic_design_id=basic_design_id,
                    project_id=project_id,
                    current_time=current_time,
                )
                for basic_design_id in basic_design_ids
            ]

            task_results = await asyncio.gather(*delete_tasks)

            deleted_ids = [
                result["deleted"] for result in task_results if result.get("deleted")
            ]
            failed_ids = [
                result["failed"] for result in task_results if result.get("failed")
            ]

            logger.info(
                f"[delete_multiple_basic_designs] Completed - deleted: {len(deleted_ids)}, failed: {len(failed_ids)}"
            )
            return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[delete_multiple_basic_designs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_git_basic_designs(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
        user_role: str = PermissionLevel.USER.value,
    ) -> Dict[str, Any]:
        """Push basic designs to Git repository"""
        logger.info(
            f"[push_git_basic_designs] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning("[push_git_basic_designs] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
            )

            # Get project to retrieve Git info and directory
            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_basic_designs] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get Git repository info
            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_basic_designs] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            # Get directory structure
            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.bd:
                logger.warning(
                    "[push_git_basic_designs] Project does not have BD directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="BD_DIRECTORY_NOT_CONFIGURED"
                )

            target_directory = directory.bd.strip("/")

            # Normalize target_directory when src is "/"
            src_path = directory.src if directory else None
            target_directory = normalize_target_directory(target_directory, src_path)

            documents = await self.repository.fetch_for_download(project_id, file_ids)

            valid_sync_statuses = [
                SyncStatus.PUSH,
                SyncStatus.PULL_PUSH,
                SyncStatus.DELETE_PUSH,
                "",
                None,
            ]
            normalized_documents = []
            for document in documents:
                normalized_doc = self._normalize_basic_design_doc(document)
                if normalized_doc.get("sync_status", "") in valid_sync_statuses:
                    normalized_documents.append(normalized_doc)

            if not normalized_documents:
                logger.warning(
                    "[push_git_basic_designs] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            files_to_push = []
            files_to_delete = []
            for normalized_doc in normalized_documents:
                if not normalized_doc.get("file_name"):
                    normalized_doc["file_name"] = FILE_NAME_BASIC_DESIGN

                contents = normalized_doc.get("contents")

                if contents and isinstance(contents, dict):
                    normalized_doc["contents"] = {
                        "content": json.dumps(contents, ensure_ascii=False, indent=2)
                    }
                    normalized_doc["content_type"] = ContentType.MARKDOWN
                sync_status = normalized_doc.get("sync_status")
                if sync_status == SyncStatus.DELETE_PUSH:
                    file_name = normalized_doc.get("file_name")
                    if file_name:
                        files_to_delete.append(file_name)
                    continue
                git_payload = self._build_git_file_payload(normalized_doc)

                if git_payload:
                    files_to_push.append(git_payload)

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_basic_designs] No valid files to push or delete after processing"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            # Check for conflicts before pushing (only for files that have commit_id)
            files_with_commit_id = []
            for normalized_doc in normalized_documents:
                if (
                    normalized_doc.get("has_valid_commit")
                    and normalized_doc.get("sync_status") != SyncStatus.DELETE_PUSH
                ):
                    collection_name = getattr(self.repository, "collection_name", None)
                    files_with_commit_id.append(
                        {
                            "file_name": normalized_doc.get("file_name"),
                            "local_commit_id": normalized_doc.get("commit_id"),
                            "file_id": normalized_doc.get("id"),
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
                        f"[push_git_basic_designs] Conflicts detected - count={len(conflict_files)}"
                    )
                    # Raise conflict exception with conflict files
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

            # Push files to Git (or delete if delete_push)
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

            # Get current time for deleted_at (for delete_push files)
            current_time = get_current_utc_time()

            # Update sync_status and commit_id for pushed files
            pushed_file_ids = [doc.get("id") for doc in normalized_documents]

            # Separate files that were delete_push (need to update deleted_at too)
            delete_push_file_ids = []
            for doc in normalized_documents:
                if doc.get("sync_status") == SyncStatus.DELETE_PUSH:
                    doc_id = doc.get("id")
                    if doc_id:
                        delete_push_file_ids.append(doc_id)
            other_file_ids = [
                doc_id
                for doc_id in pushed_file_ids
                if doc_id not in delete_push_file_ids
            ]

            # Update files that were delete_push: sync_status, commit_id, and deleted_at
            if delete_push_file_ids:
                updated_delete_count = await self.repository.update_documents(
                    document_ids=delete_push_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                        "deleted_at": current_time,
                    },
                )
                logger.info(
                    f"[push_git_basic_designs] Updated delete_push files - count={updated_delete_count}"
                )

            # Update other files: only sync_status and commit_id
            if other_file_ids:
                updated_other_count = await self.repository.update_documents(
                    document_ids=other_file_ids,
                    project_id=project_id,
                    update_data={
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.SYNCED,
                    },
                    filter_additional={"deleted_at": None},
                )
                logger.info(
                    f"[push_git_basic_designs] Updated other files - count={updated_other_count}"
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_basic_designs] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

            # Update project commit_ids after successful push
            # Add the new commit_id to the project's commit_ids array
            try:
                # Add new commit_id to project using shared function
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
                    f"[push_git_basic_designs] Failed to update project commit_ids: {e}"
                )
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

                # Don't fail the push if commit_ids update fails

            return {
                "commit_id": commit_id,
                "pushed_count": total_updated,
                "file_ids": pushed_file_ids,
            }

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[push_git_basic_designs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def restore_deleted_basic_design(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        user_role: str,
        user_name: str,
        token_password: Optional[str],
    ) -> Dict[str, Any]:
        """Restore a basic design file that is marked as delete_push"""
        logger.info(
            "[restore_deleted_basic_design] Start - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )

        if (
            not project_id
            or not file_id
            or not user_id
            or not user_role
            or not user_name
        ):
            logger.warning(
                "[restore_deleted_basic_design] Missing required parameters - project_id=%s, file_id=%s, user_id=%s",
                project_id,
                file_id,
                user_id,
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
            )

            document = await self.repository.find_one(
                {"id": file_id, "project_id": project_id, "deleted_at": None}
            )
            if not document:
                logger.warning(
                    "[restore_deleted_basic_design] Document not found - file_id=%s",
                    file_id,
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            sync_status = (document.get("sync_status") or "").lower()
            if sync_status != SyncStatus.DELETE_PUSH:
                logger.warning(
                    "[restore_deleted_basic_design] File not marked delete_push - file_id=%s, sync_status=%s",
                    file_id,
                    sync_status,
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_MARKED_DELETE_PUSH"
                )

            project = await self.project_service.get_project_by_id(project_id)
            git_info = (
                project.setting_item.git if project and project.setting_item else None
            )
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[restore_deleted_basic_design] Missing git configuration - project_id=%s",
                    project_id,
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.bd:
                logger.warning(
                    "[restore_deleted_basic_design] Missing BD directory configuration - project_id=%s",
                    project_id,
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="BD_DIRECTORY_NOT_CONFIGURED",
                )

            remote_relative_path = self._build_remote_file_path(
                directory.bd, document.get("file_name")
            )
            local_bytes = self._extract_basic_design_local_bytes(
                document.get("contents")
            )

            remote_bytes = None
            if remote_relative_path:
                remote_bytes = await fetch_remote_file_content(
                    repository_url=git_info.repository,
                    branch_name=git_info.branch,
                    user_name=user_name,
                    token_password=token_password,
                    relative_file_path=remote_relative_path,
                )

            new_sync_status = self._determine_restore_sync_status(
                local_bytes, remote_bytes
            )

            update_data = {
                "sync_status": new_sync_status,
                "deleted_at": None,
                "updated_at": get_current_utc_time(),
            }
            is_updated = await self.repository.update_document(
                document_id=file_id,
                project_id=project_id,
                update_data=update_data,
            )
            if not is_updated:
                logger.error(
                    "[restore_deleted_basic_design] Failed to update document - file_id=%s",
                    file_id,
                )
                raise_http_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_key="RESTORE_UPDATE_FAILED",
                )

            logger.info(
                "[restore_deleted_basic_design] Success - file_id=%s, sync_status=%s",
                file_id,
                new_sync_status,
            )
            return {"file_id": file_id, "sync_status": new_sync_status}

        except HTTPException:
            raise
        except Exception as e:
            error_message = "[restore_deleted_basic_design] Error - file_id=%s: %s" % (
                file_id,
                e,
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def find_one_basic_design(
        self, query_params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Find one basic design document with flexible query parameters

        Args:
            query_params: Dictionary of query parameters (e.g., {"id": "123", "project_id": "proj_1", "deleted_at": None})

        Returns:
            Document if found, None otherwise
        """
        logger.info(f"[find_one_basic_design] Start - query_params={query_params}")

        try:
            doc = await self.repository.find_one(query_params)

            if doc:
                logger.info(
                    f"[find_one_basic_design] Found document with query: {query_params}"
                )
            else:
                logger.info(
                    f"[find_one_basic_design] No document found with query: {query_params}"
                )

            return doc

        except Exception as e:
            error_message = f"[find_one_basic_design] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def download_basic_designs(
        self,
        project_id: str,
        file_id: str,
        key_names: List[str],
        user_id: str,
        user_role: str,
    ) -> Dict[str, Any]:
        """Download basic design files by extracting specific keys from contents.
        Creates one markdown file per key_name."""
        logger.info(
            f"[download_basic_designs] Start - project_id={project_id}, file_id={file_id}, key_names={key_names}, user_id={user_id}"
        )

        if not project_id or not file_id or not key_names or not user_id:
            logger.warning("[download_basic_designs] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            project = await download_service.validate_project_download(
                project_id=project_id,
                user_id=user_id,
                user_role=user_role,
                project_service=self.project_service,
            )
            # Fetch single document
            documents = await self._fetch_basic_design_documents(project_id, [file_id])

            if not documents or len(documents) == 0:
                logger.warning(
                    f"[download_basic_designs] Document not found - file_id={file_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND, error_key="FILE_NOT_FOUND")

            document = documents[0]
            normalized_document = self.repository.ensure_document_id(document)

            # Get contents from document
            contents = normalized_document.get("contents", {})
            if not isinstance(contents, dict):
                logger.warning(
                    f"[download_basic_designs] Contents is not a dictionary - file_id={file_id}"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="INVALID_CONTENTS"
                )

            # Create virtual documents for each key_name
            # Convert contents to markdown
            converted_contents = self.convert_basic_design_to_markdown(
                raw_contents=contents, key_names=key_names, skip_render=True
            )

            # Create virtual documents for each key_name
            virtual_documents = []
            for key_name in key_names:
                if key_name not in converted_contents:
                    logger.warning(
                        f"[download_basic_designs] Key '{key_name}' not found in contents - file_id={file_id}"
                    )
                    continue

                conversion_result = converted_contents[key_name]
                if not conversion_result:
                    logger.warning(
                        f"[download_basic_designs] Conversion failed or empty for key '{key_name}' - file_id={file_id}"
                    )
                    continue

                # Create virtual document with key_name
                virtual_doc = normalized_document.copy()
                virtual_doc["key_name"] = key_name

                # Set content from conversion result
                raw_markdown = conversion_result["raw_markdown"]

                # Find first MARKDOWN content
                target_item = next(
                    (
                        item
                        for item in raw_markdown
                        if item.content_type == DisplayableContentType.MARKDOWN
                    ),
                    None,
                )

                if target_item:
                    virtual_doc["content"] = target_item.content
                    # Use title as filename (append .md), sanitize to remove slashes
                    # Use full-width slash to keep appearance but avoid folder creation
                    safe_title = target_item.title.replace("/", "／").replace(
                        "\\", "＼"
                    )
                    virtual_doc["file_name"] = f"{safe_title}.md"
                else:
                    virtual_doc["content"] = (
                        raw_markdown[0].content if raw_markdown else ""
                    )
                    virtual_doc["file_name"] = f"{key_name}.md"

                virtual_documents.append(virtual_doc)

            if not virtual_documents:
                logger.warning(
                    f"[download_basic_designs] No valid key_names found - file_id={file_id}, key_names={key_names}"
                )
                raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_KEYS")

            # Ensure all virtual documents have proper IDs
            normalized_documents = [
                self.repository.ensure_document_id(doc) for doc in virtual_documents
            ]

            initial_count = len(normalized_documents)
            logger.info(
                "[download_basic_designs] Virtual documents created - count=%s, key_names=%s",
                initial_count,
                [doc.get("key_name") for doc in normalized_documents],
            )

            # No need to fetch referenced images for key-based extraction
            # Each key_name will be extracted as a separate markdown file
            document_count = len(normalized_documents)
            logger.info(
                "[download_basic_designs] Final documents - count=%s (1 document with %s keys)",
                document_count,
                len(key_names),
            )
            created_at = datetime.utcnow().isoformat() + "Z"
            logger.info(
                "[download_basic_designs] Retrieved documents - project_id=%s, document_count=%s",
                project_id,
                document_count,
            )

            activity_handler = partial(
                self._log_basic_design_download_activity,
                project,
                document_count,
            )

            # Extract project_name from project object
            project_name = self._extract_project_name(project)

            context = download_service.create_download_context(
                project_id=project_id,
                project_name=project_name,
                project=project,
                user_id=user_id,
                created_at=created_at,
                file_type="basic design document",
                file_suffix="basic_design",
            )

            # Use new content extractor that extracts by key_name
            # from app.utils.download_utils import extract_content_by_key

            resolvers = download_service.create_resolver_config(
                document_id_resolver=self.repository.extract_document_id,
                document_normalizer=self.repository.ensure_document_id,
                content_extractor=extract_detail_design_content,
            )

            handlers = download_service.create_file_handlers()

            return await download_service.build_download_payload(
                documents=normalized_documents,
                context=context,
                resolvers=resolvers,
                handlers=handlers,
                activity_logger=activity_handler,
            )

        except HTTPException:
            raise
        except ValueError:
            raise
        except Exception as e:
            error_message = f"[download_basic_designs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_basic_design_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
        user_role: str = PermissionLevel.USER.value,
    ) -> Dict[str, Any]:
        """Update basic design content with sync_status logic"""
        logger.info(
            f"[update_basic_design_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning("[update_basic_design_content] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            await self._ensure_user_has_project_access(
                project_id=project_id, user_id=user_id
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
                    f"[update_basic_design_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get current sync_status and contents
            current_sync_status = document.get("sync_status") or ""
            contents = document.get("contents", {})
            content_type = contents.get("type", ContentType.MARKDOWN)

            # Determine new sync_status
            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            # Update contents preserving type
            updated_contents = {
                "type": content_type,
                "content": content,
            }

            # Prepare update data
            current_time = get_current_utc_time()
            update_data = {
                "contents": updated_contents,
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
                    f"[update_basic_design_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            logger.info(
                f"[update_basic_design_content] Success - file_id={file_id}, sync_status={new_sync_status}"
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
            error_message = f"[update_basic_design_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # region Private Functions

    def _validate_detail_inputs(
        self, project_id: str, file_id: str, user_id: str
    ) -> None:
        """Validate required inputs for detail retrieval"""
        logger.info("[_validate_detail_inputs] Start")

        try:
            if not project_id or not file_id or not user_id:
                logger.warning("[_validate_detail_inputs] Missing required parameters")
                raise_http_error(status.HTTP_400_BAD_REQUEST)

            logger.info("[_validate_detail_inputs] Success")

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[_validate_detail_inputs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _ensure_user_has_project_access(
        self, project_id: str, user_id: str
    ) -> None:
        """Ensure user has permission to access project basic designs"""
        logger.info(
            f"[_ensure_user_has_project_access] Start - project_id={project_id}, user_id={user_id}"
        )

        try:
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning(
                    "[_ensure_user_has_project_access] User lacks project access"
                )
                raise_http_error(status.HTTP_403_FORBIDDEN)

            logger.info("[_ensure_user_has_project_access] User access validated")

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[_ensure_user_has_project_access] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _normalize_basic_design_doc(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize frequently accessed fields from a basic design document."""
        logger.info("[_normalize_basic_design_doc] Start")

        if not document:
            logger.warning("[_normalize_basic_design_doc] Missing document input")
            return {
                "id": "",
                "project_id": "",
                "commit_id": "",
                "sync_status": "",
                "contents": {},
                "content_type": "",
                "created_at": "",
                "updated_at": "",
                "has_valid_commit": False,
            }

        try:
            file_name = self._get_and_validate_file_name(document)
            contents = self._extract_contents_dict(document)
            commit_id = self._get_and_validate_commit_id(document)
            sync_status = self._get_and_validate_sync_status(document)
            content_type = self._get_and_validate_content_type(contents) or ""
            normalized_doc = {
                "id": document.get("id", ""),
                "project_id": document.get("project_id", ""),
                "file_name": file_name,
                "commit_id": commit_id,
                "sync_status": sync_status,
                "contents": contents,
                "content_type": content_type,
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
                "has_valid_commit": bool(commit_id),
            }
            logger.info("[_normalize_basic_design_doc] Success")
            return normalized_doc

        except Exception as e:
            error_message = f"[_normalize_basic_design_doc] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _extract_contents_dict(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Extract contents dictionary - keep original format if it's a dict, otherwise normalize for legacy structures."""
        default_contents = {"type": "", "content": ""}
        if not document:
            logger.warning("[_extract_contents_dict] Missing document input")
            return default_contents

        try:
            contents_candidate = document.get("contents")

            # If contents is already a dict and doesn't have legacy format (type/content), return as-is
            if isinstance(contents_candidate, dict):
                # Check if it's the new format (complex object) or legacy format (type/content)
                if (
                    "type" in contents_candidate
                    and "content" in contents_candidate
                    and len(contents_candidate) == 2
                ):
                    # Legacy format with only type and content - normalize it
                    normalized_content = self._normalize_contents_value(
                        contents_candidate
                    )
                    if normalized_content is not None:
                        return normalized_content
                else:
                    # New format - complex object, return as-is
                    return contents_candidate

            # For non-dict or legacy formats, normalize
            normalized_content = self._normalize_contents_value(contents_candidate)
            if normalized_content is not None:
                return normalized_content

            logger.warning(
                "[_extract_contents_dict] Invalid contents format, using default"
            )
            return default_contents

        except Exception as e:
            error_message = f"[_extract_contents_dict] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return default_contents

    def _normalize_contents_value(
        self, contents_candidate: Any
    ) -> Optional[Dict[str, Any]]:
        """Normalize different content representations to the canonical dict format."""

        if not contents_candidate:
            return None

        if hasattr(contents_candidate, "model_dump"):
            contents_candidate = contents_candidate.model_dump()

        if isinstance(contents_candidate, dict):
            content_type = contents_candidate.get("type", "")
            content_value = contents_candidate.get("content", "")
            if isinstance(content_value, dict) and "data" in content_value:
                content_value = self._convert_buffer_dict(content_value)
            return {
                "type": content_type or "",
                "content": content_value or "",
            }

        if isinstance(contents_candidate, list):
            for item in contents_candidate:
                normalized_item = self._normalize_contents_value(item)
                if normalized_item:
                    return normalized_item
            return None

        if isinstance(contents_candidate, (bytes, bytearray, memoryview)):
            encoded = base64.b64encode(bytes(contents_candidate)).decode("utf-8")
            return {"type": ContentType.IMAGE, "content": encoded}

        if isinstance(contents_candidate, str):
            return {"type": "", "content": contents_candidate}

        return None

    def _convert_buffer_dict(self, buffer_dict: Dict[str, Any]) -> str:
        """Convert Node.js style buffer objects into base64 strings."""
        data = buffer_dict.get("data")
        if isinstance(data, list):
            try:
                return base64.b64encode(bytes(data)).decode("utf-8")
            except Exception as e:
                error_message = (
                    f"[_convert_buffer_dict] Failed to convert buffer dict: {e}"
                )
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

                return ""
        return ""

    @staticmethod
    def _build_remote_file_path(
        target_directory: Optional[str], file_name: Optional[str]
    ) -> str:
        logger.info("[_build_remote_file_path] Start")
        try:
            if not file_name:
                logger.warning("[_build_remote_file_path] Missing file_name input")
                return ""

            directory_part = (target_directory or "").strip().strip("/")
            file_part = file_name.strip().lstrip("/")

            remote_path = (
                f"{directory_part}/{file_part}" if directory_part else file_part
            )
            logger.info(
                "[_build_remote_file_path] Success - remote_path=%s", remote_path
            )
            return remote_path
        except Exception as e:
            error_message = f"[_build_remote_file_path] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @staticmethod
    def _extract_basic_design_local_bytes(contents: Optional[Dict[str, Any]]) -> bytes:
        logger.info("[_extract_basic_design_local_bytes] Start")
        try:
            normalized_contents = contents or {}
            content_type = (
                normalized_contents.get("type") or ContentType.MARKDOWN
            ).lower()
            content_value = normalized_contents.get("content") or ""

            if content_type == ContentType.IMAGE:
                try:
                    decoded = base64.b64decode(content_value)
                    logger.info(
                        "[_extract_basic_design_local_bytes] Success - image data"
                    )
                    return decoded
                except Exception as e:
                    error_message = (
                        "[_extract_basic_design_local_bytes] Failed to decode image content: %s"
                        % (e,)
                    )
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

                    return b""

            encoded = str(content_value).encode("utf-8")
            logger.info("[_extract_basic_design_local_bytes] Success - text data")
            return encoded
        except Exception as e:
            error_message = f"[_extract_basic_design_local_bytes] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @staticmethod
    def _determine_restore_sync_status(
        local_bytes: Optional[bytes], remote_bytes: Optional[bytes]
    ) -> str:
        logger.info("[_determine_restore_sync_status] Start")
        try:
            if not local_bytes or not remote_bytes:
                logger.info(
                    "[_determine_restore_sync_status] Missing content, defaulting to push"
                )
                return SyncStatus.PUSH

            if local_bytes == remote_bytes:
                logger.info("[_determine_restore_sync_status] Contents match - synced")
                return SyncStatus.SYNCED

            logger.info("[_determine_restore_sync_status] Contents differ - push")
            return SyncStatus.PUSH
        except Exception as e:
            error_message = f"[_determine_restore_sync_status] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_git_file_payload(
        self, normalized_doc: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Generate Git payload entry from normalized document."""
        logger.info("[_build_git_file_payload] Start")

        try:
            file_name = normalized_doc.get("file_name")
            contents = normalized_doc.get("contents", {})
            content_value = contents.get("content")
            content_type = normalized_doc.get("content_type")

            if not file_name or not content_value or not content_type:
                logger.warning(
                    "[_build_git_file_payload] Missing file_name or content, skipping file"
                )
                return None

            if content_type == ContentType.MARKDOWN:
                file_content = (
                    content_value.encode("utf-8")
                    if isinstance(content_value, str)
                    else content_value
                )
            elif content_type == ContentType.IMAGE:
                try:
                    file_content = base64.b64decode(content_value)
                except Exception as e:
                    error_message = f"[_build_git_file_payload] Base64 decode failed for {file_name}: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

                    return None
            else:
                logger.warning(
                    f"[_build_git_file_payload] Unsupported content type: {content_type}"
                )
                return None

            logger.info("[_build_git_file_payload] Success")
            return {"file_name": file_name, "content": file_content}

        except Exception as e:
            error_message = f"[_build_git_file_payload] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return None

    async def _delete_single_basic_design(
        self, basic_design_id: str, project_id: str, current_time: datetime
    ) -> Dict[str, Optional[str]]:
        """Delete or mark a single basic design, return outcome for aggregation."""
        logger.info(
            f"[_delete_single_basic_design] Start - project_id={project_id}, basic_design_id={basic_design_id}"
        )

        try:
            query_params = {
                "id": basic_design_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[_delete_single_basic_design] Basic design not found: {basic_design_id}"
                )
                return {"deleted": None, "failed": basic_design_id}

            normalized_doc = self._normalize_basic_design_doc(document)

            if normalized_doc.get("has_valid_commit"):
                is_updated = await self.repository.update_document(
                    document_id=basic_design_id,
                    project_id=project_id,
                    update_data={
                        "sync_status": SyncStatus.DELETE_PUSH,
                        "updated_at": current_time,
                    },
                )
                if is_updated:
                    logger.info(
                        f"[_delete_single_basic_design] Marked for delete_push - {basic_design_id}"
                    )
                    return {"deleted": basic_design_id, "failed": None}
                logger.warning(
                    f"[_delete_single_basic_design] Failed to flag delete_push - {basic_design_id}"
                )
                return {"deleted": None, "failed": basic_design_id}

            is_deleted = await self.repository.soft_delete_document(
                document_id=basic_design_id,
                project_id=project_id,
                deleted_at=current_time,
            )
            if is_deleted:
                logger.info(
                    f"[_delete_single_basic_design] Soft deleted basic design - {basic_design_id}"
                )
                return {"deleted": basic_design_id, "failed": None}

            logger.warning(
                f"[_delete_single_basic_design] Failed to soft delete - {basic_design_id}"
            )
            return {"deleted": None, "failed": basic_design_id}

        except Exception as e:
            error_message = f"[_delete_single_basic_design] Error - basic_design_id={basic_design_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return {"deleted": None, "failed": basic_design_id}

    def _build_basic_design_detail_response(
        self, document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build response payload for detail endpoint"""
        logger.info("[_build_basic_design_detail_response] Start")

        try:
            normalized_doc = self._normalize_basic_design_doc(document)

            # Get contents object
            contents = normalized_doc.get("contents", {})
            if not isinstance(contents, dict):
                contents = {}

            # Build git object
            commit_id = normalized_doc.get("commit_id", "")
            git = {
                "commit_id": commit_id,
                "sync_status": normalized_doc.get("sync_status", ""),
            }

            # Get status: GIT if commit_id has data, LOCAL otherwise
            status = (
                FileStatus.GIT if commit_id and commit_id.strip() else FileStatus.LOCAL
            )

            response = {
                "id": normalized_doc.get("id"),
                "contents": contents,
                "status": status,
                "git": git,
                "created_at": normalized_doc.get("created_at"),
                "updated_at": normalized_doc.get("updated_at"),
            }
            logger.info("[_build_basic_design_detail_response] Success")
            return response

        except Exception as e:
            error_message = f"[_build_basic_design_detail_response] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _get_and_validate_file_name(
        self, doc: Dict[str, Any], default: str = ""
    ) -> str:
        """Get and validate file_name from document dictionary"""
        if not doc:
            logger.warning("[_get_and_validate_file_name] Missing document input")
            return default

        try:
            file_name = doc.get("file_name") or default
            if file_name:
                file_name = str(file_name).strip()
            return file_name

        except Exception as e:
            error_message = f"[_get_and_validate_file_name] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return default

    def _get_and_validate_content_type(self, contents: Dict[str, Any]) -> Optional[str]:
        """Get and validate content_type from contents dictionary"""
        if not contents:
            logger.warning("[_get_and_validate_content_type] Missing contents input")
            return None

        try:
            content_type = contents.get("type")
            if content_type and content_type in [
                ContentType.MARKDOWN,
                ContentType.IMAGE,
            ]:
                return content_type
            logger.warning(
                f"[_get_and_validate_content_type] Invalid content_type: {content_type}"
            )
            return None

        except Exception as e:
            error_message = f"[_get_and_validate_content_type] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return None

    def _get_and_validate_sync_status(
        self, doc: Dict[str, Any], default: str = ""
    ) -> str:
        """Get and validate sync_status from document dictionary"""
        if not doc:
            logger.warning("[_get_and_validate_sync_status] Missing document input")
            return default

        try:
            sync_status = doc.get("sync_status") or default
            sync_status = str(sync_status).strip() if sync_status else default
            return sync_status

        except Exception as e:
            error_message = f"[_get_and_validate_sync_status] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return default

    def _get_and_validate_commit_id(
        self, doc: Dict[str, Any], default: str = ""
    ) -> str:
        """Get and validate commit_id from document dictionary"""
        if not doc:
            logger.warning("[_get_and_validate_commit_id] Missing document input")
            return default

        try:
            commit_id = doc.get("commit_id") or default
            if commit_id:
                commit_id = str(commit_id).strip()
                if commit_id == "null" or not commit_id:
                    commit_id = default
            return commit_id

        except Exception as e:
            error_message = f"[_get_and_validate_commit_id] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return default

    def _get_and_validate_contents(
        self, doc: Dict[str, Any], default: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Get and validate contents from document dictionary"""
        if not doc:
            logger.warning("[_get_and_validate_contents] Missing document input")
            return default or {}

        try:
            contents = doc.get("contents", default or {})
            if not isinstance(contents, dict):
                logger.warning(
                    "[_get_and_validate_contents] Invalid contents type, using default"
                )
                return default or {}
            return contents

        except Exception as e:
            error_message = f"[_get_and_validate_contents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return default or {}

    def _get_content_from_contents(self, contents: Dict[str, Any]) -> Optional[Any]:
        """Get content value from contents dictionary"""
        if not contents:
            logger.warning("[_get_content_from_contents] Missing contents input")
            return None

        try:
            content = contents.get("content")
            return content

        except Exception as e:
            error_message = f"[_get_content_from_contents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return None

    def _has_valid_commit_id(self, doc: Dict[str, Any]) -> bool:
        """Check if document has a valid commit_id"""
        if not doc:
            logger.warning("[_has_valid_commit_id] Missing document input")
            return False

        try:
            commit_id = self._get_and_validate_commit_id(doc)
            has_valid = bool(commit_id and commit_id.strip() and commit_id != "null")
            return has_valid

        except Exception as e:
            error_message = f"[_has_valid_commit_id] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return False

    def _extract_project_name(self, project: Dict[str, Any]) -> str:
        """Extract project name from project dict or object"""

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

            return project_name

        except Exception as e:
            error_message = "[_extract_project_name] Error: %s" % e
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return "project"

    async def _fetch_basic_design_documents(
        self, project_id: str, file_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Fetch basic design documents by IDs."""
        logger.info("[_fetch_basic_design_documents] Start")

        try:
            documents = await self.repository.fetch_for_download(
                project_id=project_id, file_ids=file_ids
            )
            logger.info(
                f"[_fetch_basic_design_documents] Success - found {len(documents)} documents"
            )
            return documents

        except Exception as e:
            error_message = f"[_fetch_basic_design_documents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _log_basic_design_download_activity(
        self,
        project: Dict[str, Any],
        total_documents: int,
        project_id: str,
        user_id: str,
        activity_type: str,
        file_ids: List[str],
        file_type: str,
    ) -> None:
        """Create activity log entry for basic design downloads."""
        logger.info(
            "[_log_basic_design_download_activity] Start - project_id=%s, user_id=%s, activity_type=%s",
            project_id,
            user_id,
            activity_type,
        )

        try:
            file_count = len(file_ids) if file_ids else total_documents
            await create_download_activity(
                project_id=project_id,
                user_id=user_id,
                file_count=file_count,
                document_type=file_type,
            )
            logger.info(
                "[_log_basic_design_download_activity] Success - file_count=%s",
                file_count,
            )
        except Exception as e:
            error_message = (
                "[_log_basic_design_download_activity] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion


# Global service instance
basic_design_service = BasicDesignService()
