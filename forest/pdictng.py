# Copyright (c) 2023-2024 Xederif Inc.
# Copyright (c) 2022 MobileCoin Inc.
# Copyright (c) 2022 Ilia Daniher <i@mobilecoin.com>
# MIT LICENSE
import functools
import asyncio
import json
import logging
import os
import socket
import time
import uuid
from typing import Any, Generic, Optional, TypeVar, overload
import aiohttp
from forest import utils
from forest.cryptography import get_ciphertext_value, get_cleartext_value, hash_salt

# NAMESPACE and PAUTH are validated lazily, on first construction of a KV client,
# so bots that never persist state don't need them set. Importing is free.
pURL = os.getenv("PURL", "http://localhost:8000")


@functools.cache
def get_namespace() -> str:
    namespace = utils.get_secret("NAMESPACE")
    if not namespace:
        raise RuntimeError(
            "NAMESPACE envvar must be set to use persistence. It must be stable across "
            "deploys — a hostname default silently orphans data when hostnames change."
        )
    return namespace


@functools.cache
def get_pauth() -> str:
    pauth = utils.get_secret("PAUTH")
    if not pauth:
        raise RuntimeError("PAUTH envvar must be set to use persistence.")
    return pauth


def require_persistence_secrets() -> None:
    """Eagerly validate every secret persistence needs, raising if any is unset.
    Call this at startup to trade fail-on-first-use for fail-on-boot. The getters
    are cached, so this also warms them for the first real use."""
    from forest import cryptography

    get_namespace()
    get_pauth()
    cryptography.get_salt()
    cryptography.get_aeskey()


