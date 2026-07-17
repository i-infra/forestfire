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
async def test_get_falsy_value_returns_default_quirk(kv) -> None:
    """DOCUMENTS A QUIRK: get() uses `or`, so stored falsy values ("", 0, [])
    are indistinguishable from absent keys and the default wins."""
    d = await make_dict("tag")
    await d.set("zero", 0)
    await d.set("empty", "")
    assert await d.get("zero", "default") == "default"  # not 0!
    assert await d.get("empty", "default") == "default"  # not ""!


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


@pytest.mark.asyncio
async def test_getitem_setitem(kv) -> None:
    d = await make_dict("tag")
    d["k"] = "v"  # sync setitem schedules a write_task
    assert await d.get("k") == "v"  # get awaits the pending write
    with pytest.raises(KeyError):
        await d["missing"]


@pytest.mark.asyncio
async def test_setitem_rejects_overlapping_writes(kv) -> None:
    d = await make_dict("tag")
    d["a"] = "1"
    with pytest.raises(ValueError, match="write_task incomplete"):
        d["b"] = "2"


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
