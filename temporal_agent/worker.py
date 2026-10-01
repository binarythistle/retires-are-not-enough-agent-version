"""Runs the agent: the workflow and its activities. This is the only Temporal-side process
that needs OPENAI_API_KEY and reaches the two APIs. Run it separately from the web app so it
can be killed and restarted on its own:

    uv run python -m temporal_agent.worker
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from openai import AsyncOpenAI
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from conference_booker.catalog import ConferenceCatalogClient
from conference_booker.flights import FlightSearchClient
from conference_booker.settings import Settings
from temporal_agent.activities import ConferenceBookerActivities
from temporal_agent.shared import TASK_QUEUE
from temporal_agent.workflow import ConferenceBookerWorkflow

logger = logging.getLogger("temporal_agent.worker")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Fails at startup with a clear message if OPENAI_API_KEY is missing.
    settings = Settings.from_env()

    # TEMPORAL_ADDRESS and TEMPORAL_NAMESPACE override the local dev server defaults.
    connect_config = ClientConfig.load_client_connect_config()
    connect_config.setdefault("target_host", "localhost:7233")
    client = await Client.connect(**connect_config, data_converter=pydantic_data_converter)

    async with (
        httpx.AsyncClient(base_url=settings.catalog_base_url, timeout=10) as catalog_http,
        httpx.AsyncClient(base_url=settings.flights_base_url, timeout=10) as flights_http,
        # Temporal owns retries. Two retry layers hide cost and confuse timeouts.
        AsyncOpenAI(api_key=settings.openai_api_key, timeout=60, max_retries=0) as openai,
    ):
        activities = ConferenceBookerActivities(
            openai,
            settings.openai_model,
            ConferenceCatalogClient(catalog_http),
            FlightSearchClient(flights_http),
        )
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[ConferenceBookerWorkflow],
            activities=[
                activities.invoke_model,
                activities.get_available_conferences,
                activities.book_conference,
                activities.cancel_conference_booking,
                activities.search_flights,
                activities.book_flight,
            ],
        )
        logger.info("Model %s at %s; polling task queue %s", settings.openai_model, openai.base_url, TASK_QUEUE)
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
