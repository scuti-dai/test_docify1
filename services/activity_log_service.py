"""
Activity Log Service for managing user activity logs and error logs
"""

import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid6 import uuid7
from fastapi import status

from app.services.user_service import user_service
from app.utils.helpers import get_current_utc_time
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)

# region Constants

ACTIVITY_TYPE_MESSAGES = {
    "CREATE_PROJECT": "{email} がプロジェクト（ProjectID={project_id}）を作成しました。",
    "UPDATE_PROJECT": "{email} がプロジェクト（ProjectID={project_id}）の情報を更新しました。",
    "DELETE_PROJECT": "{email} がプロジェクト（ProjectID={project_id}）を削除しました。",
    "GENERATE_AUTO_RD": "{email} がプロジェクト（ProjectID={project_id}）の要件定義書（FileID={file_ids}）を自動生成しました。",
    "GENERATE_AUTO_BD": "{email} がプロジェクト（ProjectID={project_id}）の基本設計書（FileID={file_ids}）を自動生成しました。",
    "GENERATE_AUTO_DD": "{email} がプロジェクト（ProjectID={project_id}）の詳細設計書（FileID={file_ids}）を自動生成しました。",
    "GENERATE_AUTO_SOURCE": "{email} がプロジェクト（ProjectID={project_id}）のソースコード（FileID={file_ids}）を自動生成しました。",
    "GENERATE_AUTO_UTD": "{email} がプロジェクト（ProjectID={project_id}）の単体テスト設計書（FileID={file_ids}）を自動生成しました。",
    "GENERATE_AUTO_UTC": "{email} がプロジェクト（ProjectID={project_id}）の単体テストコード（FileID={file_ids}）を自動生成しました。",
    "GIT_PULL": "{email} がプロジェクト（ProjectID={project_id}）で Git Pull を実行しました。",
    "GIT_PUSH": "{email} がプロジェクト（ProjectID={project_id}）で Git Push（FileID={file_ids}）を実行しました。",
    "GIT_CHANGE_BRANCH": "{email} がプロジェクト（ProjectID={project_id}）で Git Branch を変更しました。",
    "DOWNLOAD_FILE": "{email} がプロジェクト（ProjectID={project_id}）のファイル（FileID={file_ids}, 種類={file_type}）をダウンロードしました。",
    "DELETE_FILE": "{email} がプロジェクト（ProjectID={project_id}）のファイル（FileID={file_ids}, 種類={file_type}）を削除しました。",
}

# endregion

# region Public Functions


