"""
Local Login API endpoints
"""

import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from app.utils.http_helpers import raise_http_error
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.local_login import LocalLoginCreate
from app.api.deps import get_current_user
from app.core.database import get_database
from cec_docifycode_common.models.local_login import LocalLogin
from app.utils.constants import PermissionLevel
from app.utils.helpers import create_password_hash, get_current_utc_time
from app.services.local_login_service import local_login_service

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def check_admin_role(current_user: dict, endpoint_name: str):
    """Check if user has admin role"""
    user_role = current_user.get("role")
    if user_role != PermissionLevel.ADMIN:
        logger.warning(
            f"[{endpoint_name}] Access denied - user: {current_user.get('user_name')}, role: {user_role}"
        )
        raise_http_error(status.HTTP_403_FORBIDDEN)


@router.get("/", response_model=List[dict])
@limiter.limit("100/minute")
async def get_local_logins(
    request: Request,
    search: Optional[str] = Query(None, description="Search by user_name or email"),
    current_user=Depends(get_current_user),
):
    """Get all local logins with optional search"""
    user_name = current_user.get("user_name")
    logger.info(f"[get_local_logins] Start - user: {user_name}, search: {search}")
    # check_admin_role(current_user, "get_local_logins")

    if not user_name:
        logger.warning("[get_local_logins] Missing user_name in current_user")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        # Use service layer to get local logins with search
        local_logins = await local_login_service.get_all_local_logins(search=search)

        # Transform data to match expected format
        response_data = []
        for user_doc in local_logins:
            user_data = {
                "user_id": user_doc.get("login_id"),
                "user_name": user_doc.get("user_name"),
                "email": user_doc.get("login_id"),
                "created_at": user_doc.get("created_at"),
                "updated_at": user_doc.get("updated_at"),
                "deleted_at": user_doc.get("deleted_at"),
            }
            response_data.append(user_data)

        logger.info(f"[get_local_logins] Success - Found {len(response_data)} users")
        return response_data

    except Exception as e:
        error_message = f"[get_local_logins] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/{user_id}", response_model=dict)
@limiter.limit("100/minute")
async def get_local_login(
    request: Request, user_id: str, current_user=Depends(get_current_user)
):
    """Get specific local login by user_id (login_id)"""
    logger.info(
        f"[get_local_login] Start - user_id={user_id}, requester: {current_user.get('user_name')}"
    )
    check_admin_role(current_user, "get_local_login")

    try:
        db = await get_database()
        user_collection = db.get_collection(LocalLogin.Config.collection_name)

        # Find user by login_id
        user_doc = await user_collection.find_one(
            {"login_id": user_id, "deleted_at": None}
        )

        if not user_doc:
            logger.warning(f"[get_local_login] User not found - user_id={user_id}")
            raise_http_error(status.HTTP_404_NOT_FOUND)

        # Transform data to match expected format
        user_data = {
            "user_id": user_doc.get("login_id"),
            "user_name": user_doc.get("user_name"),
            "email": user_doc.get("login_id"),
            "created_at": user_doc.get("created_at"),
            "updated_at": user_doc.get("updated_at"),
            "deleted_at": user_doc.get("deleted_at"),
        }

        logger.info(f"[get_local_login] Success - user_id={user_id}")
        return user_data

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[get_local_login] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/", response_model=dict)
@limiter.limit("10/minute")
async def create_local_login(
    request: Request, data: LocalLoginCreate, current_user=Depends(get_current_user)
):
    """Create local login using LocalLogin model"""
    logger.info(
        f"[create_local_login] Start - email={data.email}, user_name={data.user_name}"
    )
    check_admin_role(current_user, "create_local_login")

    try:
        # Hash password using helper function
        password_hash = create_password_hash(data.password)

        # Get current UTC timestamp
        current_time = get_current_utc_time()

        # Create LocalLogin instance
        local_login = LocalLogin(
            login_id=data.email,  # Use email as login_id
            user_name=data.user_name,
            password=password_hash,  # Store hashed password
            created_at=current_time,
            updated_at=current_time,
            deleted_at=None,
        )

        # Save to database using model
        db = await get_database()
        user_collection = db.get_collection(LocalLogin.Config.collection_name)

        # Check if user already exists
        existing_user = await user_collection.find_one(
            {"login_id": data.email, "deleted_at": None}
        )

        if existing_user:
            logger.warning(
                f"[create_local_login] User already exists - email={data.email}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST, error_key="EMAIL_ALREADY_EXISTS"
            )

        # Insert new user using model data
        user_doc = local_login.dict()
        result = await user_collection.insert_one(user_doc)

        if result.inserted_id:
            logger.info(f"[create_local_login] Success - email={data.email}")
            return {
                "message": "User created successfully",
                "user_id": data.email,
                "user_name": data.user_name,
            }
        else:
            raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[create_local_login] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
