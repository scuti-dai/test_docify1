"""
Helper utility functions
"""

import logging
import re
import base64
import os
from typing import Any, Dict, Optional
from datetime import datetime, timezone
from passlib.context import CryptContext
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
from fastapi import UploadFile
from fastapi import HTTPException
from app.core.error_messages import get_error_response
from config_gateway import Config as GatewayConfig

logger = logging.getLogger(__name__)

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Encryption constants
AES_KEY_LENGTH = 32  # 256 bits = 32 bytes
AES_NONCE_LENGTH = 12  # 96 bits = 12 bytes for GCM

# Import from common to use shared functions and re-export for backward compatibility
from cec_docifycode_common.utils.helpers import (
    get_current_utc_time,
    format_datetime,
)

# Re-export to maintain backward compatibility with existing imports
__all__ = ["get_current_utc_time", "format_datetime"]


def is_valid_email(email: str) -> bool:
    """Validate email format"""
    logger.debug(f"[is_valid_email] Validating email: {email}")
    try:
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        is_valid = bool(re.match(pattern, email))
        logger.debug(f"[is_valid_email] Email validation result: {is_valid}")
        return is_valid
    except Exception as e:
        logger.error(f"[is_valid_email] Error validating email: {e}")
        return False


def create_data_id() -> str:
    """Create a unique data ID"""
    logger.debug("[create_data_id] Creating data ID")
    try:
        import uuid

        data_id = str(uuid.uuid4())
        logger.debug(f"[create_data_id] Created data ID: {data_id}")
        return data_id
    except Exception as e:
        logger.error(f"[create_data_id] Error creating data ID: {e}")
        raise


def sanitize_string(text: str, max_length: Optional[int] = None) -> str:
    """Sanitize string input"""
    logger.debug(f"[sanitize_string] Sanitizing string: {text[:50]}...")
    try:
        # Remove extra whitespace
        sanitized = re.sub(r"\s+", " ", text.strip())

        # Truncate if max_length specified
        if max_length and len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
            logger.debug(f"[sanitize_string] Truncated to {max_length} characters")

        logger.debug(f"[sanitize_string] Sanitized string: {sanitized[:50]}...")
        return sanitized
    except Exception as e:
        logger.error(f"[sanitize_string] Error sanitizing string: {e}")
        raise


def validate_pagination(page: int, per_page: int, max_per_page: int = 100) -> tuple:
    """Validate and normalize pagination parameters"""
    logger.debug(
        f"[validate_pagination] Validating pagination - page: {page}, per_page: {per_page}"
    )
    try:
        # Ensure page is at least 1
        page = max(1, page)

        # Ensure per_page is within limits
        per_page = max(1, min(per_page, max_per_page))

        logger.debug(
            f"[validate_pagination] Validated pagination - page: {page}, per_page: {per_page}"
        )
        return page, per_page
    except Exception as e:
        logger.error(f"[validate_pagination] Error validating pagination: {e}")
        raise


def create_password_hash(password: str) -> str:
    """
    Create a hashed password using bcrypt

    Args:
        password: Plain text password to hash

    Returns:
        Hashed password string
    """
    logger.debug("[create_password_hash] Start - Hashing password")
    try:
        if not password:
            logger.error("[create_password_hash] Password is empty")
            raise ValueError("Password cannot be empty")

        hashed = pwd_context.hash(password)
        logger.debug("[create_password_hash] Success - Password hashed")
        return hashed
    except Exception as e:
        logger.error(f"[create_password_hash] Error: {e}")
        raise


def check_password_match(plain_password: str, hashed_password: str) -> bool:
    """
    Check if a plain password matches a hashed password

    Args:
        plain_password: Plain text password from user input
        hashed_password: Hashed password to compare against

    Returns:
        True if passwords match, False otherwise
    """
    logger.debug("[check_password_match] Start - Checking password match")
    try:
        if not plain_password or not hashed_password:
            logger.warning("[check_password_match] Empty password or hash provided")
            return False

        is_valid = pwd_context.verify(plain_password, hashed_password)
        logger.debug(f"[check_password_match] Success - Match result: {is_valid}")
        return is_valid
    except Exception as e:
        logger.error(f"[check_password_match] Error: {e}")
        return False


async def validate_file_size_and_type(file: UploadFile, file_extension: str) -> int:
    """
    Validate file size and type, return file size in bytes

    Args:
        file: UploadFile object to validate
        file_extension: File extension (e.g., '.md', '.jpg', '.png')

    Returns:
        File size in bytes

    Raises:
        HTTPException: If file size exceeds limit or extension is invalid
    """
    logger.info(
        f"[validate_file_size_and_type] Start - file_name={file.filename}, extension={file_extension}"
    )

    try:
        # Get max size based on file extension
        if file_extension == ".md":
            max_size_mb = 15  # 15MB for markdown files
        else:
            max_size_mb = 12  # 12MB for image files (.jpg, .jpeg, .png)

        max_size_bytes = max_size_mb * 1024 * 1024

        # Check file size using content_length if available, otherwise read and check
        if hasattr(file, "size") and file.size:
            file_size = file.size
        else:
            # Read file content to get size
            file_content = await file.read()
            file_size = len(file_content)
            # Reset file pointer for service to read again
            await file.seek(0)

        if file_size > max_size_bytes:
            logger.warning(
                f"[validate_file_size_and_type] File too large - size={file_size}, max={max_size_bytes}"
            )
            raise HTTPException(
                status_code=413,
                detail=get_error_response(
                    413, f"ファイルサイズが{max_size_mb}MBを超えています。"
                ).dict(),
            )

        logger.info(f"[validate_file_size_and_type] Success - file_size={file_size}")
        return file_size

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[validate_file_size_and_type] Error: {e}")
        raise HTTPException(status_code=500, detail=get_error_response(500).dict())



