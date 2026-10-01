"""Names shared by the web app, the worker and the workflow."""

from __future__ import annotations

import uuid

TASK_QUEUE = "conference-booker"


def workflow_id(conversation_id: uuid.UUID) -> str:
    return f"conversation-{conversation_id}"
