"""
Git push service for pushing files to Git repository
"""

import logging
import os
import tempfile
import shutil
import base64
from typing import List, Dict, Any, Optional, Tuple, Union

from git import Repo, GitCommandError
from app.services.git_services.git_validate import validate_git_url, validate_git_branch
from app.utils.constants import GitType, SyncStatus
from app.utils.http_helpers import raise_http_error
from fastapi import status
from cec_docifycode_common.models.source_code import SourceCode

from app.services.logs_service import save_exception_log_sync, LogLevel
from app.services.git_services.git_service import (
    cleanup_temp_dir,
    clone_repository,
    _prepare_clone_url,
)

logger = logging.getLogger(__name__)


async def check_conflicts_before_push(
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
    files_with_commit_id: List[Dict[str, Any]],
    target_directory: str,
) -> List[Dict[str, Any]]:
    """
    Check for conflicts before pushing files to Git

    Args:
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password
        files_with_commit_id: List of files with commit_id info: [{"file_name": "...", "local_commit_id": "...", "file_id": "...", "project_id": "..."}]
        target_directory: Target directory path in repository

    Returns:
        List of conflict files with remote commit_id
    """
    logger.info(
        f"[check_conflicts_before_push] Start - files_count={len(files_with_commit_id)}"
    )

    if not files_with_commit_id:
        logger.info(
            "[check_conflicts_before_push] No files to check, returning empty conflicts"
        )
        return []

    temp_dir = None
    conflict_files = []

    try:
        # Prepare clone URL with credentials
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)

        # Create temporary directory for cloning
        temp_dir = tempfile.mkdtemp(prefix="git_check_conflict_")
        logger.info(f"[check_conflicts_before_push] Created temp directory: {temp_dir}")

        # Clone repository with full history to get correct commit_id for files

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)
        logger.info("[check_conflicts_before_push] Repository cloned successfully")

        # Get target path
        target_path = os.path.join(repo.working_dir, target_directory.strip("/"))

        # Check each file for conflicts
        for file_info in files_with_commit_id:
            file_name = file_info.get("file_name")
            # Use file_path from document if available, otherwise use file_name
            file_path_from_doc = file_info.get("file_path") or file_name
            local_commit_id = file_info.get("local_commit_id") or ""
            file_id = file_info.get("file_id")
            project_id = file_info.get("project_id")
            collection_name = file_info.get("collection_name")

            if not file_name or not local_commit_id or not local_commit_id.strip():
                continue

            # Use file_path from document if available, otherwise construct from target_directory + file_name
            if file_path_from_doc and file_path_from_doc != file_name:
                # file_path_from_doc is relative to repo root (e.g., "PD/CD/method_design.md")
                git_file_path = file_path_from_doc.replace("\\", "/")
                file_path = os.path.join(repo.working_dir, git_file_path)
            else:
                # Fallback: construct path from target_directory + file_name
                file_path = os.path.join(target_path, file_name)
                git_file_path = os.path.join(
                    target_directory.strip("/"), file_name
                ).replace("\\", "/")

            # Check if file exists in repository
            if not os.path.exists(file_path):
                # File doesn't exist on remote, no conflict
                continue

            try:
                # Get commit ID for this specific file using git log --follow (same as git_clone.py)
                remote_commit_id = None

                # Try git log --follow first (most reliable)
                try:
                    commit_id = repo.git.log(
                        branch_name,
                        "--follow",
                        "-1",
                        "--format=%H",
                        "--",
                        git_file_path,
                    ).strip()

                    if commit_id:
                        logger.info(
                            f"[check_conflicts_before_push] commit_id={commit_id} for file={git_file_path}"
                        )
                        # Verify commit actually contains this file
                        commit = repo.commit(commit_id)
                        if git_file_path in commit.stats.files:
                            remote_commit_id = commit_id
                            logger.info(
                                f"[check_conflicts_before_push] Found commit_id={remote_commit_id} for file={file_name} via git log"
                            )
                except Exception as e:
                    error_message = f"[check_conflicts_before_push] Git log failed for file={file_name}: {e}, trying iter_commits"
                    logger.warning(error_message)
                    save_exception_log_sync(
                        e, error_message, __name__, level=LogLevel.WARNING
                    )

                # Fallback: try iter_commits and verify
                if not remote_commit_id:
                    try:
                        commits = list(
                            repo.iter_commits(
                                branch_name, paths=git_file_path, max_count=10
                            )
                        )

                        # Verify each commit actually contains this file
                        for commit in commits:
                            if git_file_path in commit.stats.files:
                                remote_commit_id = commit.hexsha
                                logger.info(
                                    f"[check_conflicts_before_push] Found commit_id={remote_commit_id} for file={file_name} via iter_commits"
                                )
                                break
                    except Exception as e:
                        error_message = f"[check_conflicts_before_push] iter_commits failed for file={file_name}: {e}"
                        logger.warning(error_message)
                        save_exception_log_sync(
                            e, error_message, __name__, level=LogLevel.WARNING
                        )

                # Compare local commit_id with remote commit_id
                if (
                    remote_commit_id
                    and remote_commit_id.strip() != local_commit_id.strip()
                ):
                    # Conflict detected: remote has different commit_id
                    logger.warning(
                        f"[check_conflicts_before_push] Conflict detected - file={file_name}, local_commit={local_commit_id}, remote_commit={remote_commit_id}"
                    )
                    conflict_files.append(
                        {
                            "id": file_id,
                            "project_id": project_id,
                            "file_name": file_name,
                            "file_path": git_file_path,
                            "commit_id": remote_commit_id,
                            "sync_status": SyncStatus.PULL_PUSH,
                            "collection_name": collection_name
                            or SourceCode.Config.collection_name,
                        }
                    )
                elif not remote_commit_id:
                    # File exists but no commit history found (shouldn't happen, but handle it)
                    logger.info(
                        f"[check_conflicts_before_push] File exists but no commit history - file={file_name}, assuming no conflict"
                    )
            except Exception as e:
                error_message = f"[check_conflicts_before_push] Error checking file {file_name}: {e}"
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

                # If we can't check, assume conflict to be safe
                conflict_files.append(
                    {
                        "id": file_id,
                        "project_id": project_id,
                        "file_name": file_name,
                        "file_path": git_file_path,
                        "commit_id": "",
                        "sync_status": SyncStatus.PULL_PUSH,
                        "collection_name": collection_name
                        or SourceCode.Config.collection_name,
                    }
                )

        logger.info(
            f"[check_conflicts_before_push] Completed - conflicts_count={len(conflict_files)}"
        )
        return conflict_files

    except Exception as e:
        error_message = f"[check_conflicts_before_push] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        # If we can't check conflicts, return all files as conflicts to be safe
        return [
            {
                "id": f.get("file_id"),
                "project_id": f.get("project_id"),
                "file_name": f.get("file_name"),
                "file_path": os.path.join(
                    target_directory.strip("/"), f.get("file_name", "")
                ).replace("\\", "/"),
                "commit_id": "",
                "sync_status": SyncStatus.PULL_PUSH,
                "collection_name": f.get("collection_name")
                or SourceCode.Config.collection_name,
            }
            for f in files_with_commit_id
        ]
    finally:
        # Cleanup temporary directory
        cleanup_temp_dir(temp_dir, repo)


