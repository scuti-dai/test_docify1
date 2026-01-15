import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Tuple
from enum import Enum

from fastapi import HTTPException

from app.services.project_service import ProjectService
from app.utils.download_utils import (
    validate_download_access,
    create_zip_from_documents,
    save_zip_to_disk,
    save_single_file_to_disk,
    detect_mime_type,
)

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)


# Type aliases
ActivityLogger = Callable[[str, str, str, List[str], str], Awaitable[Any]]
ContentExtractor = Callable[[Dict[str, Any]], Tuple[str, bytes]]
DocumentNormalizer = Callable[[Dict[str, Any]], Dict[str, Any]]
DocumentIdResolver = Callable[[Dict[str, Any]], str]
SingleFileSaver = Callable[[str, bytes, str, str], Tuple[str, str]]
MimeDetector = Callable[[str], str]
ZipCreator = Callable[[List[Dict[str, Any]], ContentExtractor], Any]
ZipSaver = Callable[[Any, str, str], Tuple[str, str]]


class DownloadError(Exception):
    """Base exception for download operations"""

    pass


class ValidationError(DownloadError):
    """Validation related errors"""

    pass


class ProcessingError(DownloadError):
    """Processing related errors"""

    pass


@dataclass
class DownloadContext:
    """Context data for download operations"""

    project_id: str
    project_name: str
    project: Dict[str, Any]
    user_id: str
    created_at: str
    activity_type: str
    file_type: str
    file_suffix: str


@dataclass
class ResolverConfig:
    """Configuration for various resolver functions"""

    document_id_resolver: DocumentIdResolver
    document_normalizer: DocumentNormalizer
    content_extractor: ContentExtractor


@dataclass
class FileHandlers:
    """Handlers for file operations"""

    save_single_file: SingleFileSaver
    detect_mime: MimeDetector
    create_zip_buffer: ZipCreator
    save_zip_file: ZipSaver


@dataclass
class DownloadResult:
    """Result of download operation"""

    file_name: str
    file_path: str
    created_at: str
    media_type: str


