"""
Data Management API endpoints
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.data_management import (
    DataManagementCreate,
    DataManagementResponse,
)
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


@router.get("/")
@limiter.limit("100/minute")
async def get_data_management(request, current_user=Depends(get_current_user)):
    """Get data management"""
    raise HTTPException(status_code=501, detail="Not implemented yet")


@router.post("/", response_model=DataManagementResponse)
@limiter.limit("10/minute")
async def create_data_management(
    request, data: DataManagementCreate, current_user=Depends(get_current_user)
):
    """Create data management"""
    raise HTTPException(status_code=501, detail="Not implemented yet")
