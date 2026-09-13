"""Тесты бота: разбор настроек, форматирование, сборка диспетчера.

Сеть и токен не нужны — Telegram здесь не дёргается.
"""

from __future__ import annotations

import csv
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Dispatcher  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

from bot import formatting, keyboards, search  # noqa: E402
from bot.config import Config, _parse_ids  # noqa: E402
from bot.handlers import (  # noqa: E402
    AccessMiddleware, MESSAGE_LIMIT, _fit, _picked_header, _source_prompt, router,
)
from leadfinder import scoring, sources  # noqa: E402
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
        text = formatting.page_message(self.leads, "Казань", ["медицина"])
        self.assertIn("Казань", text)
        self.assertIn(f"{len(self.leads)}", text)

    def test_empty_result_explains_what_to_do(self):
        text = formatting.page_message([], "Казань", ["медицина"])
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

    def test_every_button_has_a_handler(self):
        """Кнопка без обработчика молча ничего не делает — поймать это можно
        только так: прогнать все возможные callback_data через фильтры."""
        import asyncio
        import types

        payloads = set()
        for markup in (
            keyboards.niches(), keyboards.niches(city_known=False),
            keyboards.rating_sources(), keyboards.rating_sources(city_known=False),
            keyboards.back_to_sources(),
            keyboards.calc_targets(), keyboards.cancel(),
            keyboards.pager(0, 11), keyboards.pager(5, 11), keyboards.pager(10, 11),
            keyboards.pager(0, 1),
            keyboards.cities(["Казань", "Пермь"]),
            keyboards.admin_panel([(111, {"name": "Гость"})]),
            keyboards.confirm_revoke(111, "Гость"),
            keyboards.grant_request(111),
            keyboards.request_access(),
        ):
            for row in markup.inline_keyboard:
                for button in row:
                    payloads.add(button.callback_data)

        async def handler_for(payload):
            fake = types.SimpleNamespace(data=payload)
            for handler in router.callback_query.handlers:
                for flt in handler.filters or []:
                    try:
                        result = flt.callback(fake)
                        if asyncio.iscoroutine(result):
                            result = await result
                        if result:
                            return handler.callback.__name__
                    except Exception:
                        continue
            return None

        async def check():
            return {p: await handler_for(p) for p in sorted(payloads)}

        found = asyncio.run(check())
        orphans = [p for p, name in found.items() if name is None]
        self.assertEqual(orphans, [], f"кнопки без обработчика: {orphans}")


class SourceChoiceTest(unittest.TestCase):
    """Выбор площадки перед поиском.

    Смысл шага в том, что рейтинг у площадок свой: звонок «у вас 3.1»
    рассыпается, если владелец смотрит на карту, где у него 4.4.
    """

    def test_keyboard_offers_every_source(self):
        markup = keyboards.rating_sources()
        payloads = {b.callback_data for row in markup.inline_keyboard for b in row}
        for key in sources.SOURCES:
            self.assertIn(f"src:{key}", payloads)

    def test_unavailable_source_is_shown_not_hidden(self):
        # Убрать Яндекс из списка — значит получать вопрос «а где он» каждую
        # неделю. Кнопка есть, и по ней приходит объяснение.
        markup = keyboards.rating_sources()
        payloads = {b.callback_data for row in markup.inline_keyboard for b in row}
        self.assertIn("src:yandex", payloads)
        self.assertFalse(sources.YANDEX.available)
        self.assertTrue(sources.YANDEX.why_not)

    def test_only_two_gis_can_actually_be_searched(self):
        # У Яндекса в API нет полей рейтинга — фильтровать нечем
        self.assertEqual([s.key for s in sources.searchable()], ["2gis", "2gis_org"])

    def test_prompt_names_every_source(self):
        text = _source_prompt("Уфа")
        self.assertIn("Уфа", text)
        for source in sources.SOURCES.values():
            self.assertIn(source.caption, text)

    def test_header_after_choice_names_the_platform(self):
        header = _picked_header("Уфа", "2gis_org")
        self.assertIn("Уфа", header)
        self.assertIn("2ГИС, организация", header)

    def test_results_message_names_the_platform(self):
        leads = demo_leads()
        text = formatting.page_message(leads, "Казань", ["еда"], source="2gis_org")
        self.assertIn("2ГИС, организация", text)

    def test_empty_result_still_names_the_platform(self):
        text = formatting.page_message([], "Казань", ["еда"], source="2gis_org")
        self.assertIn("2ГИС, организация", text)

    def test_source_survives_into_the_message(self):
        leads = demo_leads()
        result = search.SearchResult("Казань", ["еда"], len(leads), leads, source="2gis_org")
        self.assertIn("2ГИС, организация", _fit(result))

    def test_default_source_when_nothing_chosen(self):
        leads = demo_leads()
        result = search.SearchResult("Казань", ["еда"], len(leads), leads)
        self.assertEqual(result.source, sources.DEFAULT)


