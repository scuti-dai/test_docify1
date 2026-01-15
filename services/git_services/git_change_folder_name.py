"""
Git service for renaming folders in repository
"""

import logging
import tempfile
import os
import shutil
import re
from typing import Optional, Dict, List
from git import Repo, GitCommandError

from app.schemas.unit_test import UNIT_TEST_CODE_COLLECTION, UNIT_TEST_DESIGN_COLLECTION
from app.services.git_services.git_clone import _prepare_clone_url
from app.services.git_services.git_service import cleanup_temp_dir, clone_repository
from app.services.logs_service import save_exception_log_sync
from app.utils.constants import SyncStatus
from app.core.database import app_db
from cec_docifycode_common.models.detail_design import (
    DETAIL_DESIGN_TYPE_CD,
    DETAIL_DESIGN_TYPE_MD,
    DETAIL_DESIGN_TYPE_ID,
    DETAIL_DESIGN_TYPE_FS,
    DETAIL_DESIGN_TYPE_AC,
)
from app.utils.helpers import get_current_utc_time

logger = logging.getLogger(__name__)


async def rename_folder_and_push(
    project_id: str,
    directory_key: str,
    old_folder_name: str,
    new_folder_name: str,
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
) -> str:
    """
    Rename folder in git repository, push changes, and update DB documents

    Args:
        project_id: Project ID
        directory_key: Directory key (rd, bd, pd, cd, md, id, fs, ac, src, utd, utc)
        old_folder_name: Old folder name
        new_folder_name: New folder name
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password

    Returns:
        Commit ID after push
    """
    logger.info(
        f"[rename_folder_and_push] Start - project_id={project_id}, directory_key={directory_key}, old={old_folder_name}, new={new_folder_name}"
    )

    if (
        not project_id
        or not directory_key
        or not old_folder_name
        or not new_folder_name
    ):
        logger.warning("[rename_folder_and_push] Missing required parameters")
        raise ValueError("INVALID_INPUT")

    if not repository_url or not branch_name or not user_name:
        logger.warning("[rename_folder_and_push] Missing git credentials")
        raise ValueError("INVALID_GIT_CREDENTIALS")

    temp_dir = None
    try:
        # Clone repository to temporary directory
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)
        temp_dir = tempfile.mkdtemp(prefix="git_rename_folder_")
        logger.info(f"[rename_folder_and_push] Cloning to temp directory: {temp_dir}")

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)
        repo_root = repo.working_dir
        logger.info(f"[rename_folder_and_push] Repository cloned successfully")

        # Pull latest changes to ensure we have the latest code
        try:
            origin = repo.remote(name="origin")
            origin.pull(branch_name)
            logger.info(f"[rename_folder_and_push] Pulled latest changes from remote")
        except Exception as e:
            logger.warning(
                f"[rename_folder_and_push] Pull failed (may be up to date): {e}"
            )

        # Check if old folder exists
        old_folder_path = os.path.join(repo_root, old_folder_name)
        new_folder_path = os.path.join(repo_root, new_folder_name)

        if not os.path.exists(old_folder_path):
            logger.warning(
                f"[rename_folder_and_push] Old folder does not exist - old_folder_path={old_folder_path}"
            )
            return None

        if os.path.exists(new_folder_path):
            logger.warning(
                f"[rename_folder_and_push] New folder already exists - new_folder_path={new_folder_path}"
            )
            return None

        # Rename folder using git mv
        try:
            repo.git.mv(old_folder_name, new_folder_name)
            logger.info(f"[rename_folder_and_push] Folder renamed using git mv")
        except GitCommandError as e:
            error_message = f"[rename_folder_and_push] Git mv failed: {e}"
            logger.error(error_message)
            raise

        # Commit the change with format "changes folder name 「a」 to 「b」"
        commit_message = (
            f"changes folder name 「{old_folder_name}」 to 「{new_folder_name}」"
        )
        repo.index.commit(commit_message)
        logger.info(f"[rename_folder_and_push] Changes committed: {commit_message}")

        # Push to remote
        origin = repo.remote(name="origin")
        try:
            push_info = origin.push(branch_name)
            logger.info(
                f"[rename_folder_and_push] Changes pushed to remote - push_info={push_info}"
            )
        except GitCommandError as e:
            error_message = f"[rename_folder_and_push] Push failed: {e}"
            logger.error(error_message)
            raise

        # Get commit ID
        commit_id = repo.head.commit.hexsha
        logger.info(f"[rename_folder_and_push] Push successful - commit_id={commit_id}")

        # Update project commit_ids: add new commit_id to project's commit_ids list
        try:
            from app.services.project_service import project_service
            from cec_docifycode_common.models.project import Project

            project = await project_service.get_project_by_id(project_id)
            if project:
                await project_service.add_commit_id_to_project(
                    project=project,
                    new_commit_id=commit_id,
                    sync_status=None,  # Keep current sync_status
                )
                logger.info(
                    f"[rename_folder_and_push] Updated project commit_ids - project_id={project_id}, commit_id={commit_id}"
                )
            else:
                logger.warning(
                    f"[rename_folder_and_push] Project not found - project_id={project_id}"
                )
        except Exception as e:
            error_message = (
                f"[rename_folder_and_push] Failed to update project commit_ids: {e}"
            )
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__)
            # Don't fail the rename if commit_ids update fails

        # Update DB documents: update file_path for documents with commit_id and sync_status != "" or null
        await _update_documents_after_folder_rename(
            project_id=project_id,
            directory_key=directory_key,
            old_folder_name=old_folder_name,
            new_folder_name=new_folder_name,
            commit_id=commit_id,
        )

        # Update data_management for SRC, UTD, UTC (they have folder structure in data_management)
        if directory_key in ["src", "utd", "utc"]:
            try:
                await _update_data_management_folder_name(
                    project_id=project_id,
                    directory_key=directory_key,
                    old_folder_name=old_folder_name,
                    new_folder_name=new_folder_name,
                )
                logger.info(
                    f"[rename_folder_and_push] Updated data_management - project_id={project_id}, directory_key={directory_key}"
                )
            except Exception as e:
                error_message = (
                    f"[rename_folder_and_push] Failed to update data_management: {e}"
                )
                logger.warning(error_message)
                save_exception_log_sync(e, error_message, __name__)
                # Don't fail the rename if data_management update fails

        logger.info(
            f"[rename_folder_and_push] Success - project_id={project_id}, directory_key={directory_key}, commit_id={commit_id}"
        )

        return commit_id

    except Exception as e:
        error_message = f"[rename_folder_and_push] Error - project_id={project_id}, directory_key={directory_key}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise
    finally:
        # Clean up temporary directory
        cleanup_temp_dir(
            temp_dir=temp_dir,
            repo=repo,
        )


