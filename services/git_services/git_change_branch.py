"""
Git change branch service
"""

import logging
import os
import tempfile
import shutil
from typing import Optional, Dict, Any
from git import Repo
from fastapi import status, HTTPException

from app.services.git_services.git_validate import validate_git_url
from app.services.git_services.git_clone import clone_code_from_git, _prepare_clone_url
from app.services.git_services.git_service import (
    cleanup_temp_dir,
    get_all_commit_ids,
    clone_repository,
)
from app.core.database import get_database, app_db
from cec_docifycode_common.models.comment import Comment
from cec_docifycode_common.models.issue import Issue
from app.utils.constants import SyncStatus
from app.utils.helpers import get_current_utc_time
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


async def check_local_changes(project_id: str) -> bool:
    """
    Check if there are local changes in DocifyCode that haven't been synced to Git

    Args:
        project_id: Project ID

    Returns:
        True if there are local changes, False otherwise
    """
    logger.info(f"[check_local_changes] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[check_local_changes] Missing project_id")
        return False

    try:
        # Sync statuses that indicate local changes
        local_change_statuses = [
            SyncStatus.PUSH,
            SyncStatus.PULL_PUSH,
            SyncStatus.DELETE_PUSH,
        ]

        # Check requirement_document collection
        requirement_repo = app_db.requirement_document_repository
        rd_collection = await requirement_repo._resolve_collection()
        rd_query = {
            "project_id": project_id,
            "$or": [
                {"sync_status": {"$in": local_change_statuses}},
                {"sync_status": {"$exists": False}},
                {"sync_status": None},
            ],
            "deleted_at": None,
        }
        rd_count = await rd_collection.count_documents(rd_query)
        if rd_count > 0:
            logger.info(
                f"[check_local_changes] Found local changes in requirement_document - count={rd_count}"
            )
            return True

        # Check basic_design collection
        bd_collection = await app_db.basic_design_repository._resolve_collection()
        bd_query = {
            "project_id": project_id,
            "$or": [
                {"sync_status": {"$in": local_change_statuses}},
                {"sync_status": {"$exists": False}},
                {"sync_status": None},
            ],
            "deleted_at": None,
        }
        bd_count = await bd_collection.count_documents(bd_query)
        if bd_count > 0:
            logger.info(
                f"[check_local_changes] Found local changes in basic_design - count={bd_count}"
            )
            return True

        # Check detail_design collection
        dd_collection = await app_db.detail_design_repository._resolve_collection()
        dd_query = {
            "project_id": project_id,
            "$or": [
                {"sync_status": {"$in": local_change_statuses}},
                {"sync_status": {"$exists": False}},
                {"sync_status": None},
            ],
            "deleted_at": None,
        }
        dd_count = await dd_collection.count_documents(dd_query)
        if dd_count > 0:
            logger.info(
                f"[check_local_changes] Found local changes in detail_design - count={dd_count}"
            )
            return True

        # Check source_code collection
        src_collection = await app_db.source_code_repository._resolve_collection()
        src_query = {
            "project_id": project_id,
            "$or": [
                {"sync_status": {"$in": local_change_statuses}},
                {"sync_status": {"$exists": False}},
                {"sync_status": None},
            ],
            "deleted_at": None,
        }
        src_count = await src_collection.count_documents(src_query)
        if src_count > 0:
            logger.info(
                f"[check_local_changes] Found local changes in source_code - count={src_count}"
            )
            return True

        # Check unit_test collection (both UTD and UTC)
        ut_collection = await app_db.unit_test_repository._resolve_collection()
        ut_query = {
            "project_id": project_id,
            "$or": [
                {"sync_status": {"$in": local_change_statuses}},
                {"sync_status": {"$exists": False}},
                {"sync_status": None},
            ],
            "deleted_at": None,
        }
        ut_count = await ut_collection.count_documents(ut_query)
        if ut_count > 0:
            logger.info(
                f"[check_local_changes] Found local changes in unit_test - count={ut_count}"
            )
            return True

        # Check data_management collection
        dm_collection = (
            await app_db.source_code_repository._resolve_data_management_collection()
        )
        # Note: data_management doesn't have sync_status, but we check if it exists
        # If data_management exists, it means there's folder structure that might need to be preserved
        # However, for branch change, we should delete and recreate it
        # So we don't need to check data_management for local changes

        logger.info("[check_local_changes] No local changes found")
        return False

    except Exception as e:
        error_message = f"[check_local_changes] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        # If we can't check, assume there are changes to be safe
        return True


