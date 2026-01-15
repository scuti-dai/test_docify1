"""
Database initializers for default data
Handles creation of default records on startup
"""

import logging

from config_gateway import Config
from app.schemas.access_right import AccessUserCreate

logger = logging.getLogger(__name__)


class DatabaseInitializer:
    """Handles initialization of default database records"""

    def __init__(self, service_factory):
        if not service_factory:
            raise ValueError("INVALID_INPUT: service_factory is required")

        self.services = service_factory

    async def initialize_default_user(self):
        """Initialize default local login and access rights"""
        logger.info("[DatabaseInitializer] Starting default user initialization")

        try:
            if not Config.DEFAULT_USER_EMAIL:
                logger.warning(
                    "[DatabaseInitializer] DEFAULT_USER_EMAIL not configured, skipping"
                )
                return

            if not Config.DEFAULT_USER_ROLE:
                logger.warning(
                    "[DatabaseInitializer] DEFAULT_USER_ROLE not configured, skipping"
                )
                return

            # Create default local login
            success_login = (
                await self.services.local_login_service.initialize_default_local_login()
            )

            # Create default access rights
            data = AccessUserCreate(
                login_id=Config.DEFAULT_USER_EMAIL,
                permissions=Config.DEFAULT_USER_ROLE,
            )
            success_role = await self.services.access_right_service.create_access_user(
                data
            )

            if success_login and success_role:
                logger.warning(
                    f"[DatabaseInitializer] Default user created - email={Config.DEFAULT_USER_EMAIL}"
                )
            else:
                logger.warning(
                    "[DatabaseInitializer] Default user creation partially failed"
                )

        except Exception as e:
            logger.error(f"[DatabaseInitializer] Error creating default user: {e}")
            # Don't raise to avoid breaking app startup

