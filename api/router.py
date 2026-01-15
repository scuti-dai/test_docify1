"""
API Router management
"""

import logging
from typing import Dict, Any
from fastapi import APIRouter

from app.core.gateway import RouteManager
from app.api.v1 import (
    auth,
    projects,
    comments,
    data_management,
    logs,
    requirement_documents,
    basic_designs,
    detail_design_documents,
    issues,
    source_codes,
    unit_tests,
    access_rights,
    local_logins,
    housekeeping,
    assets,
)

logger = logging.getLogger(__name__)


class APIRouterManager:
    """Manage all API routers"""

    def __init__(self, route_manager: RouteManager):
        self.route_manager = route_manager
        self._register_all_routes()

    def _register_all_routes(self):
        """Register all API routes"""
        # logger.info("[APIRouterManager] Registering all API routes")

        # Include project-related routers into projects router
        projects.router.include_router(comments.router, tags=["Comments"])
        projects.router.include_router(
            requirement_documents.router, tags=["Requirement Documents"]
        )
        projects.router.include_router(basic_designs.router, tags=["Basic Designs"])
        projects.router.include_router(
            detail_design_documents.router, tags=["Detail Design Documents"]
        )
        projects.router.include_router(source_codes.router, tags=["Source Codes"])
        projects.router.include_router(unit_tests.router, tags=["Unit Tests"])
        projects.router.include_router(issues.router, tags=["Issues"])
        projects.router.include_router(assets.router, tags=["Assets"])

        # V1 API Routes
        v1_routes = {
            "auth": auth.router,
            "projects": projects.router,
            "data-management": data_management.router,
            "logs": logs.router,
            "issues": issues.router,
            "access-rights": access_rights.router,
            "users": local_logins.router,
            "housekeeping": housekeeping.router,
        }

        # Register routes
        for service, router in v1_routes.items():
            self.route_manager.register_route("", service, router)

        # logger.info(f"[APIRouterManager] Registered {len(v1_routes)} routes")

    def get_route_summary(self) -> Dict[str, Any]:
        """Get summary of all registered routes"""
        return {
            "total_routes": len(self.route_manager.get_routes()),
            "versions": ["v1"],
            "services": list(
                set(key.split(":")[1] for key in self.route_manager.get_routes().keys())
            ),
            "route_info": self.route_manager.get_route_info(),
        }


# Create a main API router for additional endpoints
main_router = APIRouter()


@main_router.get("/")
async def api_root():
    """API root endpoint"""
    return {"message": "New App API Gateway", "version": "1.0.0", "status": "active"}


@main_router.get("/status")
async def api_status():
    """API status endpoint"""
    return {
        "status": "healthy",
        "timestamp": "2023-01-01T00:00:00Z",
        "version": "1.0.0",
    }
