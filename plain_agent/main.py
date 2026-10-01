"""Conference Booker, plain version: page, API, and agent all in one process. Run with:

    uv run uvicorn plain_agent.main:app --port 5055
"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from conference_booker.journey import CamelModel, ConversationEntry, ToolActivity
from plain_agent.agent import EchoAgent
from plain_agent.store import InMemoryConversationStore

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"

# Shown when the process restarted under an open page. The plain version cannot recover the
# conversation, and saying so plainly is the demo.
CONVERSATION_LOST = (
    "This conversation no longer exists: the agent process restarted and lost it. "
    "Refresh the page to start a new journey."
)


# Start chat: no message, just used to open the conversation.
class ConversationRequest(CamelModel):
    conversation_id: str | None = None


# Continue chat: message required. Optional here so a missing one is our 400, not a 422.
class ChatRequest(CamelModel):
    message: str | None = None
    conversation_id: str | None = None


class ChatReply(CamelModel):
    message: str
    conversation_id: uuid.UUID
    tool_activities: list[ToolActivity]
    is_complete: bool


# Carries the whole conversation history.
class ConversationTranscriptReply(CamelModel):
    conversation_id: uuid.UUID
    messages: list[ConversationEntry]
    is_complete: bool


app = FastAPI(title="Conference Booker (plain)")
conversations = InMemoryConversationStore()
agent = EchoAgent()


def parse_id(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(value) if value else None
    except ValueError:
        return None


def bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


@app.post("/api/welcome")
async def welcome(request: ConversationRequest | None = None) -> ChatReply:
    # Welcome is the only endpoint that mints an id: no usable id means a brand new journey.
    conversation_id = parse_id(request.conversation_id if request else None) or uuid.uuid4()
    conversation = conversations.get_or_create(conversation_id)
    reply = await agent.start(conversation)
    return ChatReply(conversation_id=conversation_id, **reply.model_dump())


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

    reply = await agent.run(conversation, request.message)
    return ChatReply(conversation_id=conversation_id, **reply.model_dump())


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
