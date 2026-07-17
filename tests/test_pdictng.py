"""aPersistDict and friends against an in-memory KV backend."""

import asyncio

import pytest
import pytest_asyncio

from forest import pdictng
from tests.conftest import FakeKV


@pytest_asyncio.fixture()
def kv(monkeypatch):
    """Replace the network-backed KV client with a shared in-memory fake"""
    store = FakeKV()
    monkeypatch.setattr(pdictng, "fasterpKVStoreClient", lambda: store)
    return store


async def make_dict(*args, **kwargs) -> pdictng.aPersistDict:
    d = pdictng.aPersistDict(*args, **kwargs)
    await d.init_task
    return d


@pytest.mark.asyncio
async def test_set_get_roundtrip(kv) -> None:
    d = await make_dict("tag")
    await d.set("k", "v")
    assert await d.get("k") == "v"


@pytest.mark.asyncio
async def test_get_default(kv) -> None:
    d = await make_dict("tag")
    assert await d.get("missing") is None
    assert await d.get("missing", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_falsy_values_are_readable(kv) -> None:
    """stored falsy values ("", 0, []) are real values, not treated as missing"""
    d = await make_dict("tag")
    await d.set("zero", 0)
    await d.set("empty", "")
    await d.set("list", [])
    assert await d.get("zero", "default") == 0
    assert await d.get("empty", "default") == ""
    assert await d.get("list", "default") == []
    assert await d["zero"] == 0  # __getitem__ doesn't raise for falsy values


@pytest.mark.asyncio
async def test_write_before_restore_does_not_clobber(kv) -> None:
    """a set() issued immediately after construction waits for the restore,
    so pre-existing persisted keys survive"""
    seed = await make_dict("tag")
    await seed.set("persisted", "value")

    d = pdictng.aPersistDict("tag")
    # no awaiting init_task: set() must do it internally
    await d.set("new", "key")
    assert await d.get("persisted") == "value"
    assert await d.get("new") == "key"


@pytest.mark.asyncio
async def test_persistence_across_instances(kv) -> None:
    """a second dict with the same tag restores state from the backend"""
    first = await make_dict("shared-tag")
    await first.set("k", "v")
    await first.set("n", 5)

    second = await make_dict("shared-tag")
    assert await second.get("k") == "v"
    assert await second.get("n") == 5


@pytest.mark.asyncio
async def test_tags_are_isolated(kv) -> None:
    a = await make_dict("tag-a")
    await a.set("k", "from-a")
    b = await make_dict("tag-b")
    assert await b.get("k") is None


@pytest.mark.asyncio
async def test_constructor_kwargs_seed_and_override(kv) -> None:
    """constructor kwargs are applied after restore, overriding persisted values"""
    first = await make_dict("tag")
    await first.set("k", "persisted")
    second = await make_dict("tag", k="overridden", fresh="new")
    assert await second.get("k") == "overridden"
    assert await second.get("fresh") == "new"


@pytest.mark.asyncio
async def test_remove_and_pop(kv) -> None:
    d = await make_dict("tag")
    await d.set("k", "v")
    await d.remove("k")
    assert await d.get("k") is None
    await d.remove("never-existed")  # no raise

    await d.set("p", "q")
    assert await d.pop("p") == "q"
    assert await d.get("p") is None
    assert await d.pop("gone", "default") == "default"
    # pop returns stored falsy values, not the default
    await d.set("zero", 0)
    assert await d.pop("zero", "default") == 0


@pytest.mark.asyncio
async def test_concurrent_pops_yield_one_winner(kv) -> None:
    """pop is atomic: exactly one of N concurrent pops gets the value"""
    d = await make_dict("tag")
    await d.set("once", "prize")
    results = await asyncio.gather(*(d.pop("once") for _ in range(10)))
    assert results.count("prize") == 1
    assert results.count(None) == 9


@pytest.mark.asyncio
async def test_keys_values_items(kv) -> None:
    d = await make_dict("tag")
    await d.set("a", 1)
    await d.set("b", 2)
    assert await d.keys() == ["a", "b"]
    assert await d.values() == [1, 2]
    assert await d.items() == [("a", 1), ("b", 2)]


@pytest.mark.asyncio
async def test_get_key_by_value(kv) -> None:
    d = await make_dict("tag")
    await d.set("alice", "+1")
    await d.set("bob", "+2")
    assert await d.get_key_by_value("+2") == "bob"
    assert await d.get_key_by_value("+999") is None
    assert await d.get_key_by_value("+999", "default") == "default"
    # duplicate values: first key in insertion order wins
    await d.set("carol", "+1")
    assert await d.get_key_by_value("+1") == "alice"
    # non-string values work too
    await d.set("count", 42)
    assert await d.get_key_by_value(42) == "count"


@pytest.mark.asyncio
async def test_getitem(kv) -> None:
    d = await make_dict("tag")
    await d.set("k", "v")
    assert await d["k"] == "v"
    with pytest.raises(KeyError):
        await d["missing"]


@pytest.mark.asyncio
async def test_setitem_protocol_removed(kv) -> None:
    """the fire-and-forget d[k] = v protocol is gone; writes are always awaited"""
    d = await make_dict("tag")
    with pytest.raises(TypeError):
        d["k"] = "v"


@pytest.mark.asyncio
async def test_concurrent_sets_all_land(kv) -> None:
    d = await make_dict("tag")
    await asyncio.gather(*(d.set(f"k{i}", i) for i in range(20)))
    assert len(await d.keys()) == 20


@pytest.mark.asyncio
async def test_json_roundtrip_types(kv) -> None:
    """values survive as their JSON forms: tuples become lists, int keys become str"""
    first = await make_dict("tag")
    await first.set("list", [1, "two", {"three": 3}])
    await first.set("dict", {"nested": {"deep": True}})
    second = await make_dict("tag")
    assert await second.get("list") == [1, "two", {"three": 3}]
    assert await second.get("dict") == {"nested": {"deep": True}}


@pytest.mark.asyncio
async def test_ints_increment_decrement(kv) -> None:
    d = pdictng.aPersistDictOfInts("counters")
    await d.init_task
    await d.increment("hits", 5)
    await d.increment("hits", 2)
    assert await d.get("hits") == 7
    await d.decrement("hits", 3)
    assert await d.get("hits") == 4
    # increments start from 0 for missing keys
    await d.increment("fresh", 1)
    assert await d.get("fresh") == 1


@pytest.mark.asyncio
async def test_ints_type_error(kv) -> None:
    d = pdictng.aPersistDictOfInts("counters")
    await d.init_task
    await d.set("s", "not an int")
    with pytest.raises(TypeError):
        await d.increment("s", 1)


@pytest.mark.asyncio
async def test_lists_extend_and_remove_from(kv) -> None:
    d = pdictng.aPersistDictOfLists("lists")
    await d.init_task
    await d.extend("l", "a")
    await d.extend("l", "b")
    await d.extend("l", "a")
    assert await d.get("l") == ["a", "b", "a"]
    # remove_from strips ALL occurrences
    await d.remove_from("l", "a")
    assert await d.get("l") == ["b"]


@pytest.mark.asyncio
async def test_lists_type_error(kv) -> None:
    d = pdictng.aPersistDictOfLists("lists")
    await d.init_task
    await d.set("s", "not a list")
    with pytest.raises(TypeError):
        await d.extend("s", "x")


@pytest.mark.asyncio
async def test_every_write_persists_whole_dict(kv) -> None:
    """DOCUMENTS DESIGN: each set() posts the entire serialized dict"""
    d = await make_dict("tag")
    await d.set("a", 1)
    await d.set("b", 2)
    assert len(kv.posts) == 2
    # the last post contains both keys
    import json

    assert json.loads(kv.posts[-1][1]) == {"a": 1, "b": 2}
