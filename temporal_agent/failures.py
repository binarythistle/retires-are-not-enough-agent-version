"""Decides whether a failure is worth retrying, and says so in the logs.

Temporal retries activities forever by default. A 503 should retry until the API comes back;
a 400 or a 404 will fail identically every time, so retrying it just hides a bug behind an
activity that never completes.

Every failure is re-raised as a short ApplicationError `from None`. Raw httpx exceptions carry
a chain of stack traces that can exceed the SDK's failure size limit, which hides the real
error in the event history.
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TypeVar

import httpx
import openai
from temporalio import activity
from temporalio.exceptions import ApplicationError

T = TypeVar("T")


def is_permanent(status: int) -> bool:
    """4xx other than 408 and 429 means the request itself is wrong. Retrying cannot fix it."""
    return 400 <= status < 500 and status not in (408, 429)


async def classify_api_failures(api: str, work: Awaitable[T]) -> T:
    try:
        return await work
    except httpx.HTTPStatusError as error:
        response = error.response
        status = response.status_code
        permanent = is_permanent(status)
        _log(api, f"{response.request.method} {response.request.url.path} failed with HTTP {status}", permanent)
        raise ApplicationError(
            f"{api} returned HTTP {status} ({response.reason_phrase}).",
            type=f"{api.replace(' ', '')}{'Permanent' if permanent else 'Retryable'}Failure",
            non_retryable=permanent,
        ) from None
    except httpx.TransportError as error:
        # Connection refused, DNS, timeouts: the API may come back, so keep retrying.
        _log(api, f"could not be reached ({type(error).__name__})", permanent=False)
        raise ApplicationError(
            f"{api} could not be reached: {type(error).__name__}: {error}",
            type=f"{api.replace(' ', '')}Unreachable",
        ) from None


async def classify_model_failures(work: Awaitable[T]) -> T:
    """A bad API key or a rejected prompt fails identically on every attempt, so without this
    the model activity retries indefinitely and the attendee's turn never returns."""
    try:
        return await work
    except openai.APIStatusError as error:
        permanent = is_permanent(error.status_code)
        _log("Model", f"request failed with HTTP {error.status_code}", permanent)
        raise ApplicationError(
            f"The model rejected the request with HTTP {error.status_code}.",
            type="ModelPermanentFailure" if permanent else "ModelRetryableFailure",
            non_retryable=permanent,
        ) from None
    except openai.APIConnectionError as error:
        # Includes APITimeoutError.
        _log("Model", f"could not be reached ({type(error).__name__})", permanent=False)
        raise ApplicationError(f"The model could not be reached: {type(error).__name__}", type="ModelUnreachable") from None


def _log(api: str, what: str, permanent: bool) -> None:
    # Without this, a retry storm is invisible in application logs: every attempt is recorded
    # in the event history but nothing reaches the console.
    activity.logger.warning(
        "%s %s on attempt %d; %s",
        api,
        what,
        activity.info().attempt,
        "not retryable, failing the activity" if permanent else "retryable, Temporal will retry",
    )
