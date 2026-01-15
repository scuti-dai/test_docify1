"""
Unit Test API endpoints
"""

import logging
from dataclasses import asdict
import os
from fastapi import APIRouter, Depends, HTTPException, Request, Path, Body, status
from app.services.git_services import git_validate
from cec_docifycode_common.models.enums import GenerationTarget
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.schemas.unit_test import (
    UnitTestCreate,
    UnitTestResponse,
    GenerateUnitTestRequest,
    GenerateUnitTestCodeRequest,
)
from app.schemas.base import BaseResponse, ConflictResponse
from app.schemas.source_code import UnitTestSpecPushRequest, UnitTestCodePushRequest
from app.services.project_service import project_service
from app.services.git_services.git_service import github_files_exist
from app.services.source_code_service import source_code_service
from app.services.unit_test_service import unit_test_service
from app.services.mqtt_service import mqtt_service
from app.api.deps import get_current_user
from app.utils.constants import DEFAULT_DIRECTORY_BD_PD, TASK_REGISTRATION_TOPIC
from app.utils.http_helpers import raise_http_error
from app.schemas.source_code import (
    FolderResponse,
    UnitTestDesignDetailResponse,
    UnitTestCodeDetailResponse,
    UnitTestDesignUpdateContentRequest,
    UnitTestCodeUpdateContentRequest,
    UnitTestDesignListResponse,
    UnitTestDesignListData,
    UnitTestCodeListResponse,
    UnitTestCodeListData,
    UnitTestFileBase,
    SourceCodeDownloadRequest,
)
from app.utils.constants import PermissionLevel
from app.utils.download_utils import build_download_file_response


