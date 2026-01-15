"""
MQTT Service - Singleton service for MQTT client management.
"""

import logging
from typing import Optional

from cec_docifycode_common.mqtt.mqtt_client import MQTTClient
from config_gateway import Config

logger = logging.getLogger(__name__)


class MQTTService:
    """Singleton service for managing MQTT client connection."""

    _instance: Optional["MQTTService"] = None
    _client: Optional[MQTTClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_client(self) -> MQTTClient:
        """
        Get or create MQTT client instance.

        Returns:
            MQTTClient: Connected MQTT client instance

        Raises:
            ConnectionError: If failed to connect to MQTT broker
        """
        if self._client is None:
            self._client = MQTTClient(
                client_id="api_test_publisher",
                broker_host=Config.MQTT_BROKER_HOST,
                broker_port=Config.MQTT_BROKER_PORT,
                keepalive=Config.MQTT_KEEPALIVE,
                username=Config.MQTT_USERNAME,
                password=Config.MQTT_PASSWORD,
                default_qos=Config.MQTT_DEFAULT_QOS,
            )
            if not self._client.connect():
                logger.error(
                    f"Failed to connect to MQTT broker at {Config.MQTT_BROKER_HOST}:{Config.MQTT_BROKER_PORT}"
                )
                raise ConnectionError(
                    f"Failed to connect to MQTT broker at {Config.MQTT_BROKER_HOST}:{Config.MQTT_BROKER_PORT}. "
                    "Please check broker configuration and availability."
                )
            logger.info(
                f"MQTT client initialized and connected to {Config.MQTT_BROKER_HOST}:{Config.MQTT_BROKER_PORT}"
            )
        return self._client

    def test_connection(self) -> dict:
        """Test MQTT connection and return status."""
        client = self.get_client()
        status_info = client.get_status()

        return {
            "mqtt_connected": status_info["connected"],
            "broker_host": status_info["broker_host"],
            "broker_port": status_info["broker_port"],
            "message": (
                "MQTT client is ready"
                if status_info["connected"]
                else "MQTT client is not connected"
            ),
        }


# Singleton instance
mqtt_service = MQTTService()
