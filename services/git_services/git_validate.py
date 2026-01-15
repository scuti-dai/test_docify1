"""
Git validation functions for repository and branch validation
"""

import logging
import re
import httpx
from typing import Optional, Tuple
from app.services.project_service import ProjectService
from app.utils.constants import GitType
from app.utils.http_helpers import raise_http_error
from fastapi import status

from app.services.logs_service import save_exception_log_sync
from cec_docifycode_common.repositories.source_code_summaries_repository import (
    SourceCodeSummariesRepository,
)
from app.core.database.connection import db_connection

logger = logging.getLogger(__name__)


def handle_git_api_error(
    response: httpx.Response,
    repository_url: str,
    function_name: str,
    branch_name: Optional[str] = None,
    is_ai_programming: bool = False,
) -> None:
    """
    Handle common Git API error responses (404, 403, 401, and other errors)

    Args:
        response: HTTP response from Git API
        repository_url: Repository URL for logging
        function_name: Function name for logging context
        branch_name: Optional branch name for logging context

    Raises:
        HTTPException: Appropriate HTTP error based on status code
    """
    logger.info(f"[handle_git_api_error] Start - function_name={function_name}")

    if response.status_code == 404:
        if branch_name:
            logger.warning(
                f"[{function_name}] Branch not found - branch_name={branch_name}, repository_url={repository_url}"
            )
        else:
            logger.warning(
                f"[{function_name}] Repository not found - repository_url={repository_url}"
            )
        raise_http_error(
            status.HTTP_404_NOT_FOUND,
            error_key="GIT_REPOSITORY_OR_BRANCH_NOT_FOUND",
        )
    elif response.status_code == 403:
        if branch_name:
            logger.warning(
                f"[{function_name}] Access denied to branch - branch_name={branch_name}, repository_url={repository_url}"
            )
        else:
            logger.warning(
                f"[{function_name}] Access denied - repository_url={repository_url}"
            )
        raise_http_error(
            status.HTTP_403_FORBIDDEN,
            error_key=(
                "GIT_REPOSITORY_ACCESS_DENIED"
                if not is_ai_programming
                else "AI_PROGRAMMING_REPOSITORY_ACCESS_DENIED"
            ),
        )
    elif response.status_code == 401:
        if branch_name:
            logger.warning(
                f"[{function_name}] Authentication failed for branch - branch_name={branch_name}, repository_url={repository_url}"
            )
        else:
            logger.warning(
                f"[{function_name}] Authentication failed - repository_url={repository_url}"
            )
        raise_http_error(
            status.HTTP_403_FORBIDDEN,
            error_key=(
                "GIT_AUTHENTICATION_FAILED"
                if not is_ai_programming
                else "AI_PROGRAMMING_AUTHENTICATION_FAILED"
            ),
        )
    elif not response.is_success:
        if branch_name:
            logger.error(
                f"[{function_name}] Unexpected error checking branch - status_code={response.status_code}, branch_name={branch_name}"
            )
        else:
            logger.error(
                f"[{function_name}] Unexpected error - status_code={response.status_code}, repository_url={repository_url}"
            )
        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_key="GIT_SOURCE_CODE_FETCH_FAILED",
        )


async def validate_git_url(git_url: str) -> Optional[GitType]:
    """Validate Git URL and return Git type"""
    logger.info(f"[validate_git_url] Start - git_url={git_url}")

    if not git_url:
        logger.warning("[validate_git_url] Missing git_url")
        return None

    try:
        GITHUB_PATTERN = r"^https://github\.com/[^/]+/[^/]+(?:\.git)?$"
        GITBUCKET_PATTERN = r"\.git$"

        if re.match(GITHUB_PATTERN, git_url):
            logger.info("[validate_git_url] Detected GitHub")
            return GitType.GITHUB
        if re.search(GITBUCKET_PATTERN, git_url) and "github.com" not in git_url:
            logger.info("[validate_git_url] Detected GitBucket")
            return GitType.GITBUCKET

        logger.warning(f"[validate_git_url] Invalid URL format - git_url={git_url}")
        return None

    except Exception as e:
        error_message = f"[validate_git_url] Error - git_url={git_url}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return None


