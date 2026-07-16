"""The aiohttp health endpoints k8s probes hit."""

from types import SimpleNamespace
from typing import Optional

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from forest import core


def make_app(bot: Optional[object]) -> web.Application:
    app = web.Application()
    app.add_routes(
        [
            web.get("/health", core.health_check),
            web.get("/ready", core.ready_check),
        ]
    )
    if bot is not None:
        app["bot"] = bot
    return app


def fake_bot(proc: Optional[object]) -> SimpleNamespace:
    return SimpleNamespace(proc=proc)


async def get_status(app: web.Application, path: str) -> int:
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(path)
        return resp.status


@pytest.mark.asyncio
async def test_no_bot_is_unhealthy() -> None:
    app = make_app(None)
    assert await get_status(app, "/ready") == 503
    assert await get_status(app, "/health") == 503


@pytest.mark.asyncio
async def test_not_started_is_unready() -> None:
    app = make_app(fake_bot(proc=None))
    assert await get_status(app, "/ready") == 503
    assert await get_status(app, "/health") == 503


@pytest.mark.asyncio
async def test_running_signal_is_healthy() -> None:
    live_proc = SimpleNamespace(returncode=None)
    app = make_app(fake_bot(proc=live_proc))
    assert await get_status(app, "/ready") == 200
    assert await get_status(app, "/health") == 200


@pytest.mark.asyncio
async def test_dead_signal_is_unhealthy_but_ready() -> None:
    dead_proc = SimpleNamespace(returncode=1)
    app = make_app(fake_bot(proc=dead_proc))
    # ready: the process was spawned at some point
    assert await get_status(app, "/ready") == 200
    # health: it's not running now
    assert await get_status(app, "/health") == 503
