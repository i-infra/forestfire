"""StdioMessage parsing of signal-cli jsonRpc envelopes."""

from forest.message import Message, StdioMessage

from tests.conftest import USER_NUMBER, USER_UUID


def envelope(data_message: dict, **envelope_fields: object) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "receive",
        "envelope": {
            "source": USER_NUMBER,
            "sourceNumber": USER_NUMBER,
            "sourceUuid": USER_UUID,
            "sourceName": "sylv",
            "sourceDevice": 3,
            "timestamp": 1637290344242,
            "dataMessage": data_message,
            **envelope_fields,
        },
        "account": "+11111111111",
    }


def test_parse_command() -> None:
    msg = StdioMessage(envelope({"timestamp": 1637290344242, "message": "/ping foo"}))
    assert msg.arg0 == "ping"
    assert msg.arg1 == "foo"
    assert msg.text == "foo"
    assert msg.full_text == "/ping foo"
    assert msg.source == USER_NUMBER
    assert msg.uuid == USER_UUID
    assert msg.name == "sylv"
    assert msg.timestamp == 1637290344242
    assert not msg.group


def test_arg0_is_lowercased_and_deslashed() -> None:
    msg = StdioMessage(envelope({"message": "/PING"}))
    assert msg.arg0 == "ping"


def test_quoted_tokens_use_shlex() -> None:
    msg = StdioMessage(envelope({"message": "/send 'a b' c"}))
    assert msg.arg1 == "a b"
    assert msg.arg2 == "c"


def test_json_payload_not_shlexed() -> None:
    msg = StdioMessage(envelope({"message": '/auth {"key": "value"}'}))
    assert msg.arg0 == "auth"
    assert msg.text == '{"key": "value"}'


def test_group_message() -> None:
    msg = StdioMessage(
        envelope({"message": "hi", "groupInfo": {"groupId": "grouppp="}})
    )
    assert msg.group == "grouppp="


def test_reaction() -> None:
    msg = StdioMessage(
        envelope(
            {
                "reaction": {
                    "emoji": "❤️",
                    "targetAuthorUuid": USER_UUID,
                    "targetSentTimestamp": 1647300333914,
                }
            }
        )
    )
    assert msg.reaction is not None
    assert msg.reaction.emoji == "❤️"
    assert msg.reaction.ts == 1647300333914
    assert not msg.text


def test_quote() -> None:
    msg = StdioMessage(
        envelope(
            {
                "message": "replying",
                "quote": {
                    "id": 1641591686224,
                    "authorNumber": USER_NUMBER,
                    "authorUuid": USER_UUID,
                    "text": "hi",
                    "attachments": [],
                },
            }
        )
    )
    assert msg.quote is not None
    assert msg.quote.text == "hi"
    assert msg.quoted_text == "hi"


def test_typing_envelope() -> None:
    msg = StdioMessage(
        envelope({}, typingMessage={"action": "STARTED", "timestamp": 1648512301846})
    )
    assert msg.typing == "STARTED"
    assert not msg.text


def test_missing_attributes_are_none() -> None:
    msg = StdioMessage(envelope({"message": "hello"}))
    assert msg.no_such_attribute is None
    assert msg.payment is None


def test_uuid_fallback_source() -> None:
    blob = envelope({"message": "hi"})
    del blob["envelope"]["source"]
    msg = StdioMessage(blob)
    assert msg.source == USER_UUID


def test_bare_message_base_class() -> None:
    class Bare(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__({})

    msg = Bare("/hello world extra words here")
    assert msg.arg0 == "hello"
    assert (msg.arg1, msg.arg2, msg.arg3) == ("world", "extra", "words")
    assert msg.tokens == ["world", "extra", "words", "here"]
