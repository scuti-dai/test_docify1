"""
Service factory and initialization
Creates and manages all service instances
"""

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.core.database.repository_factory import RepositoryFactory

logger = logging.getLogger(__name__)


class ServiceFactory:
    """Factory for creating and managing services"""

    def __init__(self, repository_factory: "RepositoryFactory"):
        if not repository_factory:
            raise ValueError("INVALID_INPUT: repository_factory is required")

        self.repos = repository_factory

        # Service instances
        self.user_service = None
        self.local_login_service = None
        self.access_right_service = None
        self.requirement_document_service = None
        self.project_service = None
        self.data_management_service = None
        self.source_code_service = None
        self.detail_design_service = None
        self.basic_design_service = None
        self.unit_test_service = None
        self.log_service = None
        self.activity_log_service = None
        self.comment_service = None
        self.issue_service = None

    def initialize(self):
        """Initialize all services"""
        logger.info("[ServiceFactory] Starting service initialization")

        try:
            from app.services.user_service import UserService
            from app.services.local_login_service import LocalLoginService
            from app.services.access_right_service import AccessRightService
            from app.services.requirement_document_service import RequirementDocumentService
            from app.services.project_service import ProjectService
            from app.services.data_management_service import DataManagementService
            from app.services.source_code_service import SourceCodeService
            from app.services.detail_design_service import DetailDesignService
            from app.services.basic_design_service import BasicDesignService
            from app.services.unit_test_service import UnitTestService
            from app.services.logs_service import LogService
            from app.services.activity_log_service import ActivityLogService
            from app.services.comment_service import CommentService
            from app.services.issue_service import IssueService

            self.user_service = UserService(self.repos.user_repository)
            self.local_login_service = LocalLoginService(
                self.repos.local_login_repository
            )
            self.access_right_service = AccessRightService(
                self.repos.access_right_repository
            )
            self.requirement_document_service = RequirementDocumentService(
                self.repos.requirement_document_repository
            )
            self.project_service = ProjectService(self.repos.project_repository)
            self.data_management_service = DataManagementService(
                self.repos.data_management_repository
            )
            self.source_code_service = SourceCodeService(
                repository=self.repos.source_code_repository,
                detail_design_repository=self.repos.detail_design_repository,
                unit_test_repository=self.repos.unit_test_repository,
            )
            self.detail_design_service = DetailDesignService(
                self.repos.detail_design_repository
            )
            self.basic_design_service = BasicDesignService(
                self.repos.basic_design_repository
            )
            self.unit_test_service = UnitTestService(
                repository=self.repos.unit_test_repository,
                source_code_repository=self.repos.source_code_repository,
            )
            self.log_service = LogService(self.repos.log_repository)
            self.activity_log_service = ActivityLogService(
                self.repos.activity_log_repository
            )
            self.comment_service = CommentService(self.repos.comment_repository)
            self.issue_service = IssueService(self.repos.issue_repository)

            logger.info("[ServiceFactory] All services initialized successfully")

        except Exception as e:
            logger.error(f"[ServiceFactory] Error initializing services: {e}")
            raise