from app.services.logs_service import save_exception_log_sync
from cec_docifycode_common import UNIT_TEST_DESIGN_COLLECTION
from cec_docifycode_common.models.basic_design import FILE_NAME_BASIC_DESIGN
from cec_docifycode_common.models.detail_design import FILE_NAME_DETAIL_DESIGN

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# #region Git Push Endpoints
@router.post(
    "/{project_id}/utd/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_utd(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: UnitTestSpecPushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push unit test spec files to Git repository"""
    logger.info(
        f"[push_git_unit_test_spec] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_unit_test_spec] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        result = await unit_test_service.push_git_unit_test_spec(
            project_id=project_id,
            file_ids=push_request.id,
            commit_message=push_request.commit_message,
            user_name=push_request.user_name,
            token_password=push_request.token_password,
            user_id=user_id,
        )

        return BaseResponse(
            statusCode=200,
            message="Files pushed to Git successfully",
            data=result,
        )

    except HTTPException as e:
        if e.status_code == 409:
            raise
        raise
    except Exception as e:
        error_message = f"[push_git_unit_test_spec] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post(
    "/{project_id}/utc/git/push",
    response_model=BaseResponse,
    responses={409: {"model": ConflictResponse}},
)
@limiter.limit("10/minute")
async def push_git_utc(
    request: Request,
    project_id: str = Path(..., description="Project ID"),
    push_request: UnitTestCodePushRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Push unit test code files to Git repository"""
    logger.info(
        f"[push_git_unit_test_code] Start - project_id={project_id}, file_ids={push_request.id}"
    )

    if not project_id:
        logger.warning("[push_git_unit_test_code] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]

        result = await unit_test_service.push_git_unit_test_code(
            project_id=project_id,
            file_ids=push_request.id,
            commit_message=push_request.commit_message,
            user_name=push_request.user_name,
            token_password=push_request.token_password,
            user_id=user_id,
        )

        return BaseResponse(
            statusCode=200,
            message="Files pushed to Git successfully",
            data=result,
        )

    except HTTPException as e:
        if e.status_code == 409:
            raise
        raise
    except Exception as e:
        error_message = f"[push_git_unit_test_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put("/{project_id}/utd/{file_id}", response_model=BaseResponse)
async def update_unit_test_design_content(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Unit test design file ID"),
    update_request: UnitTestDesignUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Update unit test design content"""
    logger.info(
        f"[update_unit_test_design_content] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning(
            "[update_unit_test_design_content] Missing project_id or file_id"
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.project_service import project_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[update_unit_test_design_content] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Use service to update unit test design content
        result = await unit_test_service.update_unit_test_design_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info(f"[update_unit_test_design_content] Success - file_id={file_id}")
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_unit_test_design_content] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get(
    "/{project_id}/utd/{file_id}",
    response_model=UnitTestDesignDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get unit test design detail",
    description="Retrieve unit test designs generated from a source file",
)
async def get_unit_test_design_detail(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Source file ID"),
    current_user=Depends(get_current_user),
):
    """Get unit test design detail for a source file"""
    logger.info(
        "[get_unit_test_design_detail] Start - project_id=%s, file_id=%s, user_id=%s",
        project_id,
        file_id,
        current_user.get("user_id"),
    )

    if not project_id or not file_id:
        logger.warning("[get_unit_test_design_detail] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        detail_data = await unit_test_service.get_unit_test_design_detail(
            project_id=project_id,
            file_id=file_id,
            user_id=user_id,
            user_role=user_role,
        )

        response = UnitTestDesignDetailResponse(
            statusCode=200,
            message="Success",
            data=detail_data,
        )

        logger.info(
            "[get_unit_test_design_detail] Success - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )
        return response
    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(
            "[get_unit_test_design_detail] Validation error - project_id=%s, file_id=%s: %s",
            project_id,
            file_id,
            ve,
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_message=str(ve))
    except Exception as e:
        error_message = f"[get_unit_test_design_detail] Error - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.put("/{project_id}/utc/{file_id}", response_model=BaseResponse)
async def update_unit_test_code_content(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Unit test code file ID"),
    update_request: UnitTestCodeUpdateContentRequest = Body(...),
    current_user=Depends(get_current_user),
):
    """Update unit test code content"""
    logger.info(
        f"[update_unit_test_code_content] Start - project_id={project_id}, file_id={file_id}"
    )

    if not project_id or not file_id:
        logger.warning("[update_unit_test_code_content] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        from app.services.project_service import project_service

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        # Check if project is generating
        is_generating = await project_service.check_is_generating(project_id)
        if is_generating:
            logger.warning(
                f"[update_unit_test_code_content] Project is generating - project_id={project_id}"
            )
            raise_http_error(
                status.HTTP_400_BAD_REQUEST,
                error_key="PROJECT_GENERATING",
            )

        # Use service to update unit test code content
        result = await unit_test_service.update_unit_test_code_content(
            project_id=project_id,
            file_id=file_id,
            content=update_request.content,
            user_id=user_id,
            user_role=user_role,
        )

        logger.info(f"[update_unit_test_code_content] Success - file_id={file_id}")
        return BaseResponse(statusCode=200, message="Success", data=result)

    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[update_unit_test_code_content] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get(
    "/{project_id}/utc/{file_id}",
    response_model=UnitTestCodeDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get unit test code detail",
    description="Retrieve unit test code files generated from a source file",
)
async def get_unit_test_code_detail(
    project_id: str = Path(..., description="Project ID"),
    file_id: str = Path(..., description="Source file ID"),
    current_user=Depends(get_current_user),
):
    """Get unit test code detail for a source file"""
    logger.info(
        "[get_unit_test_code_detail] Start - project_id=%s, file_id=%s, user_id=%s",
        project_id,
        file_id,
        current_user.get("user_id"),
    )

    if not project_id or not file_id:
        logger.warning("[get_unit_test_code_detail] Missing project_id or file_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        detail_data = await unit_test_service.get_unit_test_code_detail(
            project_id=project_id,
            file_id=file_id,
            user_id=user_id,
            user_role=user_role,
        )

        response = UnitTestCodeDetailResponse(
            statusCode=200,
            message="Success",
            data=detail_data,
        )

        logger.info(
            "[get_unit_test_code_detail] Success - project_id=%s, file_id=%s",
            project_id,
            file_id,
        )
        return response
    except HTTPException:
        raise
    except ValueError as ve:
        logger.error(
            "[get_unit_test_code_detail] Validation error - project_id=%s, file_id=%s: %s",
            project_id,
            file_id,
            ve,
        )
        raise_http_error(status.HTTP_400_BAD_REQUEST, error_message=str(ve))
    except Exception as e:
        error_message = f"[get_unit_test_code_detail] Error - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.get(
    "/{project_id}/utd",
    response_model=UnitTestDesignListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get unit test design folder data",
    description="Retrieve folder structure and unit test design files",
)
async def get_unit_test_spec_data(project_id: str):
    """Get unit test design folders + files"""
    logger.info(f"[get_unit_test_spec_data] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_unit_test_spec_data] Missing project_id")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_INPUT: project_id is required",
        )

    try:
        data = await unit_test_service.get_project_unit_test_data(project_id, "utd")
        response_data = UnitTestDesignListData(
            folders=[FolderResponse(**folder) for folder in data.get("folders", [])],
            utd_list=[
                UnitTestFileBase(**document) for document in data.get("utd_list", [])
            ],
        )

        logger.info(
            f"[get_unit_test_spec_data] Success - folders={len(response_data.folders)}, "
            f"utd_list={len(response_data.utd_list)}"
        )
        return UnitTestDesignListResponse(
            statusCode=200, message="Success", data=response_data
        )

    except ValueError as ve:
        logger.error(
            f"[get_unit_test_spec_data] Validation error - project_id={project_id}: {ve}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        error_message = (
            f"[get_unit_test_spec_data] Error - project_id={project_id}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get unit test spec data: {str(e)}",
        )


@router.get(
    "/{project_id}/utc",
    response_model=UnitTestCodeListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get unit test code folder data",
    description="Retrieve folder structure and unit test code files",
)
async def get_unit_test_code_data(project_id: str):
    """Get unit test code folders + files"""
    logger.info(f"[get_unit_test_code_data] Start - project_id={project_id}")

    if not project_id:
        logger.warning("[get_unit_test_code_data] Missing project_id")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INVALID_INPUT: project_id is required",
        )

    try:
        data = await unit_test_service.get_project_unit_test_data(project_id, "utc")
        response_data = UnitTestCodeListData(
            folders=[FolderResponse(**folder) for folder in data.get("folders", [])],
            utc_list=[
                UnitTestFileBase(**document) for document in data.get("utc_list", [])
            ],
        )

        logger.info(
            f"[get_unit_test_code_data] Success - folders={len(response_data.folders)}, "
            f"utc_list={len(response_data.utc_list)}"
        )
        return UnitTestCodeListResponse(
            statusCode=200, message="Success", data=response_data
        )

    except ValueError as ve:
        logger.error(
            f"[get_unit_test_code_data] Validation error - project_id={project_id}: {ve}"
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except Exception as e:
        error_message = (
            f"[get_unit_test_code_data] Error - project_id={project_id}: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get unit test code data: {str(e)}",
        )


# #endregion


# #region Download Unit Test Specification (UTD)
@router.post("/{project_id}/utd/download")
async def download_unit_test_spec(
    project_id: str = Path(..., description="Project ID"),
    download_request: SourceCodeDownloadRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Download unit test specification files and folders for the current user"""
    logger.info(
        f"[download_unit_test_spec] Start - project_id={project_id}, file_ids={download_request.file_id}, folder_ids={download_request.folder_id}, user_id={current_user.get('user_id')}"
    )

    try:
        if not project_id:
            logger.warning("[download_unit_test_spec] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        result = await unit_test_service.download_unit_test_documents(
            project_id=project_id,
            file_ids=download_request.file_id,
            folder_ids=download_request.folder_id,
            user_id=user_id,
            user_role=user_role,
            unit_test_type="utd",
        )

        # Convert DownloadResult dataclass to dict
        result_dict = (
            asdict(result) if hasattr(result, "__dataclass_fields__") else result
        )

        logger.info(
            f"[download_unit_test_spec] Download payload ready - file_name={result_dict.get('file_name')}, media_type={result_dict.get('media_type')}"
        )

        response = build_download_file_response(result_dict)
        logger.info("[download_unit_test_spec] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_unit_test_spec] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_unit_test_spec] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# #endregion


# #region Download Unit Test Code (UTC)
@router.post("/{project_id}/utc/download")
async def download_unit_test_code(
    project_id: str = Path(..., description="Project ID"),
    download_request: SourceCodeDownloadRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Download unit test code files and folders for the current user"""
    logger.info(
        f"[download_unit_test_code] Start - project_id={project_id}, file_ids={download_request.file_id}, folder_ids={download_request.folder_id}, user_id={current_user.get('user_id')}"
    )

    try:
        if not project_id:
            logger.warning("[download_unit_test_code] Missing project_id")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        user_id = current_user["user_id"]
        user_role = current_user.get("role", PermissionLevel.USER)

        result = await unit_test_service.download_unit_test_documents(
            project_id=project_id,
            file_ids=download_request.file_id,
            folder_ids=download_request.folder_id,
            user_id=user_id,
            user_role=user_role,
            unit_test_type="utc",
        )

        # Convert DownloadResult dataclass to dict
        result_dict = (
            asdict(result) if hasattr(result, "__dataclass_fields__") else result
        )

        logger.info(
            f"[download_unit_test_code] Download payload ready - file_name={result_dict.get('file_name')}, media_type={result_dict.get('media_type')}"
        )

        response = build_download_file_response(result_dict)
        logger.info("[download_unit_test_code] Success - response prepared")
        return response

    except ValueError as e:
        error_message = f"[download_unit_test_code] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except HTTPException:
        raise
    except Exception as e:
        error_message = f"[download_unit_test_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/generate/unit-test", response_model=BaseResponse)
@limiter.limit("10/minute")
async def generate_unit_test(
    project_id: str = Path(..., description="Project ID"),
    generate_request: GenerateUnitTestRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Generate unit test from detail design documents or source code files"""
    logger.info(
        f"[generate_unit_test] Start - project_id={project_id}, input_source={generate_request.input_source}"
    )

    if not project_id:
        logger.warning("[generate_unit_test] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        input_source = generate_request.input_source

        if input_source not in ["detail_design", "source_code"]:
            logger.warning(f"[generate_unit_test] Invalid input_source: {input_source}")
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if input_source in ["detail_design"]:
            project = await project_service.get_detail_project(project_id)
            branch = project["git"]["branch"]
            repository_url = project["git"]["repository"]
            file_paths = [
                os.path.join(DEFAULT_DIRECTORY_BD_PD, FILE_NAME_BASIC_DESIGN),
                os.path.join(DEFAULT_DIRECTORY_BD_PD, FILE_NAME_DETAIL_DESIGN),
            ]
            exists = github_files_exist(
                repository_url,
                branch_name=branch,
                file_paths=file_paths,
                user_name=generate_request.user_name,
                token_password=generate_request.token_password,
            )
            if not exists:
                raise_http_error(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    error_key="GIT_DESIGN_FILE_NOT_FOUND_FOR_UTD_UTC",
                )
        elif input_source in ["source_code"]:
            if (
                await source_code_service.get_active_document_count_by_project_id(
                    project_id
                )
                == 0
            ):
                raise_http_error(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    error_key="NO_DATA_TO_GENERATE",
                )
        if input_source == "source_code":
            should_update_revision = await git_validate.check_revision(
                project_id, generate_request.user_name, generate_request.token_password
            )
            if should_update_revision:
                topic_generation_target = GenerationTarget.SOURCE_CODE_TO_SUMMARY
            else:
                topic_generation_target = GenerationTarget.SOURCE_CODE_TO_UTD_UTC

        client = mqtt_service.get_client()
        generation_target = (
            GenerationTarget.DETAILED_DESIGN_TO_UTD_UTC
            if input_source == "detail_design"
            else GenerationTarget.SOURCE_CODE_TO_UTD_UTC
        )
        logger.info(f"[generate_unit_test] Generation target: {generation_target}")
        message = {
            "project_id": project_id,
            "generation_target": generation_target,
        }

        success = client.publish(TASK_REGISTRATION_TOPIC, message, qos=2, retain=True)

        if not success:
            raise_http_error(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_key="MQTT_NOT_STARTED",
            )

        logger.info(
            f"[generate_unit_test] Success - project_id={project_id}, input_source={input_source}"
        )
        return BaseResponse(statusCode=200, message="Unit test generated successfully")
    except HTTPException:
        raise
    except ValueError as e:
        error_message = f"[generate_unit_test] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[generate_unit_test] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


@router.post("/{project_id}/generate/unit-test-code", response_model=BaseResponse)
@limiter.limit("10/minute")
async def generate_unit_test_code(
    project_id: str = Path(..., description="Project ID"),
    generate_request: GenerateUnitTestCodeRequest = Body(...),
    request: Request = None,
    current_user=Depends(get_current_user),
):
    """Generate unit test code from unit test design documents"""
    logger.info(
        f"[generate_unit_test_code] Start - project_id={project_id}, input_source={generate_request.input_source}"
    )

    if not project_id:
        logger.warning("[generate_unit_test_code] Missing project_id")
        raise_http_error(status.HTTP_400_BAD_REQUEST)

    try:
        input_source = generate_request.input_source

        if input_source not in ["unit_test_design"]:
            logger.warning(
                f"[generate_unit_test_code] Invalid input_source: {input_source}"
            )
            raise_http_error(status.HTTP_400_BAD_REQUEST)

        if input_source in ["unit_test_design"]:
            document_count = await unit_test_service.get_active_document_count_by_project_id(
                project_id, collection_name=UNIT_TEST_DESIGN_COLLECTION
            )
            logger.info(f"[generate_unit_test_code] Document count: {document_count}")
            if document_count == 0:
                raise_http_error(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    error_key="NO_DATA_TO_GENERATE",
                )

        client = mqtt_service.get_client()
        message = {
            "project_id": project_id,
            "generation_target": GenerationTarget.UTD_TO_UTC,
        }

        success = client.publish(TASK_REGISTRATION_TOPIC, message, qos=2, retain=True)

        if not success:
            raise_http_error(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                error_key="MQTT_NOT_STARTED",
            )

        logger.info(
            f"[generate_unit_test_code] Success - project_id={project_id}, input_source={input_source}"
        )
        return BaseResponse(
            statusCode=200, message="Unit test code generated successfully"
        )
    except HTTPException:
        raise
    except ValueError as e:
        error_message = f"[generate_unit_test_code] Validation error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        error_message = f"[generate_unit_test_code] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise_http_error(status.HTTP_500_INTERNAL_SERVER_ERROR)


# #endregion
