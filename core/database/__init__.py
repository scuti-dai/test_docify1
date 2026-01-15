"""
MongoDB database connection and application initialization
Orchestrates database, repositories, services, and default data
"""

import logging
from typing import Optional

from cec_docifycode_common.database import Database
from app.core.database.connection import DatabaseConnection, db_connection
from app.core.database.repository_factory import RepositoryFactory
from app.core.database.service_factory import ServiceFactory
from app.core.database.initializers import DatabaseInitializer

logger = logging.getLogger(__name__)


class AppDatabase:
    """Application database manager - orchestrates all components"""

    def __init__(self):
        self.db_connection = db_connection
        self.repository_factory: Optional[RepositoryFactory] = None
        self.service_factory: Optional[ServiceFactory] = None
        self.initializer: Optional[DatabaseInitializer] = None

    async def initialize(self):
        """Full initialization: database -> repositories -> services -> defaults"""
        logger.info("[AppDatabase] Starting full initialization")

        try:
            # Step 1: Connect database
            database = await self.db_connection.connect()

            # Step 2: Initialize repositories
            self.repository_factory = RepositoryFactory(database)
            self.repository_factory.initialize()

            # Step 3: Initialize services
            self.service_factory = ServiceFactory(self.repository_factory)
            self.service_factory.initialize()

            # Step 4: Create default data
            self.initializer = DatabaseInitializer(self.service_factory)
            await self.initializer.initialize_default_user()

            logger.info("[AppDatabase] Full initialization completed successfully")

        except Exception as e:
            logger.error(f"[AppDatabase] Error during initialization: {e}")
            raise

    async def connect(self):
        """Connect to database (backward compatibility)"""
        logger.info("[AppDatabase] Connecting to database")
        return await self.db_connection.connect()

    async def init_repositories(self):
        """Initialize repositories (backward compatibility)"""
        logger.info("[AppDatabase] Initializing repositories")

        if not self.repository_factory:
            database = await self.db_connection.connect()
            self.repository_factory = RepositoryFactory(database)
            self.repository_factory.initialize()

    async def init_services(self):
        """Initialize services (backward compatibility)"""
        logger.info("[AppDatabase] Initializing services")

        if not self.service_factory:
            await self.init_repositories()
            self.service_factory = ServiceFactory(self.repository_factory)
            self.service_factory.initialize()

    # Convenient property access
    @property
    def repositories(self):
        """Get repository factory"""
        return self.repository_factory

    @property
    def services(self):
        """Get service factory"""
        return self.service_factory

    # Backward compatibility: expose repositories directly
    @property
    def user_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.user_repository if self.repository_factory else None
        )

    @property
    def local_login_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.local_login_repository
            if self.repository_factory
            else None
        )

    @property
    def access_right_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.access_right_repository
            if self.repository_factory
            else None
        )

    @property
    def requirement_document_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.requirement_document_repository
            if self.repository_factory
            else None
        )

    @property
    def project_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.project_repository
            if self.repository_factory
            else None
        )

    @property
    def data_management_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.data_management_repository
            if self.repository_factory
            else None
        )

    @property
    def source_code_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.source_code_repository
            if self.repository_factory
            else None
        )

    @property
    def detail_design_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.detail_design_repository
            if self.repository_factory
            else None
        )

    @property
    def basic_design_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.basic_design_repository
            if self.repository_factory
            else None
        )

    @property
    def unit_test_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.unit_test_repository
            if self.repository_factory
            else None
        )

    @property
    def log_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.log_repository if self.repository_factory else None
        )

    @property
    def activity_log_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.activity_log_repository
            if self.repository_factory
            else None
        )

    @property
    def comment_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.comment_repository
            if self.repository_factory
            else None
        )

    @property
    def group_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.group_repository
            if self.repository_factory
            else None
        )

    @property
    def issue_repository(self):
        """Backward compatibility property"""
        return (
            self.repository_factory.issue_repository
            if self.repository_factory
            else None
        )

    # Backward compatibility: expose services directly
    @property
    def user_service(self):
        """Backward compatibility property"""
        return self.service_factory.user_service if self.service_factory else None

    @property
    def local_login_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.local_login_service if self.service_factory else None
        )

    @property
    def access_right_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.access_right_service if self.service_factory else None
        )

    @property
    def requirement_document_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.requirement_document_service
            if self.service_factory
            else None
        )

    @property
    def project_service(self):
        """Backward compatibility property"""
        return self.service_factory.project_service if self.service_factory else None

    @property
    def data_management_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.data_management_service
            if self.service_factory
            else None
        )

    @property
    def source_code_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.source_code_service if self.service_factory else None
        )

    @property
    def detail_design_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.detail_design_service if self.service_factory else None
        )

    @property
    def basic_design_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.basic_design_service if self.service_factory else None
        )

    @property
    def unit_test_service(self):
        """Backward compatibility property"""
        return self.service_factory.unit_test_service if self.service_factory else None

    @property
    def log_service(self):
        """Backward compatibility property"""
        return self.service_factory.log_service if self.service_factory else None

    @property
    def activity_log_service(self):
        """Backward compatibility property"""
        return (
            self.service_factory.activity_log_service if self.service_factory else None
        )

    @property
    def comment_service(self):
        """Backward compatibility property"""
        return self.service_factory.comment_service if self.service_factory else None

    @property
    def issue_service(self):
        """Backward compatibility property"""
        return self.service_factory.issue_service if self.service_factory else None


# Global app database manager
app_db = AppDatabase()


# Dependency injection helpers
async def get_database() -> Database:
    """Get database instance for DI"""
    logger.info("[get_database] Getting database instance")

    try:
        return await db_connection.connect()
    except Exception as e:
        logger.error(f"[get_database] Error: {e}")
        raise


async def init_database():
    """Initialize everything - entry point for app startup"""
    logger.info("[init_database] Starting database initialization")

    try:
        await app_db.initialize()
        logger.info("[init_database] Database initialization completed")
    except Exception as e:
        logger.error(f"[init_database] Error: {e}")
        raise
