"""
Page routes for HTML templates
Centralizes all screen/page endpoints to keep main.py clean
"""

from app.services.logs_service import save_exception_log_sync
from flask import Flask, redirect, request, jsonify, render_template, session
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config_gateway import Config
from app.core.logging import setup_logging
from app.schemas.auth import (
    LoginRequest,
)
from app.services.auth_service import AuthService
from datetime import datetime, timedelta

from starlette.middleware.sessions import SessionMiddleware
from msal import ConfidentialClientApplication

import uuid
import json
import logging

# Initialize logger and config
logger = setup_logging()

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Create router for page routes
page_router = APIRouter()

msal_app = ConfidentialClientApplication(
    Config.CLIENT_ID, authority=Config.AUTHORITY, client_credential=Config.CLIENT_SECRET
)

logger = logging.getLogger(__name__)


def is_token_valid(token_info: dict) -> bool:
    """トークンの有効性をチェック"""
    try:
        import time

        # expires_inがある場合はチェック
        if "expires_at" in token_info:
            return time.time() < token_info["expires_at"]
        return True  # expires_atがない場合は有効とみなす
    except Exception as e:
        logger.error(f"Token validation error: {str(e)}")
        return False


# region Public Routes
@page_router.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """
    Root endpoint
    - LOCAL: render login page
    - EntraID: check token validity and redirect to main page if valid
    """
    logger.info("[root] Root page accessed")

    try:
        # ===== LOCAL authentication =====
        if Config.AUTH_TYPE == "LOCAL":
            return templates.TemplateResponse("login.html", {"request": request})

        # ===== Entra ID authentication =====
        token_info = request.session.get("token_info")

        if token_info and is_token_valid(token_info):
            logger.info(
                f"[root] User authenticated: "
                f"{token_info.get('user_info', {}).get('email')}"
            )
            return templates.TemplateResponse("index.html", {"request": request})

        # Token valid but expired
        if token_info:
            logger.warning("[root] Token expired, clearing session")
            request.session.clear()

        # Not authenticated, redirect to login
        logger.info("[root] Not authenticated, redirecting to login")
        return RedirectResponse(url="/login", status_code=303)

    except Exception as e:
        error_message = f"[root] Error rendering root page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


# endregion


@page_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Login page endpoint
    """
    logger.info("[login_page] Login page accessed")
    try:
        if Config.AUTH_TYPE == "EntraID":

            # セッション状態を生成（CSRF保護）
            state = str(uuid.uuid4())
            request.session["state"] = state

            # 認証URLを生成
            auth_url = msal_app.get_authorization_request_url(
                scopes=[Config.SCOPE],
                state=state,
                redirect_uri=request.url_for("authorized"),
            )

            # Entra IDログインページにリダイレクト
            return RedirectResponse(url=auth_url)
        else:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "MAX_LENGTH_EMAIL": Config.MAX_LENGTH_EMAIL,
                    "MAX_LENGTH_PASS": Config.MAX_LENGTH_PASS,
                },
            )
    except Exception as e:
        error_message = f"[login_page] Error rendering login page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


@page_router.get("/getAToken", response_class=HTMLResponse, name="authorized")
async def get_a_token(request: Request):
    """認証コールバック - トークンを取得してlocalStorageに保存（画面非表示）"""
    if Config.AUTH_TYPE == "LOCAL":
        raise

    if request.query_params.get("state") != request.session.get("state"):
        raise HTTPException(status_code=400, detail="State mismatch")
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description")

    if error:
        logger.error(f"Auth error: {error} - {error_description}")
        raise HTTPException(
            status_code=400,
            detail=f"Authentication failed: {error_description or error}",
        )

    if not code:
        logger.error("No authorization code received")
        raise HTTPException(status_code=400, detail="No authorization code received")

    try:
        result = msal_app.acquire_token_by_authorization_code(
            code, scopes=[Config.SCOPE], redirect_uri=Config.REDIRECT_URI
        )

        if "error" in result:
            error_msg = result.get("error_description", result.get("error"))
            logger.error(f"Token acquisition error: {error_msg}")
            raise HTTPException(
                status_code=400, detail=f"Token acquisition failed: {error_msg}"
            )

        login_request = LoginRequest(
            email=result.get("id_token_claims", {}).get("preferred_username", ""),
            name=result.get("id_token_claims", {}).get("name", ""),
            token=result.get("access_token", ""),
            groups=result.get("id_token_claims", {}).get("groups", []),
        )
        entra_id_login_service = AuthService()
        login_response = await entra_id_login_service.entra_id_login(login_request)
        login_data = login_response.data

        token_info = {
            "access_token": login_data["access_token"],
            "refresh_token": login_data["refresh_token"],
            "user_info": {
                "user_id": login_data["user_id"],
                "email": login_data["email"],
                "user_name": login_data["user_name"],
            },
            "user_role": login_data["role"],
            # "user_role": "admin",
        }

        logger.info(f"Token acquired for user: {login_data['email']}")

        session_token_info = {"entra_token": login_data["OAuth_token"]}
        request.session["token_info"] = session_token_info

        # 即座にリダイレクトするHTMLを返す
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>認証処理中...</title>
        </head>
        <body>
            <script>
                const tokenInfo = {json.dumps(token_info)};
                localStorage.setItem("access_token", tokenInfo.access_token);
                localStorage.setItem("refresh_token", tokenInfo.refresh_token);
                localStorage.setItem("user_info", JSON.stringify(tokenInfo.user_info));
                localStorage.setItem("user_role", tokenInfo.user_role);
                localStorage.setItem("AUTH_TYPE", "{Config.AUTH_TYPE}");

                console.log('Token saved successfully');
                
                // 即座にホームページにリダイレクト
                window.location.href = '/projects';
            </script>
        </body>
        </html>
        """

        return HTMLResponse(content=html_content)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_a_token: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@page_router.get("/api/token")