class PagerTest(unittest.TestCase):
    """Выдача листается кнопками прямо в чате — файл только по запросу."""

    def setUp(self):
        # 23 лида: не кратно размеру страницы, последняя неполная
        self.leads = (demo_leads() * 5)[:23]
        self.pages = formatting.total_pages(len(self.leads))

    def numbers_on(self, page):
        text = formatting.page_message(self.leads, "Уфа", ["еда"], page=page)
        return [int(m) for m in re.findall(r"^(\d+)\. ", text, re.M)]

    def test_pages_cover_every_lead_exactly_once(self):
        seen = [n for page in range(self.pages) for n in self.numbers_on(page)]
        self.assertEqual(seen, list(range(1, len(self.leads) + 1)))

    def test_numbering_is_global_not_per_page(self):
        # Иначе на третьей странице снова «1.», и не понять, кого уже обзвонил
        self.assertEqual(self.numbers_on(1)[0], formatting.PAGE_SIZE + 1)

    def test_last_page_may_be_short(self):
        self.assertEqual(len(self.numbers_on(self.pages - 1)), 23 % formatting.PAGE_SIZE)

    def test_header_repeats_on_every_page(self):
        for page in range(self.pages):
            text = formatting.page_message(self.leads, "Уфа", ["еда"], page=page)
            self.assertIn("Уфа", text)
            self.assertIn("Отобрано", text)

    def test_every_page_fits_telegram(self):
        for page in range(self.pages):
            text = formatting.page_message(self.leads, "Уфа", ["еда"], page=page)
            self.assertLessEqual(len(text), formatting.MESSAGE_LIMIT)

    def test_total_pages_math(self):
        self.assertEqual(formatting.total_pages(0), 1)
        self.assertEqual(formatting.total_pages(1), 1)
        self.assertEqual(formatting.total_pages(formatting.PAGE_SIZE), 1)
        self.assertEqual(formatting.total_pages(formatting.PAGE_SIZE + 1), 2)

    def test_stale_button_cannot_run_off_the_list(self):
        # Кнопка под старой выдачей живёт вечно: «далее» на 11-й странице
        # можно нажать и тогда, когда новый поиск вернул три компании.
        self.assertEqual(formatting.clamp_page(99, len(self.leads)), self.pages - 1)
        self.assertEqual(formatting.clamp_page(-5, len(self.leads)), 0)
        self.assertEqual(formatting.clamp_page(3, 0), 0)

    def test_arrows_disappear_at_the_ends(self):
        first = {b.callback_data for row in keyboards.pager(0, 5).inline_keyboard for b in row}
        last = {b.callback_data for row in keyboards.pager(4, 5).inline_keyboard for b in row}
        self.assertNotIn("page:-1", first)
        self.assertIn("page:1", first)
        self.assertIn("page:3", last)
        self.assertNotIn("page:5", last)

    def test_single_page_has_no_pager_row(self):
        payloads = {b.callback_data for row in keyboards.pager(0, 1).inline_keyboard for b in row}
        self.assertEqual(payloads, {"result:csv", "result:again"})

    def test_file_stays_available_on_every_page(self):
        # Таблица неудобна, но иногда нужна — кнопка никуда не девается
        for page in range(5):
            payloads = {b.callback_data for row in keyboards.pager(page, 5).inline_keyboard for b in row}
            self.assertIn("result:csv", payloads)

    def test_plural_agrees_with_the_number(self):
        cases = {1: "пятёрка", 2: "пятёрки", 5: "пятёрок", 11: "пятёрок",
                 21: "пятёрка", 106: "пятёрок", 281: "пятёрка", 1002: "пятёрки"}
        for number, want in cases.items():
            self.assertEqual(
                formatting.plural(number, "пятёрка", "пятёрки", "пятёрок"), want, number,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
