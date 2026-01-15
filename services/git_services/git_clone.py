"""
Git clone and source code processing functions
"""

import logging
import os

import tempfile
import shutil
import json
import base64
from typing import Optional, Dict, Any, List
from pathlib import Path
from uuid6 import uuid7
from git import Repo, GitCommandError
from app.services.git_services.git_validate import validate_git_branch, validate_git_url
from app.services.git_services.git_service import (
    cleanup_temp_dir,
    get_all_commit_ids,
    clone_repository,
    _prepare_clone_url,
)
from app.services.source_code_service import source_code_service
from app.services.data_management_service import data_management_service
from app.services.project_service import ProjectService
from app.services.data_structures.file_data import FileData
from app.core.database import app_db
from cec_docifycode_common.models.unit_test import UnitTestDesign, UnitTestCode
from cec_docifycode_common.models.detail_design import (
    DetailDesign,
    FILE_NAME_DETAIL_DESIGN,
)
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from app.utils.constants import (
    GitType,
    ProgrammingLanguages,
    ContentType,
    DataManagementType,
    DOCIFYCODE_DIRECTORY_NAME,
    DEFAULT_DIRECTORY_BD_PD,
    BaseSpecification,
)
from app.utils.http_helpers import raise_http_error
from app.utils.helpers import get_current_utc_time
from fastapi import status
from fastapi.exceptions import HTTPException

from app.services.logs_service import save_exception_log_sync, LogLevel

logger = logging.getLogger(__name__)

# Supported source code file extensions
SUPPORTED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".java",
    ".cpp",
    ".c",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".scala",
    ".html",
    ".css",
    ".vue",
    ".jsx",
    ".tsx",
}

# Supported text/document file extensions
TEXT_FILE_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".csv",
}

# Supported image file extensions (only save file name, not content)
IMAGE_FILE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".webp",
    ".svg",
    ".ico",
    ".tiff",
    ".tif",
}

# Supported video file extensions (only save file name, not content)
VIDEO_FILE_EXTENSIONS = {
    ".mp4",
    ".avi",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".mkv",
    ".m4v",
    ".3gp",
    ".ogv",
}


async def clone_code_from_git(
    repository_url: str,
    branch_name: str,
    user_name: Optional[str],
    token_password: Optional[str],
    git_type: Optional[GitType],
    project_id: str,
    source_code_path: Optional[str] = None,
    programming_language: Optional[str] = None,
) -> None:
    """
    Clone Git repository, analyze files in declared folders, and save to respective collections

    Args:
        repository_url: Git repository URL
        branch_name: Branch name to clone
        user_name: Git username (optional)
        token_password: Git token/password (optional)
        git_type: Git type (GitHub/GitBucket)
        project_id: Project ID to associate source code with
        source_code_path: Path within repository to analyze (optional, deprecated - use project directory structure)
        programming_language: Programming language (Python, Java, C#) - only analyze files matching this language

    Raises:
        HTTPException: If validation or cloning fails
    """
    logger.info(
        f"[clone_code_from_git] Start - repository_url={repository_url}, branch_name={branch_name}, project_id={project_id}"
    )

    if not repository_url or not branch_name or not project_id:
        logger.warning("[clone_code_from_git] Missing required parameters")
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    # Validate branch exists
    try:
        await validate_git_branch(
            repository_url=repository_url,
            branch_name=branch_name,
            user_name=user_name,
            token_password=token_password,
        )
        logger.info("[clone_code_from_git] Branch validation passed")
    except Exception as e:
        error_message = f"[clone_code_from_git] Branch validation failed: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise

    # Determine Git type if not provided
    if not git_type:
        git_type = await validate_git_url(repository_url)
        if not git_type:
            logger.warning(
                f"[clone_code_from_git] Invalid URL format - repository_url={repository_url}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST, error_key="GIT_INVALID_URL")

    # Get project directory structure
    project_service = ProjectService()
    project = await project_service.get_project_by_id(project_id)
    if not project:
        logger.warning(
            f"[clone_code_from_git] Project not found - project_id={project_id}"
        )
        raise_http_error(status.HTTP_404_NOT_FOUND, error_key="PROJECT_NOT_FOUND")

    directory = project.setting_item.directory if project.setting_item else None
    base_specification = (
        project.setting_item.base_specification if project.setting_item else None
    )
    if not directory:
        logger.warning(
            f"[clone_code_from_git] Project directory structure not found - project_id={project_id}"
        )
        raise_http_error(
            status.HTTP_400_BAD_REQUEST, error_key="PROJECT_DIRECTORY_NOT_FOUND"
        )

    # Prepare repository URL with credentials if provided
    clone_url = _prepare_clone_url(repository_url, user_name, token_password)

    # Create temporary directory for cloning
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp(prefix="git_clone_")
        logger.info(f"[clone_code_from_git] Created temp directory: {temp_dir}")

        # Clone repository
        repo = clone_repository(clone_url, branch_name, temp_dir, depth=None)

        repo_root = repo.working_dir

        # Check if folders already have files in cloned repo before processing
        # await _check_folders_have_files(repo_root, directory, base_specification)

        # Process each declared folder type
        await _process_all_folders(
            repo_root=repo_root,
            project_id=project_id,
            repo=repo,
            branch_name=branch_name,
            directory=directory,
            programming_language=programming_language,
            repository_url=repository_url,
        )

        # Get all commit IDs from branch and update project
        commit_ids = get_all_commit_ids(repo, branch_name)
        await project_service.update_project_commit_ids(project_id, commit_ids)

        logger.info("[clone_code_from_git] Success")

    except GitCommandError as e:
        error_message = f"[clone_code_from_git] Git command error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_key="GIT_SOURCE_CODE_FETCH_FAILED",
        )
    except HTTPException:
        # Re-raise HTTPException (e.g., 409 from _check_folders_have_files) without wrapping
        raise
    except Exception as e:
        error_message = f"[clone_code_from_git] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_key="GIT_SOURCE_CODE_FETCH_FAILED",
        )
    finally:
        # Clean up temporary directory
        cleanup_temp_dir(
            temp_dir=temp_dir,
            repo=repo,
        )


def _get_repo_name_from_url(repository_url: str) -> str:
    """Extract repository name from repository URL"""
    logger.info(f"[_get_repo_name_from_url] Start - repository_url={repository_url}")

    try:
        if not repository_url:
            return ""

        # Remove protocol and credentials
        url = repository_url
        if "://" in url:
            # Remove protocol (https://, http://)
            url = url.split("://", 1)[1]
            # Remove credentials if present (username:token@)
            if "@" in url:
                url = url.split("@", 1)[1]

        # Remove .git extension if present
        if url.endswith(".git"):
            url = url[:-4]

        # Get the last part (repository name)
        parts = url.split("/")
        repo_name = parts[-1] if parts else ""

        logger.info(f"[_get_repo_name_from_url] Extracted repo_name={repo_name}")
        return repo_name

    except Exception as e:
        error_message = f"[_get_repo_name_from_url] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return ""


