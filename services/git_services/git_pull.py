"""
Git pull service
Pull latest changes from Git repository and update database
"""

import logging
import os
import tempfile
import shutil
import base64
from typing import List, Dict, Any, Optional
from pathlib import Path
from git import Repo, GitCommandError
from app.services.git_services.git_validate import (
    validate_git_repository,
    validate_git_url,
)
from app.services.git_services.git_service import (
    cleanup_temp_dir,
    get_all_commit_ids,
    clone_repository,
)
from app.services.git_services.git_check_changes import (
    _get_file_changes_from_commits,
    _get_directory_key_for_file,
    DIRECTORY_MAPPING,
)
from app.services.git_services.git_clone import (
    _prepare_clone_url,
    _get_file_commit_id,
    _normalize_path,
    link_unit_test_to_source_code,
    IMAGE_FILE_EXTENSIONS,
    VIDEO_FILE_EXTENSIONS,
    _get_repo_name_from_url,
)
from app.services.data_structures.file_data import FileData
from app.utils.constants import (
    GitType,
    SyncStatus,
    ProgrammingLanguages,
    ContentType,
)
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from cec_docifycode_common.models.detail_design import FILE_NAME_DETAIL_DESIGN
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import get_current_utc_time
from app.utils.constants import DataManagementType
from app.services.data_management_service import data_management_service
from fastapi import status

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


def _get_root_folder_name(
    directory_key: str,
    directory_path: str,
    repository_url: Optional[str] = None,
) -> str:
    """
    Get root folder name from directory path or repository URL

    Args:
        directory_key: Directory key (src, utd, utc)
        directory_path: Directory path from project settings (e.g., "SRC", "/", "app/src")
        repository_url: Repository URL (used when directory_path is "/")

    Returns:
        Root folder name
    """
    logger.info(
        f"[_get_root_folder_name] Start - directory_key={directory_key}, directory_path={directory_path}"
    )

    try:
        # Normalize directory path
        normalized_path = directory_path.strip().strip("/") if directory_path else ""

        # If directory path is "/" or empty, use repository name
        if not normalized_path or normalized_path == "":
            if repository_url:
                repo_name = _get_repo_name_from_url(repository_url)
                logger.info(
                    f"[_get_root_folder_name] Using repo name - repo_name={repo_name}"
                )
                return repo_name if repo_name else "Source Code"
            else:
                # Fallback to default names
                default_names = {
                    "src": "Source Code",
                    "utd": "Unit Test Design",
                    "utc": "Unit Test Code",
                }
                return default_names.get(directory_key, "Source Code")

        # Get folder name from directory path (last part after /)
        path_parts = normalized_path.split("/")
        folder_name = path_parts[-1] if path_parts else normalized_path

        logger.info(
            f"[_get_root_folder_name] Using directory path - folder_name={folder_name}"
        )
        return folder_name

    except Exception as e:
        error_message = f"[_get_root_folder_name] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        # Fallback to default names
        default_names = {
            "src": "Source Code",
            "utd": "Unit Test Design",
            "utc": "Unit Test Code",
        }
        return default_names.get(directory_key, "Source Code")


async def pull_data_from_git(
    project_id: str,
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
    db_commit_ids: List[str],
    directory_paths: Dict[str, str],
    programming_language: Optional[str] = None,
    force_override: bool = False,
) -> Dict[str, Any]:
    """
    Pull latest changes from Git repository and update database

    Args:
        project_id: Project ID
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password
        db_commit_ids: List of commit IDs from database
        directory_paths: Dictionary of directory paths (rd, bd, pd, src, utd, utc)
        programming_language: Programming language (Python, Java, C#) for code analysis
        force_override: If True, override local changes without conflict check

    Returns:
        Dictionary with pull results including conflict files
    """
    logger.info(
        f"[pull_data_from_git] Start - project_id={project_id}, repository_url={repository_url}, branch_name={branch_name}, force_override={force_override}"
    )

    if not repository_url or not branch_name or not user_name:
        logger.warning("[pull_data_from_git] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    temp_dir = None
    try:
        # Validate git repository
        git_type = await validate_git_repository(
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
        )
        logger.info(
            f"[pull_data_from_git] Git repository validated - git_type={git_type}"
        )

        # Clone repository to temporary directory
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)
        temp_dir = tempfile.mkdtemp(prefix="git_pull_")
        logger.info(f"[pull_data_from_git] Cloning to temp directory: {temp_dir}")

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)
        repo_root = repo.working_dir
        logger.info(f"[pull_data_from_git] Repository cloned successfully")

        # Get all commit IDs from repository
        repo_commits = get_all_commit_ids(repo, branch_name)
        repo_commit_ids = repo_commits
        logger.info(
            f"[pull_data_from_git] Found {len(repo_commits)} commits in repository"
        )

        # Find new commits (commits in repo but not in DB)
        db_commit_set = set()
        if db_commit_ids:
            for c in db_commit_ids:
                if isinstance(c, dict):
                    db_commit_set.add(c.get("commit_id"))
                elif hasattr(c, "commit_id"):
                    db_commit_set.add(c.commit_id)
                else:
                    db_commit_set.add(c)

        new_commit_ids = []
        for c in repo_commits:
            commit_id = c.get("commit_id")
            if commit_id and commit_id not in db_commit_set:
                new_commit_ids.append(commit_id)

        logger.info(f"[pull_data_from_git] Found {len(new_commit_ids)} new commits")
        if not new_commit_ids:
            logger.info(f"[pull_data_from_git] No new commits found")
            return {
                "updated_files": 0,
                "conflict_files": [],
                "new_commit_ids": [],
                "repo_commit_ids": repo_commit_ids,
            }

        # Get file changes from new commits
        changed_files = _get_file_changes_from_commits(
            repo=repo,
            commit_ids=new_commit_ids,
            directory_paths=directory_paths,
        )
        logger.info(f"[pull_data_from_git] Found {len(changed_files)} changed files")

        # Process files: detect conflicts and update database
        result = await _process_pull_changes(
            project_id=project_id,
            repo=repo,
            repo_root=repo_root,
            branch_name=branch_name,
            changed_files=changed_files,
            directory_paths=directory_paths,
            programming_language=programming_language,
            force_override=force_override,
            repository_url=repository_url,
        )

        result["new_commit_ids"] = new_commit_ids
        result["repo_commit_ids"] = repo_commit_ids

        logger.info(
            f"[pull_data_from_git] Success - updated_files={result.get('updated_files', 0)}, conflict_files={len(result.get('conflict_files', []))}"
        )
        return result

    except GitCommandError as e:
        error_message = f"[pull_data_from_git] Git command error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_PULL_FAILED"
        )
    except Exception as e:
        error_message = f"[pull_data_from_git] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_PULL_FAILED"
        )
    finally:
        # Cleanup temporary directory
        cleanup_temp_dir(temp_dir, repo)


