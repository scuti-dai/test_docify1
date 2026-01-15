"""
Repository factory and initialization
Creates and manages all repository instances
"""

import logging
from typing import Optional

from cec_docifycode_common.database import Database
from cec_docifycode_common.repositories import (
    AccessRightRepository,
    ActivityLogRepository,
    BasicDesignRepository,
    CommentRepository,
    DataManagementRepository,
    DetailDesignRepository,
    GroupRepository,
    IssueRepository,
    LocalLoginRepository,
    LogRepository,
    ProjectRepository,
    RequirementDocumentRepository,
    SourceCodeRepository,
    UnitTestRepository,
    UserRepository,
)

logger = logging.getLogger(__name__)


class RepositoryFactory:
    """Factory for creating and managing repositories using registry pattern"""

    # Registry mapping repository names to their classes
    REPOSITORY_CLASSES = {
        'user': UserRepository,
        'local_login': LocalLoginRepository,
        'access_right': AccessRightRepository,
        'requirement_document': RequirementDocumentRepository,
        'project': ProjectRepository,
        'data_management': DataManagementRepository,
        'source_code': SourceCodeRepository,
        'detail_design': DetailDesignRepository,
        'basic_design': BasicDesignRepository,
        'unit_test': UnitTestRepository,
        'log': LogRepository,
        'activity_log': ActivityLogRepository,
        'comment': CommentRepository,
        'group': GroupRepository,
        'issue': IssueRepository,
    }

    # Type hints for IDE support
    user_repository: Optional[UserRepository]
    local_login_repository: Optional[LocalLoginRepository]
    access_right_repository: Optional[AccessRightRepository]
    requirement_document_repository: Optional[RequirementDocumentRepository]
    project_repository: Optional[ProjectRepository]
    data_management_repository: Optional[DataManagementRepository]
    source_code_repository: Optional[SourceCodeRepository]
    detail_design_repository: Optional[DetailDesignRepository]
    basic_design_repository: Optional[BasicDesignRepository]
    unit_test_repository: Optional[UnitTestRepository]
    log_repository: Optional[LogRepository]
    activity_log_repository: Optional[ActivityLogRepository]
    comment_repository: Optional[CommentRepository]
    group_repository: Optional[GroupRepository]
    issue_repository: Optional[IssueRepository]

    def __init__(self, database: Database):
        if not database:
            raise ValueError("INVALID_INPUT: database is required")

        self.database = database
        self._initialized = False

    def initialize(self):
        """Initialize all repositories using registry pattern"""
        logger.info("[RepositoryFactory] Starting repository initialization")

        if self._initialized:
            logger.info("[RepositoryFactory] Already initialized, skipping")
            return

        try:
            # Initialize repositories from registry using setattr
            for repo_name, repo_class in self.REPOSITORY_CLASSES.items():
                repo_instance = repo_class(self.database)
                setattr(self, f'{repo_name}_repository', repo_instance)
                logger.debug(f"[RepositoryFactory] Initialized {repo_name}_repository")

            self._initialized = True
            logger.info("[RepositoryFactory] All repositories initialized successfully")

        except Exception as e:
            logger.error(f"[RepositoryFactory] Error initializing repositories: {e}")
            raise