async def rename_multiple_folders_and_push(
    project_id: str,
    directory_changes: Dict[str, Dict[str, str]],
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
) -> str:
    """
    Rename multiple folders in git repository, push changes once, and update all DB documents

    Args:
        project_id: Project ID
        directory_changes: Dictionary of directory changes {dir_key: {"old": old_name, "new": new_name}}
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password

    Returns:
        Commit ID after push
    """
    logger.info(
        f"[rename_multiple_folders_and_push] Start - project_id={project_id}, changes_count={len(directory_changes)}"
    )

    if not project_id or not directory_changes:
        logger.warning("[rename_multiple_folders_and_push] Missing required parameters")
        raise ValueError("INVALID_INPUT")

    if not repository_url or not branch_name or not user_name:
        logger.warning("[rename_multiple_folders_and_push] Missing git credentials")
        raise ValueError("INVALID_GIT_CREDENTIALS")

    temp_dir = None
    try:
        # Clone repository to temporary directory (only once)
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)
        temp_dir = tempfile.mkdtemp(prefix="git_rename_folders_")
        logger.info(
            f"[rename_multiple_folders_and_push] Cloning to temp directory: {temp_dir}"
        )

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)
        repo_root = repo.working_dir
        logger.info(
            f"[rename_multiple_folders_and_push] Repository cloned successfully"
        )

        # Pull latest changes to ensure we have the latest code
        try:
            origin = repo.remote(name="origin")
            origin.pull(branch_name)
            logger.info(
                f"[rename_multiple_folders_and_push] Pulled latest changes from remote"
            )
        except Exception as e:
            logger.warning(
                f"[rename_multiple_folders_and_push] Pull failed (may be up to date): {e}"
            )

        # Rename all folders
        rename_operations = []
        skipped_operations = []

        for dir_key, change_info in directory_changes.items():
            old_folder_name = change_info.get("old")
            new_folder_name = change_info.get("new")

            if not old_folder_name or not new_folder_name:
                logger.warning(
                    f"[rename_multiple_folders_and_push] Missing old or new folder name for {dir_key} - skipping"
                )
                skipped_operations.append(dir_key)
                continue

            old_folder_path = os.path.join(repo_root, old_folder_name)
            new_folder_path = os.path.join(repo_root, new_folder_name)

            if not os.path.exists(old_folder_path):
                logger.warning(
                    f"[rename_multiple_folders_and_push] Old folder does not exist - dir_key={dir_key}, old_folder_path={old_folder_path}"
                )
                skipped_operations.append(dir_key)
                continue

            if os.path.exists(new_folder_path):
                logger.warning(
                    f"[rename_multiple_folders_and_push] New folder already exists - dir_key={dir_key}, new_folder_path={new_folder_path}"
                )
                skipped_operations.append(dir_key)
                continue

            # Rename folder using git mv
            try:
                repo.git.mv(old_folder_name, new_folder_name)
                rename_operations.append(
                    {
                        "dir_key": dir_key,
                        "old": old_folder_name,
                        "new": new_folder_name,
                    }
                )
                logger.info(
                    f"[rename_multiple_folders_and_push] Folder renamed - dir_key={dir_key}, old={old_folder_name}, new={new_folder_name}"
                )
            except GitCommandError as e:
                error_message = f"[rename_multiple_folders_and_push] Git mv failed for {dir_key}: {e}"
                logger.error(error_message)
                skipped_operations.append(dir_key)
                save_exception_log_sync(e, error_message, __name__)
                continue

        if not rename_operations:
            logger.warning(
                f"[rename_multiple_folders_and_push] No folders were renamed - project_id={project_id}"
            )
            return None

        # Commit all changes with a summary message
        commit_message_parts = []
        for op in rename_operations:
            commit_message_parts.append(f"「{op['old']}」 to 「{op['new']}」")

        commit_message = f"changes folder names: {', '.join(commit_message_parts)}"
        repo.index.commit(commit_message)
        logger.info(
            f"[rename_multiple_folders_and_push] Changes committed: {commit_message}"
        )

        # Push to remote (only once)
        origin = repo.remote(name="origin")
        try:
            push_info = origin.push(branch_name)
            logger.info(
                f"[rename_multiple_folders_and_push] Changes pushed to remote - push_info={push_info}"
            )
        except GitCommandError as e:
            error_message = f"[rename_multiple_folders_and_push] Push failed: {e}"
            logger.error(error_message)
            raise

        # Get commit ID
        commit_id = repo.head.commit.hexsha
        logger.info(
            f"[rename_multiple_folders_and_push] Push successful - commit_id={commit_id}"
        )

        # Update project commit_ids: add new commit_id to project's commit_ids list
        try:
            from app.services.project_service import project_service

            project = await project_service.get_project_by_id(project_id)
            if project:
                await project_service.add_commit_id_to_project(
                    project=project,
                    new_commit_id=commit_id,
                    sync_status=None,  # Keep current sync_status
                )
                logger.info(
                    f"[rename_multiple_folders_and_push] Updated project commit_ids - project_id={project_id}, commit_id={commit_id}"
                )
            else:
                logger.warning(
                    f"[rename_multiple_folders_and_push] Project not found - project_id={project_id}"
                )
        except Exception as e:
            error_message = f"[rename_multiple_folders_and_push] Failed to update project commit_ids: {e}"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__)
            # Don't fail if commit_ids update fails

        # Update DB documents for all renamed folders
        for op in rename_operations:
            try:
                await _update_documents_after_folder_rename(
                    project_id=project_id,
                    directory_key=op["dir_key"],
                    old_folder_name=op["old"],
                    new_folder_name=op["new"],
                    commit_id=commit_id,
                )
            except Exception as e:
                error_message = f"[rename_multiple_folders_and_push] Failed to update documents for {op['dir_key']}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)
                # Continue with other directories even if one fails

        # Update data_management for SRC, UTD, UTC
        for op in rename_operations:
            if op["dir_key"] in ["src", "utd", "utc"]:
                try:
                    await _update_data_management_folder_name(
                        project_id=project_id,
                        directory_key=op["dir_key"],
                        old_folder_name=op["old"],
                        new_folder_name=op["new"],
                    )
                    logger.info(
                        f"[rename_multiple_folders_and_push] Updated data_management - project_id={project_id}, directory_key={op['dir_key']}"
                    )
                except Exception as e:
                    error_message = f"[rename_multiple_folders_and_push] Failed to update data_management for {op['dir_key']}: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(e, error_message, __name__)
                    # Don't fail if data_management update fails

        logger.info(
            f"[rename_multiple_folders_and_push] Success - project_id={project_id}, commit_id={commit_id}, renamed_count={len(rename_operations)}, skipped_count={len(skipped_operations)}"
        )

        return commit_id

    except Exception as e:
        error_message = (
            f"[rename_multiple_folders_and_push] Error - project_id={project_id}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise
    finally:
        # Clean up temporary directory
        cleanup_temp_dir(
            temp_dir=temp_dir,
            repo=repo,
        )


async def _update_documents_after_folder_rename(
    project_id: str,
    directory_key: str,
    old_folder_name: str,
    new_folder_name: str,
    commit_id: str,
) -> None:
    """
    Update file_path, commit_id, sync_status in DB documents after folder rename
    Only update documents with commit_id and deleted_at = null

    Args:
        project_id: Project ID
        directory_key: Directory key (rd, bd, pd, cd, md, id, fs, ac, src, utd, utc)
        old_folder_name: Old folder name
        new_folder_name: New folder name
        commit_id: New commit ID after rename
    """
    logger.info(
        f"[_update_documents_after_folder_rename] Start - project_id={project_id}, directory_key={directory_key}, old={old_folder_name}, new={new_folder_name}"
    )

    try:
        # Map directory keys to repositories and get_all function parameters
        requirement_repo = app_db.requirement_document_repository

        # Repository map: (repository, get_all_params)
        # All collections use "file_path" as the field name
        FILE_PATH_FIELD = "file_path"

        requirement_repo = app_db.requirement_document_repository
        repository_map = {
            "rd": (requirement_repo, {}),
            "bd": (app_db.basic_design_repository, {}),
            "pd": (app_db.detail_design_repository, {}),
            "cd": (
                app_db.detail_design_repository,
                {"type": DETAIL_DESIGN_TYPE_CD},
            ),
            "md": (
                app_db.detail_design_repository,
                {"type": DETAIL_DESIGN_TYPE_MD},
            ),
            "id": (
                app_db.detail_design_repository,
                {"type": DETAIL_DESIGN_TYPE_ID},
            ),
            "fs": (
                app_db.detail_design_repository,
                {"type": DETAIL_DESIGN_TYPE_FS},
            ),
            "ac": (
                app_db.detail_design_repository,
                {"type": DETAIL_DESIGN_TYPE_AC},
            ),
            "src": (app_db.source_code_repository, {}),
            "utd": (
                app_db.unit_test_repository,
                {"collection_name": UNIT_TEST_DESIGN_COLLECTION},
            ),
            "utc": (
                app_db.unit_test_repository,
                {"collection_name": UNIT_TEST_CODE_COLLECTION},
            ),
        }

        if directory_key not in repository_map:
            logger.warning(
                f"[_update_documents_after_folder_rename] Unknown directory_key: {directory_key}"
            )
            return

        repository, get_all_params = repository_map[directory_key]

        # Get all documents with commit_id using repository function
        logger.info(
            f"[_update_documents_after_folder_rename] Getting all documents with commit_id - project_id={project_id}, directory_key={directory_key}, params={get_all_params}"
        )

        if not hasattr(repository, "get_all_with_commit_id"):
            logger.warning(
                f"[_update_documents_after_folder_rename] Repository does not have get_all_with_commit_id method - directory_key={directory_key}"
            )
            return

        all_documents = await repository.get_all_with_commit_id(
            project_id=project_id, **get_all_params
        )
        logger.info(
            f"[_update_documents_after_folder_rename] Found {len(all_documents)} total documents with commit_id - project_id={project_id}, directory_key={directory_key}"
        )

        # Filter documents by file_path (old_folder_name or new_folder_name)
        escaped_old_folder = re.escape(old_folder_name)
        escaped_new_folder = re.escape(new_folder_name)
        old_file_path_pattern = f"^{escaped_old_folder}(/|$)"
        new_file_path_pattern = f"^{escaped_new_folder}(/|$)"

        documents_to_update = []
        for doc in all_documents:
            file_path = doc.get(FILE_PATH_FIELD, "")
            if not file_path:
                continue

            # Check if file_path matches old or new folder name pattern
            if re.match(old_file_path_pattern, file_path) or re.match(
                new_file_path_pattern, file_path
            ):
                documents_to_update.append(doc)

        logger.info(
            f"[_update_documents_after_folder_rename] Found {len(documents_to_update)} documents to update - project_id={project_id}, directory_key={directory_key}, old_folder={old_folder_name}"
        )

        if not documents_to_update:
            logger.warning(
                f"[_update_documents_after_folder_rename] No documents found matching file_path pattern - project_id={project_id}, directory_key={directory_key}, old_folder={old_folder_name}"
            )
            return

        # Update each document's file_path, commit_id, sync_status
        current_time = get_current_utc_time()
        updated_count = 0

        for doc in documents_to_update:
            try:
                old_file_path = doc.get(FILE_PATH_FIELD, "")
                if not old_file_path:
                    continue

                # Replace old folder name with new folder name in file_path
                if old_file_path.startswith(f"{new_folder_name}/"):
                    # Already has new folder name, just update commit_id
                    new_file_path = old_file_path
                elif old_file_path == new_folder_name:
                    # Already matches new folder name exactly
                    new_file_path = new_folder_name
                elif old_file_path.startswith(f"{old_folder_name}/"):
                    # File in subfolder: "SRC/main.py" -> "basic/main.py"
                    new_file_path = old_file_path.replace(
                        f"{old_folder_name}/", f"{new_folder_name}/", 1
                    )
                elif old_file_path == old_folder_name:
                    # Exact match: "SRC" -> "basic"
                    new_file_path = new_folder_name
                else:
                    # Try to replace at the beginning
                    new_file_path = old_file_path.replace(
                        f"{old_folder_name}/", f"{new_folder_name}/", 1
                    )

                if old_file_path == new_file_path and doc.get("commit_id") == commit_id:
                    # Already updated, skip
                    continue

                # Update document using repository update method
                doc_id = doc.get("id") or doc.get("_id")
                if not doc_id:
                    continue

                # Use repository update_document method if available
                update_data = {
                    FILE_PATH_FIELD: new_file_path,
                    "commit_id": commit_id,
                    "sync_status": SyncStatus.SYNCED,  # Reset sync_status after folder rename
                    "updated_at": current_time,
                }

                if hasattr(repository, "update_document"):
                    # Use repository update_document method
                    is_updated = await repository.update_document(
                        document_id=str(doc_id),
                        project_id=project_id,
                        update_data=update_data,
                    )
                    if is_updated:
                        updated_count += 1
                        logger.info(
                            f"[_update_documents_after_folder_rename] Updated document - doc_id={doc_id}, old_path={old_file_path}, new_path={new_file_path}, commit_id={commit_id}"
                        )
                    else:
                        logger.warning(
                            f"[_update_documents_after_folder_rename] Document not updated - doc_id={doc_id}, old_path={old_file_path}"
                        )
                else:
                    # Fallback: use collection directly
                    logger.warning(
                        f"[_update_documents_after_folder_rename] Repository does not have update_document method, using collection directly - directory_key={directory_key}"
                    )
                    # Get collection
                    if directory_key == "src":
                        collection = await repository._resolve_collection()
                    elif directory_key in ["utd", "utc"]:
                        collection = await repository._resolve_unit_test_collection()
                    else:
                        collection = await repository._resolve_collection()

                    update_filter = {"id": doc_id} if "id" in doc else {"_id": doc_id}
                    update_operations = {"$set": update_data}
                    result = await collection.update_one(
                        update_filter, update_operations
                    )
                    if result.modified_count > 0:
                        updated_count += 1
                        logger.info(
                            f"[_update_documents_after_folder_rename] Updated document - doc_id={doc_id}, old_path={old_file_path}, new_path={new_file_path}, commit_id={commit_id}"
                        )

            except Exception as e:
                error_message = f"[_update_documents_after_folder_rename] Error updating document {doc.get('id', 'unknown')}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)
                continue

        logger.info(
            f"[_update_documents_after_folder_rename] Success - updated_count={updated_count}"
        )

    except Exception as e:
        error_message = f"[_update_documents_after_folder_rename] Error - project_id={project_id}, directory_key={directory_key}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


async def _update_data_management_folder_name(
    project_id: str,
    directory_key: str,
    old_folder_name: str,
    new_folder_name: str,
) -> None:
    """
    Update folder_name in data_management for root folder (parent_folder_id = None)
    Only for SRC, UTD, UTC directories

    Args:
        project_id: Project ID
        directory_key: Directory key (src, utd, utc)
        old_folder_name: Old folder name (root folder name)
        new_folder_name: New folder name (root folder name)
    """
    logger.info(
        f"[_update_data_management_folder_name] Start - project_id={project_id}, directory_key={directory_key}, old={old_folder_name}, new={new_folder_name}"
    )

    try:
        from app.core.database import app_db
        from app.utils.helpers import get_current_utc_time

        # Map directory keys to data_management types
        type_map = {
            "src": "source_code",
            "utd": "utd",
            "utc": "utc",
        }

        if directory_key not in type_map:
            logger.warning(
                f"[_update_data_management_folder_name] Directory key not supported for data_management: {directory_key}"
            )
            return

        data_type = type_map[directory_key]

        # Extract root folder name from old_folder_name (handle cases like "PD/CD" -> "CD")
        # For root folder, we need the last part if it contains "/"
        old_root_name = (
            old_folder_name.split("/")[-1]
            if "/" in old_folder_name
            else old_folder_name
        )
        new_root_name = (
            new_folder_name.split("/")[-1]
            if "/" in new_folder_name
            else new_folder_name
        )

        # Find data_management document
        data_management_doc = await app_db.data_management_repository.find_one(
            {
                "project_id": project_id,
                "type": data_type,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }
        )

        if not data_management_doc:
            logger.info(
                f"[_update_data_management_folder_name] No data_management document found - project_id={project_id}, type={data_type}"
            )
            return

        folders = data_management_doc.get("folders", [])
        if not folders:
            logger.info(
                f"[_update_data_management_folder_name] No folders in data_management - project_id={project_id}, type={data_type}"
            )
            return

        # Find root folder (parent_folder_id = None) with old_folder_name
        updated_count = 0
        current_time = get_current_utc_time()

        for folder in folders:
            folder_name = folder.get("folder_name", "")
            parent_folder_id = folder.get("parent_folder_id")

            # Check if this is the root folder with old name
            if parent_folder_id is None and folder_name == old_root_name:
                # Update folder_name
                folder["folder_name"] = new_root_name
                updated_count += 1
                logger.info(
                    f"[_update_data_management_folder_name] Found root folder to update - folder_id={folder.get('folder_id')}, old_name={old_root_name}, new_name={new_root_name}"
                )

        if updated_count > 0:
            # Update data_management document
            update_data = {
                "folders": folders,
                "updated_at": current_time,
            }

            query_filter = {
                "project_id": project_id,
                "type": data_type,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }

            modified_count = await app_db.data_management_repository.update_one(
                filter_dict=query_filter, update_data=update_data
            )

            if modified_count > 0:
                logger.info(
                    f"[_update_data_management_folder_name] Success - project_id={project_id}, type={data_type}, updated_folders={updated_count}"
                )
            else:
                logger.warning(
                    f"[_update_data_management_folder_name] Failed to update data_management - project_id={project_id}, type={data_type}"
                )
        else:
            logger.info(
                f"[_update_data_management_folder_name] No root folder found with name={old_root_name} - project_id={project_id}, type={data_type}"
            )

    except Exception as e:
        error_message = f"[_update_data_management_folder_name] Error - project_id={project_id}, directory_key={directory_key}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise
