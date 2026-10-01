# Conference Booker (Python) — Build Plan

A Python port of `../dotnet_agent_temporal`. The same conference booking use case, the same two
self-hosted APIs, the same frontend, and two agent hosts:

1. **Plain**: one process holds the conversation in memory. Kill it and the journey is gone.
2. **Temporal**: one workflow per conversation. Kill the worker and the journey carries on.

Built in slices. Each slice ends with something to test by hand before the next one starts.

---

## Stack (defaults, easy to change)

| Concern | Choice | Why |
| --- | --- | --- |
| Python | 3.12+ | Current Temporal SDK and typing support |
| Packaging | `uv` with a single `pyproject.toml` | One venv for the APIs, both agents, and the worker |
| HTTP servers | FastAPI + uvicorn | Closest match to ASP.NET minimal APIs |
| HTTP client | `httpx` (async) | |
| Models | Pydantic v2 | Equivalent of the C# records. Temporal's pydantic converter serialises them directly |
| LLM | `openai` SDK, **Chat Completions API** | Required so the existing WireMock LLM stubs still match (see below) |
| Temporal | `temporalio` | Later slices |

### Constraints carried from WireMock (in place from the start)

The LLM stubs in `../dotnet_agent_temporal/wiremock` match on request shape, so the Python agent
must send:

- `POST {base_url}/chat/completions` — use Chat Completions, not the Responses API, and make
  the base URL configurable (`OPENAI_BASE_URL`).
- The exact welcome prompt `Open the conversation and welcome the attendee.` as the last user
  message.
- The same 8 tool names and argument schemas.
- A forced tool as `tool_choice: {"type":"function","function":{"name": ...}}`.
- Tool results as `role: "tool"` messages carrying the model's `tool_call_id`.
- Model `gpt-5.6-luna`, `reasoning_effort: "none"`.
- Tool results as **PascalCase** JSON (`{"Saved":true,"Details":{...}}`), which is what .NET's
  serializer produced. The stubs template from these fields, e.g. `$.Booking.BookingReference`.
- Tool schemas with `"additionalProperties": false`.

Slice 3 checked this against `wiremock/recordings`: the welcome request is identical, and the
traveller-details tool result is byte-identical.

---

## Layout

```text
python_conference_agent/
  pyproject.toml
  apis/
    faults.py                  # shared fault-injection middleware + /admin/fault
    catalog/main.py, data.py   # Conference Catalog API  :5056
    flights/main.py, data.py   # Flight Search API       :5057
  conference_booker/           # shared core (≈ ConferenceBooker.Core)
    journey.py                 # JourneyState, TravellerDetails, ConversationEntry, ToolActivity
    catalog.py, flights.py     # domain models + httpx clients for the two APIs
    agent/
      tools.py                 # tool declarations (OpenAI JSON schema)
      policy.py                # system prompt, forced-tool rule, completion summary, MAX_STEPS
      state_tools.py           # save_traveller_details, select_conference, select_flight
      results.py               # tool result shapes the model sees
      model.py                 # one Chat Completions round-trip -> ModelResponse
  web/static/                  # index.html, site.css, site.js — copied from .NET wwwroot
  plain_agent/                 # Version 1: FastAPI app + in-memory store + agent loop  :5055
  temporal_agent/              # Version 2: workflow, activities, worker, thin FastAPI client  :5055
```

The shared core is the important part for the talk. Both agent versions use the same prompt,
tools, state transitions, and API clients, so the differences on screen are only the ones
Temporal causes.

**Agent loop style.** The .NET plain version used `UseFunctionInvocation()` to hide the loop.
The OpenAI Python SDK has no equivalent for Chat Completions, so both Python versions use the
same explicit loop: call the model, dispatch the tool calls, append the results, repeat. Moving
to Temporal then means running each model call and each API tool as an activity. The loop
itself barely changes.

Ports match the .NET project (5055 web, 5056 catalog, 5057 flights), so the WireMock config and
control panel can point at either implementation. Don't run both implementations at once.

---

## Slices

### Part 1 — Plain (non-Temporal)

**Slice 1 — The two APIs** ✅
- Catalog API: `GET /health`, `GET /conferences`, `GET /conferences/{id}`, `POST /bookings`,
  `GET /bookings`, `GET /bookings/{ref}`, `POST /bookings/{ref}/cancel`.
- Flights API: `GET /health`, `GET /flights?origin&destination` (city or airport code,
  case-insensitive), `GET /flights/{id}`, `POST /bookings`, `GET /bookings`, `GET /bookings/{ref}`.
- The same static data, camelCase JSON, booking reference formats (`CONF-XXXXXXXX`, `CB-XXXXXXXX`),
  optional `idempotencyKey` support, and fault injection (`/admin/fault`, `X-Demo-Control`
  bypass header, `Retry-After` on 429/503).
- *Test:* run both and use curl or the `.http` file to list, filter, book, repeat a booking with
  the same key, and arm and clear a fault.

