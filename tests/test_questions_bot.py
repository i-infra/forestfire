"""QuestionBot flows driven end-to-end as slash commands."""

import pytest
import pytest_asyncio

from forest import core
from forest.core import Message, Response, run_bot
from tests.conftest import BOT_NUMBER
from tests.mockbot import MockBot, Tree


class DialogBot(MockBot):
    """Bot that has tests for every type of question"""

    async def do_test_ask_yesno_question(self, message: Message) -> Response:
        """Asks a sample Yes or No question"""

        if await self.ask_yesno_question(
            (message.uuid, message.group), "Do you like faeries?"
        ):
            return "That's cool, me too!"
        return "Aww :c"

    async def do_test_multiple_choice_list_no_confirm(
        self, message: Message
    ) -> Response:
        """Asks a Sample Multiple Choice question with list and no confirmation"""

        question_text = "What is your favourite forest creature?"
        options = ["Deer", "Foxes", "Faeries", "Crows"]

        choice = await self.ask_multiple_choice_question(
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=False,
        )
        if choice and choice == "Faeries":
            return "Faeries are my favourite too c:"

        if choice:
            return f"I think {choice} are super neat too!"

        return "oops, sorry"

    async def do_test_multiple_choice_dict_emptyval(self, message: Message) -> Response:
        """Asks a Sample Multiple Choice question with a dict with empty values"""

        question_text = "What is your tshirt size?"
        options = {"S": "", "M": "", "L": "", "XL": "", "XXL": ""}

        choice = await self.ask_multiple_choice_question(
            (message.uuid, message.group),
            question_text,
            options,
            require_confirmation=True,
        )
        if choice:
            return choice
        return "oops, sorry"

    async def do_test_ask_freeform_question(self, message: Message) -> Response:
        """Asks a sample freeform question"""

        answer = await self.ask_freeform_question(
            (message.uuid, message.group), "What's your favourite tree?"
        )

        if answer:
            return f"No way! I love {answer} too!!"
        return "oops, sorry"


@pytest_asyncio.fixture()
async def bot():
    """Bot Fixture allows for exiting gracefully"""
    bot = DialogBot(BOT_NUMBER)
    yield bot
    bot.sigints += 1
    bot.exiting = True
    bot.handle_messages_task.cancel()
    await bot.client_session.close()
    await bot.datastore.client.conn.close()
    await core.pghelp.pool.close()


@pytest.mark.asyncio
async def test_dialog(bot) -> None:
    """Tests the bot by running a dialogue"""
    dialogue = [
        ["test_ask_yesno_question", "Do you like faeries?"],
        ["yes", "That's cool, me too!"],
    ]

    for line in dialogue:
        assert await bot.get_cmd_output(line[0]) == line[1]


@pytest.mark.asyncio
async def test_yesno_tree(bot) -> None:
    """Tests the bot by running a tree"""
    tree = Tree(
        ["test_ask_yesno_question", "Do you like faeries?"],
        [Tree(["yes", "That's cool, me too!"]), Tree(["no", "Aww :c"])],
    )
    tests = tree.get_all_paths()

    for test in tests:
        for subtest in test:
            assert await bot.get_cmd_output(subtest[0]) == subtest[1]


@pytest.mark.asyncio
async def test_freeform_and_multiple_choice(bot) -> None:
    """Free-form and multiple-choice flows driven as commands"""
    assert (
        await bot.get_cmd_output("test_ask_freeform_question")
        == "What's your favourite tree?"
    )
    assert await bot.get_cmd_output("Birch") == "No way! I love Birch too!!"

    first = await bot.get_cmd_output("test_multiple_choice_list_no_confirm")
    assert first.startswith("What is your favourite forest creature?")
    # list options get numbered labels; "Faeries" is option 3
    assert "3) Faeries" in first
    assert await bot.get_cmd_output("3") == "Faeries are my favourite too c:"


if __name__ == "__main__":
    run_bot(DialogBot)
