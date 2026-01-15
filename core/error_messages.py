"""
Error messages constants for API responses
"""

from typing import Dict
from app.schemas.base import ErrorResponse

# HTTP Status Code Messages (Japanese)
ERROR_MESSAGES: Dict[int, str] = {
    400: "リクエスト内容に誤りがあります。もう一度ご確認ください。",  # Bad Request
    401: "セッションの有効期限が切れたか、認証に失敗しました。再度ログインしてください。",  # Unauthorized
    403: "この操作を行う権限がありません。",  # Forbidden
    404: "指定されたリソースが見つかりません。",  # Not Found
    409: "データが他の操作と競合しています。再度お試しください。",  # Conflict
    413: "リクエストまたはファイルアップロードの容量が許可された上限を超えています。",  # Payload Too Large
    422: "入力データに問題があります。内容をご確認ください。",  # Unprocessable Entity
    500: "サーバーでエラーが発生しました。しばらくしてからもう一度お試しください。",  # Internal Server Error
    502: "通信中に問題が発生しました。再度お試しください。",  # Bad Gateway
    503: "現在サービスを利用できません。しばらくしてからもう一度お試しください。",  # Service Unavailable
    504: "処理がタイムアウトしました。通信環境をご確認のうえ、再度お試しください。",  # Gateway Timeout
}

# Application error messages (Japanese) - Merged from Token + Access Rights
APPLICATION_ERROR_MESSAGES: Dict[str, str] = {
    # Token errors
    "TOKEN_EXPIRED": "トークンの有効期限が切れました。",  # Token has expired
    "TOKEN_INVALID": "トークンが無効です。",  # Token is invalid
    "TOKEN_INVALID_TYPE": "トークンの種類が無効です。",  # Token type is invalid
    "TOKEN_MISSING": "トークンが必要です。",  # Token is required
    "REFRESH_TOKEN_REQUIRED": "リフレッシュトークンが必要です。",  # Refresh token is required
    "INVALID_TOKEN_PAYLOAD": "トークンの内容が無効です。",  # Token payload is invalid
    "AUTHENTICATION_FAILED": "認証に失敗しました。",  # Authentication failed
    # Access Rights errors
    "ADMIN_ROLE_REQUIRED": "管理者権限が必要です。",  # Admin role required
    # Project errors
    "PROJECT_NAME_ALREADY_EXISTS": "プロジェクトの作成中にエラーが発生しました。プロジェクト名は既に存在します。",  # Project name already exists
    "EMAIL_ALREADY_EXISTS": "メールアドレスが既に登録されています。",  # Email already exists
    # Git errors
    "GIT_AUTHENTICATION_FAILED": "Gitの認証に失敗しました。",  # Git authentication failed
    "REPOSITORY_ALREADY_EXISTS": "このリポジトリはすでに登録されています。",  # Repository already exists
    "GIT_REPOSITORY_OR_BRANCH_NOT_FOUND": "指定したリポジトリまたはブランチが存在しません。",  # Repository or branch does not exist
    "GIT_REPOSITORY_ACCESS_DENIED": "このリポジトリへのアクセス権限がありません",  # No access permission to repository
    "GIT_INVALID_URL": "GitのURLが無効です。",  # Invalid URL
    "GIT_SOURCE_CODE_FETCH_FAILED": "ソースコードの取得に失敗しました。",  # Failed to fetch source code
    "GIT_FILE_ALREADY_EXISTS": "Git上にすでにファイルが存在しているので、プロジェクトを作成できません。",  # File already exists in Git
    # File errors
    "FILE_NOT_FOUND": "ファイルが見つかりません。",  # File not found
    "FILE_NOT_CHANGED": "ファイルに変更はありません。プッシュするものはありません。",  # File not changed
    # Project status errors
    "PROJECT_GENERATING": "生成実行中なのでこの操作はできません。",  # Project is generating, operation not allowed
    # File name max length
    "FILE_NAME_TOO_LONG": "ファイル名が長すぎるため、アップロードできません。",
    "GIT_DESIGN_FILE_NOT_FOUND": "Git上に設計書ファイルが存在しないため、ソースコードの生成ができません。",
    "GIT_DESIGN_FILE_NOT_FOUND_FOR_UTD_UTC": "Git上に設計書ファイルが存在しないため、単体試験仕様書／コードの生成ができません。",
    "LAST_USER_CANNOT_LEAVE_PROJECT": "このプロジェクトには最低1人のメンバーが必要なため、削除できません。",  # Last user cannot leave project
    # generation errors
    "NO_DATA_TO_GENERATE": "生成対象のデータがありません",  # No data to generate
    "MQTT_NOT_STARTED": "MQTT が起動していません。先に起動してください。",  # MQTT not started
    # AI programming errors
    "AI_PROGRAMMING_AUTHENTICATION_FAILED": "AIプログラミングアカウントのGitの認証に失敗しました。",  # AI programming authentication failed
    "AI_PROGRAMMING_REPOSITORY_ACCESS_DENIED": "AIプログラミングアカウントはこのリポジトリへのアクセス権限がありません。",  # AI programming repository access denied
}

DEFAULT_ERROR_MESSAGE: str = "不明なエラーが発生しました。"  # Unknown error occurred


def get_error_message(status_code: int, custom_message: str = None) -> str:
    """Get error message for HTTP status code"""
    if custom_message:
        return custom_message
    return ERROR_MESSAGES.get(status_code, DEFAULT_ERROR_MESSAGE)


def get_error_response(status_code: int, custom_message: str = None) -> ErrorResponse:
    """Get error response object for HTTPException"""
    message = (
        custom_message
        if custom_message
        else ERROR_MESSAGES.get(status_code, DEFAULT_ERROR_MESSAGE)
    )
    return ErrorResponse(statusCode=status_code, error_message=message)
