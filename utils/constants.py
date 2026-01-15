"""
Common constants - Re-exports from cec_docifycode_common
"""

from uuid6 import uuid7

# Import all constants from Common package
from cec_docifycode_common.utils.constants import (
    FileStatus,
    ContentType,
    ProgrammingLanguages,
    SyncStatus,
    BaseSpecification,
    GitType,
    LanguageType,
    DataManagementType,
    PermissionLevel,
    ProjectStatus,
    DOCIFYCODE_DIRECTORY_NAME,
    DEFAULT_DIRECTORY_BD_PD,
)

# Import collection constants
from cec_docifycode_common.utils.collection_constants import (
    UNIT_TEST_COLLECTION_MAP,
    DATA_MANAGEMENT_COLLECTION_MAP,
)

# ===================== MQTT Topic Constants =====================
MQTT_TOPIC_PREFIX = "/docifycode"
MQTT_TOPIC_GITINFO = f"{MQTT_TOPIC_PREFIX}/gitinfo"
MQTT_TOPIC_TASK_REGISTRATION = f"{MQTT_TOPIC_PREFIX}/TaskRegistration"


# Task Registration Topic with unique UUID (generated once at module load)
TASK_REGISTRATION_TOPIC = f"{MQTT_TOPIC_TASK_REGISTRATION}/{str(uuid7())}"
GENERATE_SOURCE_CODE_TOPIC = f"{MQTT_TOPIC_PREFIX}/code/{str(uuid7())}"
GET_PROJECT_STATUS_TOPIC = f"{MQTT_TOPIC_PREFIX}/status/"


# MQTT Project Event Types
class ProjectEventType:
    """Project event type constants for MQTT messages"""

    NEW = "new"
    EDIT = "edit"
    DELETE = "delete"


# HTTP Status Codes - Keep this local as it's project-specific
class HTTPStatus:
    """HTTP status code constants"""

    OK = 200
    CREATED = 201
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    INTERNAL_SERVER_ERROR = 500


# Rate Limiting - Keep this local as it's project-specific
class RateLimits:
    """Rate limiting constants"""

    PROJECT_GET = "200/minute"
    PROJECT_CREATE = "10/minute"
    PROJECT_UPDATE = "20/minute"
    PROJECT_DELETE = "10/minute"
    REQUIREMENT_DOC_GET = "200/minute"
    REQUIREMENT_DOC_UPLOAD = "10/minute"
    COMMENT_CREATE = "50/minute"


__all__ = [
    # From Common
    "FileStatus",
    "ContentType",
    "ProgrammingLanguages",
    "SyncStatus",
    "BaseSpecification",
    "GitType",
    "LanguageType",
    "DataManagementType",
    "PermissionLevel",
    "ProjectStatus",
    "DOCIFYCODE_DIRECTORY_NAME",
    "DEFAULT_DIRECTORY_BD_PD",
    "UNIT_TEST_COLLECTION_MAP",
    "DATA_MANAGEMENT_COLLECTION_MAP",
    # Local
    "TASK_REGISTRATION_TOPIC",
    "MQTT_TOPIC_PREFIX",
    "MQTT_TOPIC_GITINFO",
    "ProjectEventType",
    "HTTPStatus",
    "RateLimits",
]
