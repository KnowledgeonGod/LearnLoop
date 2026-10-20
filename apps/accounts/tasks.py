import logging

logger = logging.getLogger(__name__)


def send_welcome_email(user_id: int) -> None:
    # Stub. Later, make this a Celery task and call it with .delay(user_id).
    logger.info("Welcome email queued for user %s", user_id)