"""
Middleware components for the API Gateway
"""
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware"""
    
    # Paths that don't require authentication
    EXCLUDED_PATHS = [
        "/",
        "/login",
        "/getAToken",
        "/logout",
        "/health",
        "/static",
        "/docs",
        "/redoc",
        "/openapi.json"
    ]
    
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        
        # Allow excluded paths to pass through
        if any(path.startswith(excluded) for excluded in self.EXCLUDED_PATHS):
            return await call_next(request)
        
        # Check token (from session or header)
        token_info = request.session.get('token_info')
        auth_header = request.headers.get('Authorization')
        
        # Redirect to login if not authenticated
        if not token_info and not auth_header:
            logger.warning(f"Unauthorized access to {path}, redirecting to login")
            return RedirectResponse(url="/login")
        
        return await call_next(request)
