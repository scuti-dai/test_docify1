"""
API Gateway core functionality
"""
import logging
import time
from typing import Dict, Any, Optional
from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config_gateway import Config

logger = logging.getLogger(__name__)

class APIGateway:
    """API Gateway for managing routes, middleware, and requests"""
    
    def __init__(self, app: FastAPI):
        self.app = app
        self.limiter = Limiter(key_func=get_remote_address)
        self._setup_middleware()
        self._setup_exception_handlers()
        
    def _setup_middleware(self):
        """Setup all middleware"""
        
        # Rate limiting
        self.app.state.limiter = self.limiter
        self.app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        
        # CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=Config.CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Trusted host middleware
        if Config.ALLOWED_HOSTS:
            self.app.add_middleware(
                TrustedHostMiddleware,
                allowed_hosts=Config.ALLOWED_HOSTS
            )
        
        # Request logging middleware
        @self.app.middleware("http")
        async def log_requests(request: Request, call_next):
            start_time = time.time()
            
            # Process request
            
            # Process request
            response = await call_next(request)
            
            # Calculate process time
            process_time = time.time() - start_time
            
            # Add timing header
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
        
        # Authentication middleware
        @self.app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            # Skip auth for public endpoints
            if self._is_public_endpoint(request.url.path):
                return await call_next(request)
            
            # Add authentication logic here if needed
            # For now, just pass through
            return await call_next(request)
    
    def _setup_exception_handlers(self):
        """Setup global exception handlers"""
        
        @self.app.exception_handler(HTTPException)
        async def http_exception_handler(request: Request, exc: HTTPException):
            logger.error(f"[HTTP_EXCEPTION] {exc.status_code}: {exc.detail}")
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail
            )
        
        @self.app.exception_handler(Exception)
        async def general_exception_handler(request: Request, exc: Exception):
            logger.error(f"[GENERAL_EXCEPTION] {type(exc).__name__}: {str(exc)}")
            return JSONResponse(
                status_code=500,
                content={
                    "status": 500,
                    "message": "Internal server error",
                    "error": "InternalError"
                }
            )
    
    def _is_public_endpoint(self, path: str) -> bool:
        """Check if endpoint is public (no auth required)"""
        public_paths = [
            "/",
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/api/auth/login",
            "/api/auth/register"
        ]
        return any(path.startswith(public_path) for public_path in public_paths)
    
    def add_router(self, router, prefix: str, tags: list = None, **kwargs):
        """Add router to the gateway"""
        self.app.include_router(router, prefix=prefix, tags=tags, **kwargs)
    
    def get_limiter(self) -> Limiter:
        """Get rate limiter instance"""
        return self.limiter

class RouteManager:
    """Manage API routes and versions"""
    
    def __init__(self, gateway: APIGateway):
        self.gateway = gateway
        self.routes: Dict[str, Any] = {}
        
    def register_route(self, version: str, service: str, router, **kwargs):
        """Register a route for a specific version and service"""
        prefix = f"/api/{service}"
        tags = [service]
        
        self.routes[f"{version}:{service}"] = {
            "router": router,
            "prefix": prefix,
            "tags": tags,
            "kwargs": kwargs
        }
        
        self.gateway.add_router(router, prefix=prefix, tags=tags, **kwargs)
    
    def get_routes(self) -> Dict[str, Any]:
        """Get all registered routes"""
        return self.routes
    
    def get_route_info(self) -> Dict[str, Any]:
        """Get route information for API documentation"""
        route_info = {}
        for key, route in self.routes.items():
            version, service = key.split(":")
            if version not in route_info:
                route_info[version] = {}
            route_info[version][service] = {
                "prefix": route["prefix"],
                "tags": route["tags"]
            }
        return route_info


async def log_requests(request: Request, call_next):
    """リクエストログミドルウェア"""
    logger.info(f"Request: {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        logger.info(f"Response: {response.status_code}")
        return response
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        raise