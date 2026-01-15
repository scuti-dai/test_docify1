"""
Detail Design Service
Handles detail design business logic
"""

import json
import logging
import os
import platform
import subprocess
import tempfile
import base64
import re
import copy
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from functools import partial

from fastapi import HTTPException, status

from app.services.download_service import download_service
from app.services.project_service import ProjectService
from app.utils.download_utils import (
    extract_detail_design_content,
    extract_content_by_key,
)
from app.utils.constants import PermissionLevel
from app.utils.constants import FileStatus, SyncStatus
from app.utils.http_helpers import raise_http_error
from app.services.git_services.git_push import (
    push_files_to_git,
    check_conflicts_before_push,
)
from app.services.git_services.git_service import normalize_target_directory
from app.schemas.base import ConflictResponse, ConflictFileInfo
from app.utils.helpers import get_current_utc_time
from cec_docifycode_common.models.content_models.utils.displayable_content import (
    DisplayableContent,
    DisplayableContentType,
)
from config_gateway import Config as GatewayConfig

from app.services.logs_service import save_exception_log_sync, LogLevel

from cec_docifycode_common.models.detail_design import (
    FILE_NAME_DETAIL_DESIGN,
    DetailDesignKey,
)
from cec_docifycode_common.models.content_models.detailed_design import (
    DetailedDesignClassDesignData,
    DetailedDesignFolderStructureData,
    DetailedDesignInterfaceDesignData,
    DetailedDesignMethodDesignData,
)

logger = logging.getLogger(__name__)


