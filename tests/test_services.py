import unittest
import asyncio

from bot.database import Repository
from bot.services import (
    choose_weighted_reaction,
    normalize_chat_reference,
    parse_reaction_input,
    render_welcome,
    validate_reaction_set,
)


class DummyRandom:
    def choices(self, items, weights, k):
        return [items[-1]]


class DummyUser:
    id = 42
    first_name = "Ari"
    last_name = "Toon"
    username = "ari"


class ServiceTests(unittest.TestCase):
    def test_reference_validation(self):
        self.assertEqual(normalize_chat_reference("@Anime"), "@Anime")
        self.assertEqual(normalize_chat_reference("-100123456"), -100123456)
        self.assertIsNone(normalize_chat_reference("not a chat"))

    def test_reaction_input_and_total(self):
        self.assertEqual(parse_reaction_input("❤️ 50"), ("❤️", 50))
        self.assertIsNone(parse_reaction_input("hello 50"))
        self.assertEqual(validate_reaction_set([
            {"emoji": "❤️", "percentage": 100}
        ])[0], True)
        self.assertFalse(validate_reaction_set([
            {"emoji": "❤️", "percentage": 80}
        ])[0])

    def test_weighted_selection(self):
        value = choose_weighted_reaction(
            [{"emoji": "❤️", "percentage": 50}, {"emoji": "🔥", "percentage": 50}],
            DummyRandom(),
        )
        self.assertEqual(value, "🔥")

    def test_welcome_placeholders(self):
        self.assertEqual(
            render_welcome("Hi {first_name} @{username} {user_id}", DummyUser(), "Chat"),
            "Hi Ari @ari 42",
        )

    def test_settings_flags_are_independent(self):
        async def scenario():
            repo = Repository("", "test")
            await repo.save_channel(7, {
                "chat_id": -1007, "title": "Independent", "username": "independent"
            })
            await repo.update_channel(7, -1007, {"enabled": False})
            await repo.update_channel(7, -1007, {
                "reaction_enabled": True,
                "reactions": [{"emoji": "❤️", "percentage": 100}],
            })
            result = await repo.get_channel(7, -1007)
            self.assertFalse(result["enabled"])
            self.assertTrue(result["reaction_enabled"])
            self.assertTrue(result["join_enabled"])
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()