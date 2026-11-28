import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

_IMPORT_DATA = Path(tempfile.gettempdir()) / "paradox-nexus-memory-test-import.json"
os.environ.setdefault("DATA_FILE", str(_IMPORT_DATA))
os.environ.setdefault("BOT_TOKEN", "ci-test-token")
os.environ.setdefault("BOT_OWNER_ID", "123456789")

import bot


class NexusMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_path = Path(self.temp_dir.name) / "paradox_data.json"
        self.original_data = bot.data
        bot.data = {"chats": {}, "bot_groups": {}, "settings": {}}
        self.addCleanup(setattr, bot, "data", self.original_data)

    def user(self, user_id, name="Test Member"):
        return SimpleNamespace(id=user_id, full_name=name)

    def test_private_facts_are_isolated_by_group_and_user(self):
        first_group = bot.chat_data(-1001001)
        second_group = bot.chat_data(-1001002)
        owner = self.user(111, "Member One")

        with patch.object(bot, "DATA_FILE", self.data_path):
            self.assertTrue(
                bot._nexus_store_fact(
                    first_group, "أحب ألعاب كرة القدم", owner, scope="user"
                )
            )

        self.assertEqual(
            bot._nexus_relevant_facts(first_group, 111, "ألعاب كرة القدم"),
            [],
        )
        self.assertEqual(
            bot._nexus_relevant_facts(
                first_group, 111, "ألعاب كرة القدم", include_user=True
            ),
            ["أحب ألعاب كرة القدم"],
        )
        self.assertEqual(
            bot._nexus_relevant_facts(
                first_group, 222, "ألعاب كرة القدم", include_user=True
            ),
            [],
        )
        self.assertEqual(
            bot._nexus_relevant_facts(
                second_group, 111, "ألعاب كرة القدم", include_user=True
            ),
            [],
        )

    def test_group_facts_are_shared_only_inside_their_group(self):
        first_group = bot.chat_data(-1002001)
        second_group = bot.chat_data(-1002002)
        admin = self.user(333, "Group Admin")

        with patch.object(bot, "DATA_FILE", self.data_path):
            bot._nexus_store_fact(
                first_group, "قانون المجموعة يمنع الإعلانات", admin, scope="group"
            )

        self.assertEqual(
            bot._nexus_relevant_facts(first_group, 444, "قانون الإعلانات"),
            ["قانون المجموعة يمنع الإعلانات"],
        )
        self.assertEqual(
            bot._nexus_relevant_facts(second_group, 444, "قانون الإعلانات"),
            [],
        )

    def test_existing_short_memory_and_song_library_are_preserved(self):
        old_turns = [{"role": "user", "text": "Member: old topic"}]
        old_songs = {"track": {"name": "Track", "file_id": "telegram-file-id"}}
        bot.data["chats"]["-1003001"] = {
            "nexus_memory": old_turns[:],
            "songs": old_songs.copy(),
            "message_history": [{"id": 9, "date": 123.0}],
        }

        info = bot.chat_data(-1003001)

        self.assertEqual(info["nexus_memory"], old_turns)
        self.assertEqual(info["songs"], old_songs)
        self.assertEqual(info["message_history"], [{"id": 9, "date": 123.0}])
        self.assertIn("nexus_facts", info)

    def test_long_term_memory_survives_save_and_reload(self):
        info = bot.chat_data(-1004001)
        member = self.user(555, "Member Five")

        with patch.object(bot, "DATA_FILE", self.data_path):
            bot._nexus_store_fact(
                info, "أفضل القهوة بدون سكر", member, scope="user"
            )
            loaded = bot.load_data()

        stored = loaded["chats"]["-1004001"]["nexus_facts"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["user_id"], "555")
        self.assertEqual(stored[0]["text"], "أفضل القهوة بدون سكر")

    def test_jamendo_skips_tracks_without_download_permission(self):
        class FakeResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "headers": {"status": "success"},
                    "results": [{
                        "name": "Not downloadable",
                        "audiodownload_allowed": False,
                        "audiodownload": "https://example.invalid/audio.mp3",
                        "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
                    }],
                }

        with (
            patch.object(bot, "JAMENDO_CLIENT_ID", "test-client-id"),
            patch.object(bot.requests, "get", return_value=FakeResponse()) as get,
        ):
            result = bot._search_jamendo_audio("test track")

        self.assertIsNone(result)
        get.assert_called_once()

    def test_youtube_audio_downloader_is_not_present(self):
        self.assertFalse(hasattr(bot, "youtube_search"))
        self.assertFalse(hasattr(bot, "find_youtube_song"))
        self.assertFalse(hasattr(bot, "download_youtube_cc_mp3"))


if __name__ == "__main__":
    unittest.main()