async def _process_and_save_files(
    working_dir: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    repo_root: str,
    base_path: Optional[str] = None,
    programming_language: Optional[str] = None,
    exclude_docifycode: bool = False,
    repo_name: Optional[str] = None,
) -> None:
    """Process source code files, analyze code, and save to database"""
    logger.info(
        f"[_process_and_save_files] Start - working_dir={working_dir}, project_id={project_id}, base_path={base_path}, programming_language={programming_language}, exclude_docifycode={exclude_docifycode}, repo_name={repo_name}"
    )

    if not working_dir or not project_id:
        logger.warning("[_process_and_save_files] Missing required parameters")
        return

    try:
        # Find all files (no extension filtering)
        source_files = _find_source_code_files(working_dir, exclude_docifycode)
        logger.info(f"[_process_and_save_files] Found {len(source_files)} files")

        # Dictionary to store file info by folder path
        file_info_by_folder: Dict[str, List[Dict[str, Any]]] = {}
        file_id_mapping: Dict[str, str] = {}  # file_path -> source_code_id

        # Process and save each file
        saved_count = 0
        for file_path in source_files:
            try:
                relative_path = os.path.relpath(file_path, working_dir)
                file_name = os.path.basename(file_path)

                # Normalize path separators
                normalized_path = relative_path.replace("\\", "/")

                # If base_path is provided, prepend it to normalized_path
                if base_path:
                    base_path_normalized = base_path.strip().strip("/")
                    if base_path_normalized:
                        normalized_path = f"{base_path_normalized}/{normalized_path}"

                # Check file extension
                file_ext = Path(file_path).suffix.lower()

                # Only save content for Python, Java, C# files
                # Other files: only save file name, not content
                supported_content_extensions = {".py", ".java", ".cs"}

                if file_ext in supported_content_extensions:
                    # Read file content for supported languages
                    try:
                        with open(file_path, "rb") as f:
                            file_content_bytes = f.read()
                        source_code = file_content_bytes.decode(
                            "utf-8", errors="ignore"
                        )
                        logger.info(
                            f"[_process_and_save_files] Processed file with content - file_path={normalized_path}, file_ext={file_ext}"
                        )
                    except Exception as e:
                        logger.warning(
                            f"[_process_and_save_files] Failed to read file content - file_path={normalized_path}, error={e}"
                        )
                        source_code = None
                else:
                    # Other files: only save file name, not content
                    source_code = None
                    logger.info(
                        f"[_process_and_save_files] Processed file (name only) - file_path={normalized_path}, file_ext={file_ext}, file_name={file_name}"
                    )

                # Check if file extension matches programming_language for code analysis
                should_analyze = False

                # Only analyze code if file has content and matches programming_language
                if source_code and programming_language:
                    language_to_extension = {
                        ProgrammingLanguages.PYTHON: ".py",
                        ProgrammingLanguages.JAVA: ".java",
                        ProgrammingLanguages.CSHARP: ".cs",
                    }
                    expected_extension = language_to_extension.get(programming_language)
                    if expected_extension and file_ext == expected_extension:
                        should_analyze = True
                        logger.info(
                            f"[_process_and_save_files] File matches programming_language - file_ext={file_ext}, language={programming_language}"
                        )
                    else:
                        logger.info(
                            f"[_process_and_save_files] File does not match programming_language - file_ext={file_ext}, language={programming_language}, expected={expected_extension}"
                        )

                # Analyze code using FileData (only for Python, Java, C# and not images)
                classes_list = []
                global_methods_list = []
                if should_analyze:
                    try:
                        file_data = FileData(normalized_path, source_code)
                        code_functions = file_data.get_code_functions_dict()
                        code_classes = file_data.get_code_classes_dict()

                        # Group methods by class
                        methods_by_class: Dict[str, List[Dict[str, Any]]] = {}
                        for func in code_functions:
                            class_id = func.get("class_id") or "root"
                            if class_id not in methods_by_class:
                                methods_by_class[class_id] = []

                            method_dict = {
                                "method_id": func["id"],
                                "method_name": func["name"],
                                "method_content": func["code"],
                            }
                            methods_by_class[class_id].append(method_dict)

                        # Build classes list
                        for cls in code_classes:
                            class_id = cls["id"]
                            class_dict = {
                                "class_id": class_id,
                                "class_name": cls["name"],
                                "class_content": cls["code"],
                                "methods": methods_by_class.get(class_id, []),
                            }
                            classes_list.append(class_dict)

                        # Build global methods list (methods with class_id == "root" or None)
                        root_methods = methods_by_class.get("root", [])
                        global_methods_list = root_methods

                        logger.info(
                            f"[_process_and_save_files] Analyzed code - classes={len(classes_list)}, global_methods={len(global_methods_list)}"
                        )

                    except Exception as e:
                        error_message = f"[_process_and_save_files] Code analysis failed for {normalized_path}: {e}, saving without analysis"
                        logger.warning(error_message)
                        save_exception_log_sync(
                            e, error_message, __name__, level=LogLevel.WARNING
                        )

                else:
                    logger.info(
                        f"[_process_and_save_files] Skipping code analysis for {normalized_path} (file has no content or extension: {file_ext} not supported for analysis)"
                    )

                # Get commit_id for this specific file
                file_commit_id = _get_file_commit_id(
                    repo, branch_name, file_path, repo_root
                )

                # Save using source_code_service
                result = await source_code_service.create_source_code_document(
                    project_id=project_id,
                    file_path=normalized_path,
                    file_name=file_name,
                    source_code=source_code,
                    commit_id=file_commit_id,
                    folder_id=None,  # Will be set later
                    classes=classes_list,
                    global_methods=global_methods_list,
                )

                source_code_id = result["id"]
                file_id_mapping[normalized_path] = source_code_id

                # Group files by folder
                folder_path = os.path.dirname(normalized_path) or ""
                if folder_path not in file_info_by_folder:
                    file_info_by_folder[folder_path] = []
                file_info_by_folder[folder_path].append(
                    {"id": source_code_id, "name": file_name}
                )

                saved_count += 1
                logger.info(
                    f"[_process_and_save_files] Saved file - file_path={normalized_path}, id={source_code_id}"
                )

            except Exception as e:
                error_message = (
                    f"[_process_and_save_files] Error processing file {file_path}: {e}"
                )
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

                continue

        # Create folder structure and save to data_management
        await _create_and_save_folder_structure(
            project_id=project_id,
            file_info_by_folder=file_info_by_folder,
            file_id_mapping=file_id_mapping,
            repo_name=repo_name,
        )

        logger.info(
            f"[_process_and_save_files] Success - saved {saved_count}/{len(source_files)} files"
        )

    except Exception as e:
        error_message = f"[_process_and_save_files] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _create_and_save_folder_structure(
    project_id: str,
    file_info_by_folder: Dict[str, List[Dict[str, Any]]],
    file_id_mapping: Dict[str, str],
    repo_name: Optional[str] = None,
) -> None:
    """Create folder structure and save to data_management"""
    logger.info(f"[_create_and_save_folder_structure] Start - repo_name={repo_name}")

    try:
        # Create folder mapping: folder_path -> folder_id
        folder_mapping: Dict[str, str] = {}
        folders_list = []

        # If repo_name is provided, create root folder with repo name
        repo_folder_id = None
        if repo_name:
            repo_folder_id = str(uuid7())
            folder_mapping["__repo_root__"] = repo_folder_id
            repo_folder_info = {
                "folder_id": repo_folder_id,
                "folder_name": repo_name,
                "parent_folder_id": None,
            }
            folders_list.append(repo_folder_info)
            logger.info(
                f"[_create_and_save_folder_structure] Created repo root folder - repo_name={repo_name}, folder_id={repo_folder_id}"
            )

        # Collect all folder paths including intermediate folders
        all_folder_paths = set()
        for folder_path in file_info_by_folder.keys():
            if folder_path == "":
                continue
            # Add the folder itself
            all_folder_paths.add(folder_path)
            # Add all intermediate parent folders
            path_parts = folder_path.split("/")
            for i in range(1, len(path_parts)):
                intermediate_path = "/".join(path_parts[:i])
                all_folder_paths.add(intermediate_path)

        # Sort folder paths by depth to process parent folders first
        def get_folder_depth(path: str) -> int:
            return len(path.split("/")) if path else 0

        sorted_folders = sorted(
            all_folder_paths, key=lambda x: (get_folder_depth(x), x)
        )

        for folder_path in sorted_folders:
            # Skip creating folder for root files if repo_name exists
            # Root files will be assigned to repo_folder_id directly
            if folder_path == "" and repo_name:
                # Files at root will be assigned to repo_folder_id
                folder_id = repo_folder_id
                folder_mapping[folder_path] = folder_id
                logger.info(
                    f"[_create_and_save_folder_structure] Root files will use repo folder_id={repo_folder_id}"
                )
            else:
                # Generate folder_id for non-root folders
                folder_id = str(uuid7())

                # Get folder name and parent
                if folder_path == "":
                    # Root folder without repo_name (legacy case)
                    folder_name = ""
                    parent_folder_path = None
                    parent_folder_id = None
                else:
                    path_parts = folder_path.split("/")
                    folder_name = path_parts[-1]
                    if len(path_parts) > 1:
                        parent_folder_path = "/".join(path_parts[:-1])
                    else:
                        # Single level folder - parent is repo root if repo_name exists
                        if repo_name:
                            parent_folder_path = "__repo_root__"
                        else:
                            parent_folder_path = ""

                    # Get parent folder_id
                    if parent_folder_path == "__repo_root__":
                        parent_folder_id = repo_folder_id
                    elif parent_folder_path:
                        parent_folder_id = folder_mapping.get(parent_folder_path)
                    else:
                        parent_folder_id = None

                folder_mapping[folder_path] = folder_id

                # Create folder info (only for actual folders, not root files with repo_name)
                folder_info = {
                    "folder_id": folder_id,
                    "folder_name": folder_name,
                    "parent_folder_id": parent_folder_id,
                }
                folders_list.append(folder_info)

            # Get files in this folder
            files_in_folder = file_info_by_folder.get(folder_path, [])

            # Update source_code documents with folder_id
            if files_in_folder:
                source_code_ids = [file_info["id"] for file_info in files_in_folder]
                try:
                    await source_code_service.batch_update_source_code_folder_ids(
                        source_code_ids=source_code_ids, folder_id=folder_id
                    )
                    logger.info(
                        f"[_create_and_save_folder_structure] Updated folder_id for {len(source_code_ids)} files in folder: {folder_path}"
                    )
                except Exception as e:
                    error_message = f"[_create_and_save_folder_structure] Error updating folder_id for folder {folder_path}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

        # Handle root files (folder_path = "") if repo_name exists
        if repo_name and "" in file_info_by_folder:
            root_files = file_info_by_folder.get("", [])
            if root_files:
                source_code_ids = [file_info["id"] for file_info in root_files]
                try:
                    await source_code_service.batch_update_source_code_folder_ids(
                        source_code_ids=source_code_ids, folder_id=repo_folder_id
                    )
                    logger.info(
                        f"[_create_and_save_folder_structure] Updated folder_id for {len(source_code_ids)} root files to repo_folder_id={repo_folder_id}"
                    )
                except Exception as e:
                    error_message = f"[_create_and_save_folder_structure] Error updating folder_id for root files: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

        # Save folder structure to data_management
        if folders_list:
            await data_management_service.create_or_update_data_management(
                project_id=project_id,
                type="source_code",
                folders=folders_list,
            )
            logger.info(
                f"[_create_and_save_folder_structure] Saved {len(folders_list)} folders to data_management"
            )

        logger.info("[_create_and_save_folder_structure] Success")

    except Exception as e:
        error_message = f"[_create_and_save_folder_structure] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _get_file_commit_id(
    repo: Any, branch_name: str, file_path: str, repo_root: str
) -> str:
    """Get commit ID for a specific file using --follow to track file history even if renamed/moved"""
    try:
        relative_path = os.path.relpath(file_path, repo_root).replace("\\", "/")

        # Use git log with --follow to track file history even if renamed/moved
        try:
            commit_id = repo.git.log(
                branch_name,
                "--follow",
                "-1",
                "--format=%H",
                "--",
                relative_path,
            ).strip()

            if commit_id:
                logger.info(
                    f"[_get_file_commit_id] Found commit_id={commit_id} for file={relative_path}"
                )
                return commit_id
        except Exception as e:
            error_message = f"[_get_file_commit_id] Git log with --follow failed for file={relative_path}: {e}, trying iter_commits"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

        # Fallback: try iter_commits (not perfect but ok)
        try:
            commits = list(
                repo.iter_commits(branch_name, paths=relative_path, max_count=1)
            )
            if commits:
                commit_id = commits[0].hexsha
                logger.info(
                    f"[_get_file_commit_id] Found commit_id={commit_id} via iter_commits for file={relative_path}"
                )
                return commit_id
        except Exception as e:
            error_message = f"[_get_file_commit_id] iter_commits also failed for file={relative_path}: {e}"
            logger.warning(error_message)
            save_exception_log_sync(e, error_message, __name__, level=LogLevel.WARNING)

        # Fallback: HEAD (last option)
        commit_id = repo.head.commit.hexsha
        logger.warning(
            f"[_get_file_commit_id] No commit history for file={relative_path}, using HEAD commit={commit_id}"
        )
        return commit_id

    except Exception as e:
        # Fallback to HEAD commit if error
        commit_id = repo.head.commit.hexsha
        logger.error(
            f"[_get_file_commit_id] Error getting commit_id for file={file_path}: {e}, using HEAD commit={commit_id}"
        )
        return commit_id