async def push_files_to_git(
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
    files: List[Dict[str, Any]],
    commit_message: str,
    target_directory: str,
    files_to_delete: Optional[List[str]] = None,
) -> Union[str, Dict[str, Any]]:
    """
    Push files to Git repository

    Args:
        repository_url: Git repository URL
        branch_name: Branch name to push to
        user_name: Git username
        token_password: Git token/password
        files: List of file dictionaries with 'file_name' and 'content' (bytes or str)
        commit_message: Commit message
        target_directory: Target directory path in repository (e.g., "docs/requirements")

    Returns:
        Commit ID after push

    Raises:
        HTTPException: If push fails
    """
    logger.info(
        f"[push_files_to_git] Start - repository_url={repository_url}, branch_name={branch_name}, files_count={len(files)}"
    )

    if not repository_url or not branch_name or not user_name:
        logger.warning("[push_files_to_git] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    if (not files or len(files) == 0) and (
        not files_to_delete or len(files_to_delete) == 0
    ):
        logger.warning("[push_files_to_git] No files to push or delete")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_NO_FILES")

    if not commit_message or not commit_message.strip():
        logger.warning("[push_files_to_git] Missing commit message")
        raise_http_error(
            status.HTTP_400_BAD_REQUEST, error_key="GIT_COMMIT_MESSAGE_REQUIRED"
        )

    temp_dir = None
    try:
        # Validate Git URL and branch
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[push_files_to_git] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        await validate_git_branch(
            repository_url, branch_name, user_name, token_password
        )

        # Prepare clone URL with credentials
        clone_url = _prepare_clone_url(repository_url, user_name, token_password)

        # Create temporary directory for cloning
        temp_dir = tempfile.mkdtemp(prefix="git_push_")
        logger.info(f"[push_files_to_git] Created temp directory: {temp_dir}")

        # Clone repository
        from app.services.git_services.git_service import clone_repository

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=1)
        logger.info(f"[push_files_to_git] Repository cloned successfully")

        # Ensure target directory exists
        target_path = os.path.join(repo.working_dir, target_directory.strip("/"))
        os.makedirs(target_path, exist_ok=True)
        logger.info(f"[push_files_to_git] Target directory ensured: {target_path}")

        # Write files to repository
        for file_info in files:
            file_name = file_info.get("file_name")
            content = file_info.get("content")
            file_path_from_doc = file_info.get("file_path")

            if not file_name:
                logger.warning("[push_files_to_git] Skipping file with no name")
                continue

            # Use file_path from document if available, otherwise construct from target_directory + file_name
            if file_path_from_doc and file_path_from_doc != file_name:
                # file_path_from_doc is relative to repo root (e.g., "PD/CD/method_design.md")
                git_file_path = file_path_from_doc.replace("\\", "/")
                file_path = os.path.join(repo.working_dir, git_file_path)
                # Ensure parent directories exist
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
            else:
                # Fallback: construct path from target_directory + file_name
                # Normalize file_name to use forward slashes for consistency
                normalized_file_name = file_name.replace("\\", "/")
                file_path = os.path.join(target_path, normalized_file_name)
                # Ensure parent directories exist (in case file_name contains subdirectories)
                parent_dir = os.path.dirname(file_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)

            # Write content to file
            if isinstance(content, bytes):
                with open(file_path, "wb") as f:
                    f.write(content)
            elif isinstance(content, str):
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                logger.warning(
                    f"[push_files_to_git] Unsupported content type for file: {file_name}"
                )
                continue

            logger.info(f"[push_files_to_git] File written: {file_path}")

        # Delete files if specified
        if files_to_delete:
            for file_path_or_name in files_to_delete:
                # Check if it's a full path (contains directory separator) or just file name
                if "/" in file_path_or_name or "\\" in file_path_or_name:
                    # It's a file_path relative to repo root (e.g., "PD/CD/method_design.md")
                    git_file_path = file_path_or_name.replace("\\", "/")
                    file_path = os.path.join(repo.working_dir, git_file_path)
                else:
                    # It's just a file name, use target_directory
                    file_path = os.path.join(target_path, file_path_or_name)

                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.info(f"[push_files_to_git] File deleted: {file_path}")
                else:
                    logger.warning(
                        f"[push_files_to_git] File not found for deletion: {file_path}"
                    )

        # Stage all changes (including deletions)
        repo.git.add(A=True)
        logger.info("[push_files_to_git] Files staged")

        # Check if there are changes to commit
        if repo.is_dirty() or repo.untracked_files:
            # Commit changes
            repo.index.commit(commit_message)
            logger.info(f"[push_files_to_git] Changes committed: {commit_message}")

            # Push to remote
            origin = repo.remote(name="origin")
            origin.push(branch_name)
            logger.info(f"[push_files_to_git] Changes pushed to remote")
        else:
            logger.warning("[push_files_to_git] No changes to commit")
            # Still return current commit ID even if no changes

        # Get commit ID and time
        commit = repo.head.commit
        commit_id = commit.hexsha
        commit_time = commit.committed_datetime.isoformat()

        logger.info(
            f"[push_files_to_git] Success - commit_id={commit_id}, time={commit_time}"
        )

        return {"commit_id": commit_id, "time": commit_time}

    except GitCommandError as e:
        error_message = f"[push_files_to_git] Git command error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_PUSH_FAILED"
        )
    except Exception as e:
        error_message = f"[push_files_to_git] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_PUSH_FAILED"
        )
    finally:
        # Cleanup temporary directory
        cleanup_temp_dir(temp_dir, repo)


