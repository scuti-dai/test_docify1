"""
Log API endpoints
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.log import LogCreate, LogUpdate, LogResponse
from app.schemas.base import BaseResponse
from app.api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)

@router.get("/")
@limiter.limit("100/minute")
async def get_logs(request, current_user = Depends(get_current_user)):
    """Get logs"""
    raise HTTPException(status_code=501, detail="Not implemented yet")

@router.post("/", response_model=LogResponse)
@limiter.limit("10/minute")
async def create_log(request, data: LogCreate, current_user = Depends(get_current_user)):
    """Create log"""
    raise HTTPException(status_code=501, detail="Not implemented yet")
