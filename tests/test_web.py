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
            web.post("/admin", core.admin_handler),
            web.post("/user/{recipient}", core.send_message_handler),
            web.post("/restart", core.restart),
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


async def post_status(app: web.Application, path: str, data: str = "") -> int:
    async with TestClient(TestServer(app)) as client:
        resp = await client.post(path, data=data)
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


def test_run_bot_validates_secrets_when_restore_set(monkeypatch, set_secret) -> None:
    """RESTORE guarantees keystate backup, so run_bot validates persistence
    secrets eagerly instead of failing on the first backup write"""
    monkeypatch.setattr(core.web, "run_app", lambda *a, **k: None)
    set_secret("RESTORE", "1")

    called = {"checked": False}
    monkeypatch.setattr(
        core.datastore.pdictng,
        "require_persistence_secrets",
        lambda: called.__setitem__("checked", True),
    )
    core.run_bot(core.QuestionBot)
    assert called["checked"]


def test_run_bot_skips_validation_without_restore(monkeypatch, set_secret) -> None:
    """a stateless bot (no RESTORE) doesn't demand persistence secrets at startup"""
    monkeypatch.setattr(core.web, "run_app", lambda *a, **k: None)
    set_secret("RESTORE", "")

    def boom() -> None:
        raise AssertionError("should not validate persistence secrets")

    monkeypatch.setattr(core.datastore.pdictng, "require_persistence_secrets", boom)
    core.run_bot(core.QuestionBot)  # no raise


@pytest.mark.asyncio
async def test_webhooks_disabled_by_default() -> None:
    """the action webhooks 404 unless ENABLE_WEBHOOKS is set"""
    app = make_app(fake_bot(proc=SimpleNamespace(returncode=None)))
    assert await post_status(app, "/admin", "hi") == 404
    assert await post_status(app, "/user/some-uuid", "hi") == 404
    assert await post_status(app, "/restart") == 404
    # probes are not webhooks and stay reachable
    assert await get_status(app, "/health") == 200


@pytest.mark.asyncio
async def test_webhooks_enabled_by_secret(set_secret) -> None:
    set_secret("ENABLE_WEBHOOKS", "1")

    async def admin(msg: str) -> None:
        admin.messages.append(msg)  # type: ignore[attr-defined]

    admin.messages = []  # type: ignore[attr-defined]
    app = make_app(SimpleNamespace(proc=None, admin=admin))
    assert await post_status(app, "/admin", "hello admin") == 200
    assert admin.messages == ["hello admin"]  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_dead_signal_is_unhealthy_but_ready() -> None:
    dead_proc = SimpleNamespace(returncode=1)
    app = make_app(fake_bot(proc=dead_proc))
    # ready: the process was spawned at some point
    assert await get_status(app, "/ready") == 200
    # health: it's not running now
    assert await get_status(app, "/health") == 503
