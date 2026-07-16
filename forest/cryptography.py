import gzip
import hashlib
from typing import Union, cast

import base58
from Crypto.Cipher import AES, _mode_eax

from forest import utils

SALT = utils.get_secret("SALT")
if not SALT:
    raise RuntimeError(
        "SALT envvar must be set for persistence. "
        "Generate one with: cat /dev/urandom | head -c 32 | base58"
    )
# build your AESKEY envvar with this: cat /dev/urandom | head -c 32 | base58
_aeskey_b58 = utils.get_secret("AESKEY")
if not _aeskey_b58:
    raise RuntimeError(
        "AESKEY envvar must be set for persistence (128b or 256b, base58 encoded). "
        "Generate one with: cat /dev/urandom | head -c 32 | base58"
    )
AESKEY = base58.b58decode(_aeskey_b58.encode()) * 2

if len(AESKEY) not in [16, 32, 64]:
    raise RuntimeError(
        "AESKEY must decode to 16 or 32 bytes (128b or 256b), base58 encoded."
    )

if len(AESKEY) == 64:
    AESKEY = AESKEY[:32]


def encrypt(data: bytes, key: bytes) -> bytes:
    """Accepts data (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns authenticated and encrypted blob (as bytearray)"""
    cipher = cast(_mode_eax.EaxMode, AES.new(key, AES.MODE_EAX))
    ciphertext, authtag = cipher.encrypt_and_digest(data)  # pylint: disable
    return cipher.nonce + authtag + ciphertext


def decrypt(data: bytes, key: bytes) -> bytes:
    """Accepts ciphertext (as arbitrary length bytearray) and key (as 16B or 32B bytearray) and returns decrypted (plaintext) blob (as bytearray)"""
    cipher = cast(_mode_eax.EaxMode, AES.new(key, AES.MODE_EAX, data[:16]))
    return cipher.decrypt_and_verify(data[32:], data[16:32])  # pylint: disable


def hash_salt(key_: str, salt: str = SALT) -> str:
    """returns a base58 encoded sha256sum of a salted key"""
    return base58.b58encode(hashlib.sha256(f"{salt}{key_}".encode()).digest()).decode()


def get_ciphertext_value(value_: Union[str, bytes]) -> str:
    """returns a base58 encoded aes128 AES EAX mode encrypted gzip compressed value"""
    if isinstance(value_, str):
        value_bytes = value_.encode()
    elif isinstance(value_, bytes):
        value_bytes = value_
    else:
        raise ValueError
    return base58.b58encode(encrypt(gzip.compress(value_bytes), AESKEY)).decode()


def get_cleartext_value(value_: str) -> str:
    """decrypts, decodes, decompresses a b58 blob returning cleartext"""
    return gzip.decompress(decrypt(base58.b58decode(value_), AESKEY)).decode()
