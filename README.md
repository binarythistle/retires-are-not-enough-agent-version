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

As of Slice 3 the agent can tell the time, save traveller details and list conferences.
Selecting and booking arrive in Slice 4; until then the model is told those tools are not
built yet.

The frontend in `web/static/` is copied unchanged from the .NET `wwwroot/`, and both agent
versions serve it.

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
