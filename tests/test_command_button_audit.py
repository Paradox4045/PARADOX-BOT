import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.constants import ChatType

import bot


class FakeCallbackQuery:
    def __init__(self, data, user_id, chat_type=ChatType.PRIVATE):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(type=chat_type, id=987654321)
        )
        self.answers = []
        self.edited_text = None
        self.reply_markup = None

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, reply_markup=None, **kwargs):
        self.edited_text = text
        self.reply_markup = reply_markup

    async def edit_message_reply_markup(self, reply_markup=None, **kwargs):
        self.reply_markup = reply_markup


class CommandAndButtonAuditTests(unittest.IsolatedAsyncioTestCase):
    def test_bulk_delete_command_variants(self):
        cases = {
            "/حذف 20": 20,
            "مسح 12": 12,
            "تنظيف 5": 5,
            "احذف 20 رسالة": 20,
            "احذف اخر 7 رسائل": 7,
            "امسح آخر 15 رسالة": 15,
        }
        for command, expected in cases.items():
            with self.subTest(command=command):
                self.assertEqual(bot.parse_bulk_delete_count(command), expected)

    def test_bulk_delete_parser_rejects_unrelated_text(self):
        self.assertIsNone(bot.parse_bulk_delete_count("حذف الرسائل"))
        self.assertIsNone(bot.parse_bulk_delete_count("تنظيف المجموعة"))

    def test_back_main_button_returns_to_main_menu(self):
        user_id = 123456789
        query = FakeCallbackQuery("back_main", user_id)
        update = SimpleNamespace(callback_query=query)

        with patch.object(bot, "BOT_OWNER_ID", str(user_id)):
            await bot.menu_callback(update, SimpleNamespace())

        self.assertEqual(query.edited_text, "🔥 PARADOX 404\n\nاختار القسم:")
        self.assertIsNotNone(query.reply_markup)

    async def test_private_buttons_cannot_bypass_forced_subscription(self):
        query = FakeCallbackQuery("backup_center", 222222222)
        update = SimpleNamespace(callback_query=query)

        with (
            patch.object(bot, "BOT_OWNER_ID", "123456789"),
            patch.object(bot, "force_subscription_ok", new=AsyncMock(return_value=False)),
        ):
            await bot.menu_callback(update, SimpleNamespace())

        self.assertIsNone(query.edited_text)
        self.assertTrue(query.answers)
        self.assertTrue(query.answers[0][1].get("show_alert"))

    def test_log_buttons_are_hidden_without_log_permission(self):
        management_callbacks = [
            button.callback_data
            for row in bot.management_keyboard(-100123, include_logs=False).inline_keyboard
            for button in row
        ]
        statistics_callbacks = [
            button.callback_data
            for row in bot.statistics_keyboard(-100123, include_logs=False).inline_keyboard
            for button in row
        ]
        self.assertNotIn("al:-100123", management_callbacks)
        self.assertNotIn("sx:-100123:logs", statistics_callbacks)

    def test_log_permission_can_be_granted_to_telegram_admins(self):
        self.assertEqual(bot.PERMISSIONS["logs"], "سجل الإدارة")

    def test_deputy_role_buttons_use_the_permission_checked_by_the_handler(self):
        deputy_commands = {
            command: permission
            for command, _description, permission in bot.ROLE_COMMANDS["deputy"]
        }
        self.assertEqual(deputy_commands["رفع_مشرف"], "manage_roles")
        self.assertEqual(deputy_commands["عزل_مشرف"], "manage_roles")


if __name__ == "__main__":
    unittest.main()
