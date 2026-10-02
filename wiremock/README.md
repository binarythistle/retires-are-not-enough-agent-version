# WireMock Cloud mocks

Three mocks on WireMock Cloud stand in for parts of the demo:

| Mock | File(s) here | Source |
| --- | --- | --- |
| Conference Catalog API | `catalog-stubs.json`, `catalog-outage-stubs.json`, `catalog-booking-fault-stubs.json` | Generated from `apis/catalog/data.py` and `generate_fault_stubs.py` |
| LLM, happy path replay | `llm-happy-path-stubs.json`, `llm-fault-stubs.json` | Generated from a recorded real run and `generate_fault_stubs.py` |
| LLM, proxy to OpenAI | `llm-proxy-main-stub.json`, `llm-proxy-fault-stubs.json` (or `llm-fault-stubs.json`) | Hand written |

The agent needs no code changes to use them. Point it at a mock with environment variables. Leave
`/v1` off the LLM mock's URL: the OpenAI SDK appends `/chat/completions` to the base URL as
given, and the stubs match `/chat/completions`.

```bash
export OPENAI_BASE_URL=https://<llm-mock>.wiremockapi.cloud     # any OPENAI_API_KEY works
export CATALOG_BASE_URL=https://<catalog-mock>.wiremockapi.cloud
```

For the Temporal version these go in the **worker's** terminal. The worker logs
`Model ... at <base url>` on startup, so you can check the mock is in use.

## Catalog mock

```bash
uv run python wiremock/generate_catalog_stubs.py
```

Then, in the catalog mock on WireMock Cloud, delete the existing stubs, including the
hand-made World AI Summit and list stubs, and import:

- `catalog-stubs.json`: `GET /conferences`, `POST /bookings` for all six conferences (a fresh
  `CONF-XXXXXXXX` reference each time), a 404 for an unknown conference, and cancel.
- `catalog-outage-stubs.json` (optional): the first five `GET /conferences` return 503, then the
  list works again. It's a WireMock scenario, so reset scenarios in WireMock Cloud to replay it.

Rerun the generator whenever `apis/catalog/data.py` changes.

## LLM happy path mock

### 1. Record a real run

Use the plain agent (one process, so no retried calls are recorded twice) with a real key:

```bash
uv run uvicorn apis.catalog.main:app --port 5056      # self-hosted APIs, not the mocks
uv run uvicorn apis.flights.main:app --port 5057

export OPENAI_API_KEY="your-key"
OPENAI_RECORD_TO=wiremock/recordings uv run uvicorn plain_agent.main:app --port 5055
```

Each model call is written to `wiremock/recordings/<run>-NNN.json` with the request and
response bodies. Headers are not written, so the API key is never saved.

In a fresh browser tab (a new conversation), type exactly:

| Step | Type |
| --- | --- |
| Traveller details | `Les Jackson, LCY, aisle` |
| Conferences | `Amsterdam` |
| Attend | `World AI Summit` |
| Book the conference | `book` |
| Explore flights | `yes` |
| Select | `British Airways BA 724` |
| Book the flight | `book` |

The model decides the path, so if it goes off script (say, it asks a clarifying question),
stop the agent, delete that run's files and record again. Stop the agent when you're done, so
a later conversation doesn't add to the run.

### 2. Generate the stubs

```bash
uv run python wiremock/generate_llm_stubs.py            # newest run
uv run python wiremock/generate_llm_stubs.py 20261001-160000   # or a specific one
```

It prints one line per stub. Unlike the .NET generator, nothing is tied to one recording's
step numbers: how each stub matches is worked out from the recorded request.

| Recorded request | Stub matches on |
| --- | --- |
| The agent forced a tool (`book`, a flight number) | `$.tool_choice.function.name` |
| Ends with a tool result | `$.messages[-1].tool_call_id`, the call ID the previous stub returned |
| Ends with a "yes" | a "yes"-ish message, and the agent's question in `$.messages[-2]` |
| Ends with anything else you typed | `$.messages[-1].content` containing it, ignoring case |

