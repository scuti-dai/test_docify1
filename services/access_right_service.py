"""
Access Right service for business logic
"""

import logging
from typing import List, Optional
from datetime import datetime, timezone
import uuid

from app.utils.helpers import get_current_utc_time
from app.schemas.access_right import AccessGroupCreate, AccessUserCreate
from app.utils.constants import PermissionLevel

from app.services.logs_service import save_exception_log_sync
from cec_docifycode_common.repositories import AccessRightRepository

logger = logging.getLogger(__name__)


class AccessRight:
    """Access Right model for service layer"""

    def __init__(self, **data):
        self.access_right_id = data.get("access_right_id")
        self.group_name = data.get("group_name")
        self.login_id = data.get("login_id")
        self.permissions = data.get("permissions")
        self.created_at = data.get("created_at")
        self.updated_at = data.get("updated_at")
        self.deleted_at = data.get("deleted_at")


class AccessRightService:
    """Service for access right operations - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(AccessRightService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not AccessRightService._initialized:
            AccessRightService._initialized = True
            logger.info("[AccessRightService] Singleton instance initialized")

        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    async def create_access_group(self, data: AccessGroupCreate) -> AccessRight:
        """Create access group"""
        logger.info(f"[create_access_group] Start - group_name={data.group_name}")
        try:
            # Check for duplicates
            filter_query = {
                "group_name": data.group_name,
                "permissions": data.permissions,
                "deleted_at": None,
            }
            existing = await self.repository.find_one(filter_query)
            if existing:
                logger.warning(f"[create_access_group] Duplicate found")
                raise ValueError("Group with this name and permission already exists")

            # Create access right document
            access_right_doc = {
                "access_right_id": str(uuid.uuid4()),
                "group_name": data.group_name,
                "login_id": None,
                "permissions": data.permissions,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deleted_at": None,
            }

            is_inserted = await self.repository.insert_one(access_right_doc)
            logger.info(f"[create_access_group] Success - inserted={is_inserted}")

            return AccessRight(**access_right_doc)
        except Exception as e:
            error_message = f"[create_access_group] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_access_groups(self) -> List[AccessRight]:
        """Get list of access groups"""
        logger.info(f"[get_access_groups] Start")
        try:
            # Get all records with group_name, sorted by created_at descending
            filter_query = {"group_name": {"$ne": None}, "deleted_at": None}
            sort_order = [("created_at", -1)]

            docs = await self.repository.find_many(
                filter_dict=filter_query, sort_order=sort_order
            )

            groups = [AccessRight(**doc) for doc in docs]

            logger.info(f"[get_access_groups] Success - Found {len(groups)} groups")
            return groups
        except Exception as e:
            error_message = f"[get_access_groups] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def delete_access_group(self, group_id: str) -> bool:
        """Delete access group (soft delete)"""
        logger.info(f"[delete_access_group] Start - group_id={group_id}")
        try:
            filter_query = {"access_right_id": group_id, "deleted_at": None}
            update_data = {"deleted_at": get_current_utc_time()}
            modified_count = await self.repository.update_one(
                filter_dict=filter_query, update_data=update_data
            )

            success = modified_count > 0
            logger.info(f"[delete_access_group] Success: {success}")
            return success
        except Exception as e:
            error_message = f"[delete_access_group] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def create_access_user(self, data: AccessUserCreate) -> AccessRight:
        """Create access user"""
        logger.info(f"[create_access_user] Start - login_id={data.login_id}")
        try:
            # Check for duplicates
            filter_query = {
                "login_id": data.login_id,
                "permissions": data.permissions,
                "deleted_at": None,
            }
            existing = await self.repository.find_one(filter_query)
            if existing:
                logger.warning(f"[create_access_user] Duplicate found")
                return None

            # Create access right document
            access_right_doc = {
                "access_right_id": str(uuid.uuid4()),
                "group_name": None,
                "login_id": data.login_id,
                "permissions": data.permissions,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "deleted_at": None,
            }

            is_inserted = await self.repository.insert_one(access_right_doc)
            logger.info(f"[create_access_user] Success - inserted={is_inserted}")

            return AccessRight(**access_right_doc)
        except Exception as e:
            error_message = f"[create_access_user] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_access_users(self) -> List[AccessRight]:
        """Get list of access users"""
        logger.info(f"[get_access_users] Start")
        try:
            # Get all records with login_id, sorted by created_at descending
            filter_query = {"login_id": {"$ne": None}, "deleted_at": None}
            sort_order = [("created_at", -1)]

            docs = await self.repository.find_many(
                filter_dict=filter_query, sort_order=sort_order
            )

            users = [AccessRight(**doc) for doc in docs]

            logger.info(f"[get_access_users] Success - Found {len(users)} users")
            return users
        except Exception as e:
            error_message = f"[get_access_users] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def delete_access_user(self, user_access_id: str) -> bool:
        """Delete access user (soft delete)"""
        logger.info(f"[delete_access_user] Start - user_access_id={user_access_id}")
        try:
            filter_query = {"access_right_id": user_access_id, "deleted_at": None}
            update_data = {"deleted_at": get_current_utc_time()}
            modified_count = await self.repository.update_one(
                filter_dict=filter_query, update_data=update_data
            )

            success = modified_count > 0
            logger.info(f"[delete_access_user] Success: {success}")
            return success
        except Exception as e:
            error_message = f"[delete_access_user] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def check_user_permission(self, email: str) -> Optional[str]:
        """Check user permission according to new flow chart logic"""
        logger.info(f"[check_user_permission] Start - email={email}")
        try:
            # Check if ManageAccess is empty (no records at all)
            empty_filter = {"deleted_at": None}
            total_records = await self.repository.count_documents(empty_filter)

            if total_records == 0:
                logger.info(
                    f"[check_user_permission] ManageAccess is empty - login as user"
                )
                return PermissionLevel.USER

            # Check if user has admin permission
            admin_filter = {
                "login_id": email,
                "permissions": PermissionLevel.ADMIN,
                "deleted_at": None,
            }
            admin_record = await self.repository.find_one(admin_filter)
            if admin_record:
                logger.info(
                    f"[check_user_permission] User has admin permission - login as admin"
                )
                return PermissionLevel.ADMIN

            # Check if user has user permission
            user_filter = {
                "login_id": email,
                "permissions": PermissionLevel.USER,
                "deleted_at": None,
            }
            user_record = await self.repository.find_one(user_filter)
            if user_record:
                logger.info(
                    f"[check_user_permission] User has user permission - login as user"
                )
                return PermissionLevel.USER

            # # Check if there are any admin users in the system
            # admin_count_filter = {
            #     "permissions": PermissionLevel.ADMIN,
            #     "deleted_at": None,
            # }
            # admin_count = await collection.count_documents(admin_count_filter)

            # Check if there are any role user in the system
            user_count_filter = {
                "login_id": {"$nin": [None, ""]},
                "permissions": PermissionLevel.USER,
                "deleted_at": None,
            }
            user_count = await self.repository.count_documents(user_count_filter)
            logger.info(f"[check_user_permission] user_count: {user_count}")
            if user_count == 0:

                return PermissionLevel.USER
            else:
                logger.info(
                    f"[check_user_permission] account has not been set access permission"
                )
                return None

        except Exception as e:
            error_message = f"[check_user_permission] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    @classmethod
    def reset_instance(cls):
        """Reset singleton instance (useful for testing)"""
        cls._instance = None
        cls._initialized = False
        logger.info("[AccessRightService] Singleton instance reset")


# Create global instance
access_right_service = AccessRightService()