async def get_token(request: Request):
    """セッションに保存されたトークンを取得"""
    logger.info("[api/token] Token request received")

    token_info = request.session.get("token_info")

    if not token_info:
        logger.warning("[api/token] No token found in session")
        raise HTTPException(status_code=404, detail="No token found in session")

    logger.info(
        f"[api/token] Returning token for user: {token_info.get('user_info', {}).get('email')}"
    )
    return token_info


@page_router.get("/logout")
async def logout(request: Request):
    """ログアウト"""
    logger.info("[logout] Logout requested")

    if Config.AUTH_TYPE == "LOCAL":
        # return templates.TemplateResponse("login.html", {"request": request})
        return RedirectResponse(url="/", status_code=302)

    # セッションをクリア
    request.session.clear()
    # EntraIDのログアウトURLにリダイレクト
    logout_url = (
        f"https://login.microsoftonline.com/{Config.TENANT_ID}/oauth2/v2.0/logout?"
        f"post_logout_redirect_uri={Config.REDIRECT_URI.replace('/getAToken', '')}"
    )

    logger.info(f"[logout] Redirecting to EntraID logout: {logout_url}")

    return RedirectResponse(url=logout_url, status_code=302)


@page_router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    """
    Dashboard page endpoint
    """
    logger.info("[dashboard] Dashboard page accessed")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse("dashboard.html", {"request": request})
    except Exception as e:
        error_message = f"[dashboard] Error rendering dashboard page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


@page_router.get("/projects", response_class=HTMLResponse)
async def projects(request: Request):
    """
    Projects page endpoint
    """
    logger.info("[projects] Projects page accessed")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse("projects.html", {"request": request})
    except Exception as e:
        error_message = f"[projects] Error rendering projects page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


@page_router.get("/projects/new", response_class=HTMLResponse)
async def create_project(request: Request):
    """
    Create project page endpoint
    """
    logger.info("[create_project] Create project page accessed")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "project/create_project.html",
            {
                "request": request,
                "AUTH_TYPE": Config.AUTH_TYPE,
            },
        )
    except Exception as e:
        error_message = f"[create_project] Error rendering create project page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


@page_router.get("/projects/{project_id}/settings", response_class=HTMLResponse)
async def project_settings(request: Request, project_id: str):
    """
    Project settings page endpoint
    """
    logger.info(
        f"[project_settings] Project settings page accessed - project_id={project_id}"
    )
    if not project_id:
        logger.warning("[project_settings] Missing project_id")
        raise ValueError("project_id is required")

    try:
        return templates.TemplateResponse(
            "project/update_project.html",
            {
                "request": request,
                "project_id": project_id,
                "AUTH_TYPE": Config.AUTH_TYPE,
            },
        )
    except Exception as e:
        error_message = f"[project_settings] Error rendering settings page - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: str):
    """
    Project detail page endpoint
    """
    logger.info(
        f"[project_detail] Project detail page accessed - project_id={project_id}"
    )

    if not project_id:
        logger.warning("[project_detail] Missing project_id")
        raise ValueError("project_id is required")

    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "project_detail.html",
            {
                "request": request,
                "project_id": project_id,
                "GIT_CHECK_INTERVAL": Config.GIT_CHECK_INTERVAL,
            },
        )
    except Exception as e:
        error_message = f"[project_detail] Error rendering project detail page - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/rd/{file_id}", response_class=HTMLResponse)
