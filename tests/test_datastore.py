"""SignalDatastore: account resolution, keystate backup logic, litestream wrangling."""

import asyncio
import base64
import json
import os
import pathlib
import stat

import pytest
import pytest_asyncio

from forest import datastore
from tests.conftest import ACCOUNTS_JSON, BOT_NUMBER, BOT_UUID, KEYSTATE, FakeKV


@pytest_asyncio.fixture()
async def ds():
    """A SignalDatastore against the fake account dir, with an in-memory KV backend"""
    store = datastore.SignalDatastore(BOT_NUMBER)
    await store.client.conn.close()  # don't leak the real aiohttp session
    store.client = FakeKV()  # type: ignore[assignment]
    yield store


def write_fake_litestream(script_body: str) -> None:
    """Drop a fake ./litestreambin into cwd"""
    path = pathlib.Path("litestreambin")
    path.write_text(f"#!/bin/sh\n{script_body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.mark.asyncio
async def test_account_resolution(ds) -> None:
    assert ds.account["number"] == BOT_NUMBER
    assert ds.account["uuid"] == BOT_UUID
    assert ds.litestream_database_path.endswith(".d/account.db")


@pytest.mark.asyncio
async def test_unknown_number_raises() -> None:
    with pytest.raises(Exception, match="Can't find account"):
        datastore.SignalDatastore("+19999999999")


@pytest.mark.asyncio
async def test_accounts_json_from_env(tmp_path: pathlib.Path) -> None:
    """without accounts.json on disk, it's bootstrapped from ACCOUNTS_JSON_ENCODED"""
    os.chdir(tmp_path)
    os.environ["ACCOUNTS_JSON_ENCODED"] = base64.b64encode(
        json.dumps(ACCOUNTS_JSON).encode()
    ).decode()
    try:
        store = datastore.SignalDatastore(BOT_NUMBER)
        await store.client.conn.close()
        assert store.account["number"] == BOT_NUMBER
        assert (tmp_path / "state" / "data" / "accounts.json").exists()
    finally:
        del os.environ["ACCOUNTS_JSON_ENCODED"]


@pytest.mark.asyncio
async def test_missing_accounts_json_raises(tmp_path: pathlib.Path) -> None:
    os.chdir(tmp_path)
    assert not os.getenv("ACCOUNTS_JSON_ENCODED")
    with pytest.raises(Exception, match="ACCOUNTS_JSON_ENCODED"):
        datastore.SignalDatastore(BOT_NUMBER)


@pytest.mark.asyncio
async def test_backup_only_posts_on_change(ds) -> None:
    """async_ensure_backup posts keystate once, then skips while unchanged"""
    result = await ds.async_ensure_backup()
    assert result == "OK"
    assert ds.client.store[BOT_UUID] == KEYSTATE

    # unchanged keystate: no new post
    assert await ds.async_ensure_backup() is None
    assert len(ds.client.posts) == 1


@pytest.mark.asyncio
async def test_periodic_backup_start_stop(ds) -> None:
    ds.start_periodic_backup()
    assert ds.shutting_down is False
    await asyncio.sleep(0.05)  # let the first tick run
    assert len(ds.client.posts) == 1
    await ds.stop_periodic_backup()
    assert ds.shutting_down is True
    with pytest.raises(asyncio.CancelledError):
        await ds.periodic_backup_task


@pytest.mark.asyncio
async def test_shutdown_stops_backup(ds) -> None:
    ds.start_periodic_backup()
    assert await ds.async_shutdown() is True
    assert ds.shutting_down is True


@pytest.mark.asyncio
async def test_restore_litestream_failure_is_nonfatal(ds, caplog, botdir) -> None:
    """a failed restore (e.g. no replica yet) logs a warning instead of raising"""
    os.chdir(botdir)
    write_fake_litestream("echo 'no matching backups found' >&2; exit 1")
    await ds.restore_litestream()
    assert ds.litestream_restore.returncode == 1
    assert any("litestream restore exited" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_restore_litestream_success_quiet(ds, caplog, botdir) -> None:
    os.chdir(botdir)
    write_fake_litestream("exit 0")
    await ds.restore_litestream()
    assert ds.litestream_restore.returncode == 0
    assert not any("litestream restore exited" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_start_and_stop_litestream(ds, botdir) -> None:
    os.chdir(botdir)
    write_fake_litestream("echo 'litestream v-fake replicating'; sleep 5")
    line = await ds.start_litestream()
    assert "replicating" in line
    returncode = await ds.stop_litestream()
    assert returncode != 0  # terminated
