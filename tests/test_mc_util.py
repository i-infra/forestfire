"""mc_util: MOB/pMOB conversion and protobuf address wrapping (exercises the regenerated pb2s)."""

import base64
from decimal import Decimal

import pytest

import mc_util
from mc_util import external_pb2


def test_mob2pmob() -> None:
    assert mc_util.mob2pmob(1) == 10**12
    assert mc_util.mob2pmob(Decimal("0.5")) == 5 * 10**11
    assert mc_util.mob2pmob(0.001) == 10**9


def test_pmob2mob() -> None:
    assert mc_util.pmob2mob(10**12) == Decimal("1")
    assert mc_util.pmob2mob(1) == Decimal("1e-12")


def test_roundtrip_conversion() -> None:
    for mob in (Decimal("0.1"), Decimal("42"), Decimal("0.000000000001")):
        assert mc_util.pmob2mob(mc_util.mob2pmob(mob)) == mob


def make_b64_address() -> str:
    """serialize a synthetic PublicAddress the way full-service would hand it over"""
    address = external_pb2.PublicAddress()
    address.view_public_key.data = b"\x01" * 32
    address.spend_public_key.data = b"\x02" * 32
    address.fog_report_url = ""
    return base64.b64encode(address.SerializeToString()).decode()


def test_address_b64_to_b58_roundtrip() -> None:
    b64_address = make_b64_address()
    b58_wrapper = mc_util.b64_public_address_to_b58_wrapper(b64_address)
    assert b58_wrapper
    assert mc_util.b58_wrapper_to_b64_public_address(b58_wrapper) == b64_address


def test_bad_b58_checksum_rejected() -> None:
    b58_wrapper = mc_util.b64_public_address_to_b58_wrapper(make_b64_address())
    corrupted = b58_wrapper[:-2] + ("11" if not b58_wrapper.endswith("11") else "22")
    assert mc_util.b58_wrapper_to_b64_public_address(corrupted) is None
