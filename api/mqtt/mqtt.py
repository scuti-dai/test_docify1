"""
MQTT API endpoints
Handle MQTT subscription and message retrieval
"""

import logging
import json
import asyncio
from typing import Optional
from fastapi import APIRouter
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import ValidationError

from app.services.mqtt_service import mqtt_service
from app.services.project_service import project_service
from app.utils.constants import GET_PROJECT_STATUS_TOPIC, ProjectStatus
from app.services.logs_service import save_exception_log_sync, LogLevel
from cec_docifycode_common.schemas.status_schema import StatusMessage

logger = logging.getLogger(__name__)
router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


# Queue for status messages to be processed asynchronously
# Using asyncio.Queue for proper async blocking instead of polling
_status_message_queue: asyncio.Queue[StatusMessage] = asyncio.Queue()
_processing_task: Optional[asyncio.Task] = None


def _handle_status_message(topic: str, payload: str) -> None:
    """Handle incoming status message from /docifycode/status topic"""
    logger.info(
        f"[_handle_status_message] ===== CALLBACK CALLED ===== topic={topic}, payload={payload}, payload_length={len(payload)}"
    )

    try:
        # Parse JSON message
        if not payload or payload.strip() == "":
            logger.warning("[_handle_status_message] Empty payload")
            return

        payload_dict = json.loads(payload)
        status_msg = StatusMessage.model_validate(payload_dict)

        logger.info(
            f"[_handle_status_message] Validated message - project_id={status_msg.project_id}, status={status_msg.status}"
        )

        # Add to queue for async processing
        _status_message_queue.put_nowait(status_msg)
        logger.info(
            f"[_handle_status_message] Added to queue - queue_size={_status_message_queue.qsize()}"
        )

    except json.JSONDecodeError as e:
        logger.error(f"[_handle_status_message] Invalid JSON: {e}, payload={payload}")
    except ValidationError as e:
        logger.error(
            f"[_handle_status_message] Validation error: {e}, payload={payload}"
        )
    except Exception as e:
        error_message = f"[_handle_status_message] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)


async def _process_status_update(status_msg: StatusMessage) -> None:
    """Process status update for project"""
    logger.info(
        f"[_process_status_update] Start - project_id={status_msg.project_id}, status={status_msg.status}"
    )

    if not status_msg.project_id or not status_msg.status:
        logger.warning("[_process_status_update] Missing project_id or status")
        return

    try:
        project_id = status_msg.project_id
        status_value = status_msg.status
        # Call project service to update status in DB
        is_updated = await project_service.update_project_status(
            project_id=project_id, status=status_value
        )

        if not is_updated:
            logger.warning(
                f"[_process_status_update] Failed to update status - project_id={project_id}"
            )
            return

        logger.info(
            f"[_process_status_update] Successfully updated status - project_id={project_id}, status={status_value}"
        )

        topic = GET_PROJECT_STATUS_TOPIC + "+"
        # Check if status is completed, then clear retention
        if status_value == ProjectStatus.COMPLETED:
            logger.info(
                f"[_process_status_update] Status is completed, clearing retention - topic={topic}, project_id={project_id}"
            )
            clear_retention(topic)

    except Exception as e:
        error_message = f"[_process_status_update] Error - project_id={project_id}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)


def clear_retention(topic: str) -> None:
    """Publish empty message with retain=True to clear retention"""
    logger.info(f"[clear_retention] Start - topic={topic}")

    try:
        client = mqtt_service.get_client()
        # Publish empty message with retain=True to clear retention
        success = client.publish(topic=topic, message="", qos=2, retain=False)

        if success:
            logger.info(f"[clear_retention] Success - topic={topic}")
        else:
            logger.warning(f"[clear_retention] Failed - topic={topic}")

    except Exception as e:
        error_message = f"[clear_retention] Error - topic={topic}: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)


async def _process_message_queue() -> None:
    """Background task to process status messages from queue

    Uses asyncio.Queue for proper async blocking instead of polling.
    This is the standard Python async pattern.
    """
    logger.info("[_process_message_queue] Start background processing")

    try:
        while True:
            # Block until message is available (no polling)
            message = await _status_message_queue.get()
            try:
                logger.info(
                    f"[_process_message_queue] Processing message - project_id={message.project_id}, status={message.status}"
                )
                await _process_status_update(message)
            except Exception as e:
                error_message = (
                    f"[_process_message_queue] Error processing message: {e}"
                )
                logger.error(error_message)
                save_exception_log_sync(e, error_message, __name__)
            finally:
                # Mark task as done
                _status_message_queue.task_done()

    except asyncio.CancelledError:
        logger.info("[_process_message_queue] Background task cancelled")
    except Exception as e:
        error_message = f"[_process_message_queue] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)


async def subscribe_status_topic() -> None:
    """Subscribe to MQTT topic /docifycode/status/and handle project status updates"""
    logger.info("[subscribe_status_topic] Start")

    try:
        # Get client (this will connect if not already connected)
        client = mqtt_service.get_client()

        # Wait a bit for connection to be established
        # is_connected is set in _on_connect callback which may take a moment

        max_retries = 10
        retry_count = 0

        while not client.is_connected and retry_count < max_retries:
            await asyncio.sleep(0.5)
            retry_count += 1
            logger.info(
                f"[subscribe_status_topic] Waiting for connection... (attempt {retry_count}/{max_retries})"
            )

        if not client.is_connected:
            logger.error(
                "[subscribe_status_topic] MQTT client not connected after retries"
            )
            return

        # Subscribe to topic to receive status messages
        # Use multi-level wildcard # to receive all subtopics
        topic = GET_PROJECT_STATUS_TOPIC + "+"
        logger.info(
            f"[subscribe_status_topic] Attempting to subscribe to topic: {topic}"
        )
        logger.info(f"[subscribe_status_topic] Client connected: {client.is_connected}")

        success = client.subscribe(topic=topic, callback=_handle_status_message, qos=2)

        if success:
            logger.info(
                f"[subscribe_status_topic] Successfully subscribed to topic: {topic}"
            )
            logger.info(
                f"[subscribe_status_topic] Callback registered: {_handle_status_message.__name__}"
            )

            # Start background task to process messages
            global _processing_task
            if _processing_task is None or _processing_task.done():
                _processing_task = asyncio.create_task(_process_message_queue())
                logger.info(
                    "[subscribe_status_topic] Background message processor started"
                )
        else:
            logger.error(
                f"[subscribe_status_topic] Failed to subscribe to topic: {topic}"
            )

    except Exception as e:
        error_message = f"[subscribe_status_topic] Error: {e}"
        logger.error(error_message)
        save_exception_log_sync(e, error_message, __name__)
