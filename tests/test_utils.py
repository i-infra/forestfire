"""utils: secrets parsing, get_secret semantics, phone number formatting."""

import os
import pathlib
from importlib import reload

from forest import utils


def test_parse_secrets() -> None:
    parsed = utils.parse_secrets("A=B\n# comment\nC=D=E\n\n")
    assert parsed == {"A": "B", "C": "D=E"}


def test_get_secret_reads_secrets_file(tmp_path: pathlib.Path) -> None:
    """utils.get_secret reads values from {ENV}_secrets in cwd"""
    (tmp_path / "test_secrets").write_text("A=B\nC=D")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("A") == "B"
    assert utils.get_secret("C") == "D"
    assert utils.get_secret("NOPE_NOT_SET") == ""


def test_get_secret_dotenv_fallback(tmp_path: pathlib.Path) -> None:
    """without a {ENV}_secrets file, values load from .env"""
    (tmp_path / ".env").write_text("DOTENV_ONLY_KEY=hello")
    os.chdir(tmp_path)
    reload(utils)

    assert utils.get_secret("DOTENV_ONLY_KEY") == "hello"


def test_get_secret_falsy_values() -> None:
    for falsy in ("0", "false", "no", "False", "NO"):
        os.environ["SOME_TEST_FLAG"] = falsy
        assert utils.get_secret("SOME_TEST_FLAG") == ""
    os.environ["SOME_TEST_FLAG"] = "yes"
    assert utils.get_secret("SOME_TEST_FLAG") == "yes"
    del os.environ["SOME_TEST_FLAG"]


def test_signal_format() -> None:
    assert utils.signal_format("+1 555 123 4567") == "+15551234567"
    assert utils.signal_format("(555) 123-4567") == "+15551234567"
    assert utils.signal_format("+447927948360") == "+447927948360"
    assert utils.signal_format("gibberish") is None


def test_root_dir_local() -> None:
    """running locally (not fly, not k8s), ROOT_DIR is the working directory"""
    assert not os.getenv("FLY_APP_NAME")
    assert reload(utils).ROOT_DIR == "."


def test_root_dir_fly(botdir: pathlib.Path) -> None:
    """with FLY_APP_NAME set, ROOT_DIR is /app"""
    os.environ["FLY_APP_NAME"] = "A"
    try:
        assert reload(utils).ROOT_DIR == "/app"
    finally:
        del os.environ["FLY_APP_NAME"]
        reload(utils)
