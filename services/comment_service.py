import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from uuid6 import uuid7
from fastapi import HTTPException, status
from cec_docifycode_common.models.comment import Comment
from cec_docifycode_common.models.user import User
from app.services.user_service import user_service
from app.services.project_service import ProjectService
from app.utils.http_helpers import raise_http_error

from app.services.logs_service import save_exception_log_sync

logger = logging.getLogger(__name__)


class CommentService:
    """Service for comment operations - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls, repository=None):
        if cls._instance is None:
            cls._instance = super(CommentService, cls).__new__(cls)
        return cls._instance

    def __init__(self, repository=None):
        if not CommentService._initialized:
            self.project_service = ProjectService()
            CommentService._initialized = True
            logger.info("[CommentService] Singleton instance initialized")
        
        # Always set repository if provided (allows updating after initialization)
        if repository is not None:
            self.repository = repository

    def set_repository(self, repository):
        """Set repository (for backward compatibility)"""
        self.repository = repository
        logger.info("[CommentService] Repository updated via set_repository")

    async def create_comment(
        self, project_id: str, user_id: str, content: str
    ) -> Dict[str, Any]:
        """Create a new comment"""
        logger.info(f"[create_comment] Creating comment for project: {project_id}")

        try:
            # Get user information using user_service
            user_doc = await user_service.get_user_by_id(user_id)

            if not user_doc:
                raise_http_error(status.HTTP_404_NOT_FOUND)

            # Check if user has permission to comment on this project
            has_access = await self.project_service.check_user_project_access(
                user_id, project_id
            )
            if not has_access:
                raise_http_error(status.HTTP_403_FORBIDDEN)

            # Generate UUID7 for comment ID
            comment_id = str(uuid7())

            # Create comment document
            comment_doc = {
                "id": comment_id,
                "project_id": project_id,
                "user_id": user_id,
                "user_name": user_doc.get("user_name"),
                "content": content,
                "created_at": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
                "deleted_at": None,
            }

            # Insert comment into database using repository
            is_inserted = await self.repository.insert_one(comment_doc)

            if is_inserted:
                logger.info(
                    f"[create_comment] Comment created successfully: {comment_id}"
                )
                return {
                    "comment_id": comment_id,
                    "user_id": user_id,
                    "user_name": user_doc.get("user_name"),
                    "content": content,
                    "created_at": comment_doc["created_at"],
                }
            else:
                raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)

        except HTTPException:
            raise
        except Exception as e:
            error_message = f"[create_comment] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise

    async def get_comments(
        self, project_id: str, page: int = 1, per_page: int = 20
    ) -> Dict[str, Any]:
        """Get comments for a project with pagination"""
        logger.info(f"[get_comments] Fetching comments for project: {project_id}")

        try:
            # Calculate skip
            skip = (page - 1) * per_page

            # Get comments with pagination using repository
            filter_query = {"project_id": project_id, "deleted_at": None}
            sort_order = [("created_at", -1)]

            docs = await self.repository.find_many(
                filter_dict=filter_query,
                sort_order=sort_order,
                skip=skip,
                limit=per_page,
            )

            # Convert to list
            comments = [
                {
                    "comment_id": doc.get("id"),
                    "user_id": doc.get("user_id"),
                    "user_name": doc.get("user_name"),
                    "content": doc.get("content"),
                    "created_at": doc.get("created_at"),
                }
                for doc in docs
            ]

            # Get total count using repository
            total = await self.repository.count_documents(filter_query)

            return {
                "comments": comments,
                "pagination": {
                    "page": page,
                    "total_items": total,
                    "total_pages": (total + per_page - 1) // per_page,
                },
            }

        except Exception as e:
            error_message = f"[get_comments] Error: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise


# Create service instance
comment_service = CommentService()