async def _process_pull_changes(
    project_id: str,
    repo: Repo,
    repo_root: str,
    branch_name: str,
    changed_files: List[Dict[str, Any]],
    directory_paths: Dict[str, str],
    programming_language: Optional[str] = None,
    force_override: bool = False,
    repository_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process pull changes: detect conflicts and update files

    Args:
        project_id: Project ID
        repo: GitPython Repo object
        repo_root: Repository root directory
        branch_name: Branch name
        changed_files: List of changed file information
        directory_paths: Dictionary of directory paths
        programming_language: Programming language for code analysis
        force_override: If True, override local changes

    Returns:
        Dictionary with updated_files count and conflict_files list
    """
    logger.info(
        f"[_process_pull_changes] Start - project_id={project_id}, changed_files_count={len(changed_files)}, force_override={force_override}"
    )

    from app.api.v1.projects import _find_file_by_path

    updated_files = 0
    conflict_files = []

    # Group files by directory key
    files_by_directory = {}
    for file_info in changed_files:
        directory_key = file_info.get("directory_key")
        if not directory_key:
            continue

        if directory_key not in files_by_directory:
            files_by_directory[directory_key] = []
        files_by_directory[directory_key].append(file_info)

    # Process each directory
    for directory_key, files in files_by_directory.items():
        try:
            result = await _process_directory_pull(
                project_id=project_id,
                repo=repo,
                repo_root=repo_root,
                branch_name=branch_name,
                directory_key=directory_key,
                files=files,
                directory_paths=directory_paths,
                programming_language=programming_language,
                force_override=force_override,
                repository_url=repository_url,
            )
            updated_files += result["updated_count"]
            conflict_files.extend(result["conflict_files"])
        except Exception as e:
            error_message = f"[_process_pull_changes] Error processing directory {directory_key}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            continue

    logger.info(
        f"[_process_pull_changes] Success - updated_files={updated_files}, conflict_files={len(conflict_files)}"
    )
    return {
        "updated_files": updated_files,
        "conflict_files": conflict_files,
    }


async def _process_directory_pull(
    project_id: str,
    repo: Repo,
    repo_root: str,
    branch_name: str,
    directory_key: str,
    files: List[Dict[str, Any]],
    directory_paths: Dict[str, str],
    programming_language: Optional[str] = None,
    force_override: bool = False,
    repository_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Process pull changes for a specific directory

    Args:
        project_id: Project ID
        repo: GitPython Repo object
        repo_root: Repository root directory
        branch_name: Branch name
        directory_key: Directory key (rd, bd, pd, src, utd, utc)
        files: List of changed file information
        directory_paths: Dictionary of directory paths
        programming_language: Programming language for code analysis
        force_override: If True, override local changes

    Returns:
        Dictionary with updated_count and conflict_files list
    """
    logger.info(
        f"[_process_directory_pull] Start - project_id={project_id}, directory_key={directory_key}, files_count={len(files)}"
    )

    from app.api.v1.projects import _find_file_by_path

    updated_count = 0
    conflict_files = []

    # Get repository for this directory
    repository = _get_repository_for_directory_key(directory_key)
    if not repository:
        logger.warning(
            f"[_process_directory_pull] No repository found for directory_key: {directory_key}"
        )
        return {"updated_count": 0, "conflict_files": []}

    # Process each file
    for file_info in files:
        file_path = file_info.get("file_path")
        commit_id = file_info.get("commit_id")
        change_type = file_info.get("change_type", "modified")

        if not file_path:
            continue

        # Check file name for bd and pd
        file_name = os.path.basename(file_path)
        if directory_key == "bd" and file_name != FILE_NAME_BASIC_DESIGN:
            logger.info(
                f"[_process_directory_pull] Skipping file - bd only accepts {FILE_NAME_BASIC_DESIGN}, got {file_name}"
            )
            continue
        if directory_key == "pd" and file_name != FILE_NAME_DETAIL_DESIGN:
            logger.info(
                f"[_process_directory_pull] Skipping file - pd only accepts {FILE_NAME_DETAIL_DESIGN}, got {file_name}"
            )
            continue

        try:
            # Find file in database
            file_document = await _find_file_by_path(
                repository=repository,
                project_id=project_id,
                file_path=file_path,
                directory_key=directory_key,
            )

            # Check for conflicts (unless force_override)
            if not force_override and file_document:
                current_sync_status = file_document.get("sync_status")
                # Conflict if file has local changes (push, pull_push, delete_push)
                if current_sync_status in [
                    SyncStatus.PUSH,
                    SyncStatus.PULL_PUSH,
                    SyncStatus.DELETE_PUSH,
                ]:
                    # Add to conflict files
                    conflict_file_info = {
                        "id": file_document.get("id") or file_document.get("_id"),
                        "project_id": project_id,
                        "file_name": file_document.get(
                            "file_name", os.path.basename(file_path)
                        ),
                        "file_path": file_path,
                        "collection_name": DIRECTORY_MAPPING.get(directory_key, ""),
                        "commit_id": commit_id,
                        "sync_status": SyncStatus.PULL_PUSH,
                    }
                    conflict_files.append(conflict_file_info)
                    logger.warning(
                        f"[_process_directory_pull] Conflict detected - file_path={file_path}, sync_status={current_sync_status}, change_type={change_type}"
                    )
                    continue

            # Update or create file
            if change_type == "deleted":
                # File was deleted in repo
                if file_document:
                    # Update sync_status to delete_pull
                    update_data = {
                        "sync_status": SyncStatus.DELETE_PULL,
                        "deleted_at": get_current_utc_time(),
                    }
                    is_updated = await repository.update_document(
                        document_id=file_document.get("id") or file_document.get("_id"),
                        project_id=project_id,
                        update_data=update_data,
                    )
                    if is_updated:
                        updated_count += 1
                        logger.info(
                            f"[_process_directory_pull] Updated deleted file - file_path={file_path}"
                        )
            else:
                # File was modified or added
                # Read file content from repo
                full_file_path = os.path.join(repo_root, file_path)
                if not os.path.exists(full_file_path):
                    logger.warning(
                        f"[_process_directory_pull] File not found in repo - file_path={full_file_path}"
                    )
                    continue

                # Update file based on directory_key
                is_updated = await _update_file_from_repo(
                    project_id=project_id,
                    repository=repository,
                    file_document=file_document,
                    file_path=file_path,
                    full_file_path=full_file_path,
                    commit_id=commit_id,
                    directory_key=directory_key,
                    directory_paths=directory_paths,
                    programming_language=programming_language,
                    repository_url=repository_url,
                )

                if is_updated:
                    updated_count += 1
                    logger.info(
                        f"[_process_directory_pull] Updated file - file_path={file_path}"
                    )

        except Exception as e:
            error_message = (
                f"[_process_directory_pull] Error processing file {file_path}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            continue

    logger.info(
        f"[_process_directory_pull] Success - updated_count={updated_count}, conflict_files={len(conflict_files)}"
    )
    return {"updated_count": updated_count, "conflict_files": conflict_files}


def _get_repository_for_directory_key(directory_key: str):
    """
    Get repository instance for directory key
    This function should be moved to a shared utility module
    """
    from app.core.database import app_db

    repository_map = {
        "rd": app_db.requirement_document_repository,
        "bd": app_db.basic_design_repository,
        "pd": app_db.detail_design_repository,
        "src": app_db.source_code_repository,
        "utd": app_db.unit_test_repository,
        "utc": app_db.unit_test_repository,
    }

    return repository_map.get(directory_key)


async def _get_or_create_folder_id(
    project_id: str,
    file_path: str,
    directory_paths: Dict[str, str],
    directory_key: str,
    repository_url: Optional[str] = None,
) -> Optional[str]:
    """
    Get folder_id from file_path based on data_management, or create new folder if needed

    Args:
        project_id: Project ID
        file_path: Full file path in repository (e.g., "SRC/folder1/subfolder/file.py")
        directory_paths: Dictionary of directory paths
        directory_key: Directory key (src, utd, utc)

    Returns:
        folder_id if found or created, None otherwise
    """
    logger.info(
        f"[_get_or_create_folder_id] Start - file_path={file_path}, directory_key={directory_key}"
    )

    if not project_id or not file_path or not directory_key:
        logger.warning("[_get_or_create_folder_id] Missing required parameters")
        return None

    try:
        from uuid6 import uuid7

        # Get base directory path (e.g., "SRC" for src, "UTD" for utd)
        base_path = directory_paths.get(directory_key)
        if not base_path:
            logger.warning(
                f"[_get_or_create_folder_id] Base path not found for directory_key: {directory_key}"
            )
            return None

        # Normalize paths
        normalized_file_path = file_path.replace("\\", "/")
        normalized_base_path = base_path.replace("\\", "/").strip("/")

        # Calculate folder path relative to base directory
        # e.g., "SRC/folder1/subfolder/file.py" -> "folder1/subfolder"
        if normalized_file_path.lower().startswith(normalized_base_path.lower() + "/"):
            # Remove base path prefix
            relative_path = normalized_file_path[
                len(normalized_base_path) + 1 :
            ]  # +1 for "/"
        else:
            # File is directly in base directory
            relative_path = normalized_file_path

        # Get folder path (directory of file, relative to base)
        folder_path = os.path.dirname(relative_path) or ""

        # Determine data_management type
        data_type_map = {
            "src": "source_code",
            "utd": DataManagementType.UNIT_TEST_DESIGN,
            "utc": DataManagementType.UNIT_TEST_CODE,
        }
        data_type = data_type_map.get(directory_key)
        if not data_type:
            logger.warning(
                f"[_get_or_create_folder_id] Unknown data_type for directory_key: {directory_key}"
            )
            return None

        # Get existing data_management
        from app.core.database import app_db
        from app.services.data_management_service import data_management_service

        existing_doc = await app_db.data_management_repository.find_one(
            {
                "project_id": project_id,
                "type": data_type,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }
        )

        folders = []
        if not existing_doc:
            # Create data_management if not exists
            logger.info(
                f"[_get_or_create_folder_id] Data management not found, creating new one - project_id={project_id}, type={data_type}"
            )
            # Create root folder
            root_folder_id = str(uuid7())
            root_folder_name = _get_root_folder_name(
                directory_key=directory_key,
                directory_path=base_path,
                repository_url=repository_url,
            )

            root_folder = {
                "folder_id": root_folder_id,
                "folder_name": root_folder_name,
                "parent_folder_id": None,
            }
            folders = [root_folder]

            # Create data_management document
            await data_management_service.create_or_update_data_management(
                project_id=project_id, type=data_type, folders=folders
            )
            logger.info(
                f"[_get_or_create_folder_id] Created data_management with root folder - project_id={project_id}, type={data_type}, root_folder_id={root_folder_id}"
            )
        else:
            folders = existing_doc.get("folders", [])
            # Remove duplicate folders from existing data
            if folders:
                seen_folders = set()
                unique_folders = []
                for folder_info in folders:
                    folder_name = folder_info.get("folder_name", "")
                    parent_folder_id = folder_info.get("parent_folder_id")
                    folder_key = (folder_name, parent_folder_id)

                    if folder_key not in seen_folders:
                        seen_folders.add(folder_key)
                        unique_folders.append(folder_info)

                if len(unique_folders) != len(folders):
                    logger.info(
                        f"[_get_or_create_folder_id] Found duplicate folders in database - original={len(folders)}, unique={len(unique_folders)}"
                    )
                    # Update data_management with deduplicated folders
                    await data_management_service.create_or_update_data_management(
                        project_id=project_id, type=data_type, folders=unique_folders
                    )
                    folders = unique_folders

            if not folders:
                # Create root folder if folders list is empty
                logger.info(
                    f"[_get_or_create_folder_id] No folders in data_management, creating root folder - project_id={project_id}, type={data_type}"
                )
                root_folder_id = str(uuid7())
                root_folder_name = _get_root_folder_name(
                    directory_key=directory_key,
                    directory_path=base_path,
                    repository_url=repository_url,
                )

                root_folder = {
                    "folder_id": root_folder_id,
                    "folder_name": root_folder_name,
                    "parent_folder_id": None,
                }
                folders = [root_folder]

                # Update data_management with root folder
                await data_management_service.create_or_update_data_management(
                    project_id=project_id, type=data_type, folders=folders
                )
                logger.info(
                    f"[_get_or_create_folder_id] Added root folder to data_management - project_id={project_id}, type={data_type}, root_folder_id={root_folder_id}"
                )

        # Build folder path to folder_id mapping from existing folders
        # We need to reconstruct the folder path hierarchy (relative to base directory)
        folder_map: Dict[str, str] = {}  # relative_folder_path -> folder_id
        folder_id_to_info: Dict[str, Dict[str, Any]] = {}  # folder_id -> folder_info

        # First pass: build folder_id to info mapping
        for folder_info in folders:
            folder_id = folder_info.get("folder_id")
            if folder_id:
                folder_id_to_info[folder_id] = folder_info

        # Find root folders (parent_folder_id is None or empty)
        root_folders = [f for f in folders if not f.get("parent_folder_id")]
        root_folder_id = None
        if root_folders:
            root_folder_id = root_folders[0].get("folder_id")
            # Root folder maps to empty relative path
            folder_map[""] = root_folder_id

        # Second pass: build relative path to folder_id mapping by traversing hierarchy
        # Path is relative to base directory (excluding root folder name)
        def build_path_mapping(
            folder_id: str, current_relative_path: str, visited: set
        ) -> None:
            if folder_id in visited:
                return
            visited.add(folder_id)

            folder_info = folder_id_to_info.get(folder_id)
            if not folder_info:
                return

            folder_name = folder_info.get("folder_name", "")

            # Skip root folder in path building (it's already mapped to "")
            if folder_id == root_folder_id:
                # Process children of root folder with empty relative path
                for child_folder in folders:
                    child_parent_id = child_folder.get("parent_folder_id")
                    if child_parent_id == folder_id:
                        child_folder_name = child_folder.get("folder_name", "")
                        child_relative_path = child_folder_name
                        child_folder_id = child_folder.get("folder_id")
                        if child_folder_id:
                            # Only map if path doesn't exist yet (avoid overwriting with duplicate)
                            if child_relative_path not in folder_map:
                                folder_map[child_relative_path] = child_folder_id
                                build_path_mapping(
                                    child_folder_id, child_relative_path, visited
                                )
            else:
                # For non-root folders, build relative path
                if folder_name:
                    if current_relative_path:
                        relative_path = f"{current_relative_path}/{folder_name}"
                    else:
                        relative_path = folder_name

                    # Only map if path doesn't exist yet (avoid overwriting with duplicate)
                    if relative_path not in folder_map:
                        folder_map[relative_path] = folder_id

                        # Recursively process child folders
                        for child_folder in folders:
                            child_parent_id = child_folder.get("parent_folder_id")
                            if child_parent_id == folder_id:
                                build_path_mapping(
                                    child_folder.get("folder_id"),
                                    relative_path,
                                    visited,
                                )

        # Build path mapping starting from root
        if root_folder_id:
            build_path_mapping(root_folder_id, "", set())

        # Check if folder_path exists in mapping
        if folder_path in folder_map:
            folder_id = folder_map[folder_path]
            logger.info(
                f"[_get_or_create_folder_id] Found existing folder - folder_path={folder_path}, folder_id={folder_id}"
            )
            return folder_id

        # If folder_path is empty, return root folder_id if exists
        if folder_path == "":
            if root_folder_id:
                logger.info(
                    f"[_get_or_create_folder_id] Using root folder - folder_id={root_folder_id}"
                )
                return root_folder_id
            return None

        # Folder doesn't exist, need to create it
        # But first, reload data_management to check if folder was created by another file
        logger.info(
            f"[_get_or_create_folder_id] Folder not found in cache, reloading data_management - folder_path={folder_path}"
        )

        # Reload data_management to get latest folders
        updated_doc = await app_db.data_management_repository.find_one(
            {
                "project_id": project_id,
                "type": data_type,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }
        )

        if updated_doc:
            updated_folders = updated_doc.get("folders", [])
            if updated_folders != folders:
                # Folders were updated, rebuild folder_map
                logger.info(
                    f"[_get_or_create_folder_id] Folders updated, rebuilding folder_map - folder_path={folder_path}"
                )
                folders = updated_folders

                # Rebuild folder_map with updated folders
                folder_map = {}
                folder_id_to_info = {}

                # First pass: build folder_id to info mapping
                for folder_info in folders:
                    folder_id = folder_info.get("folder_id")
                    if folder_id:
                        folder_id_to_info[folder_id] = folder_info

                # Find root folders
                root_folders = [f for f in folders if not f.get("parent_folder_id")]
                root_folder_id = None
                if root_folders:
                    root_folder_id = root_folders[0].get("folder_id")
                    folder_map[""] = root_folder_id

                # Rebuild path mapping
                if root_folder_id:
                    build_path_mapping(root_folder_id, "", set())

                # Check again if folder_path exists after reload
                if folder_path in folder_map:
                    folder_id = folder_map[folder_path]
                    logger.info(
                        f"[_get_or_create_folder_id] Found folder after reload - folder_path={folder_path}, folder_id={folder_id}"
                    )
                    return folder_id

        # Still doesn't exist, create it
        logger.info(
            f"[_get_or_create_folder_id] Creating new folder - folder_path={folder_path}"
        )

        # Create all intermediate folders if needed
        path_parts = folder_path.split("/") if folder_path else []
        new_folders = []

        for i in range(len(path_parts)):
            current_path = "/".join(path_parts[: i + 1])
            if current_path not in folder_map:
                # Check if folder with same name and parent already exists
                folder_name = path_parts[i]
                parent_path = "/".join(path_parts[:i]) if i > 0 else ""
                parent_folder_id = folder_map.get(parent_path) if parent_path else None

                # If parent_path is empty, find root folder_id
                if not parent_folder_id and parent_path == "":
                    # Find root folder
                    root_folders = [f for f in folders if not f.get("parent_folder_id")]
                    if root_folders:
                        parent_folder_id = root_folders[0].get("folder_id")

                # Check if folder with same name and parent already exists
                # Check in both existing folders and new_folders (created in this call)
                existing_folder = None
                all_folders_to_check = folders + new_folders
                for folder_info in all_folders_to_check:
                    if (
                        folder_info.get("folder_name") == folder_name
                        and folder_info.get("parent_folder_id") == parent_folder_id
                    ):
                        existing_folder = folder_info
                        break

                if existing_folder:
                    # Use existing folder
                    folder_id = existing_folder.get("folder_id")
                    logger.info(
                        f"[_get_or_create_folder_id] Found existing folder by name and parent - folder_name={folder_name}, parent_folder_id={parent_folder_id}, folder_id={folder_id}, current_path={current_path}"
                    )
                    folder_map[current_path] = folder_id
                else:
                    # Need to create this folder
                    folder_id = str(uuid7())
                    folder_info = {
                        "folder_id": folder_id,
                        "folder_name": folder_name,
                        "parent_folder_id": parent_folder_id,
                    }
                    new_folders.append(folder_info)
                    folder_map[current_path] = folder_id
                    # Also add to folders list for next iteration
                    folders.append(folder_info)
                    logger.info(
                        f"[_get_or_create_folder_id] Created new folder - folder_name={folder_name}, parent_folder_id={parent_folder_id}, folder_id={folder_id}, current_path={current_path}"
                    )

        # Update data_management with new folders
        if new_folders:
            all_folders = folders + new_folders
            # Remove duplicate folders based on (folder_name, parent_folder_id)
            # Keep the first occurrence of each unique (folder_name, parent_folder_id) pair
            seen_folders = set()
            unique_folders = []
            for folder_info in all_folders:
                folder_name = folder_info.get("folder_name", "")
                parent_folder_id = folder_info.get("parent_folder_id")
                folder_key = (folder_name, parent_folder_id)

                if folder_key not in seen_folders:
                    seen_folders.add(folder_key)
                    unique_folders.append(folder_info)
                else:
                    logger.debug(
                        f"[_get_or_create_folder_id] Skipping duplicate folder - folder_name={folder_name}, parent_folder_id={parent_folder_id}"
                    )

            logger.info(
                f"[_get_or_create_folder_id] Deduplicated folders - original={len(all_folders)}, unique={len(unique_folders)}"
            )

            await data_management_service.create_or_update_data_management(
                project_id=project_id, type=data_type, folders=unique_folders
            )
            logger.info(
                f"[_get_or_create_folder_id] Added {len(new_folders)} new folders to data_management (after deduplication: {len(unique_folders)} total)"
            )

            # After updating, reload to check if folder was created by another process
            # and verify we're using the correct folder_id
            final_reload_doc = await app_db.data_management_repository.find_one(
                {
                    "project_id": project_id,
                    "type": data_type,
                    "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
                }
            )

            if final_reload_doc:
                final_folders = final_reload_doc.get("folders", [])
                # Check if requested folder_path exists and find its folder_id
                # by matching folder_name and parent_folder_id in the path hierarchy
                path_parts = folder_path.split("/") if folder_path else []
                current_parent_id = None

                # Find root folder_id
                root_folders = [
                    f for f in final_folders if not f.get("parent_folder_id")
                ]
                if root_folders:
                    current_parent_id = root_folders[0].get("folder_id")

                # Traverse path to find folder_id
                for i, part in enumerate(path_parts):
                    found_folder = None
                    for folder_info in final_folders:
                        if (
                            folder_info.get("folder_name") == part
                            and folder_info.get("parent_folder_id") == current_parent_id
                        ):
                            found_folder = folder_info
                            break

                    if found_folder:
                        current_parent_id = found_folder.get("folder_id")
                        if i == len(path_parts) - 1:
                            # This is the target folder
                            final_folder_id = current_parent_id
                            logger.info(
                                f"[_get_or_create_folder_id] Verified folder_id after update - folder_path={folder_path}, folder_id={final_folder_id}"
                            )
                            return final_folder_id
                    else:
                        # Folder not found in reload, use the one we created
                        break

        # Return folder_id for the requested folder_path
        final_folder_id = folder_map.get(folder_path)
        logger.info(
            f"[_get_or_create_folder_id] Success - folder_path={folder_path}, folder_id={final_folder_id}"
        )
        return final_folder_id

    except Exception as e:
        error_message = f"[_get_or_create_folder_id] Error - file_path={file_path}, directory_key={directory_key}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return None


async def _update_file_from_repo(
    project_id: str,
    repository: Any,
    file_document: Optional[Dict[str, Any]],
    file_path: str,
    full_file_path: str,
    commit_id: str,
    directory_key: str,
    directory_paths: Dict[str, str],
    programming_language: Optional[str] = None,
    repository_url: Optional[str] = None,
) -> bool:
    """
    Update file from repository content

    Args:
        project_id: Project ID
        repository: Repository instance
        file_document: Existing file document (None if new file)
        file_path: File path in repository
        full_file_path: Full file path on disk
        commit_id: Commit ID
        directory_key: Directory key (rd, bd, pd, src, utd, utc)
        programming_language: Programming language for code analysis

    Returns:
        True if updated successfully, False otherwise
    """
    logger.info(
        f"[_update_file_from_repo] Start - file_path={file_path}, directory_key={directory_key}, is_new={file_document is None}"
    )

    try:
        file_name = os.path.basename(file_path)
        normalized_path = file_path.replace("\\", "/")
        file_ext = Path(full_file_path).suffix.lower()

        is_src_folder = directory_key == "src"
        is_rd_folder = directory_key == "rd"
        is_bd_folder = directory_key == "bd"
        is_pd_folder = directory_key == "pd"

        # Read file content based on file type
        if is_src_folder:
            # For src folder: only save content for Python, Java, C# files
            # Other files: only save file name, not content
            supported_content_extensions = {".py", ".java", ".cs"}

            if file_ext in supported_content_extensions:
                # Read file content for supported languages
                try:
                    with open(
                        full_file_path, "r", encoding="utf-8", errors="ignore"
                    ) as f:
                        file_content = f.read()
                    file_content_bytes = None
                    logger.info(
                        f"[_update_file_from_repo] Processed file with content - file_path={normalized_path}, file_ext={file_ext}"
                    )
                except Exception as e:
                    logger.warning(
                        f"[_update_file_from_repo] Failed to read file content - file_path={normalized_path}, error={e}"
                    )
                    file_content = None
                    file_content_bytes = None
            else:
                # Other files: only save file name, not content
                file_content = None
                file_content_bytes = None
                logger.info(
                    f"[_update_file_from_repo] Processed file (name only) - file_path={normalized_path}, file_ext={file_ext}, file_name={file_name}"
                )
        elif is_rd_folder:
            # For RD: read as binary to handle both markdown and images
            with open(full_file_path, "rb") as f:
                file_content_bytes = f.read()
            file_content = None  # Will be processed based on file extension
            logger.info(
                f"[_update_file_from_repo] Reading binary file for {directory_key} - file_path={normalized_path}, file_ext={file_ext}"
            )
        elif is_bd_folder or is_pd_folder:
            # For BD and PD: will read as JSON in update logic
            file_content_bytes = None
            file_content = None
        else:
            # Read file content as text for other files
            with open(full_file_path, "r", encoding="utf-8", errors="ignore") as f:
                file_content = f.read()
            file_content_bytes = None

        # If file doesn't exist in DB, create new document
        if not file_document:
            return await _create_new_file_from_repo(
                project_id=project_id,
                repository=repository,
                file_path=normalized_path,
                file_name=file_name,
                directory_key=directory_key,
                directory_paths=directory_paths,
                file_content=file_content,
                file_content_bytes=(file_content_bytes if is_rd_folder else None),
                full_file_path=(
                    full_file_path
                    if (is_rd_folder or is_bd_folder or is_pd_folder)
                    else None
                ),
                commit_id=commit_id,
                programming_language=programming_language,
                repository_url=repository_url,
            )

        # Update existing file
        document_id = file_document.get("id") or file_document.get("_id")
        if not document_id:
            logger.warning(
                f"[_update_file_from_repo] File document has no ID - file_path={file_path}"
            )
            return False

        # Build update data based on directory_key
        update_data = {
            "commit_id": commit_id,
            "sync_status": SyncStatus.SYNCED,
        }

        # Add content updates based on directory_key
        if directory_key == "rd":
            # Process RD: markdown or image
            if file_ext == ".md":
                # Markdown: decode to string
                rd_content = {
                    "type": ContentType.MARKDOWN,
                    "content": file_content_bytes.decode("utf-8", errors="ignore"),
                }
                logger.info(
                    f"[_update_file_from_repo] Processed markdown file - file_path={normalized_path}, file_ext={file_ext}"
                )
            else:
                # Image: encode to base64
                encoded_content = base64.b64encode(file_content_bytes).decode("utf-8")
                rd_content = {
                    "type": ContentType.IMAGE,
                    "content": encoded_content,
                }
                logger.info(
                    f"[_update_file_from_repo] Processed image file - file_path={normalized_path}, file_ext={file_ext}"
                )
            update_data["rd_content"] = rd_content
        elif directory_key == "bd":
            # Process BD: read as JSON
            if full_file_path:
                try:
                    import json

                    with open(full_file_path, "r", encoding="utf-8") as f:
                        contents = json.load(f)
                        if not isinstance(contents, dict):
                            logger.warning(
                                f"[_update_file_from_repo] File {FILE_NAME_BASIC_DESIGN} does not contain a JSON object, using empty dict"
                            )
                            contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_update_file_from_repo] Failed to parse JSON from {FILE_NAME_BASIC_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}
                except Exception as e:
                    logger.warning(
                        f"[_update_file_from_repo] Failed to read file: {e}, using empty dict"
                    )
                    contents = {}
            else:
                contents = {}
            update_data["contents"] = contents
        elif directory_key == "pd":
            # Process PD: read as JSON
            if full_file_path:
                try:
                    import json

                    with open(full_file_path, "r", encoding="utf-8") as f:
                        contents = json.load(f)
                        if not isinstance(contents, dict):
                            logger.warning(
                                f"[_update_file_from_repo] File {FILE_NAME_DETAIL_DESIGN} does not contain a JSON object, using empty dict"
                            )
                            contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_update_file_from_repo] Failed to parse JSON from {FILE_NAME_DETAIL_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}
                except Exception as e:
                    logger.warning(
                        f"[_update_file_from_repo] Failed to read file: {e}, using empty dict"
                    )
                    contents = {}
            else:
                contents = {}
            update_data["contents"] = contents
        elif directory_key == "src":
            # For source code, analyze code if needed
            classes_list = []
            global_methods_list = []

            # Check if should analyze code (only if file has content)
            should_analyze = False
            if file_content and programming_language:
                language_to_extension = {
                    ProgrammingLanguages.PYTHON: ".py",
                    ProgrammingLanguages.JAVA: ".java",
                    ProgrammingLanguages.CSHARP: ".cs",
                }
                expected_extension = language_to_extension.get(programming_language)
                if expected_extension and file_ext == expected_extension:
                    should_analyze = True

            if should_analyze:
                try:
                    file_data = FileData(normalized_path, file_content)
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
                    for cls in code_classes:
                        class_id = cls["id"]
                        classes_list.append(
                            {
                                "class_id": class_id,
                                "class_name": cls["name"],
                                "class_content": cls["code"],
                                "methods": methods_by_class.get(class_id, []),
                            }
                        )

                    # Build global methods list
                    global_methods_list = methods_by_class.get("root", [])
                    logger.info(
                        f"[_update_file_from_repo] Analyzed code - classes={len(classes_list)}, global_methods={len(global_methods_list)}"
                    )
                except Exception as e:
                    error_message = f"[_update_file_from_repo] Code analysis failed for {normalized_path}: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

            # Get or update folder_id if not set
            current_folder_id = file_document.get("folder_id")
            if not current_folder_id:
                folder_id = await _get_or_create_folder_id(
                    project_id=project_id,
                    file_path=normalized_path,
                    directory_paths=directory_paths,
                    directory_key=directory_key,
                    repository_url=repository_url,
                )
                if folder_id:
                    update_data["folder_id"] = folder_id

            # Normalize file_content: use empty string if None (for non-code files)
            normalized_file_content = file_content if file_content is not None else ""
            if not normalized_file_content:
                logger.info(
                    f"[_update_file_from_repo] Updating document with empty source_code (non-code file) - file_path={normalized_path}, file_name={file_name}"
                )

            update_data["source_code"] = normalized_file_content
            update_data["classes"] = classes_list
            update_data["global_methods"] = global_methods_list
        elif directory_key == "utd":
            # Get or update folder_id if not set
            current_folder_id = file_document.get("folder_id")
            if not current_folder_id:
                folder_id = await _get_or_create_folder_id(
                    project_id=project_id,
                    file_path=normalized_path,
                    directory_paths=directory_paths,
                    directory_key=directory_key,
                    repository_url=repository_url,
                )
                if folder_id:
                    update_data["folder_id"] = folder_id

            # Try to parse as JSON and format with indentation, otherwise use as string
            import json

            try:
                parsed_json = json.loads(file_content)
                unit_test_design_json = json.dumps(
                    parsed_json, indent=2, ensure_ascii=False
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    f"[_process_utd_folder] Failed to parse JSON in file {normalized_path}: {e}"
                )
                unit_test_design_json = file_content
            update_data["unit_test_design_json"] = unit_test_design_json
        elif directory_key == "utc":
            # Get or update folder_id if not set
            current_folder_id = file_document.get("folder_id")
            if not current_folder_id:
                folder_id = await _get_or_create_folder_id(
                    project_id=project_id,
                    file_path=normalized_path,
                    directory_paths=directory_paths,
                    directory_key=directory_key,
                    repository_url=repository_url,
                )
                if folder_id:
                    update_data["folder_id"] = folder_id

            # Unit test code - update ut_code_content
            update_data["ut_code_content"] = file_content

        # Update document
        is_updated = await repository.update_document(
            document_id=document_id,
            project_id=project_id,
            update_data=update_data,
        )

        if is_updated:
            logger.info(
                f"[_update_file_from_repo] Success - file_path={file_path}, document_id={document_id}"
            )
        else:
            logger.warning(
                f"[_update_file_from_repo] Failed to update - file_path={file_path}"
            )

        return is_updated

    except Exception as e:
        error_message = f"[_update_file_from_repo] Error - file_path={file_path}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return False


