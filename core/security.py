"""
Security utilities for authentication and authorization
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from jose import JWTError, jwt
from config_gateway import Config

logger = logging.getLogger(__name__)


def _validate_token_format(token: str) -> bool:
    """
    Validate JWT token format before decoding.
    
    Checks:
    - Token is not empty
    - Token has 3 parts separated by dots (header.payload.signature)
    
    Args:
        token: JWT token string
        
    Returns:
        True if token format is valid, False otherwise
    """
    if not token or not isinstance(token, str):
        logger.warning("[_validate_token_format] Token is empty or not a string")
        return False
    
    # JWT should have exactly 3 parts: header.payload.signature
    parts = token.split(".")
    if len(parts) != 3:
        logger.warning("[_validate_token_format] Token format is invalid (expected 3 parts, got %d)", len(parts))
        return False
    
    # Check that all parts are non-empty
    if not all(part for part in parts):
        logger.warning("[_validate_token_format] Token contains empty parts")
        return False
    
    return True

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    logger.debug("[create_access_token] Creating access token")
    try:
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=Config.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, Config.SECRET_KEY, algorithm="HS256")
        logger.debug("[create_access_token] Access token created successfully: " + str(expire) + " seconds")
        return encoded_jwt
    except Exception as e:
        logger.error(f"[create_access_token] Error creating access token: {e}")
        raise

def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT refresh token"""
    logger.debug("[create_refresh_token] Creating refresh token")
    try:
        to_encode = data.copy()
        now = datetime.now(timezone.utc)
        
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(days=Config.REFRESH_TOKEN_EXPIRE_DAYS)
        
        to_encode.update({"exp": expire, "type": "refresh"})
        encoded_jwt = jwt.encode(to_encode, Config.SECRET_KEY, algorithm="HS256")
        logger.debug("[create_refresh_token] Refresh token created successfully")
        return encoded_jwt
    except Exception as e:
        logger.error(f"[create_refresh_token] Error creating refresh token: {e}")
        raise

def verify_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT token"""
    logger.debug("[verify_token] Verifying token")
    try:
        # SECURITY - Validate token format before decode (empty, malformed)
        if not _validate_token_format(token):
            return None
        
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        logger.debug("[verify_token] Token verified successfully")
        return payload
    except JWTError as e:
        logger.error(f"[verify_token] Error verifying token: {e}")
        return None

def verify_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """Verify JWT refresh token"""
    logger.debug("[verify_refresh_token] Verifying refresh token")
    try:
        # Validate token format before decode
        if not _validate_token_format(token):
            return None
        
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "refresh":
            logger.warning("[verify_refresh_token] Token is not a refresh token")
            return None
        logger.debug("[verify_refresh_token] Refresh token verified successfully")
        return payload
    except JWTError as e:
        logger.error(f"[verify_refresh_token] Error verifying refresh token: {e}")
        return None

def extract_token_payload(token: str) -> Optional[Dict[str, Any]]:
    """
    Safely extract payload from JWT token
    
    Args:
        token: JWT token string
        
    Returns:
        Token payload dict or None if invalid
    """
    logger.debug("[extract_token_payload] Extracting token payload")
    
    if not token:
        logger.warning("[extract_token_payload] Token is empty")
        return None
    
    try:
        payload = jwt.decode(token, Config.SECRET_KEY, algorithms=["HS256"])
        logger.debug("[extract_token_payload] Payload extracted successfully")
        return payload
        
    except jwt.ExpiredSignatureError:
        logger.warning("[extract_token_payload] Token has expired")
        return None
    except JWTError as e:
        logger.error(f"[extract_token_payload] Invalid token: {e}")
        return None
    except Exception as e:
        logger.error(f"[extract_token_payload] Unexpected error: {e}")
        return None

def get_access_token_expires_in() -> int:
    """
    Get access token expiration time in seconds
    
    Returns:
        Expiration time in seconds
    """
    logger.debug("[get_access_token_expires_in] Calculating expiry")
    try:
        expires_in = Config.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        logger.debug(f"[get_access_token_expires_in] {expires_in} seconds")
        return expires_in
    except Exception as e:
        logger.error(f"[get_access_token_expires_in] Error: {e}")
        raise

def get_refresh_token_expires_in() -> int:
    """
    Get refresh token expiration time in seconds
    
    Returns:
        Expiration time in seconds
    """
    logger.debug("[get_refresh_token_expires_in] Calculating expiry")
    try:
        expires_in = Config.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
        logger.debug(f"[get_refresh_token_expires_in] {expires_in} seconds")
        return expires_in
    except Exception as e:
        logger.error(f"[get_refresh_token_expires_in] Error: {e}")
        raise
