"""Тесты памяти городов. Сеть и токен не нужны."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot import keyboards as kb  # noqa: E402
from bot.storage import MAX_CITIES, Storage  # noqa: E402


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def store(self):
        return Storage(self.path)

    def test_empty_until_something_is_remembered(self):
        self.assertEqual(self.store().recent_cities(1), [])

    def test_remembers_and_survives_restart(self):
        s = self.store()
        s.remember_city(1, "Казань")
        # Новый экземпляр читает тот же файл — как после перезапуска бота
        self.assertEqual(self.store().recent_cities(1), ["Казань"])

    def test_latest_city_comes_first(self):
        s = self.store()
        for city in ("Казань", "Пермь", "Уфа"):
            s.remember_city(1, city)
        self.assertEqual(s.recent_cities(1), ["Уфа", "Пермь", "Казань"])

    def test_repeat_moves_up_without_duplicating(self):
        s = self.store()
        s.remember_city(1, "Казань")
        s.remember_city(1, "Пермь")
        s.remember_city(1, "казань")          # тот же город другим регистром
        self.assertEqual(s.recent_cities(1), ["казань", "Пермь"])

    def test_list_is_capped(self):
        s = self.store()
        for i in range(MAX_CITIES + 4):
            s.remember_city(1, f"Город{i}")
        self.assertEqual(len(s.recent_cities(1)), MAX_CITIES)

    def test_users_do_not_see_each_other(self):
        s = self.store()
        s.remember_city(1, "Казань")
        s.remember_city(2, "Пермь")
        self.assertEqual(s.recent_cities(1), ["Казань"])
        self.assertEqual(s.recent_cities(2), ["Пермь"])

    def test_blank_city_is_ignored(self):
        s = self.store()
        s.remember_city(1, "   ")
        self.assertEqual(s.recent_cities(1), [])

    def test_broken_file_does_not_crash(self):
        self.path.write_text("{это не json", encoding="utf-8")
        s = self.store()
        self.assertEqual(s.recent_cities(1), [])
        s.remember_city(1, "Казань")
        self.assertEqual(self.store().recent_cities(1), ["Казань"])

    def test_written_file_is_readable_json(self):
        s = self.store()
        s.remember_city(77, "Нижний Новгород")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["users"]["77"]["cities"], ["Нижний Новгород"])

    def test_forget(self):
        s = self.store()
        s.remember_city(1, "Казань")
        s.forget_cities(1)
        self.assertEqual(s.recent_cities(1), [])


class CityKeyboardTest(unittest.TestCase):
    def test_city_buttons_use_index_not_name(self):
        # В callback_data всего 64 байта, а кириллица занимает по два
        markup = kb.cities(["Санкт-Петербург", "Ростов-на-Дону"])
        payloads = [b.callback_data for row in markup.inline_keyboard for b in row]

        self.assertIn("city:0", payloads)
        self.assertIn("city:1", payloads)
        self.assertIn("city:new", payloads)
        for payload in payloads:
            self.assertLessEqual(len(payload.encode()), 64)

    def test_city_names_are_visible_on_buttons(self):
        markup = kb.cities(["Казань"])
        titles = [b.text for row in markup.inline_keyboard for b in row]
        self.assertTrue(any("Казань" in t for t in titles))

    def test_every_callback_fits_the_limit(self):
        markups = [kb.niches(), kb.calc_targets(), kb.pager(3, 11), kb.cancel(),
                   kb.cities(["Казань", "Пермь", "Уфа"])]
        for markup in markups:
            for row in markup.inline_keyboard:
                for button in row:
                    self.assertLessEqual(len(button.callback_data.encode()), 64)

    def test_main_menu_labels_match_handler_filters(self):
        from bot.handlers import MENU_LABELS

        labels = {b.text for row in kb.main_menu(is_admin=True).keyboard for b in row}
        self.assertEqual(labels, set(MENU_LABELS))

    def test_admin_button_hidden_from_ordinary_users(self):
        plain = {b.text for row in kb.main_menu().keyboard for b in row}
        self.assertNotIn(kb.ADMIN, plain)
        self.assertIn(kb.ADMIN, {b.text for row in kb.main_menu(True).keyboard for b in row})

    def test_niche_keyboard_offers_way_back(self):
        payloads = [b.callback_data for row in kb.niches().inline_keyboard for b in row]
        self.assertIn("back:city", payloads)
        self.assertNotIn("back:city", [
            b.callback_data for row in kb.niches(city_known=False).inline_keyboard for b in row
        ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
