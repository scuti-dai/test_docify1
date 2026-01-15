"""
Authentication service for business logic
"""

import logging
from typing import Optional, Dict, Any, Tuple
from app.core.security import (
    create_access_token,
    create_refresh_token,
    verify_refresh_token,
    get_access_token_expires_in,
    get_refresh_token_expires_in,
)
from app.utils.constants import PermissionLevel
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    LoginData,
    RefreshResponse,
    config,
)
from app.services.local_login_service import local_login_service
from app.services.user_service import user_service
from app.services.access_right_service import access_right_service

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


class AuthService:
    """Service for authentication operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuthService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not AuthService._initialized:
            self.collection_name = "local_login"
            self.access_right_service = access_right_service
            AuthService._initialized = True
            logger.info("[AuthService] Singleton instance initialized")

    # region Public Functions

    async def authenticate_user(self, email: str, password: str) -> Optional[dict]:
        """Authenticate user with email and password"""
        logger.info(f"[authenticate_user] Start - email={email}")

        # Input validation
        if not email or not password:
            logger.warning("[authenticate_user] Missing email or password")
            raise ValueError("Email and password are required")

        try:
            user = await local_login_service.authenticate_local_login(email, password)

            if not user:
                logger.warning(f"[authenticate_user] Failed - email={email}")
                return None

            logger.info(f"[authenticate_user] Success - email={email}")
            return user

        except Exception as e:
            error_message = f"[authenticate_user] Error - email={email}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_user_role(self, email: str) -> str:
        """Get user role according to new flow chart logic"""
        logger.info(f"[get_user_role] Start - email={email}")

        # Input validation
        if not email:
            logger.warning("[get_user_role] Missing email")
            raise ValueError("Email is required")

        try:
            if config.AUTH_TYPE == "EntraID":
                return PermissionLevel.USER

            user_doc = await user_service.get_user_by_email(email)
            if not user_doc:
                logger.warning(f"[get_user_role] User not found - email={email}")
                raise ValueError("User not found in ManageUser")

            role = await self.access_right_service.check_user_permission(email)

            if role is None:
                logger.warning(f"[get_user_role] Access denied - email={email}")
                raise ValueError("Access denied")

            logger.info(f"[get_user_role] Success - email={email}, role={role}")
            return role

        except ValueError:
            raise
        except Exception as e:
            error_message = f"[get_user_role] Error - email={email}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise ValueError("Error checking user role")

    async def login(self, login_data: LoginRequest) -> LoginResponse:
        """Login user and return JWT token"""
        email = login_data.email
        password = login_data.password

        logger.info(f"[login] Start - email={email}")

        # Input validation
        if not email or not password:
            logger.warning("[login] Missing email or password")
            raise ValueError("Email and password are required")

        try:
            # Step 1: Authenticate user
            user = await self.authenticate_user(email, password)
            if not user:
                logger.warning(f"[login] Authentication failed - email={email}")
                raise ValueError("Invalid email or password")

            # Extract user data
            login_id = user["login_id"]
            user_name = user["user_name"]

            # Step 2 & 3: Get or create user
            db_user_id = await self._get_or_create_user(user)

            # Step 4: Get user role
            user_role = await self.get_user_role(login_id)

            # Step 5: Create tokens
            access_token, refresh_token = await self._create_tokens(
                db_user_id, user_name
            )

            logger.info(f"[login] Success - email={email}, role={user_role}")
            return self._build_login_response(
                db_user_id, login_id, user_name, user_role, access_token, refresh_token
            )

        except ValueError as e:
            error_message = f"[login] Validation error - email={email}: {e}"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

            raise
        except Exception as e:
            error_message = f"[login] Error - email={email}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def entra_id_login(self, login_data: LoginRequest) -> LoginResponse:
        """Login user and return JWT token"""
        login_id = login_data.email
        user_name = login_data.name

        logger.info(f"[login] Start - email={login_id}")

        # Input validation
        try:
            # Step 1: Authenticate user
            # Extract user data
            user = {"user_name": login_data.name, "login_id": login_id}

            # Step 2 & 3: Get or create user
            db_user_id = await self._get_or_create_user(user)

            # Step 4: Get user role
            user_role = PermissionLevel.USER

            # Step 5: Create tokens
            access_token, refresh_token = await self._create_tokens(
                db_user_id, user_name
            )

            logger.info(f"[login] Success - email={login_id}, role={user_role}")
            return self._build_login_response(
                db_user_id,
                login_id,
                user_name,
                user_role,
                access_token,
                refresh_token,
                login_data.token,
                login_data.groups,
            )

        except ValueError as e:
            error_message = f"[login] Validation error - email={login_id}: {e}"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

            raise
        except Exception as e:
            error_message = f"[login] Error - email={login_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def logout(self, refresh_token: str) -> bool:
        """
        Logout user (stateless approach)
        Client is responsible for removing tokens from storage
        """
        logger.info("[logout] Start")

        # Input validation
        if not refresh_token:
            logger.warning("[logout] Missing refresh token")
            raise ValueError("Refresh token is required")

        try:
            payload = verify_refresh_token(refresh_token)
            user_id = self._validate_token_payload(payload)

            # Stateless logout: no server-side blacklist
            # Log the event for audit trail
            logger.info(
                f"[logout] Success - user_id={user_id}. "
                "Client should remove tokens from storage."
            )
            return True

        except ValueError:
            raise
        except Exception as e:
            error_message = f"[logout] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def refresh_token(self, refresh_token: str) -> RefreshResponse:
        """Refresh access token with enhanced validation"""
        logger.info("[refresh_token] Start")

        # Input validation
        if not refresh_token:
            logger.warning("[refresh_token] Missing refresh token")
            raise ValueError("Refresh token is required")

        try:
            # Step 1: Verify token and extract payload
            payload = verify_refresh_token(refresh_token)
            if not payload:
                logger.warning("[refresh_token] Invalid refresh token")
                raise ValueError("Invalid refresh token")

            # Step 2: Extract user info from payload
            user_id, user_name = self._extract_user_from_payload(payload)

            # Step 3: Validate user exists (get_user_by_id filters deleted_at)
            user = await user_service.get_user_by_id(user_id)
            if not user:
                logger.warning(f"[refresh_token] User not found - user_id={user_id}")
                raise ValueError("User not found")

            # Step 4: Generate new access token
            new_token = self._generate_new_access_token(user_id, user_name)

            logger.info(f"[refresh_token] Success - user_id={user_id}")
            return new_token

        except ValueError:
            raise
        except Exception as e:
            error_message = f"[refresh_token] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion

    # region Private Functions

    async def _get_or_create_user(self, user: dict) -> str:
        """Get or create user and return user_id"""
        logger.info(f"[_get_or_create_user] Start - email={user.get('login_id')}")

        try:
            login_id = user["login_id"]

            # Create user if not exists
            await user_service.create_user_from_local_login(user)

            # Fetch user document to get user_id
            user_doc = await user_service.get_user_by_email(login_id)
            if not user_doc or "user_id" not in user_doc:
                logger.error(f"[_get_or_create_user] Failed - email={login_id}")
                raise ValueError("User not found")

            user_id = user_doc["user_id"]
            logger.info(f"[_get_or_create_user] Success - user_id={user_id}")
            return user_id

        except Exception as e:
            error_message = f"[_get_or_create_user] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def _create_tokens(self, user_id: str, user_name: str) -> Tuple[str, str]:
        """Create access and refresh tokens"""
        logger.info(f"[_create_tokens] Start - user_id={user_id}")

        try:
            token_data = {"sub": user_id, "user_name": user_name}
            access_token = create_access_token(data=token_data)
            refresh_token = create_refresh_token(data=token_data)

            logger.info(f"[_create_tokens] Success - user_id={user_id}")
            return access_token, refresh_token

        except Exception as e:
            error_message = f"[_create_tokens] Error - user_id={user_id}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _build_login_response(
        self,
        user_id: str,
        email: str,
        user_name: str,
        role: str,
        access_token: str,
        refresh_token: str,
        OAuth_token: Optional[str] = None,
        groups: Optional[list[str]] = None,
    ) -> LoginResponse:
        """Build login response"""
        access_expires_in = get_access_token_expires_in()
        refresh_expires_in = get_refresh_token_expires_in()

        login_data = LoginData(
            user_id=user_id,
            email=email,
            user_name=user_name,
            role=role,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_expires_in,
            refresh_expires_in=refresh_expires_in,
            OAuth_token=OAuth_token,
            group=groups,
        )

        return LoginResponse(
            statusCode=200, message="Login successful", data=login_data.dict()
        )

    def _validate_token_payload(self, payload: Optional[Dict[str, Any]]) -> str:
        """Validate token payload and return user_id"""
        if not payload:
            logger.warning("[_validate_token_payload] Invalid token")
            raise ValueError("Invalid token")

        user_id = payload.get("sub")
        if not user_id:
            logger.warning("[_validate_token_payload] Missing user_id")
            raise ValueError("Invalid token payload")

        return user_id

    def _extract_user_from_payload(self, payload: dict) -> tuple:
        """Extract and validate user info from token payload"""
        logger.debug("[_extract_user_from_payload] Extracting user info")

        try:
            user_id = payload.get("sub")
            user_name = payload.get("user_name")

            if not user_id or not user_name:
                logger.warning(
                    "[_extract_user_from_payload] Missing user data in payload"
                )
                raise ValueError("Invalid token payload")

            logger.debug(f"[_extract_user_from_payload] Success - user_id={user_id}")
            return user_id, user_name

        except Exception as e:
            error_message = f"[_extract_user_from_payload] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    def _generate_new_access_token(
        self, user_id: str, user_name: str
    ) -> RefreshResponse:
        """Generate new access token and build response"""
        logger.debug(f"[_generate_new_access_token] Start - user_id={user_id}")

        try:
            token_data = {"sub": user_id, "user_name": user_name}
            access_token = create_access_token(data=token_data)
            expires_in = get_access_token_expires_in()

            logger.debug(f"[_generate_new_access_token] Success - user_id={user_id}")
            logger.info(
                f"[ *** NEW ACCESS TOKEN *** ] _generate_new_access_token] Expires in: {expires_in}"
            )

            return RefreshResponse(
                access_token=access_token, token_type="bearer", expires_in=expires_in
            )

        except Exception as e:
            error_message = (
                f"[_generate_new_access_token] Error - user_id={user_id}: {e}"
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    # endregion


# Global auth service instance
auth_service = AuthService()