async def rd_detail(request: Request, project_id: str, file_id: str):
    """
    Requirement document detail page endpoint
    """
    logger.info(
        f"[rd_detail] RD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[rd_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[rd_detail] Missing file_id")
        raise ValueError("file_id is required")

    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/requirement_design_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[rd_detail] Error rendering RD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/bd/{file_id}", response_class=HTMLResponse)
async def bd_detail(request: Request, project_id: str, file_id: str):
    """
    Basic design detail page endpoint
    """
    logger.info(
        f"[bd_detail] BD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[bd_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[bd_detail] Missing file_id")
        raise ValueError("file_id is required")

    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/basic_design_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[bd_detail] Error rendering BD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/pd/{file_id}", response_class=HTMLResponse)
async def pd_detail(request: Request, project_id: str, file_id: str):
    """
    Detail design detail page endpoint
    """
    logger.info(
        f"[pd_detail] PD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[pd_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[pd_detail] Missing file_id")
        raise ValueError("file_id is required")

    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/detail_design_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[pd_detail] Error rendering PD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/src/{file_id}", response_class=HTMLResponse)
async def src_detail(request: Request, project_id: str, file_id: str):
    """
    Source code detail page endpoint
    """
    logger.info(
        f"[src_detail] PD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[src_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[src_detail] Missing file_id")
        raise ValueError("file_id is required")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/source_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[src_detail] Error rendering PD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/utd/{file_id}", response_class=HTMLResponse)
async def utd_detail(request: Request, project_id: str, file_id: str):
    """
    Unit test design detail page endpoint
    """
    logger.info(
        f"[utd_detail] PD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[utd_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[utd_detail] Missing file_id")
        raise ValueError("file_id is required")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/unit_test_design_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[utd_detail] Error rendering PD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/projects/{project_id}/utc/{file_id}", response_class=HTMLResponse)
async def utc_detail(request: Request, project_id: str, file_id: str):
    """
    Unit test code detail page endpoint
    """
    logger.info(
        f"[utc_detail] PD detail page accessed - project_id={project_id}, file_id={file_id}"
    )

    if not project_id:
        logger.warning("[utc_detail] Missing project_id")
        raise ValueError("project_id is required")

    if not file_id:
        logger.warning("[utc_detail] Missing file_id")
        raise ValueError("file_id is required")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "file_detail/unit_test_code_detail.html",
            {"request": request, "project_id": project_id, "file_id": file_id},
        )
    except Exception as e:
        error_message = f"[utc_detail] Error rendering PD detail page - project_id={project_id}, file_id={file_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/user-management", response_class=HTMLResponse)
async def user_management(request: Request):
    """
    User management page endpoint
    """
    logger.info("[user_management] User management page accessed")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "user_management.html",
            {
                "request": request,
                "MAX_LENGTH_EMAIL": Config.MAX_LENGTH_EMAIL,
                "MAX_LENGTH_NAME": Config.MAX_LENGTH_NAME,
                "MAX_LENGTH_PASS": Config.MAX_LENGTH_PASS,
            },
        )
    except Exception as e:
        error_message = f"[user_management] Error rendering user management page: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


@page_router.get("/access-management-right", response_class=HTMLResponse)
async def access_management_right(request: Request):
    """
    Access management rights page endpoint
    """
    logger.info("[access_management_right] Access management rights page accessed")
    try:
        # TODO: Add authentication check here
        return templates.TemplateResponse(
            "access_management_right.html", {"request": request}
        )
    except Exception as e:
        error_message = (
            f"[access_management_right] Error rendering access management page: {e}"
        )
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
        raise


@page_router.get("/health")
async def health_check():
    """
    Health check endpoint
    """
    logger.info("[health_check] Health check requested")
    try:
        return {"status": "healthy", "message": "Service is running"}
    except Exception as e:
        error_message = f"[health_check] Error in health check: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)

        raise


# endregion