**Slice 2 — Frontend and web shell (no LLM yet)** ✅
- Serve the copied `index.html` / `site.css` / `site.js` unchanged.
- `POST /api/welcome`, `POST /api/chat`, `GET /api/conversations/{id}`,
  `DELETE /api/conversations/{id}` (in the plain version it forgets the conversation), with the
  same request and response shapes the JS expects. A chat for a conversation lost in a restart
  returns a 404 explaining that the process restarted.
- In-memory conversation store with a per-conversation `asyncio.Lock`.
- The "agent" echoes the message back, with a fake tool activity so the activity chips render.
- *Test:* the page looks identical to the .NET one, refresh restores the transcript, and
  restarting the process loses it.

**Slice 3 — Real agent, first tools** ✅
- Shared core: journey state, system prompt, tool declarations, model round-trip.
- Explicit agent loop (`MAX_STEPS = 8`) wired into the plain app.
- Tools: `get_current_date_time`, `save_traveller_details`, `get_available_conferences`.
- *Test:* the welcome message arrives, traveller details are saved, and only future
  conferences are listed.

**Slice 4 — The full journey** ✅
- Tools: `select_conference`, `book_selected_conference`, `get_flight_options`,
  `select_flight`, `book_selected_flight`.
- Forced-tool rule (`book`, `confirm`, a flight number) and the completion summary.
- *Test:* the full happy path, ending in "Journey complete". Then the crash demo: kill the
  process mid-journey and confirm the conversation is gone. Also kill it during a booking turn
  and check `GET /bookings`: the booking exists but the agent has lost track of it.

✅ **Plain version done.**

### Part 2 — Temporal

**Slice 5 — Make it durable** ✅
- `ConferenceBookerWorkflow`: one per conversation (`conversation-<id>`) that owns `JourneyState`.
- Activities: `invoke_model`, plus the four API tools. The three state tools and the clock run
  inline in the workflow (`workflow.now()`).
- Worker process (needs `OPENAI_API_KEY`). The web app becomes a thin client: update-with-start
  for welcome, an update for chat, a query for restore, and cancel for delete.
- OpenAI client created with `max_retries=0`, because Temporal owns retries.
- *Test:* kill the worker mid-turn, restart it, and the turn completes. Restart the web app and
  the transcript is still there. Inspect the history in the Temporal UI.
- Pulled forward from Slice 7: the turn lock and the `get_token_usage` query.
- Added beyond .NET: the model activity heartbeats every second with a 5 s heartbeat timeout.
  Without it, a worker killed mid model call is only detected after the full 60 s
  `start_to_close_timeout`. With it, the turn resumes in about 5 s.
- Raw `httpx` exceptions were too big for the SDK's failure size limit, so the history showed
  `Failure exceeds size limit.`. Fixed in Slice 6, which raises short `ApplicationError`s.

**Slice 6 — Failure handling** ✅
- Classify API failures: 4xx except 408/429 → non-retryable `ApplicationError`; everything
  else retries. Do the same for model failures.
- Turn permanent failures into failed tool results that the model explains.
- Idempotency keys on both bookings (`<workflowId>:<purpose>:<id>`).
- Saga: if the flight booking fails permanently, cancel the conference booking.
- *Test:* arm faults with `/admin/fault`: 503×3 on the catalog, a 400 on flight booking (saga),
  and a 429 until cleared.
- A permanent model failure (bad key, rejected request) fails the update, not the workflow. The
  page shows "The agent could not complete that turn", and the next message works.
- The .NET "compensation also fails" scenario arms a **500** on the cancel endpoint. A 500 is
  retryable, so the compensation keeps retrying until the fault is cleared and then succeeds.
  To see the "could not be cancelled" reply, arm a 400 instead.

**Slice 7 — Lifecycle and observability** ✅
- 1 h idle timeout, cancellation recorded in the transcript, continue-as-new after 25 turns or
  when suggested. (The turn gate, token usage query and round-trip logging arrived in Slice 5.)

### Part 3 — WireMock and demo tooling

**Slice 8 — WireMock**
- Point `OPENAI_BASE_URL` at the mock and `CATALOG_BASE_URL` at the catalog stubs, and confirm
  the existing stubs replay the scripted happy path unchanged.
- Port the model-exchange recorder (`OPENAI_RECORD_TO`) if we need to re-record.

**Slice 9 — Control panel (optional)**
- Adapt `controlpanel/app.py` to launch the Python processes instead of the .NET DLLs.

---

## Watch out for

- **Dates.** Conference dates and IDs are one year later than in .NET (for example
  `world-ai-summit-amsterdam-2027`). The WireMock LLM and catalog stubs still contain the 2026
  IDs and dates, so they need updating or re-recording in Slice 8.
- **Temporal sandbox.** Pass `openai`, `httpx`, and pydantic through the workflow sandbox (or
  keep them out of workflow modules). Workflow code must use `workflow.now()` and must not
  do I/O.
- **Plain-version crash semantics.** Leave idempotency keys out of the plain version on purpose,
  so a retried booking after a crash creates a visible duplicate. This contrast is part of the
  demo.