Both forced bookings also get a "yes" twin, so `yes` works as well as `book`. Replies that
quote a booking reference copy the live one from the tool result with a response template.
Anything off the script gets a fallback reply: "(mock) That message is not in the recorded
happy path…".

WireMock scenarios are deliberately not used. A Temporal retry or a worker restart would
advance a scenario and desync the conversation. These stubs keep no state, so a retried model
call gets the same answer again, which is what makes the durability demo work with the mock.

### 3. Import

In the LLM mock on WireMock Cloud, delete the old happy path stubs and import
`llm-happy-path-stubs.json`.

The replies quote the dates as they were on the recording day, and call the conferences
"upcoming" from that day's point of view. Re-record if the data or the prompt changes.

## LLM proxy mock

This mock forwards to the real OpenAI, so the conversation is live, while still letting you
inject faults. Import `llm-proxy-main-stub.json`, which proxies every request to
`https://api.openai.com/v1` at priority 10. Then import **one** of these fault collections. Their
stubs have priority 1, so a matching request fails in WireMock instead of being forwarded:

| Collection | What happens | Good for |
| --- | --- | --- |
| `llm-proxy-fault-stubs.json` | The model call after `book` (conference or flight) returns 503 on **every** attempt, until you disable or delete the stub in WireMock Cloud | An outage you end on stage: the turn waits while Temporal retries, then you disable the stub and the reply arrives |
| `llm-fault-stubs.json` | The same calls fail a few times (503 then 429; 500 then a dropped connection), then go through to OpenAI | A blip that heals itself. Reset scenarios to re-arm it. See the retry demo below. |

- Don't import both fault collections at once. They match the same requests at the same
  priority.
- Never import `llm-happy-path-stubs.json` into this mock. Its priority 5 beats the proxy's 10,
  so it would replay the recording instead of calling OpenAI.
- The faults match the forced booking call, so type `book` to trigger them. A plain `yes` goes
  straight through to OpenAI.
- WireMock forwards the `Authorization` header, so the worker needs your **real**
  `OPENAI_API_KEY`, with `OPENAI_BASE_URL` set to this mock's URL (no `/v1`).

## Retry demo: fault stubs

```bash
uv run python wiremock/generate_fault_stubs.py
```

These calls fail a few times, then recover. Import each file **alongside** the stubs already
in that mock, not instead of them:

| File | Import into | What fails, in order |
| --- | --- | --- |
| `catalog-booking-fault-stubs.json` | Catalog mock | `POST /bookings`: 503, 429, dropped connection, then success |
| `llm-fault-stubs.json` | LLM mock | Model call after `book` (conference): 503, 429, then success |
| | | Model call after `book` (flight): 500, dropped connection, then success |

Every one of these failures is retryable, so the Temporal version retries with backoff and the
journey stays on the happy path. The attendee sees a slow reply (about 10 s for the
conference booking, about 3 s for the flight booking), not an error. On the worker, look for:

- **Worker log:** one line per failed attempt, e.g. `Conference Catalog API POST /bookings
  failed with HTTP 503 on attempt 1; retryable, Temporal will retry`.
- **Temporal UI:** the activity succeeds on attempt 3 or 4, with the last failure recorded.

Each fault is a WireMock scenario that moves on one state per call and then stays
"Recovered", so the faults fire once. **Reset scenarios** in WireMock Cloud to arm them again
before the next run. The 503 and 429 responses carry `Retry-After: 1`. The agent ignores it and
uses Temporal's backoff (1 s, 2 s, 4 s and so on, capped at 10 s for API calls).

The fault stubs are for the Temporal version. The plain agent has no retries for the APIs, so
a failed booking reaches the mocked LLM as a failed tool result, which the happy path stubs
don't cover.

Flight booking runs on the self-hosted Flights API, so arm its faults there instead, for
example the next three `POST /bookings` returning 503:

```bash
curl -X POST http://127.0.0.1:5057/admin/fault -H 'content-type: application/json' \
  -d '{"status": 503, "count": 3, "method": "POST", "pathPrefix": "/bookings"}'
```
