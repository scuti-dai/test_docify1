"""
Git service functions for repository operations
"""

import gc
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import httpx
from typing import Optional, List, Any, Dict
from git import Repo
from app.services.git_services.git_validate import (
    validate_git_url,
    handle_git_api_error,
)
from app.utils.constants import GitType, DOCIFYCODE_DIRECTORY_NAME
from app.utils.http_helpers import raise_http_error
import requests
from fastapi import status

from app.services.logs_service import save_exception_log_sync, LogLevel
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)


def _prepare_clone_url(
    repository_url: str, user_name: Optional[str], token_password: Optional[str]
) -> str:
    """Prepare clone URL with credentials"""
    logger.info("[_prepare_clone_url] Start")

    if not user_name or not token_password:
        logger.info("[_prepare_clone_url] No credentials provided, using original URL")
        return repository_url

    safe_user = quote(user_name, safe="")
    safe_password = quote(token_password, safe="")

    try:
        if repository_url.startswith("https://"):
            url_parts = repository_url.replace("https://", "").split("/", 1)
            if len(url_parts) == 2:
                clone_url = (
                    f"https://{safe_user}:{safe_password}@{url_parts[0]}/{url_parts[1]}"
                )
                logger.info("[_prepare_clone_url] Added credentials to HTTPS URL")
                return clone_url
        elif repository_url.startswith("http://"):
            url_parts = repository_url.replace("http://", "").split("/", 1)
            if len(url_parts) == 2:
                clone_url = (
                    f"http://{safe_user}:{safe_password}@{url_parts[0]}/{url_parts[1]}"
                )
                logger.info("[_prepare_clone_url] Added credentials to HTTP URL")
                return clone_url

        logger.warning("[_prepare_clone_url] Could not parse URL, using original")
        return repository_url

    except Exception as e:
        error_message = f"[_prepare_clone_url] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return repository_url