def is_filename_length_valid(filename: str) -> bool:
    """
    Check filename length
    True  = valid
    False = too long
    """
    MAX_FILENAME_LENGTH = 255
    MAX_BASENAME_LENGTH = 200

    if not filename:
        return True

    filename = filename.strip()

    # Full filename length
    if len(filename) > MAX_FILENAME_LENGTH:
        return False

    basename, _ = os.path.splitext(filename)

    # Basename length (without extension)
    if len(basename) > MAX_BASENAME_LENGTH:
        return False

    return True


def is_valid_file_extension(file_extension: str) -> bool:
    """
    Check if file extension is allowed

    Args:
        file_extension: File extension to check (e.g., '.md', '.jpg')

    Returns:
        True if extension is allowed, False otherwise
    """
    logger.debug(f"[is_valid_file_extension] Checking extension: {file_extension}")

    try:
        allowed_extensions = [".md", ".jpg", ".jpeg", ".png"]
        is_valid = file_extension in allowed_extensions

        logger.debug(
            f"[is_valid_file_extension] Extension {file_extension} valid: {is_valid}"
        )
        return is_valid

    except Exception as e:
        logger.error(f"[is_valid_file_extension] Error: {e}")
        return False


# region Encryption/Decryption Functions


def _get_encryption_key() -> bytes:
    """
    Get encryption key from config.
    ENCRYPTION_KEY must be Base64 encoded string that decodes to exactly 32 bytes.
    """
    logger.debug("[_get_encryption_key] Start")
    try:
        key_str = GatewayConfig.ENCRYPTION_KEY

        if not key_str:
            logger.error("[_get_encryption_key] ENCRYPTION_KEY not configured")
            raise ValueError("ENCRYPTION_KEY not configured")

        # Decode Base64 to get key bytes
        try:
            key_bytes = base64.b64decode(key_str.encode("utf-8"))
        except Exception as decode_error:
            logger.error(
                f"[_get_encryption_key] Failed to decode Base64 key: {decode_error}"
            )
            raise ValueError(
                "ENCRYPTION_KEY must be a valid Base64 encoded string"
            ) from decode_error

        # Validate key length - must be exactly 32 bytes
        if len(key_bytes) != AES_KEY_LENGTH:
            logger.error(
                f"[_get_encryption_key] Invalid key length: {len(key_bytes)} bytes, "
                f"expected {AES_KEY_LENGTH} bytes"
            )
            raise ValueError(
                f"ENCRYPTION_KEY must decode to exactly {AES_KEY_LENGTH} bytes "
                f"(got {len(key_bytes)} bytes)"
            )

        logger.debug("[_get_encryption_key] Success")
        return key_bytes
    except Exception as e:
        logger.error(f"[_get_encryption_key] Error: {e}")
        raise


def encrypt_ai_programming_credential(plain_text: str) -> Optional[str]:
    """
    Encrypt AI programming credential using AES-256-GCM

    Args:
        plain_text: Plain text to encrypt (password or token)

    Returns:
        Base64 encoded encrypted string, or None if input is empty
    """
    logger.debug("[encrypt_ai_programming_credential] Start")
    try:
        if not plain_text:
            logger.debug(
                "[encrypt_ai_programming_credential] Empty input, returning None"
            )
            return None

        key = _get_encryption_key()
        aesgcm = AESGCM(key)

        # Generate random nonce
        nonce = os.urandom(AES_NONCE_LENGTH)

        # Encrypt
        plain_bytes = plain_text.encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plain_bytes, None)

        # Combine nonce + ciphertext and encode to base64
        encrypted_data = nonce + ciphertext
        encrypted_b64 = base64.b64encode(encrypted_data).decode("utf-8")

        logger.debug("[encrypt_ai_programming_credential] Success")
        return encrypted_b64
    except Exception as e:
        logger.error(f"[encrypt_ai_programming_credential] Error: {e}")
        raise


def decrypt_ai_programming_credential(encrypted_text: str) -> Optional[str]:
    """
    Decrypt AI programming credential using AES-256-GCM

    Args:
        encrypted_text: Base64 encoded encrypted string

    Returns:
        Decrypted plain text, or None if input is empty
    """
    logger.debug("[decrypt_ai_programming_credential] Start")
    try:
        if not encrypted_text:
            logger.debug(
                "[decrypt_ai_programming_credential] Empty input, returning None"
            )
            return None

        key = _get_encryption_key()
        aesgcm = AESGCM(key)

        # Decode from base64
        encrypted_data = base64.b64decode(encrypted_text.encode("utf-8"))

        # Extract nonce and ciphertext
        nonce = encrypted_data[:AES_NONCE_LENGTH]
        ciphertext = encrypted_data[AES_NONCE_LENGTH:]

        # Decrypt
        plain_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        plain_text = plain_bytes.decode("utf-8")

        logger.debug("[decrypt_ai_programming_credential] Success")
        return plain_text
    except Exception as e:
        logger.error(f"[decrypt_ai_programming_credential] Error: {e}")
        raise


# endregion