async def _check_folder_has_files(repo_root: str, folder_path: str) -> bool:
    """Check if folder exists and has files in cloned repo"""
    if not folder_path:
        return False

    full_path = os.path.join(repo_root, folder_path)
    if not os.path.exists(full_path):
        return False

    if not os.path.isdir(full_path):
        return False

    # Check if directory has any files (not just subdirectories)
    try:
        items = os.listdir(full_path)
        for item in items:
            item_path = os.path.join(full_path, item)
            if os.path.isfile(item_path):
                return True
    except (OSError, PermissionError) as e:
        # ERROR HANDLING - Log specific exceptions (OSError, PermissionError, FileNotFoundError)
        # FileNotFoundError, PermissionError are subclasses of OSError
        logger.warning(
            f"[_check_folder_has_files] Error checking folder {full_path}: {type(e).__name__}"
        )
        return False

    return False


async def _check_repo_is_empty(repo_root: str) -> bool:
    """Check if repository is empty (only .git folder exists, no other files)"""
    if not repo_root or not os.path.exists(repo_root):
        return True

    try:
        items = os.listdir(repo_root)
        # Filter out .git directory
        items = [item for item in items if item != ".git"]

        # Check if there are any files (not directories)
        for item in items:
            item_path = os.path.join(repo_root, item)
            if os.path.isfile(item_path):
                return False

        # Check if there are any directories (excluding .git)
        for item in items:
            item_path = os.path.join(repo_root, item)
            if os.path.isdir(item_path):
                # Recursively check if directory has any files
                for root, dirs, files in os.walk(item_path):
                    # Skip .git directories
                    if ".git" in dirs:
                        dirs.remove(".git")
                    if files:
                        return False

        return True
    except Exception as e:
        logger.warning(f"[_check_repo_is_empty] Error checking repo: {e}")
        return False