async def get_default_branch(
    repository_url: str,
    user_name: Optional[str],
    token_password: Optional[str],
    git_type: Optional[GitType] = None,
) -> str:
    """
    Get default branch from Git repository (supports GitHub and GitBucket)

    Args:
        repository_url: Git repository URL
        user_name: Git username (optional)
        token_password: Git token/password (optional)
        git_type: Git type (GitHub/GitBucket), if None will be auto-detected

    Returns:
        Default branch name

    Raises:
        HTTPException: If repository access fails
    """
    logger.info(f"[get_default_branch] Start - repository_url={repository_url}")

    if not repository_url:
        logger.warning("[get_default_branch] Missing repository_url")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Determine Git type if not provided
        if not git_type:
            git_type = await validate_git_url(repository_url)
            if not git_type:
                logger.warning(
                    f"[get_default_branch] Invalid URL format - repository_url={repository_url}"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL"
                )

        # Get default branch based on Git type
        if git_type == GitType.GITHUB:
            return await _get_github_default_branch(
                repository_url, user_name, token_password
            )
        elif git_type == GitType.GITBUCKET:
            return await _get_gitbucket_default_branch(
                repository_url, user_name, token_password
            )
        else:
            logger.warning(
                f"[get_default_branch] Unsupported Git type - git_type={git_type}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    except Exception as e:
        error_message = (
            f"[get_default_branch] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _get_github_default_branch(
    repository_url: str,
    user_name: Optional[str],
    token: Optional[str],
) -> str:
    """Get default branch from GitHub repository"""
    logger.info(f"[_get_github_default_branch] Start - repository_url={repository_url}")

    try:
        # Extract owner and repo from URL
        url_pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[_get_github_default_branch] Invalid GitHub URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        owner = match.group(1)
        repo = match.group(2)

        # Prepare headers
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DocifyCode",
        }

        # Add authentication if token provided
        if token:
            headers["Authorization"] = f"token {token}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get repository info
            repo_url = f"https://api.github.com/repos/{owner}/{repo}"
            repo_response = await client.get(repo_url, headers=headers)

            handle_git_api_error(
                repo_response, repository_url, "_get_github_default_branch"
            )

            repo_data = repo_response.json()
            default_branch = repo_data.get("default_branch", "main")

            logger.info(
                f"[_get_github_default_branch] Success - default_branch={default_branch}"
            )
            return default_branch

    except Exception as e:
        error_message = (
            f"[_get_github_default_branch] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _get_gitbucket_default_branch(
    repository_url: str,
    user_name: Optional[str],
    password: Optional[str],
) -> str:
    """Get default branch from GitBucket repository"""
    logger.info(
        f"[_get_gitbucket_default_branch] Start - repository_url={repository_url}"
    )

    if not user_name or not password:
        logger.warning(
            "[_get_gitbucket_default_branch] Missing authentication credentials"
        )
        raise_http_error(
            status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
        )

    try:
        # Extract protocol, host, and repository path from URL (same logic as validate_gitbucket_repository)
        url_pattern = r"(https?://)([^/]+)/(.+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[_get_gitbucket_default_branch] Invalid GitBucket URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        protocol = match.group(1)  # http:// or https://
        host = match.group(2)
        repo_path = match.group(3)

        # Remove /git/ prefix if present
        if repo_path.startswith("git/"):
            repo_path = repo_path[4:]

        # Prepare authentication
        auth = (user_name, password)

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            # Get repository info
            api_url = f"{protocol}{host}/api/v3/repos/{repo_path}"
            repo_response = await client.get(api_url, auth=auth)

            handle_git_api_error(
                repo_response, repository_url, "_get_gitbucket_default_branch"
            )

            repo_data = repo_response.json()
            default_branch = repo_data.get("default_branch", "master")

            logger.info(
                f"[_get_gitbucket_default_branch] Success - default_branch={default_branch}"
            )
            return default_branch

    except Exception as e:
        error_message = f"[_get_gitbucket_default_branch] Error - repository_url={repository_url}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def get_list_branches(
    repository_url: str,
    user_name: str,
    token_password: Optional[str],
    git_type: Optional[GitType] = None,
) -> List[str]:
    """
    Get list of branches from Git repository (supports GitHub and GitBucket)

    Args:
        repository_url: Git repository URL
        user_name: Git username (required)
        token_password: Git token/password (optional)
        git_type: Git type (GitHub/GitBucket), if None will be auto-detected

    Returns:
        List of branch names

    Raises:
        HTTPException: If repository access fails
    """
    logger.info(f"[get_list_branches] Start - repository_url={repository_url}")

    if not repository_url or not user_name:
        logger.warning("[get_list_branches] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Determine Git type if not provided
        if not git_type:
            git_type = await validate_git_url(repository_url)
            if not git_type:
                logger.warning(
                    f"[get_list_branches] Invalid URL format - repository_url={repository_url}"
                )
                raise_http_error(
                    status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL"
                )

        # Get branches based on Git type
        if git_type == GitType.GITHUB:
            return await _get_github_branches(repository_url, user_name, token_password)
        elif git_type == GitType.GITBUCKET:
            return await _get_gitbucket_branches(
                repository_url, user_name, token_password
            )
        else:
            logger.warning(
                f"[get_list_branches] Unsupported Git type - git_type={git_type}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    except Exception as e:
        error_message = (
            f"[get_list_branches] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _get_github_branches(
    repository_url: str,
    user_name: Optional[str],
    token: Optional[str],
) -> List[str]:
    """Get list of branches from GitHub repository"""
    logger.info(f"[_get_github_branches] Start - repository_url={repository_url}")

    try:
        # Extract owner and repo from URL
        url_pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[_get_github_branches] Invalid GitHub URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        owner = match.group(1)
        repo = match.group(2)

        # Prepare headers
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DocifyCode",
        }

        # Add authentication if token provided
        if token:
            headers["Authorization"] = f"token {token}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Get all branches with pagination
            all_branches = []
            page = 1
            per_page = 100

            while True:
                branches_url = f"https://api.github.com/repos/{owner}/{repo}/branches?per_page={per_page}&page={page}"
                branches_response = await client.get(branches_url, headers=headers)

                handle_git_api_error(
                    branches_response, repository_url, "_get_github_branches"
                )

                branches_data = branches_response.json()

                # If this page has no more branches => break
                if not branches_data:
                    logger.info(
                        f"[_get_github_branches] No more branches on page {page}"
                    )
                    break

                # Add branch names to the list
                page_branches = [
                    branch.get("name") for branch in branches_data if branch.get("name")
                ]
                all_branches.extend(page_branches)
                logger.info(
                    f"[_get_github_branches] Page {page}: found {len(page_branches)} branches"
                )

                # If the number of branches returned is less than per_page => no more pages
                if len(branches_data) < per_page:
                    break

                # Otherwise -> go to the next page
                page += 1

            logger.info(
                f"[_get_github_branches] Success - found {len(all_branches)} branches total"
            )
            return all_branches

    except Exception as e:
        error_message = (
            f"[_get_github_branches] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _get_gitbucket_branches(
    repository_url: str,
    user_name: str,
    password: Optional[str],
) -> List[str]:
    """Get list of branches from GitBucket repository"""
    logger.info(f"[_get_gitbucket_branches] Start - repository_url={repository_url}")

    if not user_name or not password:
        logger.warning("[_get_gitbucket_branches] Missing authentication credentials")
        raise_http_error(
            status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
        )

    try:
        # Extract protocol, host, and repository path from URL
        url_pattern = r"(https?://)([^/]+)/(.+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[_get_gitbucket_branches] Invalid GitBucket URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        protocol = match.group(1)  # http:// or https://
        host = match.group(2)
        repo_path = match.group(3)

        # Remove /git/ prefix if present
        if repo_path.startswith("git/"):
            repo_path = repo_path[4:]

        # Prepare authentication
        auth = (user_name, password)

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            # Get list of branches
            branches_url = f"{protocol}{host}/api/v3/repos/{repo_path}/branches"
            branches_response = await client.get(branches_url, auth=auth)

            handle_git_api_error(
                branches_response, repository_url, "_get_gitbucket_branches"
            )

            branches_data = branches_response.json()
            branch_names = [
                branch.get("name") for branch in branches_data if branch.get("name")
            ]

            logger.info(
                f"[_get_gitbucket_branches] Success - found {len(branch_names)} branches"
            )
            return branch_names

    except Exception as e:
        error_message = (
            f"[_get_gitbucket_branches] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def clone_repository(
    clone_url: str, branch_name: str, temp_dir: str, depth: Optional[int] = None
) -> Repo:
    """
    Clone Git repository to temporary directory

    Args:
        clone_url: Git repository clone URL (with credentials if needed)
        branch_name: Branch name to clone
        temp_dir: Temporary directory path to clone into
        depth: Optional depth limit for shallow clone. If None, clones full history

    Returns:
        GitPython Repo object

    Raises:
        Exception: If clone fails
    """
    logger.info(f"[clone_repository] Start - branch_name={branch_name}, depth={depth}")

    try:
        if depth is not None:
            # Shallow clone with depth limit
            repo = Repo.clone_from(
                clone_url, temp_dir, branch=branch_name, depth=depth, single_branch=True
            )
        else:
            # Full clone without depth limit to get all commits history
            repo = Repo.clone_from(
                clone_url, temp_dir, branch=branch_name, single_branch=True
            )
        logger.info("[clone_repository] Repository cloned successfully")
        return repo
    except Exception as e:
        error_message = f"[clone_repository] Error cloning repository: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def get_all_commit_ids(repo: Any, branch_name: str) -> List[Dict[str, str]]:
    """
    Get all commit IDs from a branch with timestamps

    Args:
        repo: GitPython Repo object
        branch_name: Branch name

    Returns:
        List of commit objects [{"commit_id": "hexsha", "time": "ISO8601"}] from newest to oldest
    """
    logger.info(f"[get_all_commit_ids] Start - branch_name={branch_name}")

    try:
        # Use git log to get all commits with hash and timestamp
        # Format: %H|%cI (Hash|CommitDate-ISO8601)
        commit_log = repo.git.log(branch_name, "--format=%H|%cI").strip()

        if commit_log:
            commits = []
            for line in commit_log.split("\n"):
                if line.strip():
                    parts = line.strip().split("|")
                    if len(parts) >= 2:
                        commits.append({"commit_id": parts[0], "time": parts[1]})
                    else:
                        # Fallback if format is weird (shouldn't happen with %H|%cI)
                        commits.append({"commit_id": parts[0], "time": ""})

            logger.info(
                f"[get_all_commit_ids] Success via git log - found {len(commits)} commits"
            )
            return commits

        # Fallback: try iter_commits
        logger.warning(
            "[get_all_commit_ids] Git log returned empty, trying iter_commits"
        )
        commits = []
        for commit in repo.iter_commits(branch_name):
            try:
                # Convert datetime to ISO format
                commit_time = commit.committed_datetime.isoformat()
                commits.append({"commit_id": commit.hexsha, "time": commit_time})
            except Exception:
                commits.append({"commit_id": commit.hexsha, "time": ""})

        if commits:
            logger.info(
                f"[get_all_commit_ids] Success via iter_commits - found {len(commits)} commits"
            )
            return commits
        else:
            # Last fallback: return HEAD commit
            head_commit = repo.head.commit.hexsha
            try:
                head_time = repo.head.commit.committed_datetime.isoformat()
            except:
                head_time = ""

            logger.warning(
                f"[get_all_commit_ids] No commits found, using HEAD commit as fallback: {head_commit}"
            )
            return [{"commit_id": head_commit, "time": head_time}]

    except Exception as e:
        error_message = f"[get_all_commit_ids] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        # Fallback: try iter_commits
        try:
            logger.warning("[get_all_commit_ids] Trying iter_commits as fallback")
            commits = []
            for commit in repo.iter_commits(branch_name):
                try:
                    commit_time = commit.committed_datetime.isoformat()
                    commits.append({"commit_id": commit.hexsha, "time": commit_time})
                except:
                    commits.append({"commit_id": commit.hexsha, "time": ""})

            if commits:
                logger.info(
                    f"[get_all_commit_ids] Success via iter_commits fallback - found {len(commits)} commits"
                )
                return commits
        except Exception as e:
            error_message = (
                f"[get_all_commit_ids] iter_commits fallback also failed: {e}"
            )
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

        # Last fallback: return at least HEAD commit
        try:
            head_commit = repo.head.commit.hexsha
            try:
                head_time = repo.head.commit.committed_datetime.isoformat()
            except:
                head_time = ""

            logger.warning(
                f"[get_all_commit_ids] Using HEAD commit as last fallback: {head_commit}"
            )
            return [{"commit_id": head_commit, "time": head_time}]
        except Exception as e:
            error_message = f"[get_all_commit_ids] All fallbacks failed: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return []


def normalize_directory_paths(
    directory_paths: Dict[str, str],
) -> Dict[str, str]:
    """
    Normalize directory paths when src is "/" (root)
    NOTE: When src is "/", directory paths from project already contain full path (e.g., "Docifycode/RD"),
    so we don't need to add DOCIFYCODE_DIRECTORY_NAME prefix anymore.

    Args:
        directory_paths: Dictionary of directory paths

    Returns:
        Normalized directory paths dictionary (unchanged - use paths from project as-is)
    """
    logger.info("[normalize_directory_paths] Start")

    try:
        if not directory_paths:
            return directory_paths

        # When cloning, directory paths from project are used as-is
        # No need to add DOCIFYCODE_DIRECTORY_NAME prefix when src is "/"
        # because project directory already contains full path (e.g., "Docifycode/RD")
        normalized_paths = directory_paths.copy()

        logger.info(
            "[normalize_directory_paths] Success - using paths from project as-is"
        )
        return normalized_paths

    except Exception as e:
        error_message = f"[normalize_directory_paths] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return directory_paths


def normalize_target_directory(
    target_directory: str, src_path: Optional[str] = None
) -> str:
    """
    Normalize target_directory when src is "/" (root)
    NOTE: When src is "/", directory paths from project already contain full path (e.g., "Docifycode/RD"),
    so we don't need to add DOCIFYCODE_DIRECTORY_NAME prefix anymore.

    Args:
        target_directory: Target directory path
        src_path: Source directory path (optional, to check if src is root)

    Returns:
        Normalized target directory path (unchanged - use path from project as-is)
    """
    logger.info(
        f"[normalize_target_directory] Start - target_directory={target_directory}, src_path={src_path}"
    )

    try:
        if not target_directory:
            return target_directory

        # When cloning, directory paths from project are used as-is
        # No need to add DOCIFYCODE_DIRECTORY_NAME prefix when src is "/"
        # because project directory already contains full path (e.g., "Docifycode/RD")
        logger.info(
            "[normalize_target_directory] Success - using path from project as-is"
        )
        return target_directory

    except Exception as e:
        error_message = f"[normalize_target_directory] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return target_directory


def github_files_exist(
    repository_url: str,
    branch_name: str,
    file_paths: List[str],
    user_name: Optional[str],
    token_password: str,
    *,
    depth: int = 1,
    all_must_exist: bool = True,  # True:
) -> bool:
    """
    Check if one or more files exist in a Git repository by cloning it locally.

    """
    # from app.services.git_services.git_clone import _prepare_clone_url # Removed import

    temp_dir = None
    try:
        clone_url = _prepare_clone_url(
            repository_url=repository_url,
            user_name=user_name,
            token_password=token_password,
        )
        temp_dir = tempfile.mkdtemp(prefix="git_clone_")
        logger.info(f"[github_files_exist] Temp dir: {temp_dir}")

        # Clone repository (shallow)
        clone_repository(
            clone_url=clone_url,
            branch_name=branch_name,
            temp_dir=temp_dir,
            depth=depth,
        )

        # Check file existence
        results = []
        for file_path in file_paths:
            full_path = Path(temp_dir) / file_path
            exists = full_path.is_file()
            logger.info(f"[github_files_exist] file={file_path}, exists={exists}")
            results.append(exists)

        return all(results) if all_must_exist else any(results)

    except Exception as e:
        logger.exception(f"[github_files_exist] Failed to check files: {file_paths}")
        return False

    finally:
        if temp_dir and Path(temp_dir).exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"[github_files_exist] Cleaned temp dir: {temp_dir}")


