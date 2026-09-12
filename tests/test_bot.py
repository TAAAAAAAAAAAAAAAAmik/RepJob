"""Тесты бота: разбор настроек, форматирование, сборка диспетчера.

Сеть и токен не нужны — Telegram здесь не дёргается.
"""

from __future__ import annotations

import csv
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot import formatting, keyboards, search  # noqa: E402
from bot.config import Config, _parse_ids  # noqa: E402
from bot.handlers import AccessMiddleware, MESSAGE_LIMIT, _fit, router  # noqa: E402
from leadfinder import scoring  # noqa: E402
from leadfinder.cli import load_demo  # noqa: E402
from leadfinder.models import is_target, reviews_needed_for  # noqa: E402


def demo_leads():
    return scoring.rank([c for c in load_demo() if is_target(c)])


class ConfigTest(unittest.TestCase):
    def test_parses_mixed_separators(self):
        self.assertEqual(_parse_ids("111, 222;333"), {111, 222, 333})

    def test_ignores_garbage(self):
        self.assertEqual(_parse_ids("111, привет, , 222"), {111, 222})

    def test_empty_means_nobody(self):
        self.assertEqual(_parse_ids(""), set())

    def test_access_closed_until_configured(self):
        cfg = Config(bot_token="t", dgis_api_key="k")
        self.assertFalse(cfg.access_configured)
        self.assertFalse(cfg.is_allowed(123))

    def test_allows_listed_user_only(self):
        cfg = Config(bot_token="t", dgis_api_key="k", allowed_ids={42})
        self.assertTrue(cfg.is_allowed(42))
        self.assertFalse(cfg.is_allowed(43))


class FormattingTest(unittest.TestCase):
    def setUp(self):
        self.leads = demo_leads()

    def test_lead_line_has_the_essentials(self):
        line = formatting.lead_line(self.leads[0], 1)
        self.assertIn("Фитнес-клуб «Атлант»", line)
        self.assertIn("3.6", line)
        self.assertIn("156 отз.", line)
        self.assertIn("tel:", line)

    def test_missing_phone_is_stated_not_skipped(self):
        no_phone = next(c for c in self.leads if not c.phone)
        self.assertIn("телефона нет", formatting.lead_line(no_phone, 1))

    def test_escapes_html_in_names(self):
        lead = self.leads[0]
        lead.name = 'Салон <b>"Взлом"</b> & Ко'
        line = formatting.lead_line(lead, 1)
        self.assertIn("&lt;b&gt;", line)
        self.assertNotIn('<b>"Взлом"', line)

    def test_results_message_reports_counts(self):
        text = formatting.results_message(self.leads, "Казань", ["медицина"])
        self.assertIn("Казань", text)
        self.assertIn(f"{len(self.leads)}", text)

    def test_empty_result_explains_what_to_do(self):
        text = formatting.results_message([], "Казань", ["медицина"])
        self.assertIn("не попал никто", text)

    def test_calc_message_matches_the_formula(self):
        needed = reviews_needed_for(3.4, 48, 4.5)
        text = formatting.calc_message(3.4, 48, 4.5, needed)
        self.assertIn("106", text)
        self.assertIn("мес", text)

    def test_calc_message_handles_unreachable_target(self):
        text = formatting.calc_message(3.0, 50, 5.0, None)
        self.assertIn("недостижима", text)


class MessageLimitTest(unittest.TestCase):
    def test_long_result_is_trimmed_to_fit(self):
        leads = demo_leads() * 40  # заведомо длиннее лимита Telegram
        result = search.SearchResult("Казань", ["медицина"], len(leads), leads)
        self.assertLessEqual(len(_fit(result)), MESSAGE_LIMIT)

    def test_short_result_keeps_full_top(self):
        leads = demo_leads()
        result = search.SearchResult("Казань", ["медицина"], len(leads), leads)
        text = _fit(result)
        self.assertLessEqual(len(text), MESSAGE_LIMIT)
        self.assertIn(leads[0].name, text)


class CsvTest(unittest.TestCase):
    def test_bytes_carry_bom_and_header(self):
        payload = search.to_csv_bytes(demo_leads())
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))

        rows = list(csv.reader(payload.decode("utf-8-sig").splitlines(), delimiter=";"))
        self.assertEqual(rows[0][0], "Приоритет")
        self.assertEqual(len(rows), len(demo_leads()) + 1)


class WiringTest(unittest.TestCase):
    """Ловит опечатки в фильтрах и middleware до запуска в проде."""

    def test_dispatcher_accepts_router_and_middleware(self):
        cfg = Config(bot_token="t", dgis_api_key="k", allowed_ids={1})
        dispatcher = Dispatcher(storage=MemoryStorage())
        middleware = AccessMiddleware(cfg)
        dispatcher.message.outer_middleware(middleware)
        dispatcher.callback_query.outer_middleware(middleware)
        dispatcher.include_router(router)

        self.assertTrue(router.message.handlers)
        self.assertTrue(router.callback_query.handlers)

    def test_niche_keyboard_covers_every_preset(self):
        from leadfinder.presets import PRESETS

        markup = keyboards.niches()
        payloads = {b.callback_data for row in markup.inline_keyboard for b in row}
        for key in PRESETS:
            self.assertIn(f"niche:{key}", payloads)
        self.assertIn("niche:__all__", payloads)


if __name__ == "__main__":
    unittest.main(verbosity=2)