async def _check_folders_have_files(
    repo_root: str, directory: Any, base_specification: Optional[str] = None
) -> None:
    """
    Check if folders already have files in cloned repo, raise error if any folder has files

    Args:
        repo_root: Root directory of cloned repository
        directory: Project directory structure
        base_specification: Base specification type (REQUIREMENT or SOURCE_CODE)
            - If REQUIREMENT: Check if entire repo is empty (no files at all)
            - If SOURCE_CODE: Only check rd, bd, pd, utd, utc (skip src)
    """
    logger.info(
        f"[_check_folders_have_files] Start - repo_root={repo_root}, base_specification={base_specification}"
    )

    if not repo_root:
        logger.warning("[_check_folders_have_files] Missing repo_root")
        return

    try:
        # If base_specification is REQUIREMENT, check if entire repo is empty
        if base_specification == BaseSpecification.REQUIREMENT:
            logger.info(
                "[_check_folders_have_files] Base specification is REQUIREMENT - checking if entire repo is empty"
            )
            if not await _check_repo_is_empty(repo_root):
                logger.warning(
                    "[_check_folders_have_files] Repository is not empty - files found in repo"
                )
                raise_http_error(
                    status.HTTP_409_CONFLICT, error_key="GIT_FILE_ALREADY_EXISTS"
                )
            logger.info(
                "[_check_folders_have_files] Repository is empty - OK for REQUIREMENT base specification"
            )
            return

        # For SOURCE_CODE or other cases, check specific folders
        folder_checks = [
            (directory.rd, "RD"),
            (directory.bd, "BD"),
            (directory.pd, "PD"),
            (directory.utd, "UTD"),
            (directory.utc, "UTC"),
        ]

        if base_specification == BaseSpecification.SOURCE_CODE:
            logger.info(
                "[_check_folders_have_files] Base specification is SOURCE_CODE - checking only rd, bd, pd, utd, utc (skipping src)"
            )
        else:
            logger.info(
                f"[_check_folders_have_files] Base specification is {base_specification} - checking only rd, bd, pd, utd, utc (default)"
            )

        for folder_path, label in folder_checks:
            if not folder_path:
                continue

            if await _check_folder_has_files(repo_root, folder_path):
                logger.warning(
                    f"[_check_folders_have_files] {label} folder has files - path={folder_path}"
                )
                raise_http_error(
                    status.HTTP_409_CONFLICT, error_key="GIT_FILE_ALREADY_EXISTS"
                )

        logger.info("[_check_folders_have_files] Success - no files found in folders")

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[_check_folders_have_files] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


async def _process_all_folders(
    repo_root: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    directory: Any,
    programming_language: Optional[str] = None,
    repository_url: Optional[str] = None,
) -> None:
    """Process all declared folders and save to respective collections"""
    logger.info(f"[_process_all_folders] Start - project_id={project_id}")

    try:
        # Process rd folder
        if directory.rd:
            await _process_rd_folder(
                repo_root, project_id, repo, branch_name, directory.rd
            )

        # Process bd folder
        if directory.bd:
            await _process_bd_folder(
                repo_root, project_id, repo, branch_name, directory.bd
            )

        # Process pd folder and subfolders
        if directory.pd:
            await _process_pd_folder(
                repo_root, project_id, repo, branch_name, directory.pd, directory
            )

        # Process src folder
        if directory.src:
            await _process_src_folder(
                repo_root,
                project_id,
                repo,
                branch_name,
                directory.src,
                programming_language,
                repository_url,
            )

        # Process utd folder (after src to enable linking)
        if directory.utd:
            await _process_utd_folder(
                repo_root, project_id, repo, branch_name, directory.utd, directory.src
            )

        # Process utc folder (after src to enable linking)
        if directory.utc:
            await _process_utc_folder(
                repo_root, project_id, repo, branch_name, directory.utc, directory.src
            )

        logger.info("[_process_all_folders] Success")

    except Exception as e:
        error_message = f"[_process_all_folders] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _normalize_path(path: str, repo_root: str) -> str:
    """Normalize path relative to repo root with case-insensitive matching on Windows"""
    logger.info(f"[_normalize_path] Start - path={path}, repo_root={repo_root}")

    if not path:
        return ""
    path = path.strip().strip("/")
    if not path:
        return ""

    # Try exact path first
    full_path = os.path.join(repo_root, path)
    if os.path.exists(full_path):
        logger.info(f"[_normalize_path] Found exact path: {full_path}")
        return full_path

    # Try case-insensitive match on Windows
    if os.name == "nt":
        path_parts = path.split("/")
        current_path = repo_root

        for part in path_parts:
            if not part:
                continue
            found = False
            if os.path.exists(current_path):
                for item in os.listdir(current_path):
                    if item.lower() == part.lower():
                        current_path = os.path.join(current_path, item)
                        found = True
                        break
            if not found:
                logger.warning(
                    f"[_normalize_path] Path part not found: {part} in {current_path}"
                )
                return ""
        logger.info(f"[_normalize_path] Found case-insensitive path: {current_path}")
        return current_path

    logger.warning(f"[_normalize_path] Path not found: {path}")
    return ""


def _find_all_files(directory: str) -> List[str]:
    """Find all files in directory"""
    logger.info(f"[_find_all_files] Start - directory={directory}")

    files = []
    if not directory or not os.path.exists(directory):
        logger.warning(f"[_find_all_files] Directory does not exist: {directory}")
        return files

    try:
        for root, dirs, filenames in os.walk(directory):
            # Skip .git directory
            if ".git" in dirs:
                dirs.remove(".git")

            for filename in filenames:
                file_path = os.path.join(root, filename)
                files.append(file_path)

        logger.info(f"[_find_all_files] Found {len(files)} files")
        return files

    except Exception as e:
        error_message = f"[_find_all_files] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return files


