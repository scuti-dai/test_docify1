"""
User service for managing user data in MongoDB
"""

import logging
from uuid6 import uuid7
from typing import List, Optional
from cec_docifycode_common.models.user import User
from app.utils.helpers import get_current_utc_time
from pymongo.errors import DuplicateKeyError

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


class UserService:
    """Service for user operations - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(UserService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not UserService._initialized:
            UserService._initialized = True
            logger.info("[UserService] Singleton instance initialized")
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[UserService] Repository updated via set_repository")

    async def create_user_from_local_login(self, local_login_data: dict) -> bool:
        """Create user from local login data"""
        logger.info(
            f"[create_user_from_local_login] Creating user for: {local_login_data.get('login_id')}"
        )
        try:
            user_id = str(uuid7())
            current_time = get_current_utc_time()

            user_doc = User(
                user_id=user_id,
                user_name=local_login_data["user_name"],
                email=local_login_data["login_id"],
                group=[],
                personal_projects=[],
                created_at=current_time,
                updated_at=current_time,
                deleted_at=None,
            )

            try:
                is_inserted = await self.repository.insert_one(user_doc.to_dict())
                if is_inserted:
                    logger.info(
                        f"[create_user_from_local_login] User created successfully: {user_id}"
                    )
                    return True

                # Insert returned falsy (repository may return False on failure).
                # Fallback: check if user now exists (concurrent insert by another process).
                existing = await self.get_user_by_email(local_login_data["login_id"])
                if existing:
                    logger.info(
                        f"[create_user_from_local_login] User already exists after insert attempt: {local_login_data.get('login_id')}"
                    )
                    return True

                logger.error(
                    f"[create_user_from_local_login] Failed to create user: {user_id}"
                )
                return False

            except DuplicateKeyError:
                # Another process inserted the same email concurrently; treat as success
                logger.info(
                    f"[create_user_from_local_login] Duplicate detected, user exists: {local_login_data.get('login_id')}"
                )
                return True

        except Exception as e:
            error_message = f"[create_user_from_local_login] Error creating user: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_user_by_email(self, email: str) -> Optional[dict]:
        """Get user by email"""
        logger.info(f"[get_user_by_email] Fetching user for email: {email}")
        try:
            filter_dict = {"email": email, "deleted_at": None}
            doc = await self.repository.find_one(filter_dict)

            if doc:
                logger.info(f"[get_user_by_email] Found user for email: {email}")
                return doc
            else:
                logger.info(f"[get_user_by_email] No user found for email: {email}")
                return None

        except Exception as e:
            error_message = f"[get_user_by_email] Error fetching user: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_user_by_id(self, user_id: str) -> Optional[dict]:
        """Get user by user_id"""
        # logger.info(f"[get_user_by_id] Fetching user for ID: {user_id}")
        try:
            filter_dict = {"user_id": user_id, "deleted_at": None}
            doc = await self.repository.find_one(filter_dict)

            if doc:
                # logger.info(f"[get_user_by_id] Found user for ID: {user_id}")
                return doc
            else:
                logger.info(f"[get_user_by_id] No user found for ID: {user_id}")
                return None

        except Exception as e:
            error_message = f"[get_user_by_id] Error fetching user: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def update_user(self, user_id: str, update_data: dict) -> bool:
        """Update user data"""
        logger.info(f"[update_user] Updating user: {user_id}")
        try:
            # Add updated_at timestamp
            update_data["updated_at"] = get_current_utc_time()

            filter_dict = {"user_id": user_id, "deleted_at": None}
            modified_count = await self.repository.update_one(
                filter_dict=filter_dict, update_data=update_data
            )

            if modified_count > 0:
                logger.info(f"[update_user] User updated successfully: {user_id}")
                return True
            else:
                logger.warning(f"[update_user] No user updated: {user_id}")
                return False

        except Exception as e:
            error_message = f"[update_user] Error updating user: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def add_project_to_user(self, user_id: str, project_id: str) -> None:
        """Add project_id to user's personal_projects list"""
        logger.info(
            f"[add_project_to_user] Start - user_id={user_id}, project_id={project_id}"
        )

        if not user_id or not project_id:
            logger.warning("[add_project_to_user] Missing required input")
            return

        try:
            # Prepare query filter
            query_filter = {"user_id": user_id, "deleted_at": None}

            # Prepare update operations
            update_operations = {
                "$addToSet": {"personal_projects": project_id},
                "$set": {"updated_at": get_current_utc_time()},
            }

            # Add project_id to personal_projects array using $addToSet
            modified_count = await self.repository.update_one_with_operations(
                filter_dict=query_filter, update_operations=update_operations
            )

            if modified_count > 0:
                logger.info(
                    f"[add_project_to_user] Success - Added project_id to user's personal_projects"
                )
            else:
                logger.warning(
                    f"[add_project_to_user] User not found or project_id already exists - user_id={user_id}"
                )

        except Exception as e:
            error_message = f"[add_project_to_user] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            # Don't raise, just log - project creation should still succeed

    async def remove_project_from_user(self, user_id: str, project_id: str) -> None:
        """Remove project_id from user's personal_projects list"""

        if not user_id or not project_id:
            logger.warning("[remove_project_from_user] Missing required input")
            return

        try:
            # Prepare query filter
            query_filter = {"user_id": user_id, "deleted_at": None}

            # Prepare update operations
            update_operations = {
                "$pull": {"personal_projects": project_id},
                "$set": {"updated_at": get_current_utc_time()},
            }

            # Remove project_id from personal_projects array using $pull
            await self.repository.update_one_with_operations(
                filter_dict=query_filter, update_operations=update_operations
            )

        except Exception as e:
            error_message = f"[remove_project_from_user] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            # Don't raise, just log - project update should still succeed


# Create service instance
user_service = UserService()
