"""Тесты выдачи и отзыва доступа. Сеть и токен не нужны."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import keyboards as kb  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.handlers import _deny_if_not_admin, _panel_text, _user_label  # noqa: E402
from bot.storage import NOTIFY_COOLDOWN, Storage  # noqa: E402

ADMIN = 6887373040
GUEST = 111222333


class AccessStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_nobody_allowed_by_default(self):
        self.assertFalse(self.store.is_allowed(GUEST))

    def test_grant_and_revoke(self):
        self.store.grant(GUEST, name="Гость", username="guest", by=ADMIN)
        self.assertTrue(self.store.is_allowed(GUEST))

        self.assertTrue(self.store.revoke(GUEST))
        self.assertFalse(self.store.is_allowed(GUEST))

    def test_revoke_reports_when_there_was_nothing(self):
        self.assertFalse(self.store.revoke(GUEST))

    def test_access_survives_restart(self):
        self.store.grant(GUEST, name="Гость")
        reopened = Storage(self.store.path)
        self.assertTrue(reopened.is_allowed(GUEST))

    def test_granted_details_are_kept(self):
        self.store.grant(GUEST, name="Гость", username="guest", by=ADMIN)
        users = dict(self.store.allowed_users())
        self.assertEqual(users[GUEST]["name"], "Гость")
        self.assertEqual(users[GUEST]["granted_by"], ADMIN)

    def test_newest_grant_comes_first(self):
        self.store.grant(1, name="Первый")
        time.sleep(1.1)
        self.store.grant(2, name="Второй")
        self.assertEqual([uid for uid, _ in self.store.allowed_users()], [2, 1])


class KnockNotificationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_knock_notifies(self):
        self.assertTrue(self.store.should_notify(GUEST, "Гость", "guest"))

    def test_repeat_knock_stays_quiet(self):
        # Иначе каждое сообщение чужого человека дёргало бы админа
        self.store.should_notify(GUEST)
        self.assertFalse(self.store.should_notify(GUEST))

    def test_name_is_remembered_for_the_grant_button(self):
        self.store.should_notify(GUEST, "Гость", "guest")
        info = self.store.pending_info(GUEST)
        self.assertEqual(info["name"], "Гость")
        self.assertEqual(info["username"], "guest")

    def test_cooldown_expires(self):
        self.store.should_notify(GUEST)
        self.store._data["notified"][str(GUEST)]["at"] -= NOTIFY_COOLDOWN + 1
        self.assertTrue(self.store.should_notify(GUEST))

    def test_grant_clears_the_knock(self):
        self.store.should_notify(GUEST, "Гость")
        self.store.grant(GUEST, name="Гость")
        self.assertEqual(self.store.pending_info(GUEST), {})


class AdminRightsTest(unittest.TestCase):
    def config(self, **kw):
        return Config(bot_token="t", dgis_api_key="k", **kw)

    def test_admin_passes(self):
        self.assertFalse(_deny_if_not_admin(ADMIN, self.config(admin_ids={ADMIN})))

    def test_ordinary_user_is_stopped(self):
        # Даже с доступом к боту: callback_data админки видна любому,
        # кто до неё доберётся, и без проверки он раздавал бы доступы
        cfg = self.config(admin_ids={ADMIN}, allowed_ids={GUEST})
        self.assertTrue(_deny_if_not_admin(GUEST, cfg))

    def test_admin_counts_as_allowed(self):
        cfg = self.config(admin_ids={ADMIN})
        self.assertTrue(cfg.is_allowed(ADMIN))
        self.assertTrue(cfg.access_configured)

    def test_admin_alone_configures_access(self):
        self.assertTrue(self.config(admin_ids={ADMIN}).access_configured)
        self.assertFalse(self.config().access_configured)


class PanelTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "state.json")
        self.cfg = Config(bot_token="t", dgis_api_key="k", admin_ids={ADMIN})

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_panel_explains_what_to_do(self):
        text = _panel_text(self.cfg, self.store)
        self.assertIn("пока нет", text)

    def test_panel_lists_granted_users(self):
        self.store.grant(GUEST, name="Гость", username="guest", by=ADMIN)
        text = _panel_text(self.cfg, self.store)
        self.assertIn("Гость", text)
        self.assertIn(str(GUEST), text)

    def test_label_falls_back_through_name_username_id(self):
        self.assertEqual(_user_label(1, {"name": "Имя", "username": "nick"}), "Имя (@nick)")
        self.assertEqual(_user_label(1, {"name": "Имя"}), "Имя")
        self.assertEqual(_user_label(1, {"username": "nick"}), "@nick")
        self.assertEqual(_user_label(1, {}), "1")


class AdminKeyboardTest(unittest.TestCase):
    def test_panel_offers_revoke_per_user(self):
        markup = kb.admin_panel([(GUEST, {"name": "Гость"})])
        payloads = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn(f"revoke:{GUEST}", payloads)
        self.assertIn("admin:grant", payloads)

    def test_request_buttons(self):
        payloads = [b.callback_data for row in kb.grant_request(GUEST).inline_keyboard for b in row]
        self.assertIn(f"grant:{GUEST}", payloads)
        self.assertIn("grant:no", payloads)

    def test_callbacks_fit_the_limit(self):
        markups = [
            kb.admin_panel([(GUEST, {"name": "Гость"})]),
            kb.confirm_revoke(GUEST, "Гость" * 20),
            kb.grant_request(GUEST),
        ]
        for markup in markups:
            for row in markup.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode()), 64)
                    self.assertLessEqual(len(button.text), 64)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class MiddlewareAccessTest(unittest.TestCase):
    """Самое важное: выданный из чата доступ должен пускать в бота."""

    def setUp(self):
        import asyncio
        from bot.handlers import AccessMiddleware

        self.asyncio = asyncio
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Storage(Path(self.tmp.name) / "state.json")
        self.cfg = Config(bot_token="t", dgis_api_key="k", admin_ids={ADMIN})
        self.middleware = AccessMiddleware(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def run_through(self, user_id, text="привет", bot=None):
        """Прогоняет событие через middleware. True — хендлер вызван."""
        import types

        called = []

        async def handler(event, data):
            called.append(True)
            return "ok"

        event = types.SimpleNamespace(text=text)
        # answer нужен ветке отказа
        async def answer(*a, **kw):
            return None
        event.answer = answer

        data = {
            "event_from_user": types.SimpleNamespace(
                id=user_id, username="guest", full_name="Гость",
            ),
            "storage": self.store,
            "bot": bot,
        }
        self.asyncio.run(self.middleware(handler, event, data))
        return bool(called)

    def test_stranger_is_stopped(self):
        self.assertFalse(self.run_through(GUEST))

    def test_admin_passes(self):
        self.assertTrue(self.run_through(ADMIN))

    def test_granted_user_passes(self):
        self.store.grant(GUEST, name="Гость")
        self.assertTrue(self.run_through(GUEST))

    def test_revoked_user_is_stopped_again(self):
        self.store.grant(GUEST)
        self.assertTrue(self.run_through(GUEST))

        self.store.revoke(GUEST)
        self.assertFalse(self.run_through(GUEST))

    def test_id_command_works_for_anyone(self):
        # Иначе человеку неоткуда узнать свой номер, чтобы попросить доступ
        self.assertTrue(self.run_through(GUEST, text="/id"))

    def test_admin_is_notified_about_the_stranger(self):
        import types

        sent = []

        class FakeBot:
            async def send_message(self, chat_id, text, **kw):
                sent.append((chat_id, text))

        self.run_through(GUEST, bot=FakeBot())
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], ADMIN)
        self.assertIn(str(GUEST), sent[0][1])

    def test_admin_is_not_spammed_by_repeat_knocks(self):
        sent = []

        class FakeBot:
            async def send_message(self, chat_id, text, **kw):
                sent.append(chat_id)

        bot = FakeBot()
        self.run_through(GUEST, bot=bot)
        self.run_through(GUEST, bot=bot)
        self.run_through(GUEST, bot=bot)
        self.assertEqual(len(sent), 1)

    def test_broken_admin_chat_does_not_break_the_denial(self):
        class BrokenBot:
            async def send_message(self, *a, **kw):
                raise RuntimeError("админ заблокировал бота")

        # Отказ должен отработать, даже если уведомить некого
        self.assertFalse(self.run_through(GUEST, bot=BrokenBot()))