async def _process_rd_folder(
    repo_root: str, project_id: str, repo: Any, branch_name: str, rd_path: str
) -> None:
    """Process requirement document folder"""
    logger.info(f"[_process_rd_folder] Start - rd_path={rd_path}")

    try:
        folder_path = _normalize_path(rd_path, repo_root)
        if not folder_path:
            logger.warning(f"[_process_rd_folder] Path not found: {rd_path}")
            return

        files = _find_all_files(folder_path)
        repository = app_db.requirement_document_repository

        file_info_by_folder: Dict[str, List[Dict[str, Any]]] = {}

        for file_path in files:
            try:
                relative_path = os.path.relpath(file_path, repo_root)
                normalized_path = relative_path.replace("\\", "/")
                file_name = os.path.basename(file_path)

                # Get commit_id for this specific file
                file_commit_id = _get_file_commit_id(
                    repo, branch_name, file_path, repo_root
                )

                # Read file content
                with open(file_path, "rb") as f:
                    file_content = f.read()

                # Create requirement document
                doc_id = str(uuid7())
                current_time = get_current_utc_time()

                # Check file extension to determine content type
                file_ext = Path(file_path).suffix.lower()
                if file_ext == ".md":
                    # Markdown: decode to string
                    rd_content = {
                        "type": ContentType.MARKDOWN,
                        "content": file_content.decode("utf-8", errors="ignore"),
                    }
                    logger.info(
                        f"[_process_rd_folder] Processed markdown file - file_path={normalized_path}, file_ext={file_ext}"
                    )
                else:
                    # Image: encode to base64
                    encoded_content = base64.b64encode(file_content).decode("utf-8")
                    rd_content = {
                        "type": ContentType.IMAGE,
                        "content": encoded_content,
                    }
                    logger.info(
                        f"[_process_rd_folder] Processed image file - file_path={normalized_path}, file_ext={file_ext}"
                    )

                document = {
                    "id": doc_id,
                    "project_id": project_id,
                    "file_name": file_name,
                    "rd_content": rd_content,
                    "commit_id": file_commit_id,
                    "sync_status": "synced",
                    "created_at": current_time,
                    "updated_at": current_time,
                    "deleted_at": None,
                }

                await repository.insert_one(document)

                # Group by folder for data_management
                folder_path_str = os.path.dirname(normalized_path) or ""
                if folder_path_str not in file_info_by_folder:
                    file_info_by_folder[folder_path_str] = []
                file_info_by_folder[folder_path_str].append(
                    {"id": doc_id, "name": file_name}
                )

                logger.info(
                    f"[_process_rd_folder] Saved file - file_path={normalized_path}, id={doc_id}"
                )

            except Exception as e:
                error_message = (
                    f"[_process_rd_folder] Error processing file {file_path}: {e}"
                )
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

                continue

        # Note: data_management is not created for requirement_document
        logger.info("[_process_rd_folder] Success")

    except Exception as e:
        error_message = f"[_process_rd_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _process_bd_folder(
    repo_root: str, project_id: str, repo: Any, branch_name: str, bd_path: str
) -> None:
    """Process basic design folder - only process FILE_NAME_BASIC_DESIGN file"""
    logger.info(f"[_process_bd_folder] Start - bd_path={bd_path}")

    try:
        folder_path = _normalize_path(bd_path, repo_root)
        if not folder_path:
            logger.warning(f"[_process_bd_folder] Path not found: {bd_path}")
            return

        # Only look for FILE_NAME_BASIC_DESIGN file
        basic_design_file_path = os.path.join(folder_path, FILE_NAME_BASIC_DESIGN)

        if not os.path.exists(basic_design_file_path):
            logger.warning(
                f"[_process_bd_folder] File {FILE_NAME_BASIC_DESIGN} not found in {bd_path}"
            )
            return

        if not os.path.isfile(basic_design_file_path):
            logger.warning(
                f"[_process_bd_folder] {FILE_NAME_BASIC_DESIGN} is not a file in {bd_path}"
            )
            return

        repository = app_db.basic_design_repository

        try:
            relative_path = os.path.relpath(basic_design_file_path, repo_root)
            normalized_path = relative_path.replace("\\", "/")

            # Get commit_id for this specific file
            file_commit_id = _get_file_commit_id(
                repo, branch_name, basic_design_file_path, repo_root
            )

            # Read file content as JSON
            with open(basic_design_file_path, "r", encoding="utf-8") as f:
                try:
                    contents = json.load(f)
                    if not isinstance(contents, dict):
                        logger.warning(
                            f"[_process_bd_folder] File {FILE_NAME_BASIC_DESIGN} does not contain a JSON object, using empty dict"
                        )
                        contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_process_bd_folder] Failed to parse JSON from {FILE_NAME_BASIC_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}

            # Create basic design document
            doc_id = str(uuid7())
            current_time = get_current_utc_time()

            document = {
                "id": doc_id,
                "project_id": project_id,
                "contents": contents,
                "commit_id": file_commit_id,
                "sync_status": "synced",
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            await repository.insert_one(document)

            logger.info(
                f"[_process_bd_folder] Saved file - file_path={normalized_path}, id={doc_id}"
            )

        except Exception as e:
            error_message = f"[_process_bd_folder] Error processing file {basic_design_file_path}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            raise

        logger.info("[_process_bd_folder] Success")

    except Exception as e:
        error_message = f"[_process_bd_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _process_pd_folder(
    repo_root: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    pd_path: str,
    directory: Any,
) -> None:
    """Process detail design folder - only process FILE_NAME_DETAIL_DESIGN file"""
    logger.info(f"[_process_pd_folder] Start - pd_path={pd_path}")

    try:
        pd_folder_path = _normalize_path(pd_path, repo_root)
        if not pd_folder_path:
            logger.warning(f"[_process_pd_folder] Path not found: {pd_path}")
            return

        # Only look for FILE_NAME_DETAIL_DESIGN file
        detail_design_file_path = os.path.join(pd_folder_path, FILE_NAME_DETAIL_DESIGN)

        if not os.path.exists(detail_design_file_path):
            logger.warning(
                f"[_process_pd_folder] File {FILE_NAME_DETAIL_DESIGN} not found in {pd_path}"
            )
            return

        if not os.path.isfile(detail_design_file_path):
            logger.warning(
                f"[_process_pd_folder] {FILE_NAME_DETAIL_DESIGN} is not a file in {pd_path}"
            )
            return

        try:
            relative_path = os.path.relpath(detail_design_file_path, repo_root)
            normalized_path = relative_path.replace("\\", "/")

            # Get commit_id for this specific file
            file_commit_id = _get_file_commit_id(
                repo, branch_name, detail_design_file_path, repo_root
            )

            # Read file content as JSON
            with open(detail_design_file_path, "r", encoding="utf-8") as f:
                try:
                    contents = json.load(f)
                    if not isinstance(contents, dict):
                        logger.warning(
                            f"[_process_pd_folder] File {FILE_NAME_DETAIL_DESIGN} does not contain a JSON object, using empty dict"
                        )
                        contents = {}
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"[_process_pd_folder] Failed to parse JSON from {FILE_NAME_DETAIL_DESIGN}: {e}, using empty dict"
                    )
                    contents = {}

            # Create DetailDesign document
            detail_design_id = str(uuid7())
            current_time = get_current_utc_time()

            detail_design = {
                "id": detail_design_id,
                "project_id": project_id,
                "contents": contents,
                "commit_id": file_commit_id,
                "sync_status": "synced",
                "created_at": current_time,
                "updated_at": current_time,
                "deleted_at": None,
            }

            await app_db.detail_design_repository.insert_one(detail_design)

            logger.info(
                f"[_process_pd_folder] Saved file - file_path={normalized_path}, id={detail_design_id}"
            )

        except Exception as e:
            error_message = f"[_process_pd_folder] Error processing file {detail_design_file_path}: {e}"
            logger.error(error_message)
            save_exception_log_sync(e, error_message, __name__)
            raise

        logger.info("[_process_pd_folder] Success")

    except Exception as e:
        error_message = f"[_process_pd_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _process_src_folder(
    repo_root: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    src_path: str,
    programming_language: Optional[str] = None,
    repository_url: Optional[str] = None,
) -> None:
    """Process source code folder with code analysis"""
    logger.info(f"[_process_src_folder] Start - src_path={src_path}")

    try:
        # Check if src_path is "/" or empty (root directory)
        normalized_src_path = src_path.strip().strip("/") if src_path else ""
        is_root_path = not normalized_src_path or normalized_src_path == ""

        # Get repo name if processing root directory
        repo_name = ""
        if is_root_path and repository_url:
            repo_name = _get_repo_name_from_url(repository_url)
            logger.info(f"[_process_src_folder] Root directory - repo_name={repo_name}")

        if is_root_path:
            # Use repo_root directly when src_path is "/"
            folder_path = repo_root
            base_path = ""
            logger.info(
                f"[_process_src_folder] Processing root directory - folder_path={folder_path}"
            )
        else:
            # Normalize path for non-root directories
            folder_path = _normalize_path(src_path, repo_root)
            if not folder_path:
                logger.warning(f"[_process_src_folder] Path not found: {src_path}")
                return
            base_path = src_path

        # Use existing _process_and_save_files logic
        await _process_and_save_files(
            working_dir=folder_path,
            project_id=project_id,
            repo=repo,
            branch_name=branch_name,
            repo_root=repo_root,
            base_path=base_path,
            programming_language=programming_language,
            exclude_docifycode=is_root_path,
            repo_name=repo_name,
        )

        logger.info("[_process_src_folder] Success")

    except Exception as e:
        error_message = f"[_process_src_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def link_unit_test_to_source_code(
    project_id: str,
    unit_test_path: str,
    unit_test_file_name: str,
    src_base_path: str,
    utd_utc_base_path: str,
) -> Dict[str, str]:
    """
    Link unit test to source code based on path and file name

    Args:
        project_id: Project ID
        unit_test_path: Full path of unit test file (e.g., "UTC/config/settings.py/get_settings.py")
        unit_test_file_name: File name without extension (e.g., "get_settings")
        src_base_path: Base path for source code (e.g., "src" or "SRC")
        utd_utc_base_path: Base path for unit test (e.g., "UTC" or "UTD")

    Returns:
        Dictionary with source_code_id, class_id, method_id (empty strings if not found)
    """
    logger.info(
        f"[link_unit_test_to_source_code] Start - unit_test_path={unit_test_path}, file_name={unit_test_file_name}"
    )

    result = {
        "source_code_id": "",
        "class_id": "",
        "method_id": "",
    }

    try:
        # Extract source code path from unit test path
        # Example: UTC/config/settings.py/get_settings.py -> src/config/settings.py
        # Remove the base folder (UTC/UTD) and the last file name
        normalized_unit_test_path = unit_test_path.replace("\\", "/")
        path_parts = normalized_unit_test_path.split("/")

        # Find index of base folder (UTC/UTD) - case insensitive
        base_folder_index = -1
        for i, part in enumerate(path_parts):
            if part.upper() in ["UTC", "UTD"]:
                base_folder_index = i
                break

        if base_folder_index == -1:
            logger.warning(
                f"[link_unit_test_to_source_code] Cannot find UTC/UTD base folder in path: {unit_test_path}"
            )
            return result

        # Get path after base folder (e.g., "config/settings.py/get_settings.py")
        path_after_base = "/".join(path_parts[base_folder_index + 1 :])

        # Remove the last file name to get source code relative path
        # "config/settings.py/get_settings.py" -> "config/settings.py"
        path_after_base_parts = path_after_base.split("/")
        if len(path_after_base_parts) > 1:
            source_code_relative_path = "/".join(path_after_base_parts[:-1])
        else:
            # If only one part, it's the file name, so no relative path
            source_code_relative_path = ""

        # Build source code path: src + relative path
        if source_code_relative_path:
            source_code_path = f"{src_base_path}/{source_code_relative_path}"
        else:
            source_code_path = src_base_path
        source_code_path = source_code_path.replace("\\", "/")

        # Find source code by file_path
        source_code_collection = (
            await source_code_service.repository._resolve_collection()
        )
        source_code_doc = await source_code_collection.find_one(
            {
                "project_id": project_id,
                "file_path": source_code_path,
                "$or": [
                    {"deleted_at": None},
                    {"deleted_at": {"$exists": False}},
                ],
            }
        )

        if not source_code_doc:
            logger.warning(
                f"[link_unit_test_to_source_code] Source code not found - path={source_code_path}"
            )
            return result

        source_code_id = source_code_doc.get("id") or str(
            source_code_doc.get("_id", "")
        )
        result["source_code_id"] = source_code_id

        # Parse file name to extract class and method
        # Format: "class-method" or just "method" (for global methods)
        file_name_parts = unit_test_file_name.split("-")

        if len(file_name_parts) >= 2:
            # Has class and method: "class-method"
            class_name = file_name_parts[0]
            method_name = "-".join(
                file_name_parts[1:]
            )  # In case method name has dashes

            # Find class in the source_code_doc found above (not searching all source_code)
            classes = source_code_doc.get("classes", [])
            for class_item in classes:
                if class_item.get("class_name") == class_name:
                    class_id = class_item.get("class_id", "")
                    result["class_id"] = class_id

                    # Find method in the class found above
                    methods = class_item.get("methods", [])
                    for method_item in methods:
                        if method_item.get("method_name") == method_name:
                            method_id = method_item.get("method_id", "")
                            result["method_id"] = method_id
                            logger.info(
                                f"[link_unit_test_to_source_code] Found link - source_code_id={source_code_id}, class_id={class_id}, method_id={method_id}"
                            )
                            return result

                    logger.warning(
                        f"[link_unit_test_to_source_code] Method '{method_name}' not found in class '{class_name}' of source_code {source_code_id}"
                    )
                    return result

            logger.warning(
                f"[link_unit_test_to_source_code] Class '{class_name}' not found in source_code {source_code_id}"
            )
        else:
            # Only method name (global method): "method"
            method_name = unit_test_file_name

            # Find in global methods of the source_code_doc found above (not searching all source_code)
            global_methods = source_code_doc.get("global_methods", [])
            for method_item in global_methods:
                if method_item.get("method_name") == method_name:
                    method_id = method_item.get("method_id", "")
                    result["method_id"] = method_id
                    logger.info(
                        f"[link_unit_test_to_source_code] Found global method link - source_code_id={source_code_id}, method_id={method_id}"
                    )
                    return result

            logger.warning(
                f"[link_unit_test_to_source_code] Global method '{method_name}' not found in source_code {source_code_id}"
            )

        return result

    except Exception as e:
        error_message = f"[link_unit_test_to_source_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return result