async def fetch_remote_file_content(
    repository_url: str,
    branch_name: str,
    user_name: str,
    token_password: Optional[str],
    relative_file_path: str,
) -> Optional[bytes]:
    """
    Fetch a single file content from remote repository.

    Args:
        repository_url: Git repository URL
        branch_name: Branch name
        user_name: Git username
        token_password: Git token/password
        relative_file_path: File path relative to repository root

    Returns:
        File content in bytes if exists, otherwise None
    """
    logger.info(
        "[fetch_remote_file_content] Start - branch_name=%s, relative_file_path=%s",
        branch_name,
        relative_file_path,
    )

    if not repository_url or not branch_name or not relative_file_path:
        logger.warning(
            "[fetch_remote_file_content] Missing required parameters - repository_url=%s, branch_name=%s, relative_file_path=%s",
            repository_url,
            branch_name,
            relative_file_path,
        )
        raise_http_error(
            status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_INPUT_FOR_RESTORE"
        )

    temp_dir = None

    try:
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                "[fetch_remote_file_content] Invalid repository URL - repository_url=%s",
                repository_url,
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        await validate_git_branch(
            repository_url, branch_name, user_name, token_password
        )

        clone_url = _prepare_clone_url(repository_url, user_name, token_password)
        temp_dir = tempfile.mkdtemp(prefix="git_restore_")
        logger.info(
            "[fetch_remote_file_content] Created temp directory for restore: %s",
            temp_dir,
        )

        from app.services.git_services.git_service import clone_repository

        repo = clone_repository(clone_url, branch_name, temp_dir, depth=1)

        normalized_path = relative_file_path.replace("\\", "/").strip("/")
        if not normalized_path:
            logger.warning(
                "[fetch_remote_file_content] Normalized path is empty for file: %s",
                relative_file_path,
            )
            return None

        path_parts = [part for part in normalized_path.split("/") if part]
        file_path = os.path.join(repo.working_dir, *path_parts)

        if not os.path.exists(file_path):
            logger.warning(
                "[fetch_remote_file_content] File not found in repository - path=%s",
                file_path,
            )
            return None

        with open(file_path, "rb") as file:
            content = file.read()
            logger.info(
                "[fetch_remote_file_content] File content fetched - bytes=%s",
                len(content),
            )
            return content

    except Exception as e:
        error_message = (
            "[fetch_remote_file_content] Error fetching remote content: %s" % (e,)
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise
    finally:
        cleanup_temp_dir(temp_dir, repo)
