# Conference Booker (Python)

A Python port of `../dotnet_agent_temporal`. See [PLAN.md](PLAN.md) for the slices and progress.

## Setup

```bash
uv sync
```

## Plain agent (no Temporal) — port 5055

Start the Conference Catalog API first (see below), then:

```bash
export OPENAI_API_KEY="your-key"
uv run uvicorn plain_agent.main:app --port 5055
```

Optional settings: `OPENAI_MODEL` (default `gpt-5.6-luna`), `OPENAI_BASE_URL` (for example a
WireMock URL), `CATALOG_BASE_URL` (default `http://127.0.0.1:5056`) and `FLIGHTS_BASE_URL`
(default `http://127.0.0.1:5057`). The app refuses to start without `OPENAI_API_KEY`.

Open <http://127.0.0.1:5055>. The page, API and agent all run in this one process, and
conversations live only in its memory. Restart it and every journey is lost: a refresh starts
a new journey, and an open page that sends a message is told the conversation is gone.

The log prints one line per model round-trip with its token counts, and one line per turn
with the journey's running total.

Both APIs must be running. The full journey, which is also the WireMock script:

| Step | Type | Tool called |
| --- | --- | --- |
| Traveller details | `Les Jackson, LCY, isle` | `save_traveller_details` |
| Find conferences | `Amsterdam` | `get_current_date_time`, `get_available_conferences` |
| Choose one | `World AI Summit` | `select_conference` |
| Book it | `book` | `book_selected_conference` (forced) |
| Explore flights | `yes` | `get_flight_options` |
| Choose a flight | `British Airways BA 724` | `select_flight` (forced by the flight number) |
| Book it | `book` | `book_selected_flight` (forced), then "Journey complete" |

### Crash demo

The bookings send no idempotency key, on purpose:

1. Get as far as `book` for the conference. Kill the agent process (`Ctrl+C`, or
   `kill -9` for effect) while the reply is still pending, or straight after it arrives.
2. `curl http://127.0.0.1:5056/bookings` shows the conference booking exists.
3. Restart the agent and refresh the page. The journey is gone, and the agent has no idea the
   ticket was booked.
4. Go through the journey again and book. `GET /bookings` now holds a duplicate booking for the
   same traveller.

The frontend in `web/static/` is copied from the .NET `wwwroot/`, and both agent versions
serve it. The only change is the message box placeholder, which now reads "Type your reply…"
instead of "Which conferences can I attend?", so it fits every step of the conversation.

## Temporal agent — port 5055

The same journey and page, but each conversation is a Temporal workflow
(`conversation-<id>`). The worker runs the agent. The web app is a thin Temporal client that
needs no OpenAI key.

```bash
temporal server start-dev                         # gRPC 7233, UI http://localhost:8233
uv run uvicorn apis.catalog.main:app --port 5056
uv run uvicorn apis.flights.main:app --port 5057

export OPENAI_API_KEY="your-key"
uv run python -m temporal_agent.worker            # the only process that needs the key

uv run uvicorn temporal_agent.main:app --port 5055
```

Stop the plain agent first, because both versions use port 5055. `TEMPORAL_ADDRESS` and
`TEMPORAL_NAMESPACE` point at a different cluster. The worker reads the same `OPENAI_*`,
`CATALOG_BASE_URL` and `FLIGHTS_BASE_URL` settings as the plain agent.

| Browser call | Temporal |
| --- | --- |
| `POST /api/welcome` | update-with-start: starts the workflow and runs the opening turn |
| `POST /api/chat` | `send_message` update; the reply is the update result |
| `GET /api/conversations/{id}` | `get_transcript` query (works after completion too) |
| `DELETE /api/conversations/{id}` | cancels the workflow |

Each model round-trip and each API call is an activity, so the history in the UI shows the
whole turn step by step. The clock and the three state tools run inline in the workflow.

### Durability demos

- **Kill the worker mid-turn.** Send a message, then `kill -9` the worker while the reply is
  pending, and start it again. The turn resumes and the reply arrives. The model activity
  heartbeats, so a dead worker is noticed in about 5 s.
- **Restart the web app.** Refresh the page and the transcript is restored from the workflow.
- **API outage.** Stop the Catalog API, ask about conferences, then start it again. The turn
  waits while Temporal retries, then completes.

### Failure demos

Arm faults with `apis/requests.http` or curl against `/admin/fault`:

| Fault | What happens |
| --- | --- |
| Catalog `503`, `count: 3`, method `GET` | Temporal retries with backoff, the API heals, the turn completes. The attendee sees a slow reply, not an error. |
| Flights `429` until cleared | The turn stalls while Temporal retries. Clear the fault and the reply arrives. |
| Flights `400` on `POST /bookings` | **Saga.** The flight booking fails permanently (no retries), and the workflow cancels the conference booking: `GET /bookings` on the catalog shows it `Cancelled`. |
| Plus Catalog `400` on `POST /bookings/` | The trailing slash hits only the cancel endpoint. Compensation fails, and the reply says the ticket could not be released. (With a `500` instead, the cancel keeps retrying until you clear it.) |

The rule is the same as in .NET. A 4xx other than 408 or 429 means the request itself is wrong,
so the activity fails immediately and the model is told. Everything else is retried until it
works. The worker logs every failed attempt and whether it will be retried.

Both bookings send an idempotency key built from the workflow ID and the item being booked, so
a retried POST returns the original booking instead of a duplicate. Compare this with the plain
version's crash demo.

Token usage so far:

```bash
temporal workflow query --type get_token_usage --workflow-id conversation-<id>
```

## Conference Catalog API — port 5056

```bash
uv run uvicorn apis.catalog.main:app --port 5056
```

`GET /conferences`, `GET /conferences/{id}`, `POST /bookings`, `GET /bookings`,
`GET /bookings/{ref}`, `POST /bookings/{ref}/cancel`.

## Flight Search API — port 5057

```bash
uv run uvicorn apis.flights.main:app --port 5057
```

`GET /flights?origin=&destination=` (city or airport code, case-insensitive),
`GET /flights/{id}`, `POST /bookings`, `GET /bookings`, `GET /bookings/{ref}`.

## Shared behaviour

- JSON is camelCase, the same as the .NET APIs.
- `POST /bookings` accepts an optional `idempotencyKey`. Sending a key again returns the
  original booking with `200` instead of a new `201`.
- Bookings are held in memory, so restarting an API empties its booking list.
- Fault injection: `POST /admin/fault` with `{"status", "count"?, "method"?, "pathPrefix"?}` arms
  a fault, `GET` inspects it and `DELETE` clears it. `/admin` and `/health` are never faulted.
  Requests carrying an `X-Demo-Control` header bypass the fault. 429 and 503 responses include
  `Retry-After: 1`.

[`apis/requests.http`](apis/requests.http) has ready-made requests for manual testing.

## Demo data

The conference dates are one year later than in the .NET project, so every conference is in
the future again. Conference IDs changed with them, e.g. `world-ai-summit-amsterdam-2027`.
