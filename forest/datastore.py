from . import pdictng
from . import utils
import json
import sys
import os.path
import asyncio
import logging
import base64

from typing import Optional
from subprocess import PIPE
from asyncio.subprocess import create_subprocess_exec
import typing


class SignalDatastore:
    # startup:
    # make sure accounts.json is populated
    # make sure account keystate is populated
    # launch litestream
    # - LITESTREAM_ACCESS_KEY_ID=minioadmin LITESTREAM_SECRET_ACCESS_KEY=minioadmin ./litestreambin replicate -config ./litestream_backup_accounts.yml

    # shutdown:
    # kill signal-cli
    # backup account keystate
    # stop litestream

    # tldr - this runs before signal-cli is launched, and cleans up afterwards
    # if this is unreliable for any reason we may need to monitor the account keystate file / upload it periodically

    def __init__(self, bot_number: typing.Optional[str] = None):
        # if bot_number unknown check sys.argv[1] and if that doesn't work check BOT_NUMBER in secrets file
        # restore accounts.json file (from envvar if needed) - and make directories
        if not bot_number:
            try:
                bot_number = utils.signal_format(sys.argv[1])
                assert bot_number is not None
            except IndexError:
                bot_number = utils.get_secret("BOT_NUMBER")
        logging.debug("bot number: %s", bot_number)
        self.bot_number = bot_number
        self.litestream_path = "./litestreambin"
        self.client = pdictng.fasterpKVStoreClient()
        self.keystate: Optional[str] = None
        self.shutting_down = False
        self.periodic_backup_task: Optional[asyncio.Task] = None
        if os.path.exists("state/data/accounts.json"):
            with open("state/data/accounts.json") as f:
                self.accounts_map = json.loads(f.read())
        else:
            if not os.path.exists("state/data/"):
                os.makedirs("state/data/")
            accounts_json_encoded = os.environ.get("ACCOUNTS_JSON_ENCODED")
            if not accounts_json_encoded:
                raise Exception("No accounts.json and no ACCOUNTS_JSON_ENCODED envar!")
            accounts_json = base64.b64decode(accounts_json_encoded).decode()
            with open("state/data/accounts.json", "w") as f:
                f.write(accounts_json)
            self.accounts_map = json.loads(accounts_json)
        maybe_account = [
            a
            for a in self.accounts_map.get("accounts")
            if a.get("number").lstrip("+") == bot_number.lstrip("+")
        ]
        if maybe_account:
            self.account = maybe_account[0]
        else:
            raise Exception("Can't find account for your number!")
        if not os.path.exists(f"state/data/{self.account['path']}.d/"):
            os.makedirs(f"state/data/{self.account['path']}.d/")
        self.litestream_database_path = (
            f"state/data/{self.account['path']}.d/account.db"
        )
        self.litestream_replicate_cmd = f"{self.litestream_path} replicate -config ./litestream_backup_accounts.yml".split()
        self.litestream_restore_cmd = f"{self.litestream_path} restore -config ./litestream_backup_accounts.yml {self.litestream_database_path}".split()

    async def restore_litestream(self) -> None:
        """run the command to restore a database from litestream"""
        self.litestream_restore = await create_subprocess_exec(
            *self.litestream_restore_cmd,
            stdout=PIPE,
            stderr=PIPE
        )
        _, stderr = await self.litestream_restore.communicate()
        if self.litestream_restore.returncode != 0:
            # nonzero is expected on first run when no replica exists yet
            logging.warning(
                "litestream restore exited %s: %s",
                self.litestream_restore.returncode,
                stderr.decode().strip(),
            )

    async def start_litestream(self) -> str:
        """start the litestream replication process"""
        self.litestream = await create_subprocess_exec(
            *self.litestream_replicate_cmd,
            stdout=PIPE,
            stderr=PIPE
        )
        assert self.litestream.stdout
        line = await self.litestream.stdout.readline()
        if self.litestream.returncode is not None:
            assert self.litestream.stderr
            stderr = await self.litestream.stderr.read()
            logging.error(
                "litestream replicate exited %s at startup: %s",
                self.litestream.returncode,
                stderr.decode().strip(),
            )
        return line.decode()

    async def stop_litestream(self) -> int:
        self.litestream.terminate()
        return await self.litestream.wait()

    async def periodic_backup(self, tick_seconds: int = 10) -> bool:
        while not self.shutting_down:
            await self.async_ensure_backup()
            await asyncio.sleep(tick_seconds)
        # one last time
        await self.async_ensure_backup()
        return True

    def start_periodic_backup(self) -> None:
        self.shutting_down = False
        self.periodic_backup_task = asyncio.get_running_loop().create_task(
            self.periodic_backup()
        )

    async def stop_periodic_backup(self) -> None:
        self.shutting_down = True
        if self.periodic_backup_task:
            self.periodic_backup_task.cancel()

    async def async_ensure_backup(self) -> Optional[str]:
        """grab the keystate and post it to the persistence backend if it changed"""
        with open(f"state/data/{self.account.get('path', '')}") as f:
            self.keystate = f.read()
        # compare and store
        if (await self.client.get(self.account.get("uuid"))) != self.keystate:
            result = await self.client.post(self.account.get("uuid"), self.keystate)
            logging.debug(f"keystate change detected - backed up: {result}")
            return result
        return None

    async def async_shutdown(self) -> bool:
        await self.stop_periodic_backup()
        return True

    async def async_startup(self) -> None:
        """on startup: fetch keystate from persistence backend, write it out if it doesn't match the existing contents, then restore more keystate from litestream, then start replication"""
        self.keystate = await self.client.get(self.account.get("uuid"))
        keystate_path = f"state/data/{self.account['path']}"
        if self.keystate and (
            not os.path.exists(keystate_path)
            or self.keystate != open(keystate_path).read()
        ):
            logging.debug("wrote out keystate as it did not exist")
            with open(keystate_path, "w") as f:
                f.write(self.keystate)
        await self.restore_litestream()
        await asyncio.sleep(1)
        await self.start_litestream()
        asyncio.get_running_loop().call_later(5, self.start_periodic_backup)
