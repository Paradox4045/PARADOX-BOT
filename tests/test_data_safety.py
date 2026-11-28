import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Importing the bot must never start polling; keep its initial data path isolated.
_IMPORT_DATA = Path(tempfile.gettempdir()) / "paradox-ci-import-data.json"
os.environ.setdefault("DATA_FILE", str(_IMPORT_DATA))
os.environ.setdefault("BOT_TOKEN", "ci-test-token")
os.environ.setdefault("BOT_OWNER_ID", "123456789")

import bot


class DataSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.original_data = bot.data
        self.addCleanup(setattr, bot, "data", self.original_data)

    def test_corrupt_json_is_preserved_before_defaults_are_returned(self):
        data_file = self.root / "paradox_data.json"
        damaged = "{ this is not valid JSON"
        data_file.write_text(damaged, encoding="utf-8")

        with patch.object(bot, "DATA_FILE", data_file):
            loaded = bot.load_data()

        self.assertEqual(loaded["chats"], {})
        self.assertEqual(loaded["bot_groups"], {})
        self.assertEqual(loaded["settings"]["force_channel"], None)
        backups = list(self.root.glob("paradox_data.json.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), damaged)

    def test_invalid_top_level_section_is_preserved(self):
        data_file = self.root / "paradox_data.json"
        original = json.dumps({"chats": [], "settings": {}, "bot_groups": {}})
        data_file.write_text(original, encoding="utf-8")

        with patch.object(bot, "DATA_FILE", data_file):
            loaded = bot.load_data()

        self.assertEqual(loaded["chats"], {})
        backups = list(self.root.glob("paradox_data.json.corrupt-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8"))["chats"], [])

    def test_group_restore_does_not_change_another_group(self):
        data_file = self.root / "paradox_data.json"
        bot.data = {
            "chats": {
                "-1001": {"warnings": {"10": 2}, "message_history": []},
                "-1002": {"warnings": {"20": 3}, "message_history": []},
            },
            "bot_groups": {},
            "settings": {},
        }

        with patch.object(bot, "DATA_FILE", data_file):
            backup_path = bot.create_group_backup(-1001)
            bot.data["chats"]["-1001"]["warnings"]["10"] = 99
            bot.data["chats"]["-1002"]["warnings"]["20"] = 88
            bot.restore_group_backup(-1001, backup_path)

        self.assertEqual(bot.data["chats"]["-1001"]["warnings"]["10"], 2)
        self.assertEqual(bot.data["chats"]["-1002"]["warnings"]["20"], 88)

    def test_message_history_rebuild_skips_invalid_chat_ids(self):
        bot.data = {
            "chats": {
                "not-a-chat-id": {"message_history": [{"id": 1, "date": 1}]},
                "-1003": {"message_history": [{"id": 7, "date": 123}]},
            },
            "bot_groups": {},
            "settings": {},
        }

        bot.rebuild_message_history()

        self.assertNotIn("not-a-chat-id", bot.group_message_history)
        self.assertEqual(list(bot.group_message_history[-1003]), [{"id": 7, "date": 123}])


if __name__ == "__main__":
    unittest.main()