class DownloadService:
    """Service for handling document downloads with improved architecture - Singleton pattern"""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DownloadService, cls).__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not DownloadService._initialized:
            self.project_service = ProjectService()
            DownloadService._initialized = True
            logger.info("[DownloadService] Singleton instance initialized")

    async def validate_project_download(
        self,
        project_id: str,
        user_id: str,
        user_role: str,
        project_service: ProjectService | None = None,
    ) -> Dict[str, Any]:
        """Validate user access to project for download"""
        logger.info(
            "[validate_project_download] project_id=%s, user_id=%s",
            project_id,
            user_id,
        )

        if not project_id or not user_id:
            logger.warning("[validate_project_download] Missing required parameters")
            raise ValidationError("INVALID_INPUT: project_id and user_id are required")

        try:
            resolved_service = project_service or self.project_service
            project = await validate_download_access(
                project_id=project_id,
                user_id=user_id,
                user_role=user_role,
                project_service=resolved_service,
            )
            logger.info("[validate_project_download] Success")
            return project

        except HTTPException:
            raise
        except ValidationError:
            raise
        except Exception as e:
            error_message = (
                "[validate_project_download] Unexpected error for project_id=%s: %s"
                % (
                    project_id,
                    e,
                )
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise ProcessingError(f"Failed to validate project access: {str(e)}") from e

    async def build_download_payload(
        self,
        documents: List[Dict[str, Any]],
        context: DownloadContext,
        resolvers: ResolverConfig,
        handlers: FileHandlers,
        activity_logger: ActivityLogger,
    ) -> DownloadResult:
        """
        Build download payload for single or multiple documents

        Args:
            documents: List of documents to download
            context: Download context (project, user, timestamps, etc.)
            resolvers: Configuration for resolver functions
            handlers: File operation handlers
            activity_logger: Logger for user activities

        Returns:
            DownloadResult with file information

        Raises:
            ValidationError: If input validation fails
            ProcessingError: If download processing fails
        """
        logger.info(
            "[build_download_payload] project_id=%s, document_count=%s",
            context.project_id,
            len(documents),
        )

        # Validate input
        self._validate_download_input(documents, context)

        try:
            document_count = len(documents)

            if document_count == 1:
                logger.info("[build_download_payload] Single file download")
                result = await self._handle_single_download(
                    document=documents[0],
                    context=context,
                    resolvers=resolvers,
                    handlers=handlers,
                    activity_logger=activity_logger,
                )
            else:
                logger.info("[build_download_payload] Multiple files download (zip)")
                result = await self._handle_multiple_download(
                    documents=documents,
                    context=context,
                    resolvers=resolvers,
                    handlers=handlers,
                    activity_logger=activity_logger,
                )

            logger.info("[build_download_payload] Success - file=%s", result.file_name)
            return result

        except (ValidationError, ProcessingError):
            raise
        except Exception as e:
            error_message = (
                "[build_download_payload] Unexpected error for project_id=%s: %s"
                % (context.project_id, e)
            )
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise ProcessingError(f"Failed to build download payload: {str(e)}") from e

    # Private methods

    @staticmethod
    def create_download_context(
        project_id: str,
        project_name: str,
        project: Dict[str, Any],
        user_id: str,
        created_at: str,
        file_type: str,
        file_suffix: str,
    ) -> DownloadContext:
        """Create download context with standard configuration"""
        logger.debug(
            "[create_download_context] Creating context - project_id=%s, file_type=%s",
            project_id,
            file_type,
        )

        return DownloadContext(
            project_id=project_id,
            project_name=project_name,
            project=project,
            user_id=user_id,
            created_at=created_at,
            activity_type="DOWNLOAD_FILE",
            file_type=file_type,
            file_suffix=file_suffix,
        )

    @staticmethod
    def create_resolver_config(
        document_id_resolver: DocumentIdResolver,
        document_normalizer: DocumentNormalizer,
        content_extractor: ContentExtractor,
    ) -> ResolverConfig:
        """Create resolver config with provided resolvers"""
        logger.debug("[create_resolver_config] Creating resolver config")

        return ResolverConfig(
            document_id_resolver=document_id_resolver,
            document_normalizer=document_normalizer,
            content_extractor=content_extractor,
        )

    @staticmethod
    def create_file_handlers() -> FileHandlers:
        """Create standard file handlers for download operations"""
        logger.debug("[create_file_handlers] Creating file handlers")

        return FileHandlers(
            save_single_file=save_single_file_to_disk,
            detect_mime=detect_mime_type,
            create_zip_buffer=create_zip_from_documents,
            save_zip_file=save_zip_to_disk,
        )

    def _validate_download_input(
        self,
        documents: List[Dict[str, Any]],
        context: DownloadContext,
    ) -> None:
        """Validate download input parameters"""
        if not documents:
            raise ValidationError("NO_DOCUMENTS: Document list cannot be empty")

        if not context.project:
            raise ValidationError("INVALID_PROJECT: Project data is required")

        if not context.user_id:
            raise ValidationError("INVALID_USER: User ID is required")

    async def _handle_single_download(
        self,
        document: Dict[str, Any],
        context: DownloadContext,
        resolvers: ResolverConfig,
        handlers: FileHandlers,
        activity_logger: ActivityLogger,
    ) -> DownloadResult:
        """Handle download of a single document"""
        logger.debug("[_handle_single_download] Processing single file")

        if not document:
            raise ValidationError("INVALID_DOCUMENT: Document data is required")

        try:
            # Extract file content
            file_name, file_data = resolvers.content_extractor(document)

            # Save file
            final_file_name, final_file_path = handlers.save_single_file(
                file_name=file_name,
                file_data=file_data,
                project_name=context.project_name,
                file_suffix=context.file_suffix,
            )

            # Detect media type
            media_type = handlers.detect_mime(file_name)

            # Log activity
            normalized_doc = resolvers.document_normalizer(document)
            file_ids = self._extract_document_ids(
                [normalized_doc], resolvers.document_id_resolver
            )

            await self._log_activity(
                context=context,
                resolvers=resolvers,
                activity_logger=activity_logger,
                file_ids=file_ids,
            )

            logger.debug("[_handle_single_download] Success: %s", final_file_name)

            return DownloadResult(
                file_name=final_file_name,
                file_path=final_file_path,
                created_at=context.created_at,
                media_type=media_type,
            )

        except ValidationError:
            raise
        except Exception as e:
            error_message = "[_handle_single_download] Error: %s" % e
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise ProcessingError(f"Failed to process single download: {str(e)}") from e

    async def _handle_multiple_download(
        self,
        documents: List[Dict[str, Any]],
        context: DownloadContext,
        resolvers: ResolverConfig,
        handlers: FileHandlers,
        activity_logger: ActivityLogger,
    ) -> DownloadResult:
        """Handle download of multiple documents as a zip file"""
        logger.debug("[_handle_multiple_download] Processing %s files", len(documents))

        if not documents:
            raise ValidationError("INVALID_DOCUMENTS: Document list cannot be empty")

        try:
            # Normalize documents
            normalized_docs = [
                resolvers.document_normalizer(doc) for doc in documents if doc
            ]

            # Create zip
            zip_buffer = handlers.create_zip_buffer(
                documents=normalized_docs,
                content_extractor=resolvers.content_extractor,
            )

            # Save zip file
            zip_file_name, zip_file_path = handlers.save_zip_file(
                zip_buffer=zip_buffer,
                project_name=context.project_name,
                file_suffix=context.file_suffix,
            )

            # Log activity
            file_ids = self._extract_document_ids(
                normalized_docs, resolvers.document_id_resolver
            )

            await self._log_activity(
                context=context,
                resolvers=resolvers,
                activity_logger=activity_logger,
                file_ids=file_ids,
            )

            logger.debug("[_handle_multiple_download] Success: %s", zip_file_name)

            return DownloadResult(
                file_name=zip_file_name,
                file_path=zip_file_path,
                created_at=context.created_at,
                media_type="application/zip",
            )

        except ValidationError:
            raise
        except Exception as e:
            error_message = "[_handle_multiple_download] Error: %s" % e
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            raise ProcessingError(
                f"Failed to process multiple downloads: {str(e)}"
            ) from e

    def _extract_document_ids(
        self,
        documents: List[Dict[str, Any]],
        id_resolver: DocumentIdResolver,
    ) -> List[str]:
        """Extract document IDs from normalized documents"""
        resolved_ids: List[str] = []

        for document in documents:
            try:
                resolved_id = id_resolver(document)
                if resolved_id:
                    resolved_ids.append(resolved_id)
            except Exception as e:
                error_message = (
                    "[_extract_document_ids] Failed to resolve document ID: %s" % (e,)
                )
                logger.warning(error_message)
                save_exception_log_sync(
                    e, error_message, __name__, level=LogLevel.WARNING
                )

        return resolved_ids

    async def _log_activity(
        self,
        context: DownloadContext,
        resolvers: ResolverConfig,
        activity_logger: ActivityLogger,
        file_ids: List[str],
    ) -> None:
        """Log download activity"""
        try:
            await activity_logger(
                project_id=context.project_id,
                user_id=context.user_id,
                activity_type=context.activity_type,
                file_ids=file_ids,
                file_type=context.file_type,
            )
        except Exception as e:
            error_message = "[_log_activity] Failed to log activity: %s" % e
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)

            # Don't fail the whole operation if activity logging fails
            pass


# Singleton instance
download_service = DownloadService()