async def validate_github_repository(
    repository_url: str,
    user_name: Optional[str],
    token: Optional[str],
    is_ai_programming: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Validate GitHub repository exists and user has access"""
    logger.info(f"[validate_github_repository] Start - repository_url={repository_url}")

    if not repository_url:
        logger.warning("[validate_github_repository] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Extract owner and repo from URL
        # Format: https://github.com/owner/repo.git or https://github.com/owner/repo
        url_pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[validate_github_repository] Invalid GitHub URL format - repository_url={repository_url}"
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
            # Check if repository exists and user has access
            repo_url = f"https://api.github.com/repos/{owner}/{repo}"
            repo_response = await client.get(repo_url, headers=headers)

            handle_git_api_error(
                response=repo_response,
                repository_url=repository_url,
                function_name="validate_github_repository",
                is_ai_programming=is_ai_programming,
            )

        logger.info(
            f"[validate_github_repository] Success - repository_url={repository_url}"
        )
        return True, None

    except Exception as e:
        error_message = (
            f"[validate_github_repository] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def validate_gitbucket_repository(
    repository_url: str,
    user_name: Optional[str],
    password: Optional[str],
    is_ai_programming: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Validate GitBucket repository exists and user has access"""
    logger.info(
        f"[validate_gitbucket_repository] Start - repository_url={repository_url}"
    )

    if not repository_url:
        logger.warning("[validate_gitbucket_repository] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    if not user_name or not password:
        logger.warning(
            "[validate_gitbucket_repository] Missing credentials for GitBucket"
        )
        raise_http_error(
            status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
        )

    try:
        # Extract protocol, host, and repository path from URL
        # Format: http://gitbucket.example.com/git/owner/repo.git or http://gitbucket.example.com/owner/repo.git
        url_pattern = r"(https?://)([^/]+)/(.+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[validate_gitbucket_repository] Invalid GitBucket URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        protocol = match.group(1)  # http:// or https://
        host = match.group(2)
        repo_path = match.group(3)

        # Remove /git/ prefix if present (GitBucket URLs often have /git/ prefix)
        if repo_path.startswith("git/"):
            repo_path = repo_path[4:]  # Remove "git/" prefix

        # Prepare authentication
        auth = (user_name, password)

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            # Check if repository exists and user has access
            # GitBucket API: GET /api/v3/repos/{owner}/{repo}
            api_url = f"{protocol}{host}/api/v3/repos/{repo_path}"
            repo_response = await client.get(api_url, auth=auth)

            handle_git_api_error(
                response=repo_response,
                repository_url=repository_url,
                function_name="validate_github_repository",
                is_ai_programming=is_ai_programming,
            )

        logger.info(
            f"[validate_gitbucket_repository] Success - repository_url={repository_url}"
        )
        return True, None

    except Exception as e:
        error_message = f"[validate_gitbucket_repository] Error - repository_url={repository_url}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def validate_github_branch(
    repository_url: str,
    branch_name: str,
    user_name: Optional[str],
    token: Optional[str],
) -> bool:
    """Validate GitHub branch exists"""
    logger.info(
        f"[validate_github_branch] Start - repository_url={repository_url}, branch_name={branch_name}"
    )

    if not repository_url or not branch_name:
        logger.warning("[validate_github_branch] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Extract owner and repo from URL
        url_pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[validate_github_branch] Invalid GitHub URL format - repository_url={repository_url}"
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
            # Check if branch exists
            branch_url = (
                f"https://api.github.com/repos/{owner}/{repo}/branches/{branch_name}"
            )
            branch_response = await client.get(branch_url, headers=headers)

            handle_git_api_error(
                branch_response,
                repository_url,
                "validate_github_branch",
                branch_name=branch_name,
            )

        logger.info(
            f"[validate_github_branch] Success - repository_url={repository_url}, branch_name={branch_name}"
        )
        return True

    except Exception as e:
        error_message = (
            f"[validate_github_branch] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def validate_gitbucket_branch(
    repository_url: str,
    branch_name: str,
    user_name: Optional[str],
    password: Optional[str],
) -> bool:
    """Validate GitBucket branch exists"""
    logger.info(
        f"[validate_gitbucket_branch] Start - repository_url={repository_url}, branch_name={branch_name}"
    )

    if not repository_url or not branch_name:
        logger.warning("[validate_gitbucket_branch] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    if not user_name or not password:
        logger.warning("[validate_gitbucket_branch] Missing credentials for GitBucket")
        raise_http_error(
            status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
        )

    try:
        # Extract protocol, host, and repository path from URL
        url_pattern = r"(https?://)([^/]+)/(.+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[validate_gitbucket_branch] Invalid GitBucket URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        protocol = match.group(1)
        host = match.group(2)
        repo_path = match.group(3)

        # Remove /git/ prefix if present
        if repo_path.startswith("git/"):
            repo_path = repo_path[4:]

        # Prepare authentication
        auth = (user_name, password)

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            # Check if branch exists
            branch_url = (
                f"{protocol}{host}/api/v3/repos/{repo_path}/branches/{branch_name}"
            )
            branch_response = await client.get(branch_url, auth=auth)

            handle_git_api_error(
                branch_response,
                repository_url,
                "validate_gitbucket_branch",
                branch_name=branch_name,
            )

        logger.info(
            f"[validate_gitbucket_branch] Success - repository_url={repository_url}, branch_name={branch_name}"
        )
        return True

    except Exception as e:
        error_message = (
            f"[validate_gitbucket_branch] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def validate_git_repository(
    repository_url: str,
    branch_name: str,
    user_name: Optional[str],
    token_password: Optional[str],
    is_ai_programming: bool = False,
) -> bool:
    """Validate Git repository exists and user has access (supports GitHub and GitBucket)"""
    logger.info(f"[validate_git_repository] Start - repository_url={repository_url}")

    if not repository_url:
        logger.warning("[validate_git_repository] Missing repository_url")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Determine Git type
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[validate_git_repository] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        # Validate repository only (branch validation is separate)
        if git_type == GitType.GITHUB:
            await validate_github_repository(
                repository_url, user_name, token_password, is_ai_programming
            )
        elif git_type == GitType.GITBUCKET:
            await validate_gitbucket_repository(
                repository_url, user_name, token_password, is_ai_programming
            )
        else:
            logger.warning(
                f"[validate_git_repository] Unsupported Git type - git_type={git_type}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        logger.info("[validate_git_repository] Success")
        return git_type

    except Exception as e:
        error_message = (
            f"[validate_git_repository] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def validate_git_branch(
    repository_url: str,
    branch_name: str,
    user_name: Optional[str],
    token_password: Optional[str],
) -> bool:
    """Validate Git branch exists (supports GitHub and GitBucket)"""
    logger.info(
        f"[validate_git_branch] Start - repository_url={repository_url}, branch_name={branch_name}"
    )

    if not repository_url or not branch_name:
        logger.warning("[validate_git_branch] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Determine Git type
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[validate_git_branch] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        # Validate branch based on Git type
        if git_type == GitType.GITHUB:
            await validate_github_branch(
                repository_url, branch_name, user_name, token_password
            )
        elif git_type == GitType.GITBUCKET:
            await validate_gitbucket_branch(
                repository_url, branch_name, user_name, token_password
            )
        else:
            logger.warning(
                f"[validate_git_branch] Unsupported Git type - git_type={git_type}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        logger.info("[validate_git_branch] Success")
        return True

    except Exception as e:
        error_message = (
            f"[validate_git_branch] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def check_revision(project_id: str, user_name: str, token_password: str) -> bool:
    """Check if the project has a revision"""
    logger.info(f"[check_revision] Start - project_id={project_id}")
    from app.services.git_services.git_pull import pull_data_from_git

    if not project_id:
        logger.warning("[check_revision] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Get database instance and repository
        db = db_connection.get_instance()
        source_code_summary_repo = SourceCodeSummariesRepository(db)

        code_summary = (
            await source_code_summary_repo.get_source_code_summaries_by_project_id(
                project_id=project_id,
                deleted_at_none=True,
                sort_field="time",
                sort_desc=True,
                single=True,
            )
        )

        revision_number = code_summary.get("revision_number") if code_summary else None
        # Get the project from the database
        project_service = ProjectService()
        project = await project_service.get_project_by_id(project_id)
        setting_item = project.setting_item if project.setting_item else None
        git_info = setting_item.git if setting_item and setting_item.git else None
        directory_info = (
            setting_item.directory if setting_item and setting_item.directory else None
        )
        repository_url = git_info.repository
        branch_name = git_info.branch or "main"
        db_commit_ids = (
            git_info.commit_id if isinstance(git_info.commit_id, list) else []
        )

        # Get programming language
        programming_language = (
            setting_item.language if setting_item and setting_item.language else None
        )

        # Build directory paths dictionary
        directory_paths = {}
        if directory_info:
            directory_paths = {
                "rd": directory_info.rd or "",
                "bd": directory_info.bd or "",
                "pd": directory_info.pd or "",
                "src": directory_info.src or "",
                "utd": directory_info.utd or "",
                "utc": directory_info.utc or "",
            }

        pull_result = await pull_data_from_git(
            project_id=project_id,
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
            db_commit_ids=db_commit_ids,
            directory_paths=directory_paths,
            programming_language=programming_language,
            force_override=True,
        )
        repo_commit_id = pull_result.get("repo_commit_ids", [])[0]
        commit_id = repo_commit_id.get("commit_id")

        if revision_number and commit_id == revision_number:
            return False
        else:
            return True

    except Exception as e:
        error_message = f"[check_revision] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
