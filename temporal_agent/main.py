"""Conference Booker, Temporal version: the web app. It serves the page and talks to Temporal,
so it needs no model key and no API base URLs. The agent runs in the worker. Run with:

    uv run uvicorn temporal_agent.main:app --port 5055
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from temporalio.client import Client, WithStartWorkflowOperation
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.envconfig import ClientConfig
from temporalio.service import RPCError, RPCStatusCode

from conference_booker.journey import ALREADY_COMPLETE, AgentTurnReply
from conference_booker.web import (
    STATIC_DIR,
    ChatReply,
    ChatRequest,
    ConversationRequest,
    ConversationTranscriptReply,
    bad_request,
    parse_id,
)
from temporal_agent.shared import TASK_QUEUE, workflow_id
from temporal_agent.workflow import ConferenceBookerWorkflow

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

temporal: Client


@asynccontextmanager
async def lifespan(_: FastAPI):
    global temporal
    # TEMPORAL_ADDRESS and TEMPORAL_NAMESPACE override the local dev server defaults.
    connect_config = ClientConfig.load_client_connect_config()
    connect_config.setdefault("target_host", "localhost:7233")
    temporal = await Client.connect(**connect_config, data_converter=pydantic_data_converter)
    yield


app = FastAPI(title="Conference Booker (Temporal)", lifespan=lifespan)


@app.post("/api/welcome")
async def welcome(request: ConversationRequest | None = None) -> ChatReply:
    # Welcome is the only endpoint that mints an id: no usable id means a brand new journey.
    conversation_id = parse_id(request.conversation_id if request else None) or uuid.uuid4()

    # Update-with-start: one call both starts the conversation's workflow and delivers the
    # opening turn. USE_EXISTING means a repeated welcome lands on the workflow already running.
    reply: AgentTurnReply = await temporal.execute_update_with_start_workflow(
        ConferenceBookerWorkflow.welcome,
        start_workflow_operation=WithStartWorkflowOperation(
            ConferenceBookerWorkflow.run,
            None,
            id=workflow_id(conversation_id),
            task_queue=TASK_QUEUE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        ),
    )
    return ChatReply.of(conversation_id, reply)


@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not (request.message or "").strip():
        return bad_request("A message is required.")

    # Unlike welcome, this never invents an id. A message can only continue a conversation
    # that welcome already opened, so a missing id is a bad request rather than a new journey.
    conversation_id = parse_id(request.conversation_id)
    if conversation_id is None:
        return bad_request("A valid conversation id is required.")

    handle = temporal.get_workflow_handle_for(ConferenceBookerWorkflow.run, workflow_id(conversation_id))

    try:
        # An update rather than a signal, because the browser is waiting for the reply.
        reply = await handle.execute_update(ConferenceBookerWorkflow.send_message, request.message)
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        # The workflow has completed, so its journey is finished and it accepts no more turns.
        return ChatReply(message=ALREADY_COMPLETE, conversation_id=conversation_id, tool_activities=[], is_complete=True)

    return ChatReply.of(conversation_id, reply)


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    parsed_id = parse_id(conversation_id)
    if parsed_id is None:
        return Response(status_code=404)

    handle = temporal.get_workflow_handle_for(ConferenceBookerWorkflow.run, workflow_id(parsed_id))

    try:
        # Queries work against a completed workflow too, so a finished journey still restores.
        transcript = await handle.query(ConferenceBookerWorkflow.get_transcript)
    except RPCError as error:
        if error.status != RPCStatusCode.NOT_FOUND:
            raise
        return Response(status_code=404)

    return ConversationTranscriptReply(
        conversation_id=parsed_id, messages=transcript.messages, is_complete=transcript.is_complete
    )


# Starting a new journey cancels the old workflow rather than leaving it running.
@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> None:
    parsed_id = parse_id(conversation_id)
    if parsed_id is None:
        return

    handle = temporal.get_workflow_handle(workflow_id(parsed_id))
    try:
        await handle.cancel()
    except RPCError as error:
        # Nothing to cancel, which is the same outcome the caller wanted.
        if error.status != RPCStatusCode.NOT_FOUND:
            raise


# Mounted last so the API routes win; html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
