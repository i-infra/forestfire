"""
FYI: this module uses a lot of `or`. `None or 1` is `1`, not `True`.
We're using this because you can have `{"attachments": null}` in JSON, which
breaks our typing if we expect Message.attachments to be list[str].
Using `or` like this is a bit of a hack, but it's what we've got.
"""

import shlex
import unicodedata
import json
from typing import Optional

from forest.utils import logging


def unicode_character_name(i: int) -> str:
    """Tries to get the unicode name for a given character ordinal.
    Useful for finding various quotation marks"""
    try:
        return unicodedata.name(chr(i))
    except ValueError:
        return ""


unicode_quotes = [
    chr(i) for i in range(0, 0x10FFF) if "QUOTATION MARK" in unicode_character_name(i)
]


class Dictable:
    def to_dict(self) -> dict:
        """
        Returns a dictionary of message instance
        variables except for the blob
        """
        properties = {}
        for attr in dir(self):
            if not (
                attr.startswith("_")
                or attr in ("blob", "full_text", "envelope", "source")
            ):
                val = getattr(self, attr)
                if val and not callable(val):
                    # if attr == "text":
                    #    val = termcolor.colored(val, attrs=["bold"])
                    #    # gets mangled by repr
                    if isinstance(val, Dictable):
                        properties[attr] = val.to_dict()
                    else:
                        properties[attr] = val

        return properties


class Message(Dictable):
    """
    Base message type

    Attributes
    -----------
    blob: dict
       blob representing the jsonrpc message
    """

    timestamp: int
    text: str
    full_text: str
    attachments: list[dict[str, str]]
    group: Optional[str]
    quoted_text: str
    mentions: list[dict[str, str]]
    uuid: str
    payment: dict
    typing: str
    # set by PayBot when constructing synthetic payment-status messages
    status: str
    transaction_log_id: str
    arg0: str
    arg1: Optional[str]
    arg2: Optional[str]
    arg3: Optional[str]
    reactions: dict[str, str]
    # reaction: Optional[Reaction]
    # quote: Optional[Quote]

    def __init__(self, blob: dict) -> None:
        self.blob = blob
        # list that will hold the separate words of the message if there are any.
        self.tokens: list[str] = []
        # if message has no text, don't parse
        if self.text:
            self.parse_text(self.text)

    def parse_text(self, text: str) -> None:
        "set current self.text and tokenization to text"
        try:
            try:
                # this is if you're expecting json
                arg0, maybe_json = text.split(" ", 1)
                assert json.loads(maybe_json)  # ensure the text is valid json
                self.tokens = maybe_json.split(" ")
            except (json.JSONDecodeError, AssertionError):
                # replace quotes with single quote so as to not break when parsing
                clean_quote_text = text
                for quote in unicode_quotes:
                    clean_quote_text.replace(quote, "'")
                arg0, *self.tokens = shlex.split(clean_quote_text)
        except ValueError:
            arg0, *self.tokens = text.split(" ")
        self.arg0 = arg0.removeprefix("/").lower()
        # sets the next 3 words to arguments, or sets the argument to empty otherwise
        if self.tokens:
            self.arg1, self.arg2, self.arg3, *_ = self.tokens + [""] * 3
        # reconstitute the text minus arg0
        self.text = " ".join(self.tokens)

    _warned_source = False

    @property
    def source(self) -> Optional[str]:
        """Deprecated alias for .uuid.
        Phone numbers stopped being user-facing identities with Signal's
        phone number privacy; uuid is the only identity now."""
        if not Message._warned_source:
            Message._warned_source = True
            logging.warning(
                "Message.source is deprecated and now returns Message.uuid; "
                "switch to Message.uuid"
            )
        return self.uuid

    def __getattr__(self, attr: str) -> None:
        # return falsy string back if not found
        return None

    def __repr__(self) -> str:
        return f"Message: {json.dumps(self.to_dict())}"


class Reaction(Dictable):
    def __init__(self, reaction: dict) -> None:
        assert reaction
        self.emoji = reaction["emoji"]
        self.uuid = reaction.get("targetAuthorUuid")
        self.author = self.uuid or ""
        self.ts = reaction["targetSentTimestamp"]


class Quote(Dictable):
    def __init__(self, quote: dict) -> None:
        assert quote
        # signal-cli:
        # {"id":1641591686224,"author":"+***REMOVED***","authorNumber":"+***REMOVED***","authorUuid":"412e180d-c500-4c60-b370-14f6693d8ea7","text":"hi","attachments":[]}
        # auxin:
        # {'authorUuid': 'da1fb04c-bf1a-458f-92c7-6f21ad443684', 'id': 1647300333914, 'text': '/pong'}
        self.ts = quote["id"]
        self.uuid = quote.get("authorUuid")
        self.author = self.uuid or ""
        self.text = quote["text"]


class StdioMessage(Message):
    """Represents a Message received from signal-cli, optionally containing a command with arguments."""

    def __init__(self, blob: dict) -> None:
        self.id = blob.get("id")
        result = blob.get("result", {})
        self.envelope = envelope = blob.get("envelope", {})
        # {"envelope":{"source":"+***REMOVED***","sourceNumber":"+***REMOVED***","sourceUuid":"412e180d-c500-4c60-b370-14f6693d8ea7","sourceName":"sylv","sourceDevice":3,"timestamp":1637290589910,"dataMessage":{"timestamp":1637290589910,"message":"/ping","expiresInSeconds":0,"viewOnce":false}},"account":"+447927948360"}
        self.uuid = envelope.get("sourceUuid")
        self.name: str = envelope.get("sourceName") or self.uuid
        self.device_id = envelope.get("sourceDevice")
        self.timestamp = envelope.get("timestamp") or result.get("timestamp")

        # msg data
        msg = envelope.get("dataMessage", {})
        # "attachments":[{"contentType":"image/png","filename":"image.png","id":"1484072582431702699","size":2496}]}
        self.attachments: list[dict[str, str]] = msg.get("attachments", [])
        # "mentions":[{"name":"+447927948360","number":"+447927948360","uuid":"fc4457f0-c683-44fe-b887-fe3907d7762e","start":0,"length":1}
        self.mentions = msg.get("mentions") or []
        self.full_text = self.text = msg.get("message", "")
        self.group: Optional[str] = msg.get("groupInfo", {}).get(
            "groupId"
        ) or result.get("groupId")
        self.quoted_text = msg.get("quote", {}).get("text")
        self.typing = envelope.get("typingMessage", {}).get("action")
        self.payment = msg.get("payment")
        try:
            self.quote: Optional[Quote] = Quote(msg.get("quote"))
        except (AssertionError, KeyError):
            self.quote = None
        try:
            self.reaction: Optional[Reaction] = Reaction(msg.get("reaction"))
        except (AssertionError, KeyError):
            self.reaction = None
        self.reactions: dict[str, str] = {}
        if self.text:
            logging.info(self)  # "parsed a message with body: '%s'", self.text)
        super().__init__(blob)