class DetailDesignService:
    """Service for detail design management operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(DetailDesignService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not DetailDesignService._initialized:
            self.project_service = ProjectService()
            DetailDesignService._initialized = True
            logger.info("[DetailDesignService] Singleton instance initialized")

        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[DetailDesignService] Repository updated via set_repository")

    # #region Public Methods

    async def get_detail_designs_by_project(
        self, project_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all detail designs for a project

        Args:
            project_id: Project ID

        Returns:
            List of detail design documents with mapped fields
        """
        logger.info(f"[get_detail_designs_by_project] Start - project_id={project_id}")

        if not project_id:
            logger.warning("[get_detail_designs_by_project] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            raw_documents = await self.repository.get_detail_designs_by_project(
                project_id
            )
            mapped_documents = []
            for doc in raw_documents:
                mapped_doc = self._map_detail_design_fields(doc)
                # Include contents (dict) from original document
                mapped_doc["contents"] = doc.get("contents") or {}
                mapped_documents.append(mapped_doc)

            logger.info(
                f"[get_detail_designs_by_project] Success - count={len(mapped_documents)}"
            )
            return mapped_documents
        except ValueError as ve:
            error_message = f"[get_detail_designs_by_project] Validation error: {ve}"
            logger.error(error_message)
            save_exception_log_sync(ve, error_message, __name__)

            raise
        except Exception as e:
            error_message = (
                f"[get_detail_designs_by_project] Error - project_id={project_id}: {e}"
            )
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

    def process_design_section(
        self,
        section_key: str,
        raw_contents: Dict[str, Any],
        processor: Callable[[Any], list],
        skip_render: bool = False,
    ) -> Dict[str, Any]:
        """
        Process a single design section and convert to markdown.

        Args:
            section_key: key of section in contents
            raw_contents: original contents dict
            processor: function to convert data -> list[DisplayableContent]
            skip_render: if True, do not generate rendered_markdown
        """

        if section_key not in raw_contents or not raw_contents[section_key]:
            return ""

        try:
            raw_markdown = processor(raw_contents[section_key])

            result = {
                "raw_markdown": raw_markdown,
            }

            if not skip_render:
                rendered_list = []
                for item in raw_markdown:
                    if item.content_type == DisplayableContentType.MARKDOWN:
                        converted_content = self.convert_plantuml_to_images(
                            item.content
                        )
                        rendered_item = DisplayableContent(
                            content_type=item.content_type,
                            title=item.title,
                            content=converted_content,
                        )
                        rendered_list.append(rendered_item)
                    else:
                        rendered_list.append(item)
                result["rendered_markdown"] = rendered_list

            return result

        except Exception as e:
            logger.warning(
                "[process_design_section] Failed to convert %s to markdown: %s",
                section_key,
                e,
            )
            return ""

    def convert_detail_design_to_markdown(
        self,
        raw_contents: dict[str, Any],
        key_names: Optional[list[str]] = None,
        skip_render: bool = False,
    ) -> dict[str, Any]:
        """
        Process a detail design contents dict and convert each section to markdown.
        Can process all sections or only specific sections (key_names).
        """
        processed_contents = {}

        # Section processors
        def process_class_design(data):
            obj = DetailedDesignClassDesignData.model_validate(data)
            return obj.get_displayable_contents()

        def process_folder_structure(data):
            obj = DetailedDesignFolderStructureData.model_validate(data)
            return obj.get_displayable_contents()

        def process_interface_design(data):
            obj = DetailedDesignInterfaceDesignData.model_validate(data)
            return obj.get_displayable_contents()

        def process_method_design(data):
            obj = DetailedDesignMethodDesignData.model_validate(data)
            return obj.get_displayable_contents()

        section_processors = {
            DetailDesignKey.CLASS.value: process_class_design,
            DetailDesignKey.FOLDER.value: process_folder_structure,
            DetailDesignKey.INTERFACE.value: process_interface_design,
            DetailDesignKey.METHOD.value: process_method_design,
        }

        # Determine keys to process
        if key_names:
            keys_to_process = key_names
        else:
            keys_to_process = list(section_processors.keys())

        for key in keys_to_process:
            # Check for deep key pattern
            base_section = None
            target_file = None

            if key in section_processors:
                base_section = key
            else:
                # Check if it's a specific file key
                for section in section_processors:
                    prefix = f"{section}.files."
                    if key.startswith(prefix):
                        base_section = section
                        target_file = key[len(prefix) :]
                        break

            if not base_section:
                continue

            processor = section_processors[base_section]

            # Prepare data
            data_to_process = raw_contents

            if target_file:
                # Filter data for specific file
                section_data = raw_contents.get(base_section)
                if not section_data:
                    continue

                # Deep copy to avoid modifying original
                filtered_data = copy.deepcopy(section_data)

                # Filter files
                if "files" in filtered_data and isinstance(
                    filtered_data["files"], dict
                ):
                    if target_file in filtered_data["files"]:
                        filtered_data["files"] = {
                            target_file: filtered_data["files"][target_file]
                        }
                    else:
                        # File not found, return empty files
                        filtered_data["files"] = {}

                # Create a temporary contents dict with filtered data
                data_to_process = {base_section: filtered_data}

            processed_contents[key] = self.process_design_section(
                base_section, data_to_process, processor, skip_render
            )

        # Ensure all standard keys exist if not filtering by key_names
        if not key_names:
            for key in section_processors:
                if key not in processed_contents:
                    processed_contents[key] = ""
        return processed_contents

    async def get_detail_design_detail(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        user_role: str,
        key_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get a single detail design document with authorization enforcement
        """
        logger.info(
            "[get_detail_design_detail] Start - project_id=%s, file_id=%s, user_id=%s",
            project_id,
            file_id,
            user_id,
        )

        if not project_id or not file_id or not user_id:
            logger.warning("[get_detail_design_detail] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            await self._validate_user_authorization(
                user_role=user_role, user_id=user_id, project_id=project_id
            )

            document = await self.repository.get_detail_design_by_id(
                project_id, file_id
            )
            if not document:
                logger.warning(
                    "[get_detail_design_detail] Document not found - project_id=%s, file_id=%s",
                    project_id,
                    file_id,
                )
                raise_http_error(status.HTTP_404_NOT_FOUND, "FILE_NOT_FOUND")

            logger.info(
                "[get_detail_design_detail] Document fetched - project_id=%s, file_name=%s",
                project_id,
                document.get("file_name"),
            )

            mapped_document = self._map_detail_design_fields(document)
            mapped_document["content"] = document.get("content") or ""

            # Process contents object and convert to markdown
            raw_contents = document.get("contents") or {}
            processed_contents = self.convert_detail_design_to_markdown(
                raw_contents, key_names=[key_name]
            )

            mapped_document["contents"] = processed_contents

            return mapped_document
        except HTTPException:
            raise
        except ValueError as ve:
            error_message = (
                "[get_detail_design_detail] Validation error - project_id=%s, file_id=%s: %s"
                % (
                    project_id,
                    file_id,
                    ve,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(ve, error_message, __name__)

            raise
        except Exception as e:
            error_message = (
                "[get_detail_design_detail] Error - project_id=%s, file_id=%s: %s"
                % (
                    project_id,
                    file_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def download_detail_designs(
        self,
        project_id: str,
        file_id: str,
        key_names: List[str],
        user_id: str,
        user_role: str,
    ) -> Dict[str, Any]:
        """
        Download detail design files by extracting specific keys from contents.
        Creates one markdown file per key_name.

        Args:
            project_id: Project ID
            file_id: Detail design ID
            key_names: List of key names to extract from contents
            user_id: User ID
            user_role: User role

        Returns:
            Dict with download payload
        """
        logger.info(
            f"[download_detail_designs] Start - project_id={project_id}, file_id={file_id}, key_names={key_names}, user_id={user_id}"
        )

        if not project_id or not file_id or not key_names or not user_id:
            logger.warning("[download_detail_designs] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate project
            project = await download_service.validate_project_download(
                project_id=project_id,
                user_id=user_id,
                user_role=user_role,
                project_service=self.project_service,
            )

            # Fetch single document
            documents = await self.repository.get_detail_designs_by_ids(
                project_id, [file_id]
            )

            if not documents or len(documents) == 0:
                logger.warning(
                    f"[download_detail_designs] Document not found - file_id={file_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND, error_key="FILE_NOT_FOUND")

            document = documents[0]
            normalized_document = self._ensure_detail_design_document_id(document)

            # Get contents from document
            contents = normalized_document.get("contents", {})
            if not isinstance(contents, dict):
                logger.warning(
                    f"[download_detail_designs] Contents is not a dictionary - file_id={file_id}"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="INVALID_CONTENTS"
                )

            # --- Convert each key_name using convert_detail_design_to_markdown ---
            converted_contents = self.convert_detail_design_to_markdown(
                raw_contents=contents, key_names=key_names, skip_render=True
            )
            virtual_documents = []
            for key in key_names:
                if key not in converted_contents:
                    logger.warning(f"Key '{key}' not found")
                    continue

                raw_markdown_list = converted_contents[key].get("raw_markdown", [])
                if not raw_markdown_list:
                    continue

                virtual_doc = normalized_document.copy()
                virtual_doc["key_name"] = key

                # Find first MARKDOWN content
                target_item = next(
                    (
                        item
                        for item in raw_markdown_list
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
                    # Fallback if no markdown found, use key as filename
                    if raw_markdown_list:
                        virtual_doc["content"] = raw_markdown_list[0].content
                    virtual_doc["file_name"] = f"{key}.md"

                virtual_documents.append(virtual_doc)
            if not virtual_documents:
                logger.warning(
                    f"[download_detail_designs] No valid key_names found - file_id={file_id}, key_names={key_names}"
                )
                raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_KEYS")

            # Ensure proper IDs
            normalized_documents = [
                self._ensure_detail_design_document_id(doc) for doc in virtual_documents
            ]

            document_count = len(normalized_documents)
            created_at = datetime.utcnow().isoformat() + "Z"
            logger.info(
                "[download_detail_designs] Documents ready for download - count=%s",
                document_count,
            )

            activity_handler = partial(
                self._log_detail_design_download_activity,
                project,
                document_count,
            )

            # Extract project_name
            project_name = self._extract_project_name(project)

            context = download_service.create_download_context(
                project_id=project_id,
                project_name=project_name,
                project=project,
                user_id=user_id,
                created_at=created_at,
                file_type="detail design document",
                file_suffix="detail_design",
            )

            # Use resolver and handlers for file download
            resolvers = download_service.create_resolver_config(
                document_id_resolver=self._extract_detail_design_document_id,
                document_normalizer=self._ensure_detail_design_document_id,
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
            error_message = f"[download_detail_designs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            raise

    async def push_git_detail_designs(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """Push detail designs to Git repository"""
        logger.info(
            f"[push_git_detail_designs] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning("[push_git_detail_designs] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(status.HTTP_403_FORBIDDEN)

            # Get project to retrieve Git info and directory
            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_detail_designs] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get Git repository info
            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_detail_designs] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            # Get directory structure
            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.pd:
                logger.warning(
                    "[push_git_detail_designs] Project does not have PD directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="PD_DIRECTORY_NOT_CONFIGURED"
                )

            target_directory = directory.pd.strip("/")

            # Normalize target_directory when src is "/"
            src_path = directory.src if directory else None
            target_directory = normalize_target_directory(target_directory, src_path)

            # Get files from repository
            documents = await self.repository.get_detail_designs_by_ids(
                project_id, file_ids
            )

            # Filter files by sync_status: push, pull_push, delete_push, or empty
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
                if (doc.get("sync_status") or "") in valid_sync_statuses
            ]

            if not filtered_documents:
                logger.warning(
                    "[push_git_detail_designs] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            # Separate files to push and files to delete
            files_to_push = []
            files_to_delete = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status") or ""
                #  delete_push
                if sync_status == SyncStatus.DELETE_PUSH:
                    file_name = doc.get("file_name") or FILE_NAME_DETAIL_DESIGN
                    file_path = doc.get("file_path") or file_name
                    files_to_delete.append(file_path)
                    continue

                #  default file name
                file_name = doc.get("file_name") or FILE_NAME_DETAIL_DESIGN
                doc["file_name"] = file_name

                #  normalize content
                content = doc.get("content")
                contents_obj = doc.get("contents")

                if not content and contents_obj and isinstance(contents_obj, dict):
                    content = json.dumps(contents_obj, ensure_ascii=False, indent=2)
                    doc["content"] = content

                if not content:
                    logger.warning(
                        f"[push_git_detail_designs] Skipping document with missing content - doc_id={doc.get('id')}"
                    )
                    continue

                file_content = (
                    content.encode("utf-8") if isinstance(content, str) else content
                )
                file_path = doc.get("file_path") or file_name

                files_to_push.append(
                    {
                        "file_name": file_name,
                        "file_path": file_path,
                        "content": file_content,
                    }
                )

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_detail_designs] No valid files to push or delete after processing"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            # Check for conflicts before pushing (only for files that have commit_id)
            collection_name = getattr(self.repository, "collection_name", None)
            files_with_commit_id = []
            for doc in filtered_documents:
                if (
                    doc.get("commit_id")
                    and doc.get("commit_id").strip()
                    and doc.get("commit_id") != "null"
                    and (doc.get("sync_status") or "") != SyncStatus.DELETE_PUSH
                ):
                    # Use file_path from document if available, otherwise use file_name
                    file_path = doc.get("file_path") or doc.get("file_name")
                    files_with_commit_id.append(
                        {
                            "file_name": doc.get("file_name"),
                            "file_path": file_path,  # Use file_path from document
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
                        f"[push_git_detail_designs] Conflicts detected - count={len(conflict_files)}"
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
            pushed_file_ids = [doc.get("id") for doc in filtered_documents]

            # Separate files that were delete_push (need to update deleted_at too)
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
                    f"[push_git_detail_designs] Updated delete_push files - count={updated_delete_count}"
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
                )
                logger.info(
                    f"[push_git_detail_designs] Updated other files - count={updated_other_count}"
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_detail_designs] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

            # Update project commit_ids
            try:
                await self.project_service.add_commit_id_to_project(
                    project=project,
                    new_commit_id=push_result,
                )
            except Exception as e:
                error_message = f"[push_git_detail_designs] Failed to update project commit_ids: {e}"
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

            return {
                "commit_id": commit_id,
                "updated_count": total_updated,
            }

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[push_git_detail_designs] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_detail_design_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
        user_role: str = PermissionLevel.USER,
    ) -> Dict[str, Any]:
        """Update detail design content with sync_status logic"""
        logger.info(
            f"[update_detail_design_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning("[update_detail_design_content] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            await self._validate_user_authorization(
                project_id=project_id, user_id=user_id, user_role=user_role
            )

            # Find the document
            document = await self.repository.get_detail_design_by_id(
                project_id, file_id
            )

            if not document:
                logger.warning(
                    f"[update_detail_design_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get current sync_status
            current_sync_status = document.get("sync_status") or ""

            # Determine new sync_status
            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            # Prepare update data
            from app.utils.helpers import get_current_utc_time

            current_time = get_current_utc_time()
            update_data = {
                "content": content,
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
                    f"[update_detail_design_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Check if file_type is activity_diagram and generate image
            file_type = document.get("file_type")
            images = None
            if file_type == "activity_diagram":
                logger.info(
                    f"[update_detail_design_content] Generating image for activity_diagram - file_id={file_id}"
                )
                try:
                    puml_content = self.extract_plantuml_content(content)
                    if puml_content:
                        images = self.generate_puml_image(puml_content)
                        logger.info(
                            f"[update_detail_design_content] Image generated successfully - file_id={file_id}"
                        )
                    else:
                        logger.warning(
                            f"[update_detail_design_content] No PlantUML content found - file_id={file_id}"
                        )
                except Exception as e:
                    error_message = f"[update_detail_design_content] Failed to generate image - file_id={file_id}: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

                    images = None

            logger.info(
                f"[update_detail_design_content] Success - file_id={file_id}, sync_status={new_sync_status}"
            )

            result = {
                "project_id": project_id,
                "file_id": file_id,
                "updated_at": current_time,
                "content": content,
            }

            if images is not None:
                result["images"] = images

            return result

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[update_detail_design_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # #endregion

    # #region Private Methods

    def _map_detail_design_fields(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map MongoDB document fields to API response format

        Args:
            document: MongoDB document

        Returns:
            Mapped document with API fields
        """
        logger.info(
            "[DetailDesignService._map_detail_design_fields] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )

        if not document:
            logger.warning(
                "[DetailDesignService._map_detail_design_fields] Missing document"
            )
            return {}

        try:
            # Extract id from _id or id field
            document_id = document.get("id") or document.get("_id")
            if isinstance(document_id, str):
                resolved_id = document_id
            else:
                resolved_id = str(document_id) if document_id else ""

            commit_id = document.get("commit_id")
            status = (
                FileStatus.GIT if commit_id and commit_id.strip() else FileStatus.LOCAL
            )

            mapped = {
                "id": resolved_id,
                "project_id": document.get("project_id", ""),
                "type": document.get("type", ""),
                "file_name": document.get("file_name", ""),
                "commit_id": commit_id,
                "sync_status": document.get("sync_status"),
                "status": status,
                "file_type": document.get("file_type"),
                "created_at": document.get("created_at"),
                "updated_at": document.get("updated_at"),
                "deleted_at": document.get("deleted_at"),
            }

            logger.info("[DetailDesignService._map_detail_design_fields] Success")
            return mapped
        except Exception as e:
            error_message = (
                "[DetailDesignService._map_detail_design_fields] Error: %s" % (e,)
            )
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
            error_message = "[_extract_project_name] Error: %s" % e
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return "project"

    def _ensure_detail_design_document_id(
        self, document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Ensure document has id field

        Args:
            document: MongoDB document

        Returns:
            Document with id field
        """
        logger.info(
            "[DetailDesignService._ensure_detail_design_document_id] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )

        if not document:
            logger.warning(
                "[DetailDesignService._ensure_detail_design_document_id] Missing document"
            )
            return {}

        try:
            resolved_id = self._extract_detail_design_document_id(document)
            if resolved_id:
                document["id"] = resolved_id

            logger.info(
                "[DetailDesignService._ensure_detail_design_document_id] Success"
            )
            return document
        except Exception as e:
            error_message = (
                "[DetailDesignService._ensure_detail_design_document_id] Error: %s"
                % (e,)
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _extract_detail_design_document_id(self, document: Dict[str, Any]) -> str:
        """
        Extract identifier from detail design document

        Args:
            document: MongoDB document

        Returns:
            Document ID as string
        """
        logger.info(
            "[DetailDesignService._extract_detail_design_document_id] Start - document_keys=%s",
            list(document.keys()) if document else None,
        )

        if not document:
            logger.warning(
                "[DetailDesignService._extract_detail_design_document_id] Missing document input"
            )
            return ""

        try:
            doc_id = document.get("id")
            if isinstance(doc_id, str) and doc_id.strip():
                resolved_id = doc_id.strip()
            else:
                fallback_id = document.get("_id")
                resolved_id = str(fallback_id) if fallback_id else ""

            logger.info(
                "[DetailDesignService._extract_detail_design_document_id] Success - document_id=%s",
                resolved_id,
            )
            return resolved_id
        except Exception as e:
            error_message = (
                "[DetailDesignService._extract_detail_design_document_id] Error: %s"
                % (e,)
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _validate_user_authorization(
        self, user_role: str, user_id: str, project_id: str
    ) -> None:
        """Validate whether the user can access a project's detail design"""
        logger.info(
            "[DetailDesignService._validate_user_authorization] Start - project_id=%s, user_id=%s",
            project_id,
            user_id,
        )

        if not user_id or not project_id:
            logger.warning(
                "[DetailDesignService._validate_user_authorization] Missing input"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            normalized_role = user_role or PermissionLevel.USER
            if normalized_role == PermissionLevel.ADMIN:
                logger.info(
                    "[DetailDesignService._validate_user_authorization] Admin role granted"
                )
                return

            has_access = await self.project_service.check_user_project_access(
                user_id=user_id, project_id=project_id
            )
            if not has_access:
                logger.warning(
                    "[DetailDesignService._validate_user_authorization] Access denied - project_id=%s, user_id=%s",
                    project_id,
                    user_id,
                )
                raise_http_error(status.HTTP_403_FORBIDDEN)

            logger.info(
                "[DetailDesignService._validate_user_authorization] Access granted - project_id=%s, user_id=%s",
                project_id,
                user_id,
            )
        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[DetailDesignService._validate_user_authorization] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _log_detail_design_download_activity(
        self,
        project: Dict[str, Any],
        total_documents: int,
        project_id: str,
        user_id: str,
        activity_type: str,
        file_ids: List[str],
        file_type: str,
    ) -> None:
        """
        Log detail design download activity

        Args:
            project: Project data
            total_documents: Total number of documents
            project_id: Project ID
            user_id: User ID
            activity_type: Activity type
            file_ids: List of file IDs
            file_type: File type
        """
        logger.info(
            "[DetailDesignService._log_detail_design_download_activity] Start - project_id=%s, user_id=%s, total_documents=%s",
            project_id,
            user_id,
            total_documents,
        )

        try:
            from app.utils.download_utils import create_download_activity

            file_count = len(file_ids) if file_ids else total_documents
            await create_download_activity(
                project_id=project_id,
                user_id=user_id,
                file_count=file_count,
                document_type=file_type,
            )

            logger.info(
                "[DetailDesignService._log_detail_design_download_activity] Success - file_count=%s",
                file_count,
            )
        except Exception as e:
            error_message = (
                "[DetailDesignService._log_detail_design_download_activity] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

    def find_java_executable(self) -> str:
        """Find Java executable path"""
        logger.info("[find_java_executable] Start")
        try:
            # Try config JAVA_PATH first
            java_path_from_config = getattr(GatewayConfig, "JAVA_PATH", None)
            if java_path_from_config and os.path.isfile(java_path_from_config):
                logger.info(
                    "[find_java_executable] Success - path=%s (from config)",
                    java_path_from_config,
                )
                return java_path_from_config

            if platform.system() == "Windows":
                # Try to find java.exe in PATH first
                try:
                    result = subprocess.run(
                        ["where", "java.exe"],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        java_path = result.stdout.strip().split("\n")[0].strip()
                        if java_path and os.path.isfile(java_path):
                            logger.info(
                                "[find_java_executable] Success - path=%s", java_path
                            )
                            return java_path
                except Exception:
                    pass

                # Try JAVA_HOME
                java_home = os.environ.get("JAVA_HOME", "")
                if java_home:
                    java_path = os.path.join(java_home, "bin", "java.exe")
                    if os.path.isfile(java_path):
                        logger.info(
                            "[find_java_executable] Success - path=%s", java_path
                        )
                        return java_path

                # Try common Program Files paths
                program_files = os.environ.get("ProgramFiles", "")
                program_files_x86 = os.environ.get("ProgramFiles(x86)", "")
                for pf in [program_files, program_files_x86]:
                    if pf:
                        for java_dir in ["Java", "Eclipse Adoptium", "Microsoft"]:
                            java_path = os.path.join(pf, java_dir, "bin", "java.exe")
                            if os.path.isfile(java_path):
                                logger.info(
                                    "[find_java_executable] Success - path=%s",
                                    java_path,
                                )
                                return java_path

                raise FileNotFoundError(
                    "Could not find 'java.exe'. Ensure Java is installed and added to PATH or set JAVA_PATH in config."
                )
            else:
                java_path = subprocess.run(
                    ["which", "java"], capture_output=True, text=True
                ).stdout.strip()
                if not java_path or not os.path.isfile(java_path):
                    raise FileNotFoundError(
                        "Could not find 'java'. Ensure Java is installed and added to PATH or set JAVA_PATH in config."
                    )
                logger.info("[find_java_executable] Success - path=%s", java_path)
                return java_path
        except Exception as e:
            error_message = "[find_java_executable] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def extract_plantuml_content(self, content: str) -> Optional[str]:
        """Extract PlantUML content from markdown code block or return as-is"""
        logger.info("[extract_plantuml_content] Start")

        if not content or not isinstance(content, str):
            logger.info("[extract_plantuml_content] Empty or invalid content")
            return None

        try:
            # Check if content contains PlantUML code blocks
            pattern = r"```(?:plantuml|puml)?\s*\n(.*?)```"
            matches = re.findall(pattern, content, re.DOTALL | re.IGNORECASE)

            if matches:
                # Extract first PlantUML block
                puml_content = matches[0].strip()
                logger.info(
                    "[extract_plantuml_content] Extracted from markdown code block"
                )
                return puml_content

            # Check if content is already PlantUML (contains @startuml)
            if "@startuml" in content.lower():
                logger.info("[extract_plantuml_content] Content is already PlantUML")
                return content.strip()

            # If no PlantUML found, return None
            logger.warning("[extract_plantuml_content] No PlantUML content found")
            return None

        except Exception as e:
            error_message = f"[extract_plantuml_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return None

    def convert_plantuml_to_images(self, markdown_content: str) -> str:
        """
        Convert PlantUML code blocks in markdown to base64 images.

        Args:
            markdown_content: Markdown string that may contain PlantUML code blocks

        Returns:
            Markdown string with PlantUML blocks replaced by base64 images
        """
        if not markdown_content:
            return markdown_content

        try:
            # Pattern to match ```plantuml ... ``` code blocks
            pattern = r"```plantuml\s*\n(.*?)\n```"

            def replace_plantuml(match):
                """Replace a single PlantUML block with an image"""
                puml_content = match.group(1).strip()

                if not puml_content:
                    return match.group(0)  # Return original if empty

                try:
                    # Generate base64 image
                    image_base64 = self.generate_puml_image(puml_content)

                    if image_base64:
                        # Replace with markdown image syntax
                        return (
                            f"![PlantUML Diagram](data:image/png;base64,{image_base64})"
                        )
                    else:
                        # If image generation failed, keep original code block
                        logger.warning(
                            "[convert_plantuml_to_images] Failed to generate image for PlantUML block"
                        )
                        return match.group(0)

                except Exception as e:
                    logger.warning(
                        f"[convert_plantuml_to_images] Error processing PlantUML block: {e}"
                    )
                    return match.group(0)  # Return original on error

            # Replace all PlantUML blocks
            result = re.sub(
                pattern, replace_plantuml, markdown_content, flags=re.DOTALL
            )

            return result

        except Exception as e:
            error_message = f"[convert_plantuml_to_images] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            return markdown_content  # Return original on error

    def generate_puml_image(self, puml_content: str) -> Optional[str]:
        """Generate base64-encoded PNG image from PlantUML content"""
        logger.info("[generate_puml_image] Start")
        if not puml_content:
            logger.warning("[generate_puml_image] Empty puml_content")
            return None

        try:

            # normalize puml content
            puml_content = self.normalize_puml_content(puml_content)
            # Get paths
            project_dir = os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            )
            plantuml_jar_path = os.path.join(project_dir, "plantuml.jar")
            if not os.path.exists(plantuml_jar_path):
                logger.warning(
                    "[generate_puml_image] plantuml.jar not found at %s",
                    plantuml_jar_path,
                )
                return None

            java_path = self.find_java_executable()

            with tempfile.TemporaryDirectory() as temp_dir:
                puml_file_path = os.path.join(temp_dir, "diagram.puml")

                # Write PUML file
                with open(puml_file_path, "w", encoding="utf-8") as puml_file:
                    puml_file.write(puml_content.strip())

                # Generate PNG
                subprocess.run(
                    [
                        java_path,
                        "-DPLANTUML_LIMIT_SIZE=8192",
                        "-Dfile.encoding=UTF-8",
                        "-jar",
                        plantuml_jar_path,
                        "-tpng",
                        puml_file_path,
                    ],
                    check=False,
                    capture_output=True,
                )

                # Check if PNG was generated
                generated_png_path = puml_file_path.replace(".puml", ".png")
                if os.path.exists(generated_png_path):
                    # Read and encode to base64
                    with open(generated_png_path, "rb") as img_file:
                        image_bytes = img_file.read()
                        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
                        logger.info(
                            "[generate_puml_image] Success - size=%s bytes",
                            len(image_bytes),
                        )
                        return image_base64

            logger.warning("[generate_puml_image] PNG file was not generated")
            return None
        except Exception as e:
            error_message = "[generate_puml_image] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return None

    # region normalize puml content
    def normalize_puml_content(self, puml: str) -> str:
        lines = puml.strip().splitlines()
        if not lines:
            return puml

        first = lines[0].strip()
        title = None

        if first.startswith("@startuml") and first != "@startuml":
            title = first[len("@startuml") :].strip()
            lines[0] = "@startuml"

        if title:
            body = "\n".join(lines[1:])
            if title in body:
                return "\n".join(lines)
            else:
                lines.insert(1, f"title {title}")

        return "\n".join(lines)

    # #endregion


detail_design_service = DetailDesignService()
