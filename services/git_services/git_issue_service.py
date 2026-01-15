"""
Git Issue Service
Handles creating issues on GitHub and GitBucket
"""

import logging
import re
import httpx
from typing import Optional, Dict, Any
from fastapi import status

from app.utils.constants import GitType
from app.utils.http_helpers import raise_http_error
from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


async def create_github_issue(
    repository_url: str,
    title: str,
    body: str,
    user_name: str,
    token: str,
    assignee: Optional[str] = None,
    labels: Optional[list] = None,
) -> Dict[str, Any]:
    """Create an issue on GitHub"""
    logger.info(f"[create_github_issue] Start - repository_url={repository_url}")

    if not repository_url or not title or not user_name or not token:
        logger.warning("[create_github_issue] Missing required parameters")
        raise_http_error(
            status.HTTP_400_BAD_REQUEST, error_key="GIT_MISSING_PARAMETERS"
        )

    try:
        # Extract owner and repo from URL
        url_pattern = r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[create_github_issue] Invalid GitHub URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        owner = match.group(1)
        repo = match.group(2)

        # Prepare headers
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "DocifyCode",
            "Authorization": f"token {token}",
        }

        # Prepare issue data
        issue_data = {
            "title": title,
            "body": body,
        }

        if assignee:
            issue_data["assignees"] = [assignee]

        if labels:
            issue_data["labels"] = labels

        async with httpx.AsyncClient(timeout=10.0) as client:
            # Create issue
            issue_url = f"https://api.github.com/repos/{owner}/{repo}/issues"
            response = await client.post(issue_url, headers=headers, json=issue_data)

            if response.status_code == 201:
                issue_result = response.json()
                logger.info(
                    f"[create_github_issue] Success - issue_number={issue_result.get('number')}"
                )
                return issue_result
            elif response.status_code == 401:
                logger.warning("[create_github_issue] Authentication failed")
                raise_http_error(
                    status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
                )
            elif response.status_code == 403:
                logger.warning("[create_github_issue] Access denied")
                raise_http_error(
                    status.HTTP_403_FORBIDDEN, error_key="GIT_ACCESS_DENIED"
                )
            elif response.status_code == 404:
                logger.warning("[create_github_issue] Repository not found")
                raise_http_error(
                    status.HTTP_404_NOT_FOUND, error_key="GIT_REPOSITORY_NOT_FOUND"
                )
            else:
                error_msg = response.text
                logger.error(
                    f"[create_github_issue] Unexpected error - status_code={response.status_code}, error={error_msg}"
                )
                raise_http_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_API_ERROR"
                )

    except Exception as e:
        error_message = (
            f"[create_github_issue] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


async def create_gitbucket_issue(
    repository_url: str,
    title: str,
    body: str,
    user_name: str,
    password: str,
    assignee: Optional[str] = None,
    labels: Optional[list] = None,
) -> Dict[str, Any]:
    """Create an issue on GitBucket, create labels if they don't exist"""
    logger.info(f"[create_gitbucket_issue] Start - repository_url={repository_url}")

    if not repository_url or not title or not user_name or not password:
        logger.warning("[create_gitbucket_issue] Missing required parameters")
        raise_http_error(
            status.HTTP_400_BAD_REQUEST, error_key="GIT_MISSING_PARAMETERS"
        )

    try:
        # Extract protocol, host, and repository path from URL
        url_pattern = r"(https?://)([^/]+)/(.+?)(?:\.git)?/?$"
        match = re.search(url_pattern, repository_url)
        if not match:
            logger.warning(
                f"[create_gitbucket_issue] Invalid GitBucket URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        protocol = match.group(1)
        host = match.group(2)
        repo_path = match.group(3)

        # Remove /git/ prefix if present
        if repo_path.startswith("git/"):
            repo_path = repo_path[4:]

        auth = (user_name, password)

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            # ---Check existing labels ---
            existing_labels_resp = await client.get(
                f"{protocol}{host}/api/v3/repos/{repo_path}/labels", auth=auth
            )
            if existing_labels_resp.status_code != 200:
                logger.warning(
                    f"[create_gitbucket_issue] Failed to fetch labels, status_code={existing_labels_resp.status_code}"
                )
                raise_http_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    error_key="GIT_FETCH_LABELS_FAILED",
                )

            existing_labels = [l["name"] for l in existing_labels_resp.json()]

            # ---Create missing labels ---
            for label in labels or []:
                if label not in existing_labels:
                    create_label_resp = await client.post(
                        f"{protocol}{host}/api/v3/repos/{repo_path}/labels",
                        auth=auth,
                        json={"name": label, "color": "f29513"},  # color custom
                    )
                    if create_label_resp.status_code not in (200, 201):
                        logger.warning(
                            f"[create_gitbucket_issue] Failed to create label '{label}' - status_code={create_label_resp.status_code}"
                        )

            # --- Create the issue ---
            issue_data = {
                "title": title,
                "body": body,
                "assignees": [assignee] if assignee else [],
                "labels": labels or [],
            }

            issue_url = f"{protocol}{host}/api/v3/repos/{repo_path}/issues"
            response = await client.post(issue_url, auth=auth, json=issue_data)

            if response.status_code == 201:
                issue_result = response.json()
                logger.info(
                    f"[create_gitbucket_issue] Success - issue_number={issue_result.get('number')}"
                )
                return issue_result
            elif response.status_code == 401:
                logger.warning("[create_gitbucket_issue] Authentication failed")
                raise_http_error(
                    status.HTTP_401_UNAUTHORIZED, error_key="GIT_AUTHENTICATION_FAILED"
                )
            elif response.status_code == 403:
                logger.warning("[create_gitbucket_issue] Access denied")
                raise_http_error(
                    status.HTTP_403_FORBIDDEN, error_key="GIT_ACCESS_DENIED"
                )
            elif response.status_code == 404:
                logger.warning("[create_gitbucket_issue] Repository not found")
                raise_http_error(
                    status.HTTP_404_NOT_FOUND, error_key="GIT_REPOSITORY_NOT_FOUND"
                )
            else:
                error_msg = response.text
                logger.error(
                    f"[create_gitbucket_issue] Unexpected error - status_code={response.status_code}, error={error_msg}"
                )
                raise_http_error(
                    status.HTTP_500_INTERNAL_SERVER_ERROR, error_key="GIT_API_ERROR"
                )

    except Exception as e:
        error_message = (
            f"[create_gitbucket_issue] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


async def create_git_issue(
    repository_url: str,
    repo_provider: str,
    title: str,
    body: str,
    user_name: str,
    token_password: str,
    assignee: Optional[str] = None,
    labels: Optional[list] = None,
) -> Optional[Dict[str, Any]]:
    """Create an issue on Git (GitHub or GitBucket)"""
    logger.info(
        f"[create_git_issue] Start - repository_url={repository_url}, repo_provider={repo_provider}"
    )

    if (
        not repository_url
        or not repo_provider
        or not title
        or not user_name
        or not token_password
    ):
        logger.warning("[create_git_issue] Missing required parameters")
        return None

    try:
        if repo_provider.lower() == GitType.GITHUB:
            return await create_github_issue(
                repository_url=repository_url,
                title=title,
                body=body,
                user_name=user_name,
                token=token_password,
                assignee=assignee,
                labels=labels,
            )
        elif repo_provider.lower() == GitType.GITBUCKET:
            return await create_gitbucket_issue(
                repository_url=repository_url,
                title=title,
                body=body,
                user_name=user_name,
                password=token_password,
                assignee=assignee,
                labels=labels,
            )
        else:
            logger.warning(
                f"[create_git_issue] Unsupported repo_provider - repo_provider={repo_provider}"
            )
            return None

    except Exception as e:
        error_message = (
            f"[create_git_issue] Error - repository_url={repository_url}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        # Don't raise - return None so DB save can still succeed
        return None
