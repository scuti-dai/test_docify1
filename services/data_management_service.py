"""
Data Management Service
Handles data management operations for folders and other data structures
"""

import logging
from typing import Dict, List, Any, Optional
from cec_docifycode_common.models.data_management import DataManagement

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


class DataManagementService:
    """Service for data management operations - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(DataManagementService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not DataManagementService._initialized:
            DataManagementService._initialized = True
            logger.info("[DataManagementService] Singleton instance initialized")
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[DataManagementService] Repository updated via set_repository")

    # #region Public Methods

    async def create_or_update_data_management(
        self,
        project_id: str,
        type: str,
        folders: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Create or update data management document with folder structure

        Args:
            project_id: Project ID
            type: Type (e.g., "source_code")
            folders: List of folder dictionaries with folder_id, folder_name, parent_folder_id

        Returns:
            Dict with created/updated document info

        Raises:
            ValueError: If required parameters are missing
            Exception: If database operation fails
        """
        logger.info(
            f"[create_or_update_data_management] Start - project_id={project_id}, type={type}"
        )

        if not project_id or not type:
            logger.warning(
                "[create_or_update_data_management] Missing required parameters"
            )
            raise ValueError("Missing required parameters: project_id or type")

        try:
            from uuid6 import uuid7
            from app.utils.helpers import get_current_utc_time

            current_time = get_current_utc_time()

            # Check if document exists (not soft deleted)
            existing_doc = await self.repository.find_one(
                {
                    "project_id": project_id,
                    "type": type,
                    "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
                }
            )

            if existing_doc:
                # Update existing document
                logger.info(
                    f"[create_or_update_data_management] Updating existing document - id={existing_doc.get('id') or existing_doc.get('_id')}"
                )

                update_data = {
                    "folders": folders,
                    "updated_at": current_time,
                }

                query_filter = {
                    "project_id": project_id,
                    "type": type,
                    "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
                }

                modified_count = await self.repository.update_one(
                    filter_dict=query_filter, update_data=update_data
                )

                if modified_count > 0:
                    logger.info(
                        f"[create_or_update_data_management] Document updated successfully"
                    )
                    doc_id = existing_doc.get("id") or existing_doc.get("_id")
                    return {
                        "id": doc_id,
                        "project_id": project_id,
                        "type": type,
                        "updated_at": current_time,
                    }
                else:
                    logger.warning(
                        "[create_or_update_data_management] No document was updated"
                    )
                    raise ValueError("Failed to update data management document")
            else:
                # Create new document
                logger.info("[create_or_update_data_management] Creating new document")

                doc_id = str(uuid7())

                data_management_doc = {
                    "id": doc_id,
                    "project_id": project_id,
                    "type": type,
                    "folders": folders,
                    "created_at": current_time,
                    "updated_at": current_time,
                    "deleted_at": None,
                }

                is_inserted = await self.repository.insert_one(data_management_doc)

                if is_inserted:
                    logger.info(
                        f"[create_or_update_data_management] Document created successfully - id={doc_id}"
                    )
                    return {
                        "id": doc_id,
                        "project_id": project_id,
                        "type": type,
                        "created_at": current_time,
                    }
                else:
                    logger.error(
                        "[create_or_update_data_management] Failed to insert document"
                    )
                    raise ValueError("Failed to create data management document")

        except ValueError as ve:
            error_message = f"[create_or_update_data_management] Validation error: {ve}"
            logger.error(error_message)
            save_exception_log_sync(ve, error_message, __name__)

            raise
        except Exception as e:
            error_message = f"[create_or_update_data_management] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # #endregion


# Global service instance
data_management_service = DataManagementService()
