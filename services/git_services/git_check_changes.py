"""
Git check changes service
Check for new commits and file changes in Git repository
"""

import logging
import os
import tempfile
import shutil
from typing import List, Dict, Any, Optional, Set
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
from app.services.git_services.git_clone import _prepare_clone_url
from app.utils.constants import (
    GitType,
    SyncStatus,
    DOCIFYCODE_DIRECTORY_NAME,
    DEFAULT_DIRECTORY_BD_PD,
)
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from cec_docifycode_common.models.detail_design import FILE_NAME_DETAIL_DESIGN
from app.utils.http_helpers import raise_http_error
from fastapi import HTTPException, status
from app.schemas.unit_test import (
    UNIT_TEST_DESIGN_COLLECTION,
    UNIT_TEST_CODE_COLLECTION,
)

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)

# Directory mapping for file tracking
DIRECTORY_MAPPING = {
    "rd": "requirement_document",
    "bd": "basic_design",
    "pd": "detail_design",
    "src": "source_code",
    "utd": UNIT_TEST_DESIGN_COLLECTION,
    "utc": UNIT_TEST_CODE_COLLECTION,
}


async def check_repo_changes(
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
    db_commit_ids: List[str],
    directory_paths: Dict[str, str],
) -> Dict[str, Any]:
    """
    Check for new commits and file changes in Git repository

    Args:
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password
        db_commit_ids: List of commit IDs from database
        directory_paths: Dictionary of directory paths (rd, bd, pd, src, utd, utc)

    Returns:
        Dictionary with changed files information
    """
    logger.info(
        f"[check_repo_changes] Start - repository_url={repository_url}, branch_name={branch_name}"
    )

    if not repository_url or not branch_name or not user_name:
        logger.warning("[check_repo_changes] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    temp_dir = None
    repo = None
    try:
        # Validate git repository
        git_type = await validate_git_repository(
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
        )
        logger.info(
            f"[check_repo_changes] Git repository validated - git_type={git_type}"
        )

        # Clone repository to temporary directory
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)
        temp_dir = tempfile.mkdtemp(prefix="git_check_changes_")
        logger.info(f"[check_repo_changes] Cloning to temp directory: {temp_dir}")

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)
        logger.info(f"[check_repo_changes] Repository cloned successfully")

        # Get all commit IDs from repository
        repo_commits = get_all_commit_ids(repo, branch_name)
        repo_commit_ids = [
            c.get("commit_id") for c in repo_commits if c.get("commit_id")
        ]
        logger.info(
            f"[check_repo_changes] Found {len(repo_commit_ids)} commits in repository"
        )

        # Find new commits (commits in repo but not in DB)
        normalized_db_commits = []
        if db_commit_ids:
            for c in db_commit_ids:
                if isinstance(c, str):
                    normalized_db_commits.append(c)
                elif hasattr(c, "commit_id"):  # CommitInfo object
                    normalized_db_commits.append(c.commit_id)
                elif isinstance(c, dict) and "commit_id" in c:
                    normalized_db_commits.append(c["commit_id"])

        db_commit_set = set(normalized_db_commits)
        new_commit_ids = [
            commit_id for commit_id in repo_commit_ids if commit_id not in db_commit_set
        ]
        logger.info(f"[check_repo_changes] Found {len(new_commit_ids)} new commits")

        # Get file changes from new commits
        changed_files = _get_file_changes_from_commits(
            repo=repo,
            commit_ids=new_commit_ids,
            directory_paths=directory_paths,
        )

        logger.info(
            f"[check_repo_changes] Success - found {len(changed_files)} changed files"
        )
        return {
            "new_commits": new_commit_ids,
            "changed_files": changed_files,
        }

    except HTTPException:
        raise
    except GitCommandError as e:
        error_message = f"[check_repo_changes] Git command error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_CHECK_CHANGES_FAILED"
        )
    except Exception as e:
        error_message = f"[check_repo_changes] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_CHECK_CHANGES_FAILED"
        )
    finally:
        cleanup_temp_dir(temp_dir, repo)


