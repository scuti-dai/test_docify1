"""
Log schemas for API requests and responses
"""
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class LogCreate(BaseModel):
    """Schema for creating a log"""
    
    log_id: str = Field(..., description="UUID7, unique identifier")
    user_id: str = Field(..., description="Reference to user.user_id")
    username: str = Field(..., description="Username")
    level: str = Field(..., pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$", description="Log level")
    message: str = Field(..., description="Log message")
    module: str = Field(..., description="Module name")
    function: str = Field(..., description="Function name")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional details")
    
    class Config:
        pass

class LogUpdate(BaseModel):
    """Schema for updating a log"""
    
    level: Optional[str] = Field(None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    message: Optional[str] = None
    module: Optional[str] = None
    function: Optional[str] = None
    details: Optional[Dict[str, Any]] = None
    
    class Config:
        pass

class LogResponse(BaseModel):
    """Schema for log response"""
    
    log_id: str
    user_id: str
    username: str
    level: str
    message: str
    module: str
    function: str
    details: Optional[Dict[str, Any]]
    created_at: str
    updated_at: str
    deleted_at: Optional[str]
    
    class Config:
        pass
