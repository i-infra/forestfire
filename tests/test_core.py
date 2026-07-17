"""Command dispatch, admin gating, do_eval, and QuestionBot flows through MockBot."""

import asyncio

import pytest
import pytest_asyncio

from forest import core
from tests.conftest import ADMIN_UUID, BOT_NUMBER, USER_UUID
from tests.mockbot import MockBot


@pytest_asyncio.fixture()
async def bot():
    """Bot Fixture allows for exiting gracefully"""
    bot = MockBot(BOT_NUMBER)
    yield bot
    bot.sigints += 1
    bot.exiting = True
    bot.handle_messages_task.cancel()
    await bot.client_session.close()
    if bot.datastore._client is not None:
        await bot.datastore._client.conn.close()
    await core.pghelp.pool.close()


@pytest.mark.asyncio
async def test_commands(bot, set_secret) -> None:
    """Tests commands"""
    # Enable Magic allows for mistyped commands
    set_secret("ENABLE_MAGIC", "1")

    # Tests do_ping with a mistyped command, expecting "/pong foo"
    assert await bot.get_cmd_output("/pingg foo") == "/pong foo"

    # tests the uptime command just checks to see if it starts with "Uptime: "
    assert (await bot.get_cmd_output("uptime")).startswith("Uptime: ")

    assert (await bot.get_cmd_output("/help")).startswith("Documented commands:")

    # test the default behaviour
    assert (await bot.get_cmd_output("gibberish two")).startswith(
        "That didn't look like a valid command"
    )


@pytest.mark.asyncio
async def test_eval_requires_admin(bot) -> None:
    assert (
        await bot.get_cmd_output("/eval return 1+1")
        == "you must be an admin to use this command"
    )


@pytest.mark.asyncio
async def test_eval_as_admin(bot) -> None:
    assert await bot.get_cmd_output("/eval return 1+1", uuid=ADMIN_UUID) == "2"


@pytest.mark.asyncio
async def test_eval_disabled(bot, set_secret) -> None:
    set_secret("ENABLE_EVAL", "")
    assert (await bot.get_cmd_output("/eval return 1+1", uuid=ADMIN_UUID)).startswith(
        "do_eval is disabled"
    )


@pytest.mark.asyncio
async def test_mentions_us_by_uuid(bot) -> None:
    from tests.conftest import BOT_UUID
    from tests.mockbot import MockMessage

    msg = MockMessage("hey bot")
    msg.mentions = [{"name": "forestbot", "uuid": BOT_UUID, "start": 0, "length": 1}]
    assert bot.mentions_us(msg)
    msg.mentions = [{"name": "someone else", "uuid": "not-the-bot"}]
    assert not bot.mentions_us(msg)


@pytest.mark.asyncio
async def test_questions(bot) -> None:
    """Tests the various questions from questionbot class"""
    # the issue here is that we need to send "yes" *after* the question has been asked
    # so we make it as create_task, then send the input, then await the task to get the result

    answer = asyncio.create_task(
        bot.ask_yesno_question(USER_UUID, "Do you like faeries?")
    )
    await bot.send_input("yes")
    assert await answer is True

    answer = asyncio.create_task(
        bot.ask_freeform_question(USER_UUID, "What's your favourite tree?")
    )
    await bot.send_input("Birch")
    assert await answer == "Birch"

    answer = asyncio.create_task(
        bot.ask_intable_question(USER_UUID, "How many fingers am I holding up?")
    )
    await bot.send_input("7")
    assert await answer == 7

    answer = asyncio.create_task(
        bot.ask_floatable_question(USER_UUID, "How much is your life worth stranger?")
    )
    await bot.send_input("7.99")
    assert await answer == 7.99

    question_text = "What is your tshirt size?"
    options = {"S": "", "M": "", "L": "", "XL": "", "XXL": ""}

    choice = asyncio.create_task(
        bot.ask_multiple_choice_question(
            USER_UUID, question_text, options, require_confirmation=False
        )
    )
    await bot.send_input("M")
    assert await choice == "M"

    choice = asyncio.create_task(
        bot.ask_multiple_choice_question(
            USER_UUID, question_text, options, require_confirmation=True
        )
    )
    await bot.send_input("XXL")
    await asyncio.sleep(0)
    await bot.send_input("yes")
    assert await choice == "XXL"