async def _create_new_file_from_repo(
    project_id: str,
    repository: Any,
    file_path: str,
    file_name: str,
    directory_key: str,
    directory_paths: Dict[str, str],
    file_content: Optional[str] = None,
    file_content_bytes: Optional[bytes] = None,
    full_file_path: Optional[str] = None,
    commit_id: Optional[str] = None,
    programming_language: Optional[str] = None,
    repository_url: Optional[str] = None,
) -> bool:
    """
    Create new file document from repository content

    Args:
        project_id: Project ID
        repository: Repository instance
        file_path: File path in repository
        file_name: File name
        file_content: File content
        commit_id: Commit ID
        directory_key: Directory key
        programming_language: Programming language for code analysis

    Returns:
        True if created successfully, False otherwise
    """
    logger.info(
        f"[_create_new_file_from_repo] Start - file_path={file_path}, directory_key={directory_key}"
    )

    try:
        from uuid6 import uuid7
        from cec_docifycode_common.models.unit_test import UnitTestDesign, UnitTestCode
        from app.services.source_code_service import source_code_service

        doc_id = str(uuid7())
        current_time = get_current_utc_time()
        normalized_path = file_path.replace("\\", "/")
        file_ext = Path(file_path).suffix.lower()

        # Create document based on directory_key
        if directory_key == "rd":
            # Read file if not provided
            if file_content_bytes is None and full_file_path:
                try:
                    with open(full_file_path, "rb") as f:
                        file_content_bytes = f.read()
                    logger.info(
                        f"[_create_new_file_from_repo] Read binary file for RD - file_path={normalized_path}"
                    )
                except Exception as e:
                    error_message = (
                        f"[_create_new_file_from_repo] Failed to read file: {e}"
                    )
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)
                    return False

            # Process RD: markdown or image
            if file_content_bytes:
                if file_ext == ".md":
                    # Markdown: decode to string
                    rd_content = {
                        "type": ContentType.MARKDOWN,
                        "content": file_content_bytes.decode("utf-8", errors="ignore"),
                    }
                    logger.info(
                        f"[_create_new_file_from_repo] Processed markdown file - file_path={normalized_path}, file_ext={file_ext}"
                    )
                else:
                    # Image: encode to base64
                    encoded_content = base64.b64encode(file_content_bytes).decode(
                        "utf-8"
                    )
                    rd_content = {
                        "type": ContentType.IMAGE,
                        "content": encoded_content,
                    }
                    logger.info(
                        f"[_create_new_file_from_repo] Processed image file - file_path={normalized_path}, file_ext={file_ext}"
                    )
            else:
                # Fallback to text content if provided
                rd_content = {
                    "type": ContentType.MARKDOWN,
                    "content": file_content or "",
                }
                logger.warning(
                    f"[_create_new_file_from_repo] Using text content fallback for RD - file_path={normalized_path}"
                )

            document = {
                "id": doc_id,
                "project_id": project_id,
                "file_name": file_name,
                "rd_content": rd_content,
                "commit_id": commit_id,
                "sync_status": SyncStatus.SYNCED,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }
            return await repository.insert_one(document)

        elif directory_key == "bd":
            # Only process FILE_NAME_BASIC_DESIGN
            if file_name != FILE_NAME_BASIC_DESIGN:
                logger.warning(
                    f"[_create_new_file_from_repo] BD only accepts {FILE_NAME_BASIC_DESIGN}, got {file_name}"
                )
                return False

            # Read file as JSON
            if full_file_path:
                try:
                    import json

                    with open(full_file_path, "r", encoding="utf-8") as f:
                        contents = json.load(f)
                        if not isinstance(contents, dict):
                            logger.warning(
                                f"[_create_new_file_from_repo] File {FILE_NAME_BASIC_DESIGN} does not contain a JSON object, using empty dict"
                            )
                            contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_create_new_file_from_repo] Failed to parse JSON from {FILE_NAME_BASIC_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}
                except Exception as e:
                    error_message = (
                        f"[_create_new_file_from_repo] Failed to read file: {e}"
                    )
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)
                    return False
            else:
                contents = {}

            document = {
                "id": doc_id,
                "project_id": project_id,
                "contents": contents,
                "commit_id": commit_id,
                "sync_status": SyncStatus.SYNCED,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }
            return await repository.insert_one(document)

        elif directory_key == "pd":
            # Only process FILE_NAME_DETAIL_DESIGN
            if file_name != FILE_NAME_DETAIL_DESIGN:
                logger.warning(
                    f"[_create_new_file_from_repo] PD only accepts {FILE_NAME_DETAIL_DESIGN}, got {file_name}"
                )
                return False

            # Read file as JSON
            if full_file_path:
                try:
                    import json

                    with open(full_file_path, "r", encoding="utf-8") as f:
                        contents = json.load(f)
                        if not isinstance(contents, dict):
                            logger.warning(
                                f"[_create_new_file_from_repo] File {FILE_NAME_DETAIL_DESIGN} does not contain a JSON object, using empty dict"
                            )
                            contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_create_new_file_from_repo] Failed to parse JSON from {FILE_NAME_DETAIL_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}
                except Exception as e:
                    error_message = (
                        f"[_create_new_file_from_repo] Failed to read file: {e}"
                    )
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)
                    return False
            else:
                contents = {}

            document = {
                "id": doc_id,
                "project_id": project_id,
                "contents": contents,
                "commit_id": commit_id,
                "sync_status": SyncStatus.SYNCED,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }
            return await repository.insert_one(document)

        elif directory_key == "src":
            # For source code, use source_code_service
            # Get or create folder_id from file_path
            folder_id = await _get_or_create_folder_id(
                project_id=project_id,
                file_path=normalized_path,
                directory_paths=directory_paths,
                directory_key=directory_key,
                repository_url=repository_url,
            )

            classes_list = []
            global_methods_list = []

            # Analyze code if needed (only if file has content)
            should_analyze = False
            if file_content and programming_language:
                language_to_extension = {
                    ProgrammingLanguages.PYTHON: ".py",
                    ProgrammingLanguages.JAVA: ".java",
                    ProgrammingLanguages.CSHARP: ".cs",
                }
                expected_extension = language_to_extension.get(programming_language)
                if expected_extension and file_ext == expected_extension:
                    should_analyze = True

            if should_analyze:
                try:
                    file_data = FileData(normalized_path, file_content)
                    code_functions = file_data.get_code_functions_dict()
                    code_classes = file_data.get_code_classes_dict()

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

                    for cls in code_classes:
                        class_id = cls["id"]
                        classes_list.append(
                            {
                                "class_id": class_id,
                                "class_name": cls["name"],
                                "class_content": cls["code"],
                                "methods": methods_by_class.get(class_id, []),
                            }
                        )

                    global_methods_list = methods_by_class.get("root", [])
                except Exception as e:
                    error_message = (
                        f"[_create_new_file_from_repo] Code analysis failed: {e}"
                    )
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

            result = await source_code_service.create_source_code_document(
                project_id=project_id,
                file_path=normalized_path,
                file_name=file_name,
                source_code=file_content,
                commit_id=commit_id,
                folder_id=folder_id,
                classes=classes_list,
                global_methods=global_methods_list,
            )
            return result is not None

        elif directory_key == "utd":
            import json

            # Get or create folder_id from file_path
            folder_id = await _get_or_create_folder_id(
                project_id=project_id,
                file_path=normalized_path,
                directory_paths=directory_paths,
                directory_key=directory_key,
                repository_url=repository_url,
            )

            try:
                parsed_json = json.loads(file_content)
                unit_test_design_json = json.dumps(
                    parsed_json, indent=2, ensure_ascii=False
                )
            except:
                unit_test_design_json = file_content

            # Link to source code based on path
            file_name_without_ext = Path(file_name).stem
            link_result = {
                "source_code_id": "",
                "class_id": "",
                "method_id": "",
            }

            # Get src_path from directory_paths
            src_path = directory_paths.get("src")
            utd_path = directory_paths.get("utd")
            if src_path and utd_path:
                try:
                    link_result = await link_unit_test_to_source_code(
                        project_id=project_id,
                        unit_test_path=normalized_path,
                        unit_test_file_name=file_name_without_ext,
                        src_base_path=src_path,
                        utd_utc_base_path=utd_path,
                    )
                except Exception as e:
                    error_message = f"[_create_new_file_from_repo] Failed to link unit test to source code: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

            document = {
                "id": doc_id,
                "project_id": project_id,
                "source_code_id": link_result.get("source_code_id", ""),
                "class_id": link_result.get("class_id", ""),
                "method_id": link_result.get("method_id", ""),
                "file_name": file_name,
                "file_path": normalized_path,
                "folder_id": folder_id,
                "collection_name": UnitTestDesign.Config.collection_name,
                "unit_test_design_json": unit_test_design_json,
                "decision_table": None,
                "test_pattern": None,
                "commit_id": commit_id,
                "sync_status": SyncStatus.SYNCED,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }
            from app.core.database import app_db

            return await app_db.unit_test_repository.insert_unit_test(document)

        elif directory_key == "utc":
            # Get or create folder_id from file_path
            folder_id = await _get_or_create_folder_id(
                project_id=project_id,
                file_path=normalized_path,
                directory_paths=directory_paths,
                directory_key=directory_key,
                repository_url=repository_url,
            )

            # Link to source code based on path
            file_name_without_ext = Path(file_name).stem
            link_result = {
                "source_code_id": "",
                "class_id": "",
                "method_id": "",
            }

            # Get src_path from directory_paths
            src_path = directory_paths.get("src")
            utc_path = directory_paths.get("utc")
            if src_path and utc_path:
                try:
                    link_result = await link_unit_test_to_source_code(
                        project_id=project_id,
                        unit_test_path=normalized_path,
                        unit_test_file_name=file_name_without_ext,
                        src_base_path=src_path,
                        utd_utc_base_path=utc_path,
                    )
                except Exception as e:
                    error_message = f"[_create_new_file_from_repo] Failed to link unit test to source code: {e}"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

            document = {
                "id": doc_id,
                "project_id": project_id,
                "source_code_id": link_result.get("source_code_id", ""),
                "class_id": link_result.get("class_id", ""),
                "method_id": link_result.get("method_id", ""),
                "file_name": file_name,
                "file_path": normalized_path,
                "folder_id": folder_id,
                "collection_name": UnitTestCode.Config.collection_name,
                "ut_code_content": file_content,
                "commit_id": commit_id,
                "sync_status": SyncStatus.SYNCED,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }
            from app.core.database import app_db

            return await app_db.unit_test_repository.insert_unit_test(document)

        else:
            logger.warning(
                f"[_create_new_file_from_repo] Unknown directory_key: {directory_key}"
            )
            return False

    except Exception as e:
        error_message = (
            f"[_create_new_file_from_repo] Error - file_path={file_path}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return False
