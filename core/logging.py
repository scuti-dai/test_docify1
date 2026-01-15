"""
Logging configuration
"""

import logging
import sys
from datetime import datetime
from config_gateway import Config


def setup_logging():
    """Setup logging configuration"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO if not Config.DEBUG else logging.DEBUG)

    # If handlers already exist (e.g., when running under uvicorn), avoid adding duplicates
    if logger.handlers:
        return logger

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (optional)
    if Config.ENVIRONMENT == "production":
        file_handler = logging.FileHandler(
            f"logs/app_{datetime.now().strftime('%Y%m%d')}.log"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Disable DEBUG logs from third-party libraries
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("multipart.multipart").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpcore.connection").setLevel(logging.WARNING)
    logging.getLogger("httpcore.http11").setLevel(logging.WARNING)

    return logger
