"""
Database connection manager
Handles MongoDB connection lifecycle
"""

import logging
from typing import Optional

from config_gateway import Config
from cec_docifycode_common.database import Database

logger = logging.getLogger(__name__)


class DatabaseConnection:
    """Manages MongoDB database connection"""

    def __init__(self):
        if not Config.MONGODB_URL:
            raise ValueError("INVALID_CONFIG: MONGODB_URL is required")
        if not Config.MONGODB_DATABASE:
            raise ValueError("INVALID_CONFIG: MONGODB_DATABASE is required")

        self._database: Optional[Database] = None

    def get_instance(self) -> Database:
        """Get or create database instance"""
        logger.info("[DatabaseConnection] Getting database instance")

        try:
            if self._database is None:
                self._database = Database(
                    mongodb_url=Config.MONGODB_URL,
                    mongodb_database=Config.MONGODB_DATABASE,
                )
                logger.info("[DatabaseConnection] Database instance created")

            return self._database

        except (ValueError, TypeError) as e:
            # Configuration error - log without exposing connection string
            logger.error(f"[DatabaseConnection] Configuration error: Invalid database settings")
            raise ValueError("Database configuration is invalid") from e
        except Exception as e:
            # Generic error - log without exposing connection string
            logger.error(f"[DatabaseConnection] Error creating instance: {type(e).__name__}")
            raise

    async def connect(self) -> Database:
        """Connect to database"""
        logger.info("[DatabaseConnection] Connecting to database")

        try:
            db = self.get_instance()
            if not await db.is_connected():
                await db.connect()
                logger.info("[DatabaseConnection] Connected successfully")
            else:
                logger.info("[DatabaseConnection] Already connected")

            return db

        except Exception as e:
            logger.error(f"[DatabaseConnection] Error connecting: {e}")
            raise

    async def disconnect(self):
        """Disconnect from database"""
        logger.info("[DatabaseConnection] Disconnecting from database")

        try:
            if self._database and await self._database.is_connected():
                await self._database.disconnect()
                logger.info("[DatabaseConnection] Disconnected successfully")
            else:
                logger.info("[DatabaseConnection] Already disconnected")

        except Exception as e:
            logger.error(f"[DatabaseConnection] Error disconnecting: {e}")
            raise


# Global instance
db_connection = DatabaseConnection()

