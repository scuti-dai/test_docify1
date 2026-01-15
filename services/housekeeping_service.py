"""
Housekeeping service for cleanup operations
"""

import logging
import os
from typing import Dict, Any, List
from datetime import datetime, timedelta
from fastapi import HTTPException, status

from app.core.database import get_database
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import get_current_utc_time
from app.utils.download_utils import DOWNLOADS_DIR, FALLBACK_DOWNLOADS_DIR

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


class HousekeepingService:
    """Service for housekeeping operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(HousekeepingService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not HousekeepingService._initialized:
            self.collections_to_cleanup = [
                # Requested collections
                "user",
                "project",
                "data_management",
                "comment",
                "requirement_document",
                "basic_design",
                "detail_design",
                "issue",
                "source_code",
                "unit_test",
                "usage_stat",
                "activity",
                "log",
                "access_right",
                "local_login",
            ]
            self._db = None
            HousekeepingService._initialized = True
            logger.info("[HousekeepingService] Singleton instance initialized")

    async def _get_database(self):
        """Get database instance (lazy initialization)"""
        if self._db is None:
            logger.debug(
                "[HousekeepingService._get_database] Initializing database connection"
            )
            self._db = await get_database()
        return self._db

    async def cleanup_deleted_records(self, days: int) -> Dict[str, Any]:
        """
        Clean up records that have been marked as deleted for more than specified days

        Args:
            days: Number of days to look back for deleted records

        Returns:
            Dictionary with cleanup results
        """
        logger.info(f"[cleanup_deleted_records] Start - days={days}")

        if days <= 0:
            logger.warning("[cleanup_deleted_records] Invalid days parameter")
            raise ValueError("Days must be greater than 0")

        try:
            db = await self._get_database()
            current_time = datetime.utcnow()
            cutoff_time = current_time - timedelta(days=days)
            cutoff_time_str = cutoff_time.isoformat()

            total_deleted = 0
            collection_results = {}

            for collection_name in self.collections_to_cleanup:
                try:
                    collection = db.get_collection(collection_name)

                    # Special handling for activity and log collections - check created_at instead of deleted_at
                    if collection_name in ["activity", "log"]:
                        query = {"created_at": {"$lt": cutoff_time_str}}
                        logger.debug(
                            f"[cleanup_deleted_records] Using created_at for {collection_name}"
                        )
                    else:
                        # Find records with deleted_at before cutoff time
                        query = {"deleted_at": {"$lt": cutoff_time_str}}
                        logger.debug(
                            f"[cleanup_deleted_records] Using deleted_at for {collection_name}"
                        )

                    delete_result = await collection.delete_many(query)
                    deleted_count = delete_result.deleted_count

                    collection_results[collection_name] = {
                        "deleted": deleted_count,
                    }
                    total_deleted += deleted_count

                    if deleted_count > 0:
                        logger.info(
                            f"[cleanup_deleted_records] Cleaned {deleted_count} records from {collection_name}"
                        )
                    else:
                        logger.debug(
                            f"[cleanup_deleted_records] No records to clean from {collection_name}"
                        )

                except Exception as e:
                    error_message = f"[cleanup_deleted_records] Error cleaning {collection_name}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

                    collection_results[collection_name] = {
                        "found": 0,
                        "deleted": 0,
                        "error": str(e),
                    }

            # Clean up old files in downloads folder
            download_cleanup_result = await self._cleanup_download_files(days)

            result = {
                "total_deleted": total_deleted,
                "cutoff_date": cutoff_time_str,
                "days_processed": days,
                "collections": collection_results,
                "download_files": download_cleanup_result,
            }

            logger.info(
                f"[cleanup_deleted_records] Completed - total_deleted={total_deleted}, download_files_deleted={download_cleanup_result.get('deleted_count', 0)}"
            )
            return result

        except Exception as e:
            error_message = f"[cleanup_deleted_records] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _cleanup_download_files(self, days: int) -> Dict[str, Any]:
        """Clean up old files in downloads folder"""
        logger.info(f"[_cleanup_download_files] Start - days={days}")

        try:
            current_time = datetime.utcnow()
            cutoff_time = current_time - timedelta(days=days)
            cutoff_timestamp = cutoff_time.timestamp()

            deleted_count = 0
            error_count = 0
            directories_processed = []

            # Process both download directories
            for download_dir in [DOWNLOADS_DIR, FALLBACK_DOWNLOADS_DIR]:
                if not os.path.exists(download_dir):
                    logger.info(
                        f"[_cleanup_download_files] Directory does not exist: {download_dir}"
                    )
                    continue

                try:
                    dir_deleted = 0
                    for root, dirs, files in os.walk(download_dir):
                        for file_name in files:
                            file_path = os.path.join(root, file_name)
                            try:
                                # Get file modification time
                                file_mtime = os.path.getmtime(file_path)

                                # Delete if file is older than cutoff time
                                if file_mtime < cutoff_timestamp:
                                    os.remove(file_path)
                                    dir_deleted += 1
                                    deleted_count += 1
                                    logger.debug(
                                        f"[_cleanup_download_files] Deleted file: {file_path}"
                                    )
                            except FileNotFoundError:
                                # File already deleted, skip
                                continue
                            except Exception as e:
                                error_message = f"[_cleanup_download_files] Error deleting file {file_path}: {e}"
                                logger.warning(error_message)
                                save_exception_log_sync(
                                    e, error_message, __name__, level=LogLevel.WARNING
                                )

                                error_count += 1

                    directories_processed.append(
                        {"directory": download_dir, "deleted": dir_deleted}
                    )
                    if dir_deleted > 0:
                        logger.info(
                            f"[_cleanup_download_files] Cleaned {dir_deleted} files from {download_dir}"
                        )

                except Exception as e:
                    error_message = f"[_cleanup_download_files] Error processing directory {download_dir}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

                    error_count += 1

            result = {
                "deleted_count": deleted_count,
                "error_count": error_count,
                "directories_processed": directories_processed,
            }

            logger.info(
                f"[_cleanup_download_files] Success - deleted_count={deleted_count}, error_count={error_count}"
            )
            return result

        except Exception as e:
            error_message = f"[_cleanup_download_files] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            return {"deleted_count": 0, "error": str(e)}


# Create service instance
housekeeping_service = HousekeepingService()
