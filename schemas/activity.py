"""
Activity schemas for API requests and responses
"""
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ActivityCreate(BaseModel):
    """Schema for creating an activity"""
    
    activity_id: str = Field(..., description="UUID7, unique identifier")
    user_id: str = Field(..., description="Reference to user.user_id")
    username: str = Field(..., description="Username")
    action: str = Field(..., description="Action performed")
    target_type: str = Field(..., description="Type of target (project, comment, etc.)")
    target_id: str = Field(..., description="ID of the target")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")
    
    class Config:
        pass

class ActivityUpdate(BaseModel):
    """Schema for updating an activity"""
    
    action: Optional[str] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    
    class Config:
        pass

class ActivityResponse(BaseModel):
    """Schema for activity response"""
    
    activity_id: str
    user_id: str
    username: str
    action: str
    target_type: str
    target_id: str
    details: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str
    deleted_at: Optional[str]
    
    class Config:
        pass