# Multi-writer is unsupported: whole-dict writes are last-write-wins, so two
# processes sharing a namespace silently clobber each other. Each namespace is
# claimed by one writer process at a time via an *unencrypted* claim key in the
# backend, so the refusal is legible even to a process holding the wrong keys.
# This is a guard-rail against accidental double-deploys, not a distributed
# lock — that would need compare-and-swap support in the backend.
CLAIM_KEY = "NAMESPACE_CLAIM"
CLAIM_TTL = int(os.getenv("NAMESPACE_CLAIM_TTL", "60"))
CLAIM_HEARTBEAT = max(CLAIM_TTL // 3, 1)
WRITER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class persistentKVStoreClient:
    async def post(self, key: str, data: str) -> str:
        raise NotImplementedError

    async def get(self, key: str) -> Optional[str]:
        raise NotImplementedError

    async def post_raw(self, key: str, data: str) -> str:
        """post without encrypting the value (for the namespace claim)"""
        raise NotImplementedError

    async def get_raw(self, key: str) -> Optional[str]:
        """get without decrypting the value (for the namespace claim)"""
        raise NotImplementedError


class fasterpKVStoreClient(persistentKVStoreClient):
    """Strongly persistent storage on top of Cloudflare/Deno workers KV.
    Upstash no longer supports strong consistency so let's do the simplest thing that might work.
    Cloudflare Durable Objects have the consistency model we want but they're not free.
    Deno openKV supports strong consistency though!
    """

    def __init__(
        self,
        base_url: str = pURL,
        auth_str: Optional[str] = None,
        namespace: Optional[str] = None,
    ):
        self.url = base_url
        # resolve/validate secrets before opening the session so a missing
        # PAUTH/NAMESPACE surfaces as a clean RuntimeError, not a loop error
        self.auth = auth_str if auth_str is not None else get_pauth()
        self.namespace = hash_salt(
            namespace if namespace is not None else get_namespace()
        )
        self.conn = aiohttp.ClientSession()
        self.exists: dict[str, bool] = {}
        self.headers = {
            "Authorization": f"{self.auth}",
        }

    async def post(self, key: str, data: str) -> str:
        return await self.post_raw(key, get_ciphertext_value(data))

    async def get(self, key: str) -> Optional[str]:
        """Get and return value of an object with the specified key and namespace"""
        ciphertext = await self.get_raw(key)
        if not ciphertext:
            return None
        return get_cleartext_value(ciphertext)

    async def post_raw(self, key: str, data: str) -> str:
        """post with a hashed key but a plaintext value (for the namespace claim)"""
        key = hash_salt(f"{self.namespace}_{key}")
        async with self.conn.post(
            f"{self.url}/{key}", headers=self.headers, data=data
        ) as resp:
            return await resp.text()

    async def get_raw(self, key: str) -> Optional[str]:
        """get the stored value without decrypting it"""
        key = hash_salt(f"{self.namespace}_{key}")
        async with self.conn.get(f"{self.url}/{key}", headers=self.headers) as resp:
            if resp.status != 200:
                return None
            return (await resp.text()) or None


V = TypeVar("V")
# V = TypeVar("V", str, int, list, dict[str, str])
# that would be nice but causes an error with aPersistDictOfLists
# Value of type variable "V" of "aPersistDict" cannot be "list"
# possibly related: https://stackoverflow.com/questions/59933946/difference-between-typevart-a-b-and-typevart-bound-uniona-b
# https://stackoverflow.com/questions/55375362/why-does-mypy-ignore-a-generic-typed-variable-that-contains-a-type-incompatible


class aPersistDict(Generic[V]):
    """Async, consistent, persistent storage.
    Does not inherit from dict, but behaves mostly in the same way.
    Care is taken to offer asynchronous methods and strong consistency.
    This can be used for
        - inventory
        - subscribers
        - config info
    in a way that are persisted across reboots.
    No schemas and privacy preserving, but could be faster.
    Each write takes about 70 ms.

    This takes a type parameter for the value
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """If an argument is provided or a 'tag' keyword argument is passed...
        this will be used as a tag for backup / restore.
        """
        self.tag = ""
        if args:
            self.tag = args[0]
        if "tag" in kwargs:
            self.tag = kwargs.pop("tag")
        self.writer_id: str = kwargs.pop("writer_id", None) or WRITER_ID
        self.dict_: dict[str, Any] = {}
        self.client: persistentKVStoreClient = fasterpKVStoreClient()
        self.rwlock = asyncio.Lock()
        self.claim_lost = False
        self.claim_task: Optional[asyncio.Task] = None
        self.init_task = asyncio.create_task(self.finish_init(**kwargs))

    def __repr__(self) -> str:
        return f"a{self.dict_}"

    def __str__(self) -> str:
        return f"a{self.dict_}"

    _MISSING = object()

    async def __getitem__(self, key: str) -> V:
        value = await self.get(key, self._MISSING)  # type: ignore[arg-type]
        if value is self._MISSING:
            raise KeyError(key)
        return value

    async def finish_init(self, **kwargs: Any) -> None:
        """Does the asynchrnous part of the initialisation process."""
        async with self.rwlock:
            await self._acquire_namespace_claim()
            key = f"Persist_{self.tag}"
            result = await self.client.get(key)
            if result:
                self.dict_ = json.loads(result)
            self.dict_.update(**kwargs)
        self.claim_task = asyncio.create_task(
            self._claim_heartbeat(), name="namespace-claim-heartbeat"
        )

    @staticmethod
    def _parse_claim(raw: Optional[str]) -> dict:
        try:
            claim = json.loads(raw or "")
            return claim if isinstance(claim, dict) else {}
        except ValueError:
            return {}

    def _claim_is_foreign_and_fresh(self, claim: dict) -> bool:
        fresh = time.time() - claim.get("ts", 0) < CLAIM_TTL
        return bool(claim) and fresh and claim.get("writer") != self.writer_id

    async def _acquire_namespace_claim(self) -> None:
        """Refuse to start if another live writer holds this namespace.
        Post our claim, then read it back: of two simultaneous acquirers,
        only the one whose write landed last survives the verify read."""
        claim = self._parse_claim(await self.client.get_raw(CLAIM_KEY))
        if self._claim_is_foreign_and_fresh(claim):
            raise RuntimeError(
                f"namespace already claimed by writer {claim.get('writer')!r}; "
                "multi-writer is unsupported. Stop the other process, use a "
                "different NAMESPACE, or wait for the claim to expire "
                f"({CLAIM_TTL}s)."
            )
        await self._post_claim()
        verify = self._parse_claim(await self.client.get_raw(CLAIM_KEY))
        if verify.get("writer") != self.writer_id:
            raise RuntimeError(
                f"lost namespace claim race to writer {verify.get('writer')!r}; "
                "multi-writer is unsupported."
            )

    async def _post_claim(self) -> None:
        await self.client.post_raw(
            CLAIM_KEY, json.dumps({"writer": self.writer_id, "ts": time.time()})
        )

    async def _check_claim(self) -> None:
        """Heartbeat body: renew our claim, or mark it lost if a live foreign
        writer has taken the namespace (writes will then refuse; reads still work)."""
        claim = self._parse_claim(await self.client.get_raw(CLAIM_KEY))
        if self._claim_is_foreign_and_fresh(claim):
            self.claim_lost = True
            logging.critical(
                "namespace claim for tag %r lost to writer %r; refusing further writes",
                self.tag,
                claim.get("writer"),
            )
            return
        await self._post_claim()

    async def _claim_heartbeat(self) -> None:
        while not self.claim_lost:
            await asyncio.sleep(CLAIM_HEARTBEAT)
            await self._check_claim()

    async def close(self) -> None:
        """Cancel the claim heartbeat (for tests and graceful shutdown)."""
        if self.claim_task:
            self.claim_task.cancel()

    @overload
    async def get(self, key: str, default: V) -> V: ...

    @overload
    async def get(self, key: str, default: None = None) -> Optional[V]: ...

    async def get(self, key: str, default: Optional[V] = None) -> Optional[V]:
        """Analogous to dict().get() - but async. Waits until writes have completed on the backend before returning results."""
        await self.init_task
        async with self.rwlock:
            # explicit membership check so stored falsy values ("", 0, []) are readable
            if key in self.dict_:
                return self.dict_[key]
            return default

    async def get_key_by_value(
        self, value: V, default: Optional[str] = None
    ) -> Optional[str]:
        """Reverse lookup: returns the first key whose value equals `value`
        (insertion order), or `default` if no key matches. O(n)."""
        await self.init_task
        async with self.rwlock:
            for key, val in self.dict_.items():
                if val == value:
                    return key
            return default

    async def keys(self) -> list[str]:
        await self.init_task
        async with self.rwlock:
            return list(self.dict_.keys())

    async def values(self) -> list[V]:
        await self.init_task
        async with self.rwlock:
            return list(self.dict_.values())

    async def items(self) -> list[tuple[str, V]]:
        await self.init_task
        async with self.rwlock:
            return list(self.dict_.items())

    async def remove(self, key: str) -> None:
        """Removes a value from the map, if it exists."""
        await self.set(key, None)
        return None

    @overload
    async def pop(self, key: str, default: V) -> V: ...

    @overload
    async def pop(self, key: str, default: None = None) -> Optional[V]: ...

    async def pop(self, key: str, default: Optional[V] = None) -> Optional[V]:
        """Returns and removes a value if it exists. Atomic: the read and the
        removal happen under one lock acquisition."""
        await self.init_task
        async with self.rwlock:
            if key in self.dict_:
                res: V = self.dict_[key]
                await self._set(key, None)
                return res
            return default

    async def _set(self, key: str, value: Optional[V]) -> str:
        """Sets a value at a given key, returns metadata.
        This function exists so *OTHER FUNCTIONS* holding the lock can set values."""
        if self.claim_lost:
            raise RuntimeError(
                "namespace claim lost to another writer; refusing to write "
                "(multi-writer is unsupported)"
            )
        if value is not None:
            # reject unserializable values before mutating local state, so the
            # in-memory dict can't diverge from what the backend will accept
            try:
                json.dumps(value)
            except (TypeError, ValueError) as e:
                raise TypeError(
                    f"value for key {key!r} is not JSON-serializable: {e}"
                ) from e
        if key is not None and value is not None:
            self.dict_.update({key: value})
        elif key and value is None and key in self.dict_:
            self.dict_.pop(key)
        client_key = f"Persist_{self.tag}"
        client_value = json.dumps(self.dict_)
        return await self.client.post(client_key, client_value)

    async def set(self, key: str, value: Optional[V]) -> str:
        """Sets a value at a given key, returns metadata."""
        await self.init_task
        async with self.rwlock:
            return await self._set(key, value)


class aPersistDictOfInts(aPersistDict[int]):
    async def increment(self, key: str, value: int) -> str:
        """Atomically adds `value` to the int at `key`, starting from 0 for
        missing keys. Raises TypeError if the existing value is not an int."""
        value_to_extend: Any = 0
        await self.init_task
        async with self.rwlock:
            value_to_extend = self.dict_.get(key, 0)
            if isinstance(value_to_extend, int):
                return await self._set(key, value_to_extend + value)
            raise TypeError(f"key {key} is not an int")

    async def decrement(self, key: str, value: int) -> str:
        """Atomically subtracts `value` from the int at `key`, starting from 0
        for missing keys. Raises TypeError if the existing value is not an int."""
        value_to_extend: Any = 0
        await self.init_task
        async with self.rwlock:
            value_to_extend = self.dict_.get(key, 0)
            if isinstance(value_to_extend, int):
                return await self._set(key, value_to_extend - value)
            raise TypeError(f"key {key} is not an int")


I = TypeVar("I")  # inner value


class aPersistDictOfLists(aPersistDict[list[I]]):
    "This takes a type parameter for the values in the *inner* list"

    async def extend(self, key: str, value: I) -> str:
        """Since one cannot simply add to a coroutine, this function exists.
        If the key exists and the value is None, or an empty array, the provided value is added to a(the) list at that value.
        """
        value_to_extend: Optional[list[I]] = []
        await self.init_task
        async with self.rwlock:
            value_to_extend = self.dict_.get(key, [])
            if isinstance(value_to_extend, list):
                value_to_extend.append(value)
                return await self._set(key, value_to_extend)
            raise TypeError(f"value {value_to_extend} for key {key} is not a list")

    async def remove_from(self, key: str, not_value: I) -> str:
        """Removes a value specified from the list, if present.
        Returns metadata"""
        await self.init_task
        async with self.rwlock:
            values_to_filter = self.dict_.get(key, [])
            if isinstance(values_to_filter, list):
                values_without_specified = [
                    el for el in values_to_filter if not_value != el
                ]
                return await self._set(key, values_without_specified)
            raise TypeError(f"key {key} is not a list")
