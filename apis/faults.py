"""Lets the demo control panel make an API misbehave on demand, so failure handling can be
shown without killing the process. Nothing here runs unless a fault is armed.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from apis.models import ApiModel

# The control panel sends this so it can keep reading bookings while a fault is armed.
# The agent never sends it, so its calls still fail.
BYPASS_HEADER = "X-Demo-Control"

# Always reachable, so a fault can be inspected and cleared while one is armed.
CONTROL_PATHS = ("/admin", "/health")


class FaultView(ApiModel):
    armed: bool
    status: int | None
    remaining: int | None
    method: str | None
    path_prefix: str | None


class ArmFaultRequest(ApiModel):
    status: int
    # None keeps failing until cleared; a number fails that many requests then self-heals.
    count: int | None = None
    method: str | None = None
    path_prefix: str | None = None


class FaultState:
    def __init__(self) -> None:
        self.clear()

    @property
    def is_armed(self) -> bool:
        return self.status > 0 and self.remaining != 0

    def arm(self, status: int, count: int | None, method: str | None, path_prefix: str | None) -> None:
        self.status = status
        self.remaining = count
        self.method = method.strip().upper() if method and method.strip() else None
        self.path_prefix = path_prefix.strip() if path_prefix and path_prefix.strip() else None

    def clear(self) -> None:
        self.status = 0
        self.remaining: int | None = None
        self.method: str | None = None
        self.path_prefix: str | None = None

    def consume(self, method: str, path: str) -> int | None:
        """Returns the status to fail this request with, consuming one of a finite budget, or
        None to let the request through. No lock needed: it runs on the single event loop."""
        if not self.is_armed or not self._matches(method, path):
            return None

        if self.remaining is not None and self.remaining > 0:
            self.remaining -= 1

        return self.status

    def _matches(self, method: str, path: str) -> bool:
        return (self.method is None or self.method == method.upper()) and (
            self.path_prefix is None or path.lower().startswith(self.path_prefix.lower())
        )

    def to_view(self) -> FaultView:
        return FaultView(
            armed=self.is_armed,
            status=self.status or None,
            remaining=self.remaining,
            method=self.method,
            path_prefix=self.path_prefix,
        )


def use_fault_injection(app: FastAPI) -> FaultState:
    faults = FaultState()

    @app.middleware("http")
    async def inject_faults(request: Request, call_next):
        path = request.url.path
        if (
            path.startswith(CONTROL_PATHS)
            or BYPASS_HEADER.lower() in request.headers
            or (status := faults.consume(request.method, path)) is None
        ):
            return await call_next(request)

        # 429 and 503 are the ones a client is meant to back off from, so say so.
        headers = {"Retry-After": "1"} if status in (429, 503) else None
        return JSONResponse(
            {"error": f"Injected fault: the API was told to return HTTP {status}.", "injected": True},
            status_code=status,
            headers=headers,
        )

    @app.get("/admin/fault")
    def get_fault() -> FaultView:
        return faults.to_view()

    @app.post("/admin/fault")
    def arm_fault(request: ArmFaultRequest):
        if not 400 <= request.status <= 599:
            return JSONResponse({"error": "status must be between 400 and 599."}, status_code=400)

        if request.count is not None and request.count <= 0:
            return JSONResponse({"error": "count must be greater than zero, or omitted."}, status_code=400)

        faults.arm(request.status, request.count, request.method, request.path_prefix)
        return faults.to_view()

    @app.delete("/admin/fault")
    def clear_fault() -> FaultView:
        faults.clear()
        return faults.to_view()

    return faults
