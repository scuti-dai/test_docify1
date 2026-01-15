"""
Access Right schemas for API requests and responses
"""
import logging
from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr
from app.schemas.base import BaseResponse
from app.utils.constants import PermissionLevel

logger = logging.getLogger(__name__)

# Schemas for Group Access APIs
class AccessGroupCreate(BaseModel):
    """Schema for creating access group"""
    
    group_name: str = Field(..., min_length=1, max_length=100, description="Group name")
    permissions: str = Field(..., description="Permission: admin or user")
    
    class Config:
        json_schema_extra = {
            "example": {
                "group_name": "developers",
                "permissions": PermissionLevel.ADMIN
            }
        }

class AccessGroupResponse(BaseModel):
    """Schema for access group response"""
    
    access_right_id: str = Field(..., description="Access right ID")
    group_name: str = Field(..., description="Group name")
    permissions: str = Field(..., description="Permission")
    created_at: str = Field(..., description="Creation date")
    updated_at: str = Field(..., description="Last update date")
    deleted_at: Optional[str] = Field(None, description="Deletion date")
    
    class Config:
        pass

# Schemas for User Access APIs
class AccessUserCreate(BaseModel):
    """Schema for creating access user"""
    
    login_id: EmailStr = Field(..., description="User email")
    permissions: str = Field(..., description="Permission: admin or user")
    
    class Config:
        json_schema_extra = {
            "example": {
                "login_id": "user@example.com",
                "permissions": PermissionLevel.USER
            }
        }

class AccessUserResponse(BaseModel):
    """Schema for access user response"""
    
    access_right_id: str = Field(..., description="Access right ID")
    login_id: str = Field(..., description="User email")
    permissions: str = Field(..., description="Permission")
    created_at: str = Field(..., description="Creation date")
    updated_at: str = Field(..., description="Last update date")
    deleted_at: Optional[str] = Field(None, description="Deletion date")
    
    class Config:
        pass

# Response schemas using BaseResponse
class AccessGroupListResponse(BaseResponse):
    """Response for GET list access groups"""
    data: List[AccessGroupResponse] = Field(..., description="List of access groups")

class AccessUserListResponse(BaseResponse):
    """Response for GET list access users"""
    data: List[AccessUserResponse] = Field(..., description="List of access users")

class AccessGroupCreateResponse(BaseResponse):
    """Response for POST create access group"""
    data: AccessGroupResponse = Field(..., description="Created access group")

class AccessUserCreateResponse(BaseResponse):
    """Response for POST create access user"""
    data: AccessUserResponse = Field(..., description="Created access user")