async def _process_utd_folder(
    repo_root: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    utd_path: str,
    src_path: Optional[str] = None,
) -> None:
    """Process unit test design folder"""
    logger.info(f"[_process_utd_folder] Start - utd_path={utd_path}")

    try:
        folder_path = _normalize_path(utd_path, repo_root)
        if not folder_path:
            logger.warning(f"[_process_utd_folder] Path not found: {utd_path}")
            return

        files = _find_all_files(folder_path)

        # Get base folder name (utd or utc)
        base_folder_name = os.path.basename(folder_path.rstrip("/\\")) or "utd"

        file_info_by_folder: Dict[str, List[Dict[str, Any]]] = {}

        # First pass: collect files and group by folder (relative to utd/utc folder)
        for file_path in files:
            try:
                relative_path = os.path.relpath(file_path, repo_root)
                normalized_path = relative_path.replace("\\", "/")
                file_name = os.path.basename(file_path)

                # Calculate folder path relative to utd/utc folder (not repo_root)
                folder_path_relative_to_base = os.path.relpath(
                    os.path.dirname(file_path), folder_path
                )
                if folder_path_relative_to_base == ".":
                    folder_path_str = ""
                else:
                    folder_path_str = folder_path_relative_to_base.replace("\\", "/")

                if folder_path_str not in file_info_by_folder:
                    file_info_by_folder[folder_path_str] = []
                file_info_by_folder[folder_path_str].append(
                    {
                        "file_path": file_path,
                        "normalized_path": normalized_path,
                        "file_name": file_name,
                    }
                )

            except Exception as e:
                error_message = (
                    f"[_process_utd_folder] Error processing file {file_path}: {e}"
                )
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

                continue

        # Create folder structure and get folder_id mapping
        folder_id_mapping: Dict[str, str] = {}
        if file_info_by_folder:
            folder_id_mapping = await _create_folder_structure_and_get_mapping(
                project_id,
                DataManagementType.UNIT_TEST_DESIGN,
                file_info_by_folder,
                base_folder_name,
            )

        # Second pass: create documents with folder_id
        for folder_path_str, file_list in file_info_by_folder.items():
            folder_id = folder_id_mapping.get(folder_path_str)

            for file_info in file_list:
                try:
                    file_path = file_info["file_path"]
                    normalized_path = file_info["normalized_path"]
                    file_name = file_info["file_name"]

                    # Get commit_id for this specific file
                    file_commit_id = _get_file_commit_id(
                        repo, branch_name, file_path, repo_root
                    )

                    # Read file content as binary
                    with open(file_path, "rb") as f:
                        file_content_bytes = f.read()

                    # Create unit test design document
                    doc_id = str(uuid7())
                    current_time = get_current_utc_time()
                    file_ext = Path(file_path).suffix.lower()

                    # Check if file is image - if yes, encode to base64, otherwise decode to string
                    if file_ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]:
                        # Image: encode to base64
                        unit_test_design_json = base64.b64encode(
                            file_content_bytes
                        ).decode("utf-8")
                        logger.info(
                            f"[_process_utd_folder] Processed image file - file_path={normalized_path}, file_ext={file_ext}"
                        )
                    else:
                        # Text file: decode to string
                        file_content = file_content_bytes.decode(
                            "utf-8", errors="ignore"
                        )
                        # Try to parse as JSON and format with indentation, otherwise use as string
                        try:
                            parsed_json = json.loads(file_content)
                            unit_test_design_json = json.dumps(
                                parsed_json, indent=2, ensure_ascii=False
                            )
                        except json.JSONDecodeError as e:
                            logger.warning(
                                f"[_process_utd_folder] Failed to parse JSON in file {normalized_path}: {e}"
                            )
                            unit_test_design_json = file_content
                        logger.info(
                            f"[_process_utd_folder] Processed text file - file_path={normalized_path}, file_ext={file_ext}"
                        )

                    # Link to source code based on path
                    file_name_without_ext = Path(file_name).stem
                    link_result = {
                        "source_code_id": "",
                        "class_id": "",
                        "method_id": "",
                    }

                    if src_path:
                        link_result = await link_unit_test_to_source_code(
                            project_id=project_id,
                            unit_test_path=normalized_path,
                            unit_test_file_name=file_name_without_ext,
                            src_base_path=src_path,
                            utd_utc_base_path=utd_path,
                        )

                    document = {
                        "id": doc_id,
                        "project_id": project_id,
                        "source_code_id": link_result.get("source_code_id", ""),
                        "class_id": link_result.get("class_id", ""),
                        "method_id": link_result.get("method_id", ""),
                        "file_name": file_name,
                        "file_path": normalized_path,
                        "folder_id": folder_id,  # ID của folder cha
                        "collection_name": UnitTestDesign.Config.collection_name,
                        "unit_test_design_json": unit_test_design_json,
                        "decision_table": None,
                        "test_pattern": None,
                        "commit_id": file_commit_id,
                        "sync_status": "synced",
                        "created_at": current_time,
                        "updated_at": current_time,
                        "deleted_at": None,
                    }

                    await app_db.unit_test_repository.insert_unit_test(document)

                    logger.info(
                        f"[_process_utd_folder] Saved file - file_path={normalized_path}, folder_id={folder_id}, id={doc_id}"
                    )

                except Exception as e:
                    error_message = f"[_process_utd_folder] Error creating document for {file_path}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

                    continue

        logger.info("[_process_utd_folder] Success")

    except Exception as e:
        error_message = f"[_process_utd_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _process_utc_folder(
    repo_root: str,
    project_id: str,
    repo: Any,
    branch_name: str,
    utc_path: str,
    src_path: Optional[str] = None,
) -> None:
    """Process unit test code folder"""
    logger.info(f"[_process_utc_folder] Start - utc_path={utc_path}")

    try:
        folder_path = _normalize_path(utc_path, repo_root)
        if not folder_path:
            logger.warning(f"[_process_utc_folder] Path not found: {utc_path}")
            return

        files = _find_all_files(folder_path)

        # Get base folder name (utd or utc)
        base_folder_name = os.path.basename(folder_path.rstrip("/\\")) or "utc"

        file_info_by_folder: Dict[str, List[Dict[str, Any]]] = {}

        # First pass: collect files and group by folder (relative to utd/utc folder)
        for file_path in files:
            try:
                relative_path = os.path.relpath(file_path, repo_root)
                normalized_path = relative_path.replace("\\", "/")
                file_name = os.path.basename(file_path)

                # Calculate folder path relative to utd/utc folder (not repo_root)
                folder_path_relative_to_base = os.path.relpath(
                    os.path.dirname(file_path), folder_path
                )
                if folder_path_relative_to_base == ".":
                    folder_path_str = ""
                else:
                    folder_path_str = folder_path_relative_to_base.replace("\\", "/")

                if folder_path_str not in file_info_by_folder:
                    file_info_by_folder[folder_path_str] = []
                file_info_by_folder[folder_path_str].append(
                    {
                        "file_path": file_path,
                        "normalized_path": normalized_path,
                        "file_name": file_name,
                    }
                )

            except Exception as e:
                error_message = (
                    f"[_process_utc_folder] Error processing file {file_path}: {e}"
                )
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)

                continue

        # Create folder structure and get folder_id mapping
        folder_id_mapping: Dict[str, str] = {}
        if file_info_by_folder:
            folder_id_mapping = await _create_folder_structure_and_get_mapping(
                project_id,
                DataManagementType.UNIT_TEST_CODE,
                file_info_by_folder,
                base_folder_name,
            )

        # Second pass: create documents with folder_id
        for folder_path_str, file_list in file_info_by_folder.items():
            folder_id = folder_id_mapping.get(folder_path_str)

            for file_info in file_list:
                try:
                    file_path = file_info["file_path"]
                    normalized_path = file_info["normalized_path"]
                    file_name = file_info["file_name"]

                    # Get commit_id for this specific file
                    file_commit_id = _get_file_commit_id(
                        repo, branch_name, file_path, repo_root
                    )

                    # Read file content as binary
                    with open(file_path, "rb") as f:
                        file_content_bytes = f.read()

                    # Create unit test code document
                    doc_id = str(uuid7())
                    current_time = get_current_utc_time()
                    file_ext = Path(file_path).suffix.lower()

                    # Check if file is image - if yes, encode to base64, otherwise decode to string
                    if file_ext in [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"]:
                        # Image: encode to base64
                        ut_code_content = base64.b64encode(file_content_bytes).decode(
                            "utf-8"
                        )
                        logger.info(
                            f"[_process_utc_folder] Processed image file - file_path={normalized_path}, file_ext={file_ext}"
                        )
                    else:
                        # Text file: decode to string
                        ut_code_content = file_content_bytes.decode(
                            "utf-8", errors="ignore"
                        )
                        logger.info(
                            f"[_process_utc_folder] Processed text file - file_path={normalized_path}, file_ext={file_ext}"
                        )

                    # Link to source code based on path
                    file_name_without_ext = Path(file_name).stem
                    link_result = {
                        "source_code_id": "",
                        "class_id": "",
                        "method_id": "",
                    }

                    if src_path:
                        link_result = await link_unit_test_to_source_code(
                            project_id=project_id,
                            unit_test_path=normalized_path,
                            unit_test_file_name=file_name_without_ext,
                            src_base_path=src_path,
                            utd_utc_base_path=utc_path,
                        )

                    document = {
                        "id": doc_id,
                        "project_id": project_id,
                        "source_code_id": link_result.get("source_code_id", ""),
                        "class_id": link_result.get("class_id", ""),
                        "method_id": link_result.get("method_id", ""),
                        "file_name": file_name,
                        "file_path": normalized_path,
                        "folder_id": folder_id,  # ID của folder cha
                        "collection_name": UnitTestCode.Config.collection_name,
                        "ut_code_content": ut_code_content,
                        "commit_id": file_commit_id,
                        "sync_status": "synced",
                        "created_at": current_time,
                        "updated_at": current_time,
                        "deleted_at": None,
                    }

                    await app_db.unit_test_repository.insert_unit_test(document)

                    logger.info(
                        f"[_process_utc_folder] Saved file - file_path={normalized_path}, folder_id={folder_id}, id={doc_id}"
                    )

                except Exception as e:
                    error_message = f"[_process_utc_folder] Error creating document for {file_path}: {e}"
                    logger.error(error_message)
                    save_exception_log_sync(e, error_message, __name__)

                    continue

        logger.info("[_process_utc_folder] Success")

    except Exception as e:
        error_message = f"[_process_utc_folder] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _create_folder_structure_and_get_mapping(
    project_id: str,
    data_type: str,
    file_info_by_folder: Dict[str, List[Dict[str, Any]]],
    base_folder_name: str = "",
) -> Dict[str, str]:
    """Create folder structure in data_management and return folder_id mapping"""
    logger.info(
        f"[_create_folder_structure_and_get_mapping] Start - project_id={project_id}, type={data_type}, base_folder_name={base_folder_name}"
    )

    try:
        folders_list = []
        folder_mapping: Dict[str, str] = {}

        # Add root folder (utd/utc) first
        root_folder_id = str(uuid7())
        folder_mapping[""] = root_folder_id  # Empty string represents root
        root_folder_info = {
            "folder_id": root_folder_id,
            "folder_name": base_folder_name or "",
            "parent_folder_id": None,
        }
        folders_list.append(root_folder_info)

        # Collect all folder paths including intermediate folders
        all_folder_paths = set()
        for folder_path in file_info_by_folder.keys():
            if folder_path == "":
                continue
            # Add the folder itself
            all_folder_paths.add(folder_path)
            # Add all intermediate parent folders
            path_parts = folder_path.split("/")
            for i in range(1, len(path_parts)):
                intermediate_path = "/".join(path_parts[:i])
                all_folder_paths.add(intermediate_path)

        # Sort folder paths by depth (process parent folders first)
        def get_folder_depth(path: str) -> int:
            return len(path.split("/")) if path else 0

        sorted_folders = sorted(
            all_folder_paths, key=lambda x: (get_folder_depth(x), x)
        )

        for folder_path in sorted_folders:
            folder_id = str(uuid7())
            path_parts = folder_path.split("/")
            folder_name = path_parts[-1]
            parent_folder_path = (
                "/".join(path_parts[:-1]) if len(path_parts) > 1 else ""
            )

            # Parent folder_id: if parent is root, use root_folder_id, otherwise get from mapping
            if parent_folder_path == "":
                parent_folder_id = root_folder_id
            else:
                parent_folder_id = folder_mapping.get(parent_folder_path)
                if not parent_folder_id:
                    logger.warning(
                        f"[_create_folder_structure_and_get_mapping] Parent folder not found: {parent_folder_path}"
                    )

            folder_mapping[folder_path] = folder_id

            folder_info = {
                "folder_id": folder_id,
                "folder_name": folder_name,
                "parent_folder_id": parent_folder_id,
            }

            folders_list.append(folder_info)

        # Save to data_management
        if folders_list:
            await data_management_service.create_or_update_data_management(
                project_id=project_id, type=data_type, folders=folders_list
            )
            logger.info(
                f"[_create_folder_structure_and_get_mapping] Saved {len(folders_list)} folders to data_management for type={data_type}"
            )

        logger.info("[_create_folder_structure_and_get_mapping] Success")
        return folder_mapping

    except Exception as e:
        error_message = f"[_create_folder_structure_and_get_mapping] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