class ActivityLogService:
    """Service for activity and log operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(ActivityLogService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not ActivityLogService._initialized:
            logger.info("[ActivityLogService.__init__] Start")
            try:
                ActivityLogService._initialized = True
                logger.info("[ActivityLogService] Singleton instance initialized")
            except Exception as e:
                error_message = f"[ActivityLogService.__init__] Error: {e}"
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    async def create_activity_log(
        self,
        project_id: str,
        user_id: str,
        activity_type: str,
        file_ids: Optional[List[str]] = None,
        file_type: Optional[str] = None,
    ) -> bool:
        """Create activity log entry"""
        logger.info(
            f"[create_activity_log] Start - project_id={project_id}, user_id={user_id}, activity_type={activity_type}"
        )

        if not project_id or not user_id or not activity_type:
            logger.warning("[create_activity_log] Missing required input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Get user email
            user_doc = await user_service.get_user_by_id(user_id)
            if not user_doc:
                logger.warning(
                    f"[create_activity_log] User not found - user_id={user_id}"
                )
                raise ValueError("USER_NOT_FOUND")

            email = user_doc.get("email", "")

            # Format message
            description = self._format_activity_message(
                activity_type=activity_type,
                email=email,
                project_id=project_id,
                file_ids=file_ids,
                file_type=file_type,
            )

            activity_id = str(uuid7())
            activity_doc = self._build_activity_document(
                activity_id=activity_id,
                project_id=project_id,
                user_id=user_id,
                activity_type=activity_type,
                description=description,
            )

            logger.info(
                f"[create_activity_log] Document prepared - activity_id={activity_id}"
            )

            is_saved = await self.repository.insert_activity(activity_doc)

            if is_saved:
                logger.info(
                    f"[create_activity_log] Success - activity_id={activity_id}, activity_type={activity_type}"
                )
                return True
            else:
                logger.error("[create_activity_log] Failed to insert activity")
                return False

        except ValueError:
            raise
        except Exception as e:
            error_message = f"[create_activity_log] Error - project_id={project_id}, user_id={user_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def create_error_log(
        self,
        log_type: str,
        level: str,
        detail: Dict[str, Any],
        error_message: Optional[str] = None,
    ) -> bool:
        """Create error log entry"""
        logger.info(f"[create_error_log] Start - type={log_type}, level={level}")

        if not log_type or not level or not detail:
            logger.warning("[create_error_log] Missing required input")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        try:
            # Validate level
            valid_levels = [
                "Emergency",
                "Alert",
                "Critical",
                "Error",
                "Warning",
                "Notice",
                "Informational",
                "Debug",
            ]
            if level not in valid_levels:
                logger.warning(f"[create_error_log] Invalid level: {level}")
                raise ValueError("INVALID_LOG_LEVEL")

            # Add error message to detail if provided
            if error_message:
                detail["error_message"] = error_message

            log_doc = self._build_log_document(
                log_type=log_type,
                level=level,
                detail=detail,
            )

            logger.info(f"[create_error_log] Document prepared - type={log_type}")

            is_saved = await self.repository.insert_log(log_doc)

            if is_saved:
                logger.info(
                    f"[create_error_log] Success - type={log_type}, level={level}"
                )
                return True
            else:
                logger.error("[create_error_log] Failed to insert log")
                return False

        except ValueError:
            raise
        except Exception as e:
            error_message = (
                f"[create_error_log] Error - type={log_type}, level={level}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion

    # region Private Functions

    def _format_activity_message(
        self,
        activity_type: str,
        email: str,
        project_id: str,
        file_ids: Optional[List[str]] = None,
        file_type: Optional[str] = None,
    ) -> str:
        """Format activity message from template"""
        logger.info(f"[_format_activity_message] Start - activity_type={activity_type}")

        if not activity_type or activity_type not in ACTIVITY_TYPE_MESSAGES:
            logger.warning(
                f"[_format_activity_message] Unknown activity_type: {activity_type}"
            )
            raise ValueError("INVALID_ACTIVITY_TYPE")

        try:
            template = ACTIVITY_TYPE_MESSAGES[activity_type]

            # Format file_ids if provided
            file_ids_str = ""
            if file_ids:
                file_ids_str = ",".join(file_ids)

            # Replace placeholders
            message = template.format(
                email=email,
                project_id=project_id,
                file_ids=file_ids_str if file_ids_str else "",
                file_type=file_type or "",
            )

            logger.info(
                f"[_format_activity_message] Success - activity_type={activity_type}"
            )
            return message

        except Exception as e:
            error_message = (
                f"[_format_activity_message] Error - activity_type={activity_type}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_activity_document(
        self,
        activity_id: str,
        project_id: str,
        user_id: str,
        activity_type: str,
        description: str,
    ) -> Dict[str, Any]:
        """Build activity document payload"""
        logger.info(
            f"[_build_activity_document] Start - activity_id={activity_id}, project_id={project_id}"
        )

        if not all([activity_id, project_id, user_id, activity_type, description]):
            logger.warning(
                "[_build_activity_document] Missing required activity fields"
            )
            raise ValueError("INVALID_ACTIVITY_DOCUMENT")

        try:
            current_time = get_current_utc_time()

            activity_doc = {
                "id": activity_id,
                "project_id": project_id,
                "user_id": user_id,
                "activity_type": activity_type,
                "description": description,
                "created_at": current_time,
            }

            logger.info(
                f"[_build_activity_document] Success - activity_id={activity_id}, project_id={project_id}"
            )
            return activity_doc
        except Exception as e:
            error_message = f"[_build_activity_document] Error - activity_id={activity_id}, project_id={project_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_log_document(
        self,
        log_type: str,
        level: str,
        detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build error log document payload"""
        logger.info(f"[_build_log_document] Start - type={log_type}, level={level}")

        if not log_type or not level or not detail:
            logger.warning("[_build_log_document] Missing required log fields")
            raise ValueError("INVALID_LOG_DOCUMENT")

        try:
            current_time = get_current_utc_time()
            current_date = datetime.utcnow().strftime("%Y-%m-%d")

            log_doc = {
                "date": current_date,
                "type": log_type,
                "level": level,
                "detail": detail,
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            logger.info(
                f"[_build_log_document] Success - type={log_type}, level={level}"
            )
            return log_doc
        except Exception as e:
            error_message = (
                f"[_build_log_document] Error - type={log_type}, level={level}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise


# endregion

# Create singleton service instance
activity_log_service = ActivityLogService()