def cleanup_temp_dir(temp_dir: str, repo=None, retries: int = 7, delay: float = 0.5):
    """
    Safely cleanup a temporary directory, closing GitPython repo if needed.
    Handles Windows locked files.

    Args:
        temp_dir (str): Path to temporary directory
        repo (Optional[Repo]): GitPython Repo object to close before cleanup
        retries (int): Number of retry attempts
        delay (float): Delay in seconds between retries
    """
    if not temp_dir or not os.path.exists(temp_dir):
        return

    # Close repo
    if repo is not None:
        try:
            repo.close()  # GitPython >=3.1
        except AttributeError:
            del repo
            gc.collect()
        except Exception as e:
            logger.warning(f"[cleanup_temp_dir] Failed to close repo: {e}")

    # Change read-only files to writable (Windows)
    def onerror(func, path, exc_info):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception as e:
            logger.warning(f"[cleanup_temp_dir] Failed to remove {path}: {e}")

    # Retry delete
    for attempt in range(retries):
        try:
            shutil.rmtree(temp_dir, onerror=onerror)
            logger.info(
                f"[cleanup_temp_dir] Successfully cleaned temp directory: {temp_dir}"
            )
            return
        except PermissionError:
            logger.debug(
                f"[cleanup_temp_dir] PermissionError, retry {attempt+1}/{retries}"
            )
            time.sleep(delay)
        except Exception as e:
            logger.warning(
                f"[cleanup_temp_dir] Unexpected error deleting temp directory: {e}"
            )
            break

    logger.warning(
        f"[cleanup_temp_dir] Failed to cleanup temp directory after {retries} retries: {temp_dir}"
    )
