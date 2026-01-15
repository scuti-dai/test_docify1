"""
Requirement Document service for managing requirement documents in MongoDB
"""

import logging
import base64
from datetime import datetime
from functools import partial
from typing import List, Dict, Any, Optional
from uuid6 import uuid7
from fastapi import HTTPException, status

from app.services.project_service import ProjectService
from app.utils.constants import FileStatus, ContentType, SyncStatus
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import get_current_utc_time
from app.services.download_service import download_service
from app.services.git_services.git_push import (
    push_files_to_git,
    check_conflicts_before_push,
    fetch_remote_file_content,
)
from app.services.git_services.git_service import normalize_target_directory
from app.utils.download_utils import (
    create_download_activity,
    extract_requirement_content,
    fetch_referenced_images,
)

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


class RequirementDocumentService:
    """Service for requirement document operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(RequirementDocumentService, cls).__new__(cls)
        return cls._instance

    # region Public Methods

    def __init__(self, repository=None):
        if not RequirementDocumentService._initialized:
            self.project_service = ProjectService()
            RequirementDocumentService._initialized = True
            logger.info("[RequirementDocumentService] Singleton instance initialized")

        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info(
            "[RequirementDocumentService] Repository updated via set_repository"
        )

    async def create_requirement_document(
        self,
        project_id: str,
        file_name: str,
        file_content: bytes,
        content_type: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Create a new requirement document or update existing one with same file name"""
        logger.info(
            f"[create_requirement_document] Start - project_id={project_id}, file_name={file_name}, user_id={user_id}"
        )

        if not file_name or not project_id or not user_id:
            logger.warning("[create_requirement_document] Missing required parameters")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate user has permission to upload to this project
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(403)

            # Determine file type and prepare content
            file_extension = "." + file_name.split(".")[-1].lower()
            rd_content = self._prepare_file_content(
                file_content, file_extension, file_name, content_type
            )

            current_time = get_current_utc_time()

            # Check if file with same name already exists in this project
            duplicate_file_query = {
                "project_id": project_id,
                "file_name": file_name,
                "deleted_at": None,
            }
            duplicate_file = await self.repository.find_one(duplicate_file_query)

            if duplicate_file:
                return await self._update_existing_requirement_document(
                    duplicate_file=duplicate_file,
                    rd_content=rd_content,
                    current_time=current_time,
                    project_id=project_id,
                    file_name=file_name,
                    user_id=user_id,
                )

            return await self._create_new_requirement_document(
                project_id=project_id,
                file_name=file_name,
                rd_content=rd_content,
                current_time=current_time,
                user_id=user_id,
            )

        except Exception as e:
            error_message = f"[create_requirement_document] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_requirement_documents_list(
        self, project_id: str, user_id: str
    ) -> List[Dict[str, Any]]:
        """Get all requirement documents for a project with user validation"""
        logger.info(
            f"[get_requirement_documents_list] Fetching documents for project: {project_id}"
        )

        if not project_id or not user_id:
            logger.warning(
                "[get_requirement_documents_list] Missing required input - project_id or user_id"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate user has permission to access this project
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(403)

            documents = await self.repository.list_by_project(project_id)

            formatted_documents = []
            for document in documents:
                commit_id = document.get("commit_id") or ""
                sync_status = document.get("sync_status") or ""

                # Determine status: Local if no commit_id, Git if has commit_id
                file_status = FileStatus.GIT if commit_id else FileStatus.LOCAL

                formatted_documents.append(
                    {
                        "id": document.get("id"),
                        "file_name": document.get("file_name"),
                        "status": file_status,
                        "git": {
                            "commit_id": commit_id,
                            "sync_status": sync_status,
                        },
                        "created_at": document.get("created_at"),
                        "updated_at": document.get("updated_at"),
                    }
                )

            logger.info(
                f"[get_requirement_documents_list] Found {len(formatted_documents)} documents"
            )
            return formatted_documents

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[get_requirement_documents_list] Error: {e}"
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

    async def delete_multiple_requirement_documents(
        self, document_ids: List[str], user_id: str, project_id: str
    ) -> Dict[str, List[str]]:
        """Delete multiple requirement documents with access validation"""
        logger.info(
            f"[delete_multiple_requirement_documents] Start - document_ids={document_ids}, user_id={user_id}"
        )

        if not document_ids:
            logger.warning(
                "[delete_multiple_requirement_documents] No document IDs provided"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate user has permission to delete from this project
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(403)

            current_time = get_current_utc_time()

            deleted_ids = []
            failed_ids = []

            for document_id in document_ids:
                try:
                    # Check if document exists and belongs to the project
                    query_params = {
                        "id": document_id,
                        "project_id": project_id,
                        "deleted_at": None,
                    }
                    doc = await self.repository.find_one(query_params)

                    if not doc:
                        logger.warning(
                            f"[delete_multiple_requirement_documents] Document not found: {document_id}"
                        )
                        failed_ids.append(document_id)
                        continue

                    # Check if document has commit_id
                    commit_id = doc.get("commit_id") or ""
                    has_commit_id = (
                        commit_id and commit_id.strip() and commit_id != "null"
                    )

                    if has_commit_id:
                        # If has commit_id, set sync_status to delete_push (not deleted_at)
                        is_updated = (
                            await self.repository.update_sync_status_for_delete(
                                document_id=document_id,
                                project_id=project_id,
                                sync_status=SyncStatus.DELETE_PUSH,
                                updated_at=current_time,
                            )
                        )

                        if is_updated:
                            deleted_ids.append(document_id)
                            logger.info(
                                f"[delete_multiple_requirement_documents] Document marked for delete_push: {document_id}"
                            )
                        else:
                            failed_ids.append(document_id)
                            logger.warning(
                                f"[delete_multiple_requirement_documents] Failed to update sync_status: {document_id}"
                            )
                    else:
                        # If no commit_id, soft delete the document
                        is_deleted = await self.repository.soft_delete_document(
                            document_id=document_id,
                            project_id=project_id,
                            deleted_at=current_time,
                        )

                        if is_deleted:
                            deleted_ids.append(document_id)
                            logger.info(
                                f"[delete_multiple_requirement_documents] Document deleted: {document_id}"
                            )
                        else:
                            failed_ids.append(document_id)
                            logger.warning(
                                f"[delete_multiple_requirement_documents] Failed to delete: {document_id}"
                            )

                except Exception as e:
                    error_message = f"[delete_multiple_requirement_documents] Error deleting {document_id}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

                    failed_ids.append(document_id)

            logger.info(
                f"[delete_multiple_requirement_documents] Completed - deleted: {len(deleted_ids)}, failed: {len(failed_ids)}"
            )
            return {"deleted_ids": deleted_ids, "failed_ids": failed_ids}

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[delete_multiple_requirement_documents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def find_one_requirement_document(
        self, query_params: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Find one requirement document with flexible query parameters

        Args:
            query_params: Dictionary of query parameters (e.g., {"id": "123", "project_id": "proj_1", "deleted_at": None})

        Returns:
            Document if found, None otherwise
        """
        logger.info(
            f"[find_one_requirement_document] Start - query_params={query_params}"
        )

        try:
            document = await self.repository.find_one(query_params)

            if document:
                logger.info(
                    f"[find_one_requirement_document] Found document with query: {query_params}"
                )
            else:
                logger.info(
                    f"[find_one_requirement_document] No document found with query: {query_params}"
                )

            return document

        except Exception as e:
            error_message = f"[find_one_requirement_document] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_requirement_document_detail(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        user_role: str,
    ) -> Dict[str, Any]:
        """Get requirement document detail by ID with authorization check"""
        logger.info(
            f"[get_requirement_document_detail] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not user_id:
            logger.warning(
                "[get_requirement_document_detail] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate user access
            await self._validate_detail_access(user_id, user_role, project_id)

            # Query document from repository
            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[get_requirement_document_detail] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Map document to response format
            result = self._map_requirement_document_detail(document)
            logger.info(
                f"[get_requirement_document_detail] Success - file_id={file_id}"
            )
            return result

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[get_requirement_document_detail] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def download_requirement_documents(
        self,
        project_id: str,
        file_ids: List[str],
        user_id: str,
        user_role: str,
    ) -> Dict[str, Any]:
        """Download requirement documents for a project.
        Automatically includes referenced images from markdown files."""
        logger.info(
            f"[download_requirement_documents] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning(
                "[download_requirement_documents] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            project = await download_service.validate_project_download(
                project_id=project_id,
                user_id=user_id,
                user_role=user_role,
                project_service=self.project_service,
            )

            documents = await self.repository.fetch_for_download(
                project_id=project_id,
                file_ids=file_ids,
            )

            # Determine download strategy based on file type and count
            # Strategy:
            # 1. Single image → Download directly (no ZIP)
            # 2. Single markdown WITHOUT images → Download directly (no ZIP)
            # 3. Single markdown WITH images → Download as ZIP (markdown + images)
            # 4. Multiple files → Download as ZIP

            referenced_images = []

            # Only fetch referenced images if we have markdown documents
            markdown_documents = [
                doc
                for doc in documents
                if doc.get("rd_content", {}).get("type") == ContentType.MARKDOWN
            ]

            if markdown_documents:
                referenced_images = await fetch_referenced_images(
                    project_id=project_id,
                    documents=markdown_documents,
                    collection_type="requirement_document",
                )

                # Add images to documents list (avoid duplicates)
                existing_ids = {doc.get("id") for doc in documents}
                for img_doc in referenced_images:
                    img_id = img_doc.get("id") or str(img_doc.get("_id", ""))
                    if img_id and img_id not in existing_ids:
                        documents.append(self.repository.ensure_document_id(img_doc))
                        existing_ids.add(img_id)

            document_count = len(documents)
            created_at = get_current_utc_time()
            activity_handler = partial(
                self._log_requirement_download_activity,
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
                file_type="requirement document",
                file_suffix="requirement",
            )

            resolvers = download_service.create_resolver_config(
                document_id_resolver=self.repository.extract_document_id,
                document_normalizer=self.repository.ensure_document_id,
                content_extractor=extract_requirement_content,
            )

            handlers = download_service.create_file_handlers()

            result = await download_service.build_download_payload(
                documents=documents,
                context=context,
                resolvers=resolvers,
                handlers=handlers,
                activity_logger=activity_handler,
            )

            logger.info(
                "[download_requirement_documents] Success - download payload prepared"
            )
            return result

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[download_requirement_documents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def push_git_requirement_documents(
        self,
        project_id: str,
        file_ids: List[str],
        commit_message: str,
        user_name: str,
        token_password: Optional[str],
        user_id: str,
    ) -> Dict[str, Any]:
        """Push requirement documents to Git repository"""
        logger.info(
            f"[push_git_requirement_documents] Start - project_id={project_id}, file_ids={file_ids}, user_id={user_id}"
        )

        if not project_id or not file_ids or not user_id:
            logger.warning(
                "[push_git_requirement_documents] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(403)

            # Get project to retrieve Git info and directory
            project = await self.project_service.get_project_by_id(project_id)
            if not project:
                logger.warning(
                    f"[push_git_requirement_documents] Project not found - project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get Git repository info
            git_info = project.setting_item.git if project.setting_item else None
            if not git_info or not git_info.repository or not git_info.branch:
                logger.warning(
                    "[push_git_requirement_documents] Project does not have Git configuration"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            repository_url = git_info.repository
            branch_name = git_info.branch

            # Get directory structure
            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.rd:
                logger.warning(
                    "[push_git_requirement_documents] Project does not have RD directory configured"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="RD_DIRECTORY_NOT_CONFIGURED"
                )

            target_directory = directory.rd.strip("/")

            # Normalize target_directory when src is "/"
            src_path = directory.src if directory else None
            target_directory = normalize_target_directory(target_directory, src_path)

            # Get files from repository
            documents = await self.repository.fetch_for_download(project_id, file_ids)

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
                    "[push_git_requirement_documents] No files with valid sync_status to push"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="FILE_NOT_CHANGED"
                )

            # Separate files to push and files to delete
            files_to_push = []
            files_to_delete = []
            for doc in filtered_documents:
                sync_status = doc.get("sync_status") or ""
                if sync_status == SyncStatus.DELETE_PUSH:
                    # For delete_push, just add file name to delete list
                    file_name = doc.get("file_name")
                    if file_name:
                        files_to_delete.append(file_name)
                    continue
                file_name = doc.get("file_name")
                rd_content = doc.get("rd_content", {})
                content_type = rd_content.get("type")
                content = rd_content.get("content")

                if not file_name or not content:
                    logger.warning(
                        f"[push_git_requirement_documents] Skipping document with missing content - doc_id={doc.get('id')}"
                    )
                    continue

                # Convert content based on type
                if content_type == ContentType.MARKDOWN:
                    # Markdown is stored as string
                    file_content = (
                        content.encode("utf-8") if isinstance(content, str) else content
                    )
                elif content_type == ContentType.IMAGE:
                    # Image is stored as base64 string
                    try:
                        file_content = base64.b64decode(content)
                    except Exception as e:
                        error_message = f"[push_git_requirement_documents] Failed to decode base64 content for {file_name}: {e}"
                        logger.warning(error_message)
                        save_exception_log_sync(
                            e, error_message, __name__, level=LogLevel.WARNING
                        )

                        continue
                else:
                    logger.warning(
                        f"[push_git_requirement_documents] Unsupported content type: {content_type}"
                    )
                    continue

                files_to_push.append(
                    {
                        "file_name": file_name,
                        "content": file_content,
                    }
                )

            if not files_to_push and not files_to_delete:
                logger.warning(
                    "[push_git_requirement_documents] No valid files to push or delete after processing"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="NO_VALID_FILES_TO_PUSH"
                )

            # Check for conflicts before pushing (only for files that have commit_id)
            collection_name = getattr(self.repository, "collection_name", None)
            files_with_commit_id = [
                {
                    "file_name": doc.get("file_name"),
                    "local_commit_id": doc.get("commit_id") or "",
                    "file_id": doc.get("id"),
                    "project_id": project_id,
                    "collection_name": collection_name,
                }
                for doc in filtered_documents
                if doc.get("commit_id")
                and doc.get("commit_id").strip()
                and doc.get("commit_id") != "null"
                and (doc.get("sync_status") or "") != SyncStatus.DELETE_PUSH
            ]

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
                        f"[push_git_requirement_documents] Conflicts detected - count={len(conflict_files)}"
                    )
                    # Raise conflict exception with conflict files
                    from app.schemas.base import ConflictResponse, ConflictFileInfo

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
            # Returns dict {"commit_id": "sha", "time": "iso"} or str "sha" (legacy)
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

            # Extract commit hash string for document updates
            if isinstance(push_result, dict):
                commit_id = push_result.get("commit_id")
            else:
                commit_id = push_result

            # Get current time for deleted_at (for delete_push files)
            current_time = get_current_utc_time()

            # Update sync_status and commit_id for pushed files
            pushed_file_ids = [doc.get("id") for doc in filtered_documents]

            # Separate files that were delete_push (need to update deleted_at too)
            delete_push_file_ids = [
                doc.get("id")
                for doc in filtered_documents
                if (doc.get("sync_status") or "") == SyncStatus.DELETE_PUSH
            ]
            other_file_ids = [
                doc_id
                for doc_id in pushed_file_ids
                if doc_id not in delete_push_file_ids
            ]

            # Update files that were delete_push: sync_status, commit_id, and deleted_at
            if delete_push_file_ids:
                updated_delete_count = (
                    await self.repository.update_sync_status_commit_id_and_deleted_at(
                        document_ids=delete_push_file_ids,
                        project_id=project_id,
                        commit_id=commit_id,
                        sync_status=SyncStatus.SYNCED,
                        deleted_at=current_time,
                    )
                )
                logger.info(
                    f"[push_git_requirement_documents] Updated delete_push files - count={updated_delete_count}"
                )

            # Update other files: only sync_status and commit_id
            if other_file_ids:
                updated_other_count = (
                    await self.repository.update_sync_status_and_commit_id(
                        document_ids=other_file_ids,
                        project_id=project_id,
                        commit_id=commit_id,
                        sync_status=SyncStatus.SYNCED,
                    )
                )
                logger.info(
                    f"[push_git_requirement_documents] Updated other files - count={updated_other_count}"
                )

            total_updated = len(delete_push_file_ids) + len(other_file_ids)
            logger.info(
                f"[push_git_requirement_documents] Success - commit_id={commit_id}, total_updated={total_updated}"
            )

            # Update project commit_ids after successful push
            # Add the new commit_id to the project's commit_ids array
            try:
                # Add new commit_id to project using shared function
                # Note: push_result might be dict (with time) or str. add_commit_id_to_project handles both.
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
                error_message = f"[push_git_requirement_documents] Failed to update project commit_ids: {e}"
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
            error_message = f"[push_git_requirement_documents] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def restore_deleted_requirement_document(
        self,
        project_id: str,
        file_id: str,
        user_id: str,
        user_name: str,
        token_password: Optional[str],
    ) -> Dict[str, Any]:
        """Restore a requirement document that is marked as delete_push"""
        logger.info(
            "[restore_deleted_requirement_document] Start - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )

        if not project_id or not file_id or not user_id or not user_name:
            logger.warning(
                "[restore_deleted_requirement_document] Missing required parameters - project_id=%s, file_id=%s, user_id=%s, user_name=%s",
                project_id,
                file_id,
                user_id,
                user_name,
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(status.HTTP_403_FORBIDDEN)

            document = await self.repository.find_one(
                {"id": file_id, "project_id": project_id, "deleted_at": None}
            )
            if not document:
                logger.warning(
                    "[restore_deleted_requirement_document] Document not found - file_id=%s",
                    file_id,
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            sync_status = (document.get("sync_status") or "").lower()
            if sync_status != SyncStatus.DELETE_PUSH:
                logger.warning(
                    "[restore_deleted_requirement_document] File not marked as delete_push - file_id=%s, sync_status=%s",
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
                    "[restore_deleted_requirement_document] Missing git configuration - project_id=%s",
                    project_id,
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_NOT_CONFIGURED"
                )

            directory = project.setting_item.directory if project.setting_item else None
            if not directory or not directory.rd:
                logger.warning(
                    "[restore_deleted_requirement_document] Missing RD directory configuration - project_id=%s",
                    project_id,
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST,
                    error_key="RD_DIRECTORY_NOT_CONFIGURED",
                )

            remote_relative_path = self._build_remote_file_path(
                directory.rd, document.get("file_name")
            )
            local_bytes = self._extract_requirement_local_bytes(document)

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
                    "[restore_deleted_requirement_document] Failed to update document - file_id=%s",
                    file_id,
                )
                raise_http_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_key="RESTORE_UPDATE_FAILED",
                )

            logger.info(
                "[restore_deleted_requirement_document] Success - file_id=%s, sync_status=%s",
                file_id,
                new_sync_status,
            )
            return {"file_id": file_id, "sync_status": new_sync_status}

        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[restore_deleted_requirement_document] Error - file_id=%s: %s"
                % (
                    file_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_requirement_document_content(
        self,
        project_id: str,
        file_id: str,
        content: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Update requirement document content with sync_status logic"""
        logger.info(
            f"[update_requirement_document_content] Start - project_id={project_id}, file_id={file_id}, user_id={user_id}"
        )

        if not project_id or not file_id or not content or not user_id:
            logger.warning(
                "[update_requirement_document_content] Missing required parameters"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Check user authorization
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning(
                    f"[update_requirement_document_content] Access denied - user_id={user_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_403_FORBIDDEN)

            # Find the document
            query_params = {
                "id": file_id,
                "project_id": project_id,
                "deleted_at": None,
            }
            document = await self.repository.find_one(query_params)

            if not document:
                logger.warning(
                    f"[update_requirement_document_content] Document not found - file_id={file_id}, project_id={project_id}"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Get current sync_status and rd_content
            current_sync_status = document.get("sync_status") or ""
            rd_content = document.get("rd_content", {})
            content_type = rd_content.get("type", ContentType.MARKDOWN)

            # Determine new sync_status
            if current_sync_status == SyncStatus.PULL:
                new_sync_status = SyncStatus.PULL_PUSH
            else:
                new_sync_status = SyncStatus.PUSH

            # Update rd_content preserving type
            updated_rd_content = {
                "type": content_type,
                "content": content,
            }

            # Prepare update data
            current_time = get_current_utc_time()
            update_data = {
                "rd_content": updated_rd_content,
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
                    f"[update_requirement_document_content] Failed to update document - file_id={file_id}"
                )
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            logger.info(
                f"[update_requirement_document_content] Success - file_id={file_id}, sync_status={new_sync_status}"
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
            error_message = f"[update_requirement_document_content] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion

    # region Private Methods

    def _build_response_payload(
        self,
        document_id: str,
        file_name: str,
        user_id: str,
        project_id: str,
        created_at: datetime,
        updated_at: datetime,
    ) -> Dict[str, Any]:
        """Build response payload for requirement document operations."""
        try:
            return {
                "file_id": document_id,
                "file_name": file_name,
                "uploaded_by": user_id,
                "project_id": project_id,
                "created_at": created_at,
                "updated_at": updated_at,
            }
        except Exception as e:
            error_message = "[_build_response_payload] Error: %s" % (e,)
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

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

    def _prepare_file_content(
        self,
        file_content: bytes,
        file_extension: str,
        file_name: str,
        content_type: str,
    ) -> Dict[str, Any]:
        """Prepare file content based on file type."""
        logger.info(
            "[_prepare_file_content] Start - file_name=%s, file_extension=%s, content_type=%s",
            file_name,
            file_extension,
            content_type,
        )

        if not file_content or not file_extension:
            logger.warning("[_prepare_file_content] Missing file content or extension")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            if file_extension == ".md":
                markdown_content = file_content.decode("utf-8")
                logger.info(
                    "[_prepare_file_content] Processed markdown - file_name=%s",
                    file_name,
                )
                return {"type": ContentType.MARKDOWN, "content": markdown_content}

            encoded_content = base64.b64encode(file_content).decode("utf-8")
            logger.info(
                "[_prepare_file_content] Processed binary - file_name=%s", file_name
            )
            return {"type": ContentType.IMAGE, "content": encoded_content}

        except Exception as e:
            error_message = "[_prepare_file_content] Error - file_name=%s: %s" % (
                file_name,
                e,
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _update_existing_requirement_document(
        self,
        duplicate_file: Dict[str, Any],
        rd_content: Dict[str, Any],
        current_time: datetime,
        project_id: str,
        file_name: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """Update content for existing requirement document."""
        logger.info(
            "[_update_existing_requirement_document] Start - project_id=%s, file_name=%s",
            project_id,
            file_name,
        )

        if not duplicate_file:
            logger.warning(
                "[_update_existing_requirement_document] Missing duplicate_file input"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            document_id = self.repository.extract_document_id(duplicate_file)
            if not document_id:
                logger.warning(
                    "[_update_existing_requirement_document] Unable to resolve document_id"
                )
                raise_http_error(status.HTTP_404_NOT_FOUND)

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
                "rd_content": rd_content,
                "updated_at": current_time,
                "sync_status": new_sync_status if new_sync_status else None,
            }
            is_updated = await self.repository.update_document_content(
                document_id=document_id,
                project_id=project_id,
                update_data=update_data,
            )

            if not is_updated:
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            response_payload = self._build_response_payload(
                document_id=document_id,
                file_name=file_name,
                user_id=user_id,
                project_id=project_id,
                created_at=duplicate_file.get("created_at"),
                updated_at=current_time,
            )
            logger.info(
                "[_update_existing_requirement_document] Success - document_id=%s",
                document_id,
            )
            return response_payload

        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[_update_existing_requirement_document] Error - project_id=%s, file_name=%s: %s"
                % (
                    project_id,
                    file_name,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _create_new_requirement_document(
        self,
        project_id: str,
        file_name: str,
        rd_content: Dict[str, Any],
        current_time: datetime,
        user_id: str,
    ) -> Dict[str, Any]:
        """Create a new requirement document."""
        logger.info(
            "[_create_new_requirement_document] Start - project_id=%s, file_name=%s",
            project_id,
            file_name,
        )

        if not project_id or not file_name or not rd_content:
            logger.warning(
                "[_create_new_requirement_document] Missing required input values"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            document_id = str(uuid7())
            document_doc = {
                "id": document_id,
                "project_id": project_id,
                "file_name": file_name,
                "rd_content": rd_content,
                "commit_id": None,
                "sync_status": None,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            is_inserted = await self.repository.insert_one(document_doc)
            if not is_inserted:
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

            response_payload = self._build_response_payload(
                document_id=document_id,
                file_name=file_name,
                user_id=user_id,
                project_id=project_id,
                created_at=current_time,
                updated_at=current_time,
            )
            logger.info(
                "[_create_new_requirement_document] Success - document_id=%s",
                document_id,
            )
            return response_payload

        except HTTPException:
            raise
        except Exception as e:
            error_message = (
                "[_create_new_requirement_document] Error - project_id=%s, file_name=%s: %s"
                % (
                    project_id,
                    file_name,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

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
    def _extract_requirement_local_bytes(document: Dict[str, Any]) -> bytes:
        logger.info("[_extract_requirement_local_bytes] Start")
        try:
            rd_content = (document or {}).get("rd_content") or {}
            content_type = (rd_content.get("type") or ContentType.MARKDOWN).lower()
            content_value = rd_content.get("content") or ""

            if content_type == ContentType.IMAGE:
                try:
                    decoded = base64.b64decode(content_value)
                    logger.info(
                        "[_extract_requirement_local_bytes] Success - image data"
                    )
                    return decoded
                except Exception as e:
                    error_message = (
                        "[_extract_requirement_local_bytes] Failed to decode image content: %s"
                        % (e,)
                    )
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

                    return b""

            encoded = str(content_value).encode("utf-8")
            logger.info("[_extract_requirement_local_bytes] Success - text data")
            return encoded
        except Exception as e:
            error_message = f"[_extract_requirement_local_bytes] Error: {e}"
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

    async def _log_requirement_download_activity(
        self,
        project: Dict[str, Any],
        total_documents: int,
        project_id: str,
        user_id: str,
        activity_type: str,
        file_ids: List[str],
        file_type: str,
    ) -> None:
        """Create activity log entry for requirement document download."""
        logger.info(
            "[_log_requirement_download_activity] Start - project_id=%s, user_id=%s, activity_type=%s",
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
                "[_log_requirement_download_activity] Success - file_count=%s",
                file_count,
            )
        except Exception as e:
            error_message = (
                "[_log_requirement_download_activity] Error - project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _validate_detail_access(
        self, user_id: str, user_role: str, project_id: str
    ) -> None:
        """Validate user access to requirement document detail"""
        logger.info(
            f"[_validate_detail_access] Start - user_id={user_id}, user_role={user_role}, project_id={project_id}"
        )

        try:
            from app.utils.constants import PermissionLevel

            # Admin has full access
            if user_role == PermissionLevel.ADMIN:
                logger.info(
                    "[_validate_detail_access] Admin access granted - user_id=%s",
                    user_id,
                )
                return

            # User must have project access
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                logger.warning(
                    "[_validate_detail_access] Access denied - user_id=%s, project_id=%s",
                    user_id,
                    project_id,
                )
                raise_http_error(403)

            logger.info(
                "[_validate_detail_access] Access granted - user_id=%s, project_id=%s",
                user_id,
                project_id,
            )

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[_validate_detail_access] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _map_requirement_document_detail(
        self, document: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Map requirement document to detail response format"""
        logger.info("[_map_requirement_document_detail] Start")

        try:
            document_id = document.get("id") or ""
            rd_content = document.get("rd_content") or {}
            commit_id = document.get("commit_id") or ""
            sync_status = document.get("sync_status") or ""

            result = {
                "id": document_id,
                "project_id": document.get("project_id") or "",
                "commit_id": commit_id,
                "sync_status": sync_status,
                "file_name": document.get("file_name") or "",
                "rd_content": {
                    "type": rd_content.get("type") or "",
                    "content": rd_content.get("content") or "",
                },
                "created_at": document.get("created_at") or "",
                "updated_at": document.get("updated_at") or "",
            }

            logger.info("[_map_requirement_document_detail] Success")
            return result

        except Exception as e:
            error_message = f"[_map_requirement_document_detail] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion


# Create service instance
requirement_document_service = RequirementDocumentService()