async def _create_data_management_for_files(
    project_id: str,
    data_type: str,
    file_info_by_folder: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Create or update data_management for files"""
    logger.info(
        f"[_create_data_management_for_files] Start - project_id={project_id}, type={data_type}"
    )

    try:
        await _create_folder_structure_and_get_mapping(
            project_id, data_type, file_info_by_folder
        )

    except Exception as e:
        error_message = f"[_create_data_management_for_files] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


def _find_source_code_files(directory: str, exclude_docifycode: bool = False) -> list:
    """Find all files in directory (no extension filtering - processes all files)"""
    logger.info(
        f"[_find_source_code_files] Start - directory={directory}, exclude_docifycode={exclude_docifycode}"
    )

    source_files = []

    try:
        for root, dirs, files in os.walk(directory):
            # Skip .git directory
            if ".git" in dirs:
                dirs.remove(".git")

            # Always skip DOCIFYCODE_DIRECTORY_NAME and DEFAULT_DIRECTORY_BD_PD folders
            # when processing source code in src directory
            dirs_to_remove = []
            for d in dirs:
                # Skip DOCIFYCODE_DIRECTORY_NAME (case-insensitive)
                if d.lower() == DOCIFYCODE_DIRECTORY_NAME.lower():
                    dirs_to_remove.append(d)
                    logger.info(
                        f"[_find_source_code_files] Excluded {DOCIFYCODE_DIRECTORY_NAME} directory: {os.path.join(root, d)}"
                    )
                # Skip DEFAULT_DIRECTORY_BD_PD (case-insensitive)
                elif d.lower() == DEFAULT_DIRECTORY_BD_PD.lower():
                    dirs_to_remove.append(d)
                    logger.info(
                        f"[_find_source_code_files] Excluded {DEFAULT_DIRECTORY_BD_PD} directory: {os.path.join(root, d)}"
                    )

            # Remove excluded directories
            for d in dirs_to_remove:
                dirs.remove(d)

            for file in files:
                file_path = os.path.join(root, file)
                # Include all files (no extension filtering)
                source_files.append(file_path)

        logger.info(f"[_find_source_code_files] Found {len(source_files)} files")
        return source_files

    except Exception as e:
        error_message = f"[_find_source_code_files] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        return source_files