async def soft_delete_project_data(project_id: str) -> None:
    """
    Soft delete all data related to a project

    Args:
        project_id: Project ID
    """
    logger.info(f"[soft_delete_project_data] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[soft_delete_project_data] Missing project_id")
        return

    try:
        current_time = get_current_utc_time()
        db = await get_database()

        # Soft delete requirement_document
        requirement_repo = app_db.requirement_document_repository
        rd_collection = await requirement_repo._resolve_collection()
        rd_query = {"project_id": project_id, "deleted_at": None}
        rd_update = {"$set": {"deleted_at": current_time}}
        await rd_collection.update_many(rd_query, rd_update)
        logger.info("[soft_delete_project_data] Soft deleted requirement_document")

        # Soft delete basic_design
        bd_collection = await app_db.basic_design_repository._resolve_collection()
        bd_query = {"project_id": project_id, "deleted_at": None}
        bd_update = {"$set": {"deleted_at": current_time}}
        await bd_collection.update_many(bd_query, bd_update)
        logger.info("[soft_delete_project_data] Soft deleted basic_design")

        # Soft delete detail_design (includes detail_design_document)
        dd_collection = await app_db.detail_design_repository._resolve_collection()
        dd_query = {"project_id": project_id, "deleted_at": None}
        dd_update = {"$set": {"deleted_at": current_time}}
        await dd_collection.update_many(dd_query, dd_update)
        logger.info("[soft_delete_project_data] Soft deleted detail_design")

        # Soft delete source_code
        src_collection = await app_db.source_code_repository._resolve_collection()
        src_query = {"project_id": project_id, "deleted_at": None}
        src_update = {"$set": {"deleted_at": current_time}}
        await src_collection.update_many(src_query, src_update)
        logger.info("[soft_delete_project_data] Soft deleted source_code")

        # Soft delete unit_test
        ut_collection = await app_db.unit_test_repository._resolve_collection()
        ut_query = {"project_id": project_id, "deleted_at": None}
        ut_update = {"$set": {"deleted_at": current_time}}
        await ut_collection.update_many(ut_query, ut_update)
        logger.info("[soft_delete_project_data] Soft deleted unit_test")

        # Soft delete data_management
        dm_collection = (
            await app_db.source_code_repository._resolve_data_management_collection()
        )
        dm_query = {"project_id": project_id, "deleted_at": None}
        dm_update = {"$set": {"deleted_at": current_time}}
        await dm_collection.update_many(dm_query, dm_update)
        logger.info("[soft_delete_project_data] Soft deleted data_management")

        # Soft delete comment
        comment_collection = db.get_collection(Comment.Config.collection_name)
        comment_query = {"project_id": project_id, "deleted_at": None}
        comment_update = {"$set": {"deleted_at": current_time}}
        await comment_collection.update_many(comment_query, comment_update)
        logger.info("[soft_delete_project_data] Soft deleted comment")

        # Soft delete issue
        issue_collection = db.get_collection(Issue.Config.collection_name)
        issue_query = {"project_id": project_id, "deleted_at": None}
        issue_update = {"$set": {"deleted_at": current_time}}
        await issue_collection.update_many(issue_query, issue_update)
        logger.info("[soft_delete_project_data] Soft deleted issue")

        logger.info("[soft_delete_project_data] Success")

    except Exception as e:
        error_message = f"[soft_delete_project_data] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def change_git_branch(
    project_id: str,
    repository_url: str,
    new_branch: str,
    user_name: Optional[str],
    token_password: Optional[str],
    force: bool = False,
    source_code_path: Optional[str] = None,
    programming_language: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Change Git branch for a project

    Args:
        project_id: Project ID
        repository_url: Git repository URL
        new_branch: New branch name to switch to
        user_name: Git username (optional)
        token_password: Git token/password (optional)
        force: If True, skip local changes check and force branch change
        source_code_path: Source code path within repository (optional)
        programming_language: Programming language (optional)

    Returns:
        Dictionary with success status and has_local_changes flag

    Raises:
        HTTPException: If branch change fails
    """
    logger.info(
        f"[change_git_branch] Start - project_id={project_id}, new_branch={new_branch}, force={force}"
    )

    if not project_id or not repository_url or not new_branch:
        logger.warning("[change_git_branch] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    try:
        # Determine Git type
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[change_git_branch] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

        # Check for local changes if force is False
        has_local_changes = False
        if not force:
            logger.info("[change_git_branch] Checking for local changes")
            has_local_changes = await check_local_changes(project_id)

            if has_local_changes:
                logger.info(
                    "[change_git_branch] Local changes detected, returning has_local_changes=true"
                )
                return {
                    "success": True,
                    "has_local_changes": True,
                    "message": "Local changes detected",
                }

        # No local changes or force=True, proceed with branch change
        logger.info(
            "[change_git_branch] No local changes or force=True, proceeding with branch change"
        )

        # Soft delete all existing project data
        logger.info("[change_git_branch] Soft deleting existing project data")
        await soft_delete_project_data(project_id)

        # Clone new branch
        logger.info(
            f"[change_git_branch] Cloning new branch - new_branch={new_branch}, project_id={project_id}"
        )

        # Clone repository temporarily to get commit IDs
        temp_dir = None
        commit_ids = []

        try:
            # Prepare clone URL with credentials
            clone_url = _prepare_clone_url(repository_url, user_name, token_password)

            # Create temporary directory for cloning
            temp_dir = tempfile.mkdtemp()
            logger.info(
                f"[change_git_branch] Created temp directory - temp_dir={temp_dir}"
            )

            # Clone repository to get commit IDs
            repo = clone_repository(clone_url, new_branch, temp_dir, depth=None)

            # Get all commit IDs from new branch
            commit_ids = get_all_commit_ids(repo, new_branch)
            logger.info(
                f"[change_git_branch] Got commit IDs from new branch - count={len(commit_ids)}"
            )
        except Exception as e:
            error_message = f"[change_git_branch] Failed to get commit IDs, will continue with clone: {e}"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

        finally:
            # Clean up temporary directory
            cleanup_temp_dir(temp_dir, repo)

        # Clone and process files
        await clone_code_from_git(
            repository_url=repository_url,
            branch_name=new_branch,
            user_name=user_name,
            token_password=token_password,
            git_type=git_type,
            project_id=project_id,
            source_code_path=source_code_path,
            programming_language=programming_language,
        )

        logger.info(
            f"[change_git_branch] Success - project_id={project_id}, new_branch={new_branch}, commit_count={len(commit_ids)}"
        )
        return {
            "success": True,
            "has_local_changes": False,
            "message": f"Successfully switched to branch '{new_branch}'",
            "commit_ids": commit_ids,
        }

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[change_git_branch] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
