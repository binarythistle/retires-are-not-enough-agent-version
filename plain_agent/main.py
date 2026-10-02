"""Conference Booker, plain version: page, API, and agent all in one process. Run with:

    uv run uvicorn plain_agent.main:app --port 5055
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import openai
from openai import AsyncOpenAI

from conference_booker.catalog import ConferenceCatalogClient
from conference_booker.flights import FlightSearchClient
from conference_booker.recording import recording_http_client
from conference_booker.settings import Settings
from conference_booker.web import (
    STATIC_DIR,
    ChatReply,
    ChatRequest,
    ConversationRequest,
    ConversationTranscriptReply,
    bad_request,
    parse_id,
)
from plain_agent.agent import ConferenceBookerAgent
from plain_agent.store import InMemoryConversationStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

# Shown when the process restarted under an open page. The plain version cannot recover the
# conversation, and saying so plainly is the demo.
# The model failed even after the OpenAI SDK's own in-process retries (two by default). Nothing
# retries the turn after that: the attendee has to send the message again.
TURN_FAILED = "The agent could not complete that turn. Please try again."

CONVERSATION_LOST = (
    "This conversation no longer exists: the agent process restarted and lost it. "
    "Refresh the page to start a new journey."
)


conversations = InMemoryConversationStore()
agent: ConferenceBookerAgent


@asynccontextmanager
async def lifespan(_: FastAPI):
    global agent
    # Fails at startup with a clear message if OPENAI_API_KEY is missing.
    settings = Settings.from_env()

    async with (
        httpx.AsyncClient(base_url=settings.catalog_base_url, timeout=10) as catalog_http,
        httpx.AsyncClient(base_url=settings.flights_base_url, timeout=10) as flights_http,
        AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=60,
            http_client=recording_http_client(settings.openai_record_to) if settings.openai_record_to else None,
        ) as openai,
    ):
        agent = ConferenceBookerAgent(
            openai,
            settings.openai_model,
            ConferenceCatalogClient(catalog_http),
            FlightSearchClient(flights_http),
        )
        logging.getLogger(__name__).info("Model %s at %s", settings.openai_model, openai.base_url)
        yield


app = FastAPI(title="Conference Booker (plain)", lifespan=lifespan)


@app.post("/api/welcome")
async def welcome(request: ConversationRequest | None = None) -> ChatReply:
    # Welcome is the only endpoint that mints an id: no usable id means a brand new journey.
    conversation_id = parse_id(request.conversation_id if request else None) or uuid.uuid4()
    conversation = conversations.get_or_create(conversation_id)
    try:
        reply = await agent.start(conversation)
    except openai.OpenAIError as error:
        return turn_failed(error)
    return ChatReply.of(conversation_id, reply)


@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not (request.message or "").strip():
        return bad_request("A message is required.")

    # Unlike welcome, this never invents an id. A message can only continue a conversation
    # that welcome already opened.
    conversation_id = parse_id(request.conversation_id)
    if conversation_id is None:
        return bad_request("A valid conversation id is required.")

    conversation = conversations.get(conversation_id)
    if conversation is None:
        return JSONResponse({"error": CONVERSATION_LOST}, status_code=404)

    try:
        reply = await agent.run(conversation, request.message)
    except openai.OpenAIError as error:
        return turn_failed(error)
    return ChatReply.of(conversation_id, reply)


def turn_failed(error: openai.OpenAIError) -> JSONResponse:
    logging.getLogger(__name__).warning("Turn failed: %r", error)
    return JSONResponse({"error": TURN_FAILED}, status_code=502)


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    parsed_id = parse_id(conversation_id)
    conversation = conversations.get(parsed_id) if parsed_id else None
    if conversation is None:
        return Response(status_code=404)

    return ConversationTranscriptReply(
        conversation_id=parsed_id,
        messages=conversation.state.entries,
        is_complete=conversation.state.is_complete,
    )


# "Start new journey" forgets the old conversation rather than leaving it in memory.
@app.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str) -> None:
    if parsed_id := parse_id(conversation_id):
        conversations.remove(parsed_id)


# Mounted last so the API routes win; html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
