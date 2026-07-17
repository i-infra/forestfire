"""Shared test setup: environment, a fake signal-cli account dir, and helpers.

Env vars must be set before any forest module is imported, since utils and
cryptography read them at import time (and cryptography fails closed).
"""

import json
import os
import pathlib
from typing import Iterator, Optional

import pytest

BOT_NUMBER = "+11111111111"
BOT_UUID = "00000000-0000-0000-0000-000000000000"
USER_NUMBER = "+22222222222"
USER_UUID = "11111111-1111-1111-1111-111111111111"
ADMIN_UUID = "99999999-9999-9999-9999-999999999999"

os.environ["ENV"] = "test"
os.environ.setdefault("SALT", "testsalt")
# decodes to 16 bytes -> doubled to a 32B AES key
os.environ.setdefault("AESKEY", "4444444444444444444444")
os.environ.setdefault("ADMIN", ADMIN_UUID)
os.environ.setdefault("ENABLE_EVAL", "1")
os.environ.setdefault("NAMESPACE", "test-namespace")
os.environ.setdefault("PAUTH", "test-pauth")

ACCOUNTS_JSON = {
    "accounts": [
        {
            "path": BOT_NUMBER.lstrip("+"),
            "number": BOT_NUMBER,
            "uuid": BOT_UUID,
        }
    ]
}
KEYSTATE = json.dumps({"fake": "signal-cli keystate"})


def make_botdir(path: pathlib.Path) -> None:
    """Populate a directory with the state/data layout SignalDatastore expects"""
    data = path / "state" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "accounts.json").write_text(json.dumps(ACCOUNTS_JSON))
    (data / BOT_NUMBER.lstrip("+")).write_text(KEYSTATE)


@pytest.fixture(scope="session")
def botdir(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    path = tmp_path_factory.mktemp("botdir")
    make_botdir(path)
    return path


@pytest.fixture(autouse=True)
def in_botdir(botdir: pathlib.Path) -> Iterator[pathlib.Path]:
    """Run every test chdir'd to the fake bot dir (tests may chdir away freely)"""
    old = os.getcwd()
    os.chdir(botdir)
    yield botdir
    os.chdir(old)


@pytest.fixture()
def set_secret() -> Iterator:
    """Override a secret for one test, bypassing utils.get_secret's miss-cache"""
    from forest import utils

    saved: dict[str, tuple[Optional[str], Optional[str]]] = {}

    def _set(key: str, value: str) -> None:
        if key not in saved:
            saved[key] = (os.environ.get(key), utils.secret_cache.get(key))
        os.environ[key] = value
        utils.secret_cache[key] = value

    yield _set
    for key, (env_val, cache_val) in saved.items():
        if env_val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = env_val
        if cache_val is None:
            utils.secret_cache.pop(key, None)
        else:
            utils.secret_cache[key] = cache_val


class FakeKV:
    """In-memory stand-in for pdictng.fasterpKVStoreClient"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.posts: list[tuple[str, str]] = []

    async def get(self, key: str) -> Optional[str]:
        return self.store.get(key)

    async def post(self, key: str, data: str) -> str:
        self.store[key] = data
        self.posts.append((key, data))
        return "OK"