def _get_file_changes_from_commits(
    repo: Repo, commit_ids: List[str], directory_paths: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Get file changes from commits that are in tracked directories

    Args:
        repo: GitPython Repo object
        commit_ids: List of commit IDs to check
        directory_paths: Dictionary of directory paths

    Returns:
        List of changed files with their information
    """
    logger.info(
        f"[_get_file_changes_from_commits] Start - commit_count={len(commit_ids)}"
    )

    if not commit_ids:
        logger.info("[_get_file_changes_from_commits] No commits to check")
        return []

    changed_files = []
    tracked_directories = set(directory_paths.values())

    try:
        for commit_id in commit_ids:
            try:
                commit = repo.commit(commit_id)
                # Get files changed in this commit
                changed_files_in_commit = _get_files_from_commit(
                    commit=commit,
                    tracked_directories=tracked_directories,
                    directory_paths=directory_paths,
                )
                changed_files.extend(changed_files_in_commit)
            except Exception as e:
                error_message = f"[_get_file_changes_from_commits] Error processing commit {commit_id}: {e}"
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

                continue

        logger.info(
            f"[_get_file_changes_from_commits] Success - found {len(changed_files)} changed files"
        )
        return changed_files

    except Exception as e:
        error_message = f"[_get_file_changes_from_commits] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _get_files_from_commit(
    commit: Any, tracked_directories: Set[str], directory_paths: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Get files from a commit that are in tracked directories

    Args:
        commit: Git commit object
        tracked_directories: Set of tracked directory paths
        directory_paths: Dictionary mapping directory keys to paths

    Returns:
        List of file information dictionaries
    """
    changed_files = []

    try:
        # Get all files changed in this commit (added, modified, deleted)
        all_files = list(commit.stats.files.keys())
        logger.info(
            f"[_get_files_from_commit] Start - commit_id={commit.hexsha[:8]}, total_files={len(all_files)}"
        )
        logger.debug(f"[_get_files_from_commit] All files in commit: {all_files}")

        for file_path in all_files:
            # Check if file is in any tracked directory
            directory_key = _get_directory_key_for_file(file_path, directory_paths)
            if not directory_key:
                logger.debug(
                    f"[_get_files_from_commit] Skipping file (not in tracked directory): {file_path}"
                )
                continue

            # Determine change type
            change_type = "modified"
            try:
                # Check if file exists in commit
                commit.tree[file_path]
            except KeyError:
                # File doesn't exist in commit, it was deleted
                change_type = "deleted"

            logger.debug(
                f"[_get_files_from_commit] Adding file - path={file_path}, directory_key={directory_key}, change_type={change_type}"
            )
            changed_files.append(
                {
                    "file_path": file_path,
                    "commit_id": commit.hexsha,
                    "directory_key": directory_key,
                    "change_type": change_type,
                }
            )

        logger.info(
            f"[_get_files_from_commit] Success - commit_id={commit.hexsha[:8]}, tracked_files={len(changed_files)}/{len(all_files)}"
        )
        return changed_files

    except Exception as e:
        error_message = f"[_get_files_from_commit] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return []


def _get_directory_key_for_file(
    file_path: str, directory_paths: Dict[str, str]
) -> Optional[str]:
    """
    Get directory key for a file path based on root directory

    Args:
        file_path: File path in repository (e.g., "BD/business.md", "PD/class_design.md", "Docifycode/BD/business.md", "main.py")
        directory_paths: Dictionary mapping directory keys to paths

    Returns:
        Directory key if file is in tracked directory, None otherwise
    """
    # Normalize file path
    normalized_path = file_path.replace("\\", "/").strip("/")

    if not normalized_path:
        logger.debug(f"[_get_directory_key_for_file] Empty path: {file_path}")
        return None

    # Get root directory (first part of path)
    # e.g., "BD/business.md" -> "BD", "PD/CD/class_design.md" -> "PD", "Docifycode/BD/business.md" -> "Docifycode", "main.py" -> "main.py"
    path_parts = normalized_path.split("/")
    root_dir = path_parts[0] if path_parts else None

    if not root_dir:
        logger.debug(f"[_get_directory_key_for_file] No root directory: {file_path}")
        return None

    # Check if src is "/" (root) - files at root (not in DOCIFYCODE_DIRECTORY_NAME) belong to src
    src_path = directory_paths.get("src", "")
    normalized_src = src_path.strip().strip("/") if src_path else ""
    is_root_src = not normalized_src or normalized_src == ""

    # Track if we're inside DOCIFYCODE_DIRECTORY_NAME
    is_in_docifycode = False
    check_index_offset = 0

    # If root is DOCIFYCODE_DIRECTORY_NAME, check the next part
    if root_dir.lower() == DOCIFYCODE_DIRECTORY_NAME.lower():
        if len(path_parts) > 1:
            # File is in DOCIFYCODE_DIRECTORY_NAME, check the next directory
            is_in_docifycode = True
            check_index_offset = 1
            root_dir = path_parts[1]
            normalized_path = "/".join(path_parts[1:])
        else:
            logger.debug(
                f"[_get_directory_key_for_file] File in {DOCIFYCODE_DIRECTORY_NAME} but no subdirectory: {file_path}"
            )
            return None

    # Note: Don't return "src" here yet - need to check all directories first
    # Only return "src" if file doesn't match any tracked directory

    # Check each directory path to find matching root (case-insensitive)
    for key, dir_path in directory_paths.items():
        if not dir_path:
            continue

        # Skip src if it's "/" (root) - already handled above
        if key == "src" and is_root_src:
            continue

        # Normalize directory path
        normalized_dir = dir_path.replace("\\", "/").strip("/")

        # If directory path starts with DOCIFYCODE_DIRECTORY_NAME, remove it for comparison
        # e.g., "Docifycode/RD" -> "RD" when comparing with normalized_path "RD/test.md"
        dir_path_parts = normalized_dir.split("/")
        if (
            dir_path_parts
            and dir_path_parts[0].lower() == DOCIFYCODE_DIRECTORY_NAME.lower()
        ):
            # Remove DOCIFYCODE_DIRECTORY_NAME from directory path for comparison
            normalized_dir_for_match = "/".join(dir_path_parts[1:])
        else:
            normalized_dir_for_match = normalized_dir

        # Check if root directory matches (case-insensitive)
        # Case 1: Exact match of root directory (e.g., "RD" == "RD")
        # Case 2: File path starts with directory path (e.g., "RD/test.md" starts with "RD/")
        directory_matches = (
            normalized_dir_for_match.lower() == root_dir.lower()
            or normalized_path.lower().startswith(
                normalized_dir_for_match.lower() + "/"
            )
            or normalized_path.lower() == normalized_dir_for_match.lower()
        )

        if directory_matches:
            # Note: bd and pd may share the same folder path (e.g., ".design_document")
            # but they require different file names:
            # - bd: only accepts FILE_NAME_BASIC_DESIGN -> collection: basic_design
            # - pd: only accepts FILE_NAME_DETAIL_DESIGN -> collection: detail_design
            # If file name doesn't match, continue to check other directories (e.g., pd after bd)

            # For bd: only accept FILE_NAME_BASIC_DESIGN
            if key == "bd":
                file_name = os.path.basename(normalized_path)
                if file_name != FILE_NAME_BASIC_DESIGN:
                    logger.debug(
                        f"[_get_directory_key_for_file] File {file_name} does not match {FILE_NAME_BASIC_DESIGN} for bd, continuing to check pd"
                    )
                    continue
            # For pd: only accept FILE_NAME_DETAIL_DESIGN
            elif key == "pd":
                file_name = os.path.basename(normalized_path)
                if file_name != FILE_NAME_DETAIL_DESIGN:
                    logger.debug(
                        f"[_get_directory_key_for_file] File {file_name} does not match {FILE_NAME_DETAIL_DESIGN} for pd, continuing to check other directories"
                    )
                    continue
            logger.debug(
                f"[_get_directory_key_for_file] Matched - file={file_path}, directory_key={key}, dir_path={dir_path}, collection={'basic_design' if key == 'bd' else 'detail_design' if key == 'pd' else key}"
            )
            return key

    # If no match found and src is "/" (root), check if file belongs to src
    # Files belong to src if they are:
    # 1. At root (e.g., "main.py")
    # 2. In any folder EXCEPT "Docifycode/" and ".design_document/"
    if is_root_src:
        # Get original root_dir (before any processing)
        original_root_dir = (
            normalized_path.split("/")[0] if normalized_path else root_dir
        )

        # Check if file is NOT in excluded folders
        is_not_in_excluded_folders = (
            original_root_dir.lower() != DOCIFYCODE_DIRECTORY_NAME.lower()
            and original_root_dir.lower() != DEFAULT_DIRECTORY_BD_PD.lower()
        )

        if is_not_in_excluded_folders:
            logger.debug(
                f"[_get_directory_key_for_file] File belongs to src (root) - file={file_path}, root_dir={original_root_dir}"
            )
            return "src"

    logger.debug(
        f"[_get_directory_key_for_file] No match found - file={file_path}, root_dir={root_dir}, directory_paths={directory_paths}"
    )
    return None
