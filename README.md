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
