"""
Access Right API endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.access_right import (
    AccessGroupCreate,
    AccessGroupResponse,
    AccessGroupListResponse,
    AccessGroupCreateResponse,
    AccessUserCreate,
    AccessUserResponse,
    AccessUserListResponse,
    AccessUserCreateResponse,
)
from app.schemas.base import BaseResponse
from app.services.access_right_service import access_right_service
from app.api.deps import get_current_user
from app.utils.constants import PermissionLevel
from app.utils.http_helpers import raise_http_error
from fastapi import status

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
        raise_http_error(status.HTTP_403_FORBIDDEN, error_key="ADMIN_ROLE_REQUIRED")


# Group Access APIs
@router.post("/groups", response_model=AccessGroupCreateResponse)
@limiter.limit("10/minute")
async def create_access_group(
    request: Request, data: AccessGroupCreate, current_user=Depends(get_current_user)
):
    """API-06: Create access group"""
    logger.info(f"[create_access_group] Start - group_name={data.group_name}")
    check_admin_role(current_user, "create_access_group")

    try:
        access_right = await access_right_service.create_access_group(data)

        response_data = AccessGroupResponse(
            access_right_id=access_right.access_right_id,
            group_name=access_right.group_name,
            permissions=access_right.permissions,
            created_at=access_right.created_at,
            updated_at=access_right.updated_at,
            deleted_at=access_right.deleted_at,
        )

        logger.info(f"[create_access_group] Success")
        return AccessGroupCreateResponse(data=response_data)
    except ValueError as e:
        error_message = f"[create_access_group] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST, error_message=str(e))
    except Exception as e:
        error_message = f"[create_access_group] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/groups", response_model=AccessGroupListResponse)
@limiter.limit("100/minute")
async def get_access_groups(request: Request, current_user=Depends(get_current_user)):
    """API-07: Get list of access groups"""
    logger.info(f"[get_access_groups] Start")
    check_admin_role(current_user, "get_access_groups")

    try:
        groups = await access_right_service.get_access_groups()

        response_data = [
            AccessGroupResponse(
                access_right_id=group.access_right_id,
                group_name=group.group_name,
                permissions=group.permissions,
                created_at=group.created_at,
                updated_at=group.updated_at,
                deleted_at=group.deleted_at,
            )
            for group in groups
        ]

        logger.info(f"[get_access_groups] Success - Found {len(response_data)} groups")
        return AccessGroupListResponse(data=response_data)
    except Exception as e:
        error_message = f"[get_access_groups] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete("/groups/{group_id}", response_model=BaseResponse)
@limiter.limit("10/minute")
async def delete_access_group(
    request: Request, group_id: str, current_user=Depends(get_current_user)
):
    """API-08: Delete access group"""
    logger.info(f"[delete_access_group] Start - group_id={group_id}")
    check_admin_role(current_user, "delete_access_group")

    try:
        success = await access_right_service.delete_access_group(group_id)

        if not success:
            logger.warning(f"[delete_access_group] Group not found")
            raise_http_error(status.HTTP_404_NOT_FOUND)

        logger.info(f"[delete_access_group] Success")
        return BaseResponse(message="Access group deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[delete_access_group] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# User Access APIs
@router.post("/users", response_model=AccessUserCreateResponse)
@limiter.limit("10/minute")
async def create_access_user(
    request: Request, data: AccessUserCreate, current_user=Depends(get_current_user)
):
    """API-09: Create access user"""
    logger.info(f"[create_access_user] Start - login_id={data.login_id}")
    check_admin_role(current_user, "create_access_user")

    try:
        access_right = await access_right_service.create_access_user(data)

        response_data = AccessUserResponse(
            access_right_id=access_right.access_right_id,
            login_id=access_right.login_id,
            permissions=access_right.permissions,
            created_at=access_right.created_at,
            updated_at=access_right.updated_at,
            deleted_at=access_right.deleted_at,
        )

        logger.info(f"[create_access_user] Success")
        return AccessUserCreateResponse(data=response_data)
    except ValueError as e:
        error_message = f"[create_access_user] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST, error_message=str(e))
    except Exception as e:
        error_message = f"[create_access_user] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get("/users", response_model=AccessUserListResponse)
@limiter.limit("100/minute")
async def get_access_users(request: Request, current_user=Depends(get_current_user)):
    """API-10: Get list of access users"""
    logger.info(f"[get_access_users] Start")
    check_admin_role(current_user, "get_access_users")

    try:
        users = await access_right_service.get_access_users()

        response_data = [
            AccessUserResponse(
                access_right_id=user.access_right_id,
                login_id=user.login_id,
                permissions=user.permissions,
                created_at=user.created_at,
                updated_at=user.updated_at,
                deleted_at=user.deleted_at,
            )
            for user in users
        ]

        logger.info(f"[get_access_users] Success - Found {len(response_data)} users")
        return AccessUserListResponse(data=response_data)
    except Exception as e:
        error_message = f"[get_access_users] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.delete("/users/{user_access_id}", response_model=BaseResponse)
@limiter.limit("10/minute")
async def delete_access_user(
    request: Request, user_access_id: str, current_user=Depends(get_current_user)
):
    """API-11: Delete access user"""
    logger.info(f"[delete_access_user] Start - user_access_id={user_access_id}")
    check_admin_role(current_user, "delete_access_user")

    try:
        success = await access_right_service.delete_access_user(user_access_id)

        if not success:
            logger.warning(f"[delete_access_user] User not found")
            raise_http_error(status.HTTP_404_NOT_FOUND)

        logger.info(f"[delete_access_user] Success")
        return BaseResponse(message="Access user deleted successfully")
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[delete_access_user] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)
