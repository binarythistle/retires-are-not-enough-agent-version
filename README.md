# Conference Booker: a durable AI agent with Temporal

AI agents call models and APIs that fail: rate limits, outages, dropped connections, crashed
processes. This repo is a small conference travel agent, written twice:

- a **plain Python agent**, which loses its place when things go wrong, and
- the **same agent on [Temporal](https://temporal.io)**, which retries, recovers and carries on.

You'll run both against the same OpenAI model and the same two APIs, break things on purpose,
and watch how each one copes.

---

## How it fits together

```text
Browser ──► Agent web app :5055 ──► OpenAI (the model decides which tools to call)
                 │
                 ├──► Conference Catalog API :5056   list conferences, book, cancel
                 └──► Flight Search API      :5057   search flights, book
```

- **Plain agent:** the page, the API and the agent loop all run in one process. Conversations
  live in its memory.
- **Temporal agent:** the web app is a thin client. Each conversation is a Temporal workflow,
  and a separate **worker** process runs the agent loop. Every model call and every API call is
  an activity, recorded in the workflow's history and retried by Temporal.

Both agents share the same prompt, tools and page, so any difference you see comes from Temporal.

---

## 1. Prerequisites

| You need | Why | Install |
| --- | --- | --- |
| **uv** | Runs everything and installs the right Python (3.12 or later) for you | macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh`<br>Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"`<br>or `brew install uv` |
| **Temporal CLI** | Runs a local Temporal server for part 3 onwards | macOS: `brew install temporal`<br>Linux/macOS: `curl -sSf https://temporal.download/cli.sh \| sh`<br>Windows: download from <https://temporal.download/cli> |
| **An OpenAI API key** | The agent's model | <https://platform.openai.com/api-keys> |
| **Git** | To clone the repo | <https://git-scm.com> |
| A **WireMock Cloud** account (free) | Only for failure scenarios 4.2 to 4.4 | <https://app.wiremock.cloud> |

You don't need to install Python yourself: `uv` downloads the version this repo pins.

Check the tools are on your path:

```bash
uv --version
temporal --version
```

Clone the repo and install the dependencies:

```bash
git clone <repo-url> conference-booker
cd conference-booker
uv sync
```

Run every command in this README from the repo root.

> **Windows (PowerShell):** where this README says `export NAME=value`, use
> `$env:NAME="value"` instead. Where it says `pkill -9 -f <name>`, close that terminal instead.

---

## 2. Baseline: the agent without Temporal

This setup uses the live OpenAI API and both self-hosted APIs. It needs no WireMock and no
Temporal.

Open three terminals:

```bash
# Terminal 1: Conference Catalog API
uv run uvicorn apis.catalog.main:app --port 5056
```

```bash
# Terminal 2: Flight Search API
uv run uvicorn apis.flights.main:app --port 5057
```

```bash
# Terminal 3: the plain agent
export OPENAI_API_KEY="sk-..."
uv run uvicorn plain_agent.main:app --port 5055
```

Open <http://127.0.0.1:5055> and book a trip. Any wording works with the live model; this
script is the one used throughout the workshop:

| Step | Type |
| --- | --- |
| Traveller details | `Les Jackson, LCY, aisle` |
| Find conferences | `Amsterdam` |
| Choose one | `World AI Summit` |
| Book it | `book` |
| Explore flights | `yes` |
| Choose a flight | `British Airways BA 724` |
| Book it | `book` |

Each reply lists the tools the agent called, with their outcome and duration. The journey ends
with **Journey complete** and both booking references.

### Try it: pull the plug

1. Start a new journey and get as far as booking the conference (`book`).
2. Kill the agent, hard:

   ```bash
   pkill -9 -f plain_agent.main
   ```

3. The booking exists, so the API did its job:

   ```bash
   curl -s http://127.0.0.1:5056/bookings
   ```

4. Start the agent again (terminal 3) and refresh the page. **The journey is gone.** The agent
   has no idea the ticket was booked.
5. Go through the journey again and book. `GET /bookings` now shows a **duplicate** booking for
   the same traveller.

Everything the plain agent knew lived in one process's memory.

Stop the plain agent (`Ctrl+C` in terminal 3) before moving on. Both agents use port 5055.
Leave the two APIs running.

---

## 3. Baseline: the same agent on Temporal

The same caveats apply: live OpenAI, both self-hosted APIs, no WireMock. The APIs from part 2
keep running in terminals 1 and 2.

```bash
# Terminal 3: a local Temporal server (UI on http://localhost:8233)
temporal server start-dev
```

```bash
# Terminal 4: the worker, which runs the agent and is the only process that needs the key
export OPENAI_API_KEY="sk-..."
uv run python -m temporal_agent.worker
```

```bash
# Terminal 5: the web app, a thin Temporal client that needs no key
uv run uvicorn temporal_agent.main:app --port 5055
```

Open <http://127.0.0.1:5055> and book the same trip. Then open the Temporal UI at
<http://localhost:8233>: each conversation is a workflow called `conversation-<id>`. Its event
history shows every model call (`invoke_model`) and every API call as a separate activity.

### Try it: kill the worker mid-turn

1. Send a message, and while the agent is thinking, kill the worker:

   ```bash
   pkill -9 -f temporal_agent.worker
   ```

2. The page keeps waiting. Start the worker again (terminal 4).
3. The reply arrives. The new worker replays the history and carries on from the same step. In
   the Temporal UI, the interrupted activity shows a second attempt.

> **Tip:** to make the timing easy, hold the turn open on purpose. Arm a fault on the Catalog
> API that lasts until cleared (see 4.1), ask about conferences, kill and restart the worker,
> then clear the fault.

### Try it: restart the web app

Stop the web app (terminal 5), start it again and refresh the page. The transcript comes back:
it lives in the workflow, not the web app.

### Try it: no duplicate bookings

Repeat the plain agent's "pull the plug" experiment, but kill the worker. The booking is never
duplicated: both bookings send an idempotency key derived from the workflow, so a retried
request returns the original booking.

---

## 4. Exploring failures

With a baseline running, inject failures and compare the two agents. Run each scenario against
**both** agents: part 2's setup for the plain agent, part 3's for the Temporal agent.

What to expect, in short:

| Failure | Plain agent | Temporal agent |
| --- | --- | --- |
| **Retryable** API error (429, 5xx, network) | Fails the tool call at once. The model tells you the API is unavailable. | Retries with backoff (1 s, 2 s, 4 s, … capped at 10 s) until it works. You see a slower reply, not an error. |
| **Permanent** API error (4xx other than 408 and 429) | Fails the tool call. The model explains. | Fails at once, with no retries. The model explains. A failed flight booking also cancels the conference booking (a saga). |
| **Retryable** model error | The OpenAI SDK retries twice inside the process. If that isn't enough, the turn fails: "The agent could not complete that turn". | Temporal retries until it works. The turn waits, then completes. |
| **Process crash** | Everything is lost. | Another worker carries on from the last completed step. |

Watch the worker's terminal too. The Temporal agent logs one line per failed attempt, for
example:

```text
Conference Catalog API GET /conferences failed with HTTP 503 on attempt 2; retryable, Temporal will retry
```

### 4.1 API errors: self-hosted APIs only

No WireMock needed. Each self-hosted API can be told to fail with `POST /admin/fault`:

| Field | Meaning |
| --- | --- |
| `status` | The HTTP status to return (400 to 599) |
| `count` | How many requests to fail before healing. Leave it out to fail until cleared. |
| `method` | Only fail this HTTP method, for example `POST` |
| `pathPrefix` | Only fail paths starting with this, for example `/bookings` |

Try these, one at a time:

**Catalog is flaky: 503 for the next 3 requests.** Then ask about conferences.

```bash
curl -X POST http://127.0.0.1:5056/admin/fault -H 'content-type: application/json' \
  -d '{"status": 503, "count": 3, "method": "GET"}'
```

- Plain: the agent says the catalogue is unavailable. Ask again once the three failures are used up.
- Temporal: the reply takes a few seconds longer, then lists the conferences.

**Flights rate limited: 429 until cleared.** Then ask for flights.

```bash
curl -X POST http://127.0.0.1:5057/admin/fault -H 'content-type: application/json' \
  -d '{"status": 429}'
```

- Plain: the agent says flight search is unavailable.
- Temporal: the turn waits. Clear the fault (below) and the reply arrives.

**Flight booking fails permanently: 400 on `POST /bookings`.** Then book the flight.

```bash
curl -X POST http://127.0.0.1:5057/admin/fault -H 'content-type: application/json' \
  -d '{"status": 400, "method": "POST", "pathPrefix": "/bookings"}'
```

- Plain: the agent says the flight couldn't be booked. Your conference ticket stays booked.
- Temporal: no retries. The workflow **cancels the conference booking** and tells you nothing
  has been charged. Check it with `curl -s http://127.0.0.1:5056/bookings`: the status is
  `Cancelled`.

**…and the cancellation fails too.** Keep the flight fault armed, and also fail the catalog's
cancel endpoint (the trailing slash matches `/bookings/<ref>/cancel` but not new bookings):

```bash
curl -X POST http://127.0.0.1:5056/admin/fault -H 'content-type: application/json' \
  -d '{"status": 400, "method": "POST", "pathPrefix": "/bookings/"}'
```

- Temporal: the reply admits the ticket could not be released automatically. Use `500` instead
  of `400` and the cancellation keeps retrying until you clear the fault, then succeeds.
- Plain: there's no compensation step, so this doesn't apply.

**Inspect and clear faults:**

```bash
curl -s http://127.0.0.1:5056/admin/fault                  # what's armed
curl -X DELETE http://127.0.0.1:5056/admin/fault           # clear the Catalog API
curl -X DELETE http://127.0.0.1:5057/admin/fault           # clear the Flight Search API
```

### 4.2 API errors: WireMock-hosted Catalog API

The Catalog API is replaced by a WireMock Cloud mock whose stubs fail in a scripted sequence.
The Flight Search API stays self-hosted.

1. In WireMock Cloud, create a mock API and import into its stubs:
   - `wiremock/catalog-stubs.json`, the working API (always),
   - plus `wiremock/catalog-outage-stubs.json`, where the first **5** `GET /conferences` return 503,
   - and/or `wiremock/catalog-booking-fault-stubs.json`, where conference booking fails with
     **503, then 429, then a dropped connection**, then works.
2. Point the agent at the mock, using the mock's base URL. For the plain agent, set it in its
   terminal. For the Temporal agent, set it in the **worker's** terminal. Then restart that
   process:

   ```bash
   export CATALOG_BASE_URL=https://<your-catalog-mock>.wiremockapi.cloud
   ```

3. Book a trip.
   - Plain: the first failure reaches the model straight away and it tells you. Each retry
     you ask for moves the mock on one failure.
   - Temporal: the turns just take longer (about 25 s for the outage, about 10 s for the
     booking). The Temporal UI shows the activity succeeding on a later attempt.

The faults are WireMock **scenarios**: each failing request moves them on one step, and once
recovered they stay recovered. **Reset the mock's scenarios** in WireMock Cloud to arm them
again. The mock keeps no bookings, so `GET /bookings` shows nothing there.

### 4.3 LLM errors: mocked LLM in WireMock

The model is replaced by a WireMock mock that replays a recorded conversation. It's
deterministic and needs no OpenAI key, but you must **type the script from part 2 exactly**.
Anything else gets "(mock) That message is not in the recorded happy path…". `yes` also
works in place of `book`.

1. In WireMock Cloud, create a mock API and import:
   - `wiremock/llm-happy-path-stubs.json`, the recorded conversation (always),
   - plus **one** fault set:
     - `wiremock/llm-fault-stubs.json`: **blips**. The model call after `book` (conference)
       fails with 503 then 429. After `book` (flight) it fails with 500 then a dropped
       connection. Each then recovers.
     - `wiremock/llm-proxy-fault-stubs.json`: **an outage**. The model call after either
       `book` returns 503 every time, until you disable the stub in WireMock Cloud.
2. Point the agent at the mock. Set this in the plain agent's terminal, or the worker's terminal
   for Temporal, and restart that process. Leave `/v1` off the URL, and any key value works:

   ```bash
   export OPENAI_BASE_URL=https://<your-llm-mock>.wiremockapi.cloud
   export OPENAI_API_KEY=mock
   ```

   On startup, the agent logs `Model … at https://<your-llm-mock>…` to confirm the change.
3. Follow the script.
   - **Blips, plain agent:** the OpenAI SDK's two built-in retries absorb them inside the
     process, so `book` is slightly slower. Nothing durable is involved: kill the process
     mid-retry and the turn is lost.
   - **Blips, Temporal agent:** Temporal retries. `book` takes a few seconds longer, and the
     Temporal UI shows `invoke_model` succeeding on attempt 3.
   - **Outage, plain agent:** after three attempts (one plus two SDK retries) the turn fails
     with "The agent could not complete that turn". Send `book` again once you've disabled the
     stub.
   - **Outage, Temporal agent:** the turn waits while Temporal keeps retrying. Disable the
     fault stub and the reply arrives.

For the blips, **reset the mock's scenarios** before each run to arm them again.

### 4.4 LLM errors: WireMock proxying to OpenAI

Like 4.3, but the conversation is live: WireMock forwards each request to OpenAI unless a fault
stub matches it first. You can type anything, and faults still trigger on `book`.

1. In WireMock Cloud, create a mock API and import:
   - `wiremock/llm-proxy-main-stub.json`, which forwards everything to OpenAI (always),
   - plus **one** fault set, the same two as in 4.3: `wiremock/llm-fault-stubs.json` (blips)
     or `wiremock/llm-proxy-fault-stubs.json` (an outage until disabled).

   Don't import `llm-happy-path-stubs.json` into this mock. It would replay the recording
   instead of calling OpenAI.
2. Point the agent at the mock, this time with your **real** key, because WireMock passes it on
   to OpenAI:

   ```bash
   export OPENAI_BASE_URL=https://<your-proxy-mock>.wiremockapi.cloud
   export OPENAI_API_KEY="sk-..."
   ```

3. Book a trip, answering the booking questions with `book`. A plain `yes` doesn't force the
   booking tool, so it isn't matched by the fault stubs and goes straight through to OpenAI.
   Expect the same behaviour as in 4.3, with a live model.

> **No WireMock Cloud account?** The same JSON files work with a local
> [WireMock standalone](https://wiremock.org/docs/standalone/) server. Import each file with
> `curl -X POST http://localhost:8080/__admin/mappings/import -d @wiremock/<file>.json`, then use
> `http://localhost:8080` as the base URL.

---

## Reference

### Environment variables

Set them in the terminal that starts the process. For the Temporal agent, anything to do with
the model or the APIs goes in the **worker's** terminal.

| Variable | Default | Plain agent | Worker | Temporal web app |
| --- | --- | :-: | :-: | :-: |
| `OPENAI_API_KEY` | none, **required** | ✓ | ✓ | |
| `OPENAI_MODEL` | `gpt-5.6-luna` | ✓ | ✓ | |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | ✓ | ✓ | |
| `OPENAI_RECORD_TO` | off | ✓ | ✓ | |
| `CATALOG_BASE_URL` | `http://127.0.0.1:5056` | ✓ | ✓ | |
| `FLIGHTS_BASE_URL` | `http://127.0.0.1:5057` | ✓ | ✓ | |
| `TEMPORAL_ADDRESS` | `localhost:7233` | | ✓ | ✓ |
| `TEMPORAL_NAMESPACE` | `default` | | ✓ | ✓ |
| `TEMPORAL_API_KEY` | none | | ✓ | ✓ |

- **`OPENAI_API_KEY`**: the agent stops at startup with a clear error if it's missing. Any
  value works against the mocked LLM (4.3).
- **`OPENAI_MODEL`**: the model sent in every request.
- **`OPENAI_BASE_URL`**: where model requests go. Read by the OpenAI SDK itself. For a WireMock
  mock, leave `/v1` off, because the SDK appends `/chat/completions` to the URL exactly as given.
- **`OPENAI_RECORD_TO`**: a folder. When set, every model request and response is saved there
  as JSON, without headers, so the key is never saved. This is used to re-record the mocked
  LLM; see [wiremock/README.md](wiremock/README.md). Unset it afterwards.
- **`CATALOG_BASE_URL`**, **`FLIGHTS_BASE_URL`**: the two APIs, self-hosted or mocked.
- **`TEMPORAL_ADDRESS`**, **`TEMPORAL_NAMESPACE`**: the Temporal cluster. The worker and the web
  app must point at the same one.
- **`TEMPORAL_API_KEY`**: for Temporal Cloud with API key auth. Setting it also turns on TLS.

The Temporal SDK's environment configuration also reads `TEMPORAL_TLS`,
`TEMPORAL_TLS_CLIENT_CERT_PATH` / `_KEY_PATH` (and `_DATA` variants),
`TEMPORAL_TLS_SERVER_CA_CERT_PATH`, `TEMPORAL_TLS_SERVER_NAME`,
`TEMPORAL_TLS_DISABLE_HOST_VERIFICATION`, `TEMPORAL_PROFILE`, `TEMPORAL_CONFIG_FILE` and
`TEMPORAL_GRPC_META_<NAME>`, for clusters other than the local dev server. The OpenAI SDK also
honours `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID`.

### Ports

| Port | Process |
| --- | --- |
| 5055 | Agent web app (plain **or** Temporal, not both) |
| 5056 | Conference Catalog API |
| 5057 | Flight Search API |
| 7233 | Temporal server (gRPC) |
| 8233 | Temporal UI |

### The two APIs

Both keep bookings in memory, so restarting an API empties its booking list. JSON is camelCase.
[`apis/requests.http`](apis/requests.http) has ready-made requests for the VS Code REST Client
or the JetBrains HTTP Client.

| Conference Catalog API (5056) | Flight Search API (5057) |
| --- | --- |
| `GET /conferences`, `GET /conferences/{id}` | `GET /flights?origin=&destination=` (city or airport code) |
| `POST /bookings`, `GET /bookings`, `GET /bookings/{ref}` | `GET /flights/{id}` |
| `POST /bookings/{ref}/cancel` | `POST /bookings`, `GET /bookings`, `GET /bookings/{ref}` |

On both:
- **Idempotency:** `POST /bookings` accepts an optional `idempotencyKey`. Sending the same key
  again returns the original booking.
- **Faults:** `/admin/fault` arms and clears faults (see 4.1). `/admin` and `/health` are never
  faulted, and requests with an `X-Demo-Control` header bypass faults.

### Handy commands

```bash
curl -s http://127.0.0.1:5056/bookings             # conference bookings
curl -s http://127.0.0.1:5057/bookings             # flight bookings
temporal workflow list                              # all conversations
temporal workflow query --type get_token_usage --workflow-id conversation-<id>   # tokens used
```

### Journey lifecycle (Temporal agent)

- **Start new journey** cancels the old workflow, which records why in its transcript.
- A conversation with no message for **one hour** closes itself.
- After **25 turns** the workflow continues as new: it starts a fresh run with a short event
  history and carries the whole journey over.

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| `address already in use` on 5055 | The other agent is still running. Only one of plain or Temporal can run at a time. |
| The page says "Connecting to the agent…" forever (Temporal) | The worker isn't running, or it's connected to a different Temporal server or namespace from the web app. |
| The worker starts but nothing happens | Start `temporal server start-dev` first, then the worker. |
| `OPENAI_API_KEY must be set` | Export it in the terminal that runs the plain agent or the worker. |
| Changes to `OPENAI_BASE_URL` / `CATALOG_BASE_URL` have no effect | Restart the process after exporting. For Temporal, that's the worker. Check the `Model … at …` startup line. |
| "(mock) That message is not in the recorded happy path" | You're on the mocked LLM (4.3). Type the script exactly. |
| WireMock faults stopped firing | Scenarios stay recovered after one run. Reset the mock's scenarios. |

### What's in the repo

| Path | What it is |
| --- | --- |
| `conference_booker/` | Shared by both agents: prompt, tool declarations, tool logic, journey state, API clients |
| `plain_agent/` | The plain agent: web app, in-memory conversation store, agent loop |
| `temporal_agent/` | The Temporal agent: workflow, activities, failure classification, worker, web app |
| `apis/` | The self-hosted Conference Catalog and Flight Search APIs, with fault injection |
| `web/static/` | The chat page, shared by both agents |
| `wiremock/` | Stubs for the WireMock mocks, their generators, and the recording they came from. See [wiremock/README.md](wiremock/README.md) for regenerating them. |
| `PLAN.md` | How this port was built, slice by slice |
