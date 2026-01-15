"""
Local Login Service
Handles local login operations
"""

import logging
from typing import List, Optional
from fastapi import status
from cec_docifycode_common.models.local_login import LocalLogin

from app.utils.helpers import (
    create_password_hash,
    check_password_match,
    get_current_utc_time,
)
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync
from cec_docifycode_common.repositories import LocalLoginRepository

logger = logging.getLogger(__name__)


class LocalLoginService:
    """Service for managing local login data - Singleton pattern with DI support"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(LocalLoginService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not LocalLoginService._initialized:
            LocalLoginService._initialized = True
            logger.info("[LocalLoginService] Singleton instance initialized")

        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository: LocalLoginRepository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[LocalLoginService] Repository updated via set_repository")

    async def create_local_login(self, login_data: dict) -> bool:
        """Create a new local login entry"""
        login_id = login_data.get("login_id")
        user_name = login_data.get("user_name")
        plain_password = login_data.get("password")

        logger.info(f"[create_local_login] Start - login_id={login_id}")

        try:
            # Validate required fields
            if not all([login_id, user_name, plain_password]):
                logger.error("[create_local_login] Missing required fields")
                raise_http_error(status.HTTP_400_BAD_REQUEST)

            # Check if email already exists
            existing_login = await self.get_local_login_by_email(login_id)
            if existing_login:
                logger.warning(
                    f"[create_local_login] Email already exists - login_id={login_id}"
                )
                raise ValueError("EMAIL_EXISTS")

            # Hash password
            logger.info(f"[create_local_login] Plain password: {plain_password}")
            hashed_password = create_password_hash(plain_password)

            # Get current UTC timestamp
            current_time = get_current_utc_time()

            # Create local login document
            local_login_doc = LocalLogin(
                login_id=login_id,
                password=hashed_password,
                user_name=user_name,
                created_at=current_time,
                updated_at=current_time,
                deleted_at=None,
            )

            # Convert to dict
            doc_dict = local_login_doc.to_dict()

            # Insert into database using repository
            is_inserted = await self.repository.insert_one(doc_dict)

            if is_inserted:
                logger.info(f"[create_local_login] Success - login_id={login_id}")
                return True
            else:
                logger.error(f"[create_local_login] Failed - login_id={login_id}")
                return False

        except Exception as e:
            error_message = f"[create_local_login] Error - login_id={login_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_all_local_logins(self, search: Optional[str] = None) -> List[dict]:
        """Get all local login entries with optional search"""
        logger.info(f"[get_all_local_logins] Start - search={search}")

        try:
            # Build base query (not deleted)
            deleted_filter = {
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}]
            }

            # Build search query if provided
            if search:
                search_regex = {"$regex": search, "$options": "i"}
                search_conditions = {
                    "$or": [{"user_name": search_regex}, {"login_id": search_regex}]
                }
                filter_query = {"$and": [deleted_filter, search_conditions]}
            else:
                filter_query = deleted_filter

            # Sort order (newest first)
            sort_order = [("created_at", -1)]

            # Execute query using repository
            docs = await self.repository.find_many(
                filter_dict=filter_query, sort_order=sort_order
            )

            # Remove password for security
            local_logins = []
            for doc in docs:
                doc.pop("password", None)
                local_logins.append(doc)

            logger.info(
                f"[get_all_local_logins] Success - Found {len(local_logins)} logins"
            )
            return local_logins

        except Exception as e:
            error_message = f"[get_all_local_logins] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_local_login_by_email(self, email: str) -> Optional[dict]:
        """Get local login by email"""
        logger.info(f"[get_local_login_by_email] Start - email={email}")

        try:
            # Validate email input
            if not email or not isinstance(email, str):
                logger.error("[get_local_login_by_email] Invalid email input")
                return None

            # Build filter query
            filter_query = {
                "login_id": email,
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}],
            }

            # Find document using repository
            doc = await self.repository.find_one(filter_query)

            if doc:
                logger.info(f"[get_local_login_by_email] Success - Found login")
                return doc
            else:
                logger.info(f"[get_local_login_by_email] Not found - email={email}")
                return None

        except Exception as e:
            error_message = f"[get_local_login_by_email] Error - email={email}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def authenticate_local_login(
        self, email: str, password: str
    ) -> Optional[dict]:
        """Authenticate local login"""
        logger.info(f"[authenticate_local_login] Start - email={email}")

        try:
            # Validate input
            if not all([email, password]):
                logger.error("[authenticate_local_login] Missing email or password")
                return None

            # Get local login by email
            local_login = await self.get_local_login_by_email(email)

            if not local_login:
                logger.warning(
                    f"[authenticate_local_login] Login not found - email={email}"
                )
                return None

            # Extract hashed password
            hashed_password = local_login.get("password")
            if not hashed_password:
                logger.error(
                    f"[authenticate_local_login] No password in document - email={email}"
                )
                return None

            # Verify password
            is_password_valid = check_password_match(password, hashed_password)

            if is_password_valid:
                logger.info(f"[authenticate_local_login] Success - email={email}")

                # Remove sensitive data
                local_login.pop("password", None)

                # Convert ObjectId to string
                if "_id" in local_login:
                    local_login["_id"] = str(local_login["_id"])

                return local_login
            else:
                logger.warning(
                    f"[authenticate_local_login] Invalid password - email={email}"
                )
                return None

        except Exception as e:
            error_message = f"[authenticate_local_login] Error - email={email}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def count_local_logins(self) -> int:
        """Count total local logins"""
        logger.info("[count_local_logins] Start")

        try:
            # Build filter query (not deleted)
            filter_query = {
                "$or": [{"deleted_at": None}, {"deleted_at": {"$exists": False}}]
            }

            # Count documents using repository
            count = await self.repository.count_documents(filter_query)

            logger.info(f"[count_local_logins] Success - count={count}")
            return count

        except Exception as e:
            error_message = f"[count_local_logins] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def initialize_default_local_login(self) -> bool:
        """Initialize default local login if no logins exist"""
        logger.info("[initialize_default_local_login] Start")

        try:
            from config_gateway import Config

            # Get default user config
            default_email = Config.DEFAULT_USER_EMAIL
            default_password = Config.DEFAULT_USER_PASSWORD
            default_name = Config.DEFAULT_USER_NAME

            # Check if any local logins exist
            login_count = await self.count_local_logins()
            if login_count > 0:
                logger.info(
                    f"[initialize_default_local_login] Logins exist - count={login_count}, skipping"
                )
                return True

            logger.info(
                "[initialize_default_local_login] No logins found, creating default"
            )

            # Check if default login already exists
            existing_login = await self.get_local_login_by_email(default_email)
            if existing_login:
                logger.info(
                    f"[initialize_default_local_login] Default already exists - email={default_email}"
                )
                return True

            # Prepare default login data
            login_data = {
                "login_id": default_email,
                "password": default_password,
                "user_name": default_name,
            }

            # Create default local login
            success = await self.create_local_login(login_data)

            if success:
                logger.info(
                    f"[initialize_default_local_login] Success - email={default_email}"
                )
                return True
            else:
                logger.error(
                    f"[initialize_default_local_login] Failed - email={default_email}"
                )
                return False

        except Exception as e:
            error_message = f"[initialize_default_local_login] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise


# Global local login service instance
local_login_service = LocalLoginService()
