"""Тесты логики отбора и скоринга. Сеть и ключи не нужны."""

from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from leadfinder import dgis, export, presets, scoring, sources, yandex  # noqa: E402
from leadfinder.cli import load_demo  # noqa: E402
from leadfinder.models import Company, is_target, reviews_needed_for  # noqa: E402


def make(rating=3.5, count=50, phone="+7 900 000-00-00", name="Тест") -> Company:
    return Company(
        source="2gis", source_id="1", name=name,
        rating=rating, review_count=count, phone=phone,
    )


class ReviewsNeededTest(unittest.TestCase):
    def test_matches_guide_example(self):
        # 48 отзывов при 3.4, цель 4.5 → 106 пятёрок (пример из руководства)
        self.assertEqual(reviews_needed_for(3.4, 48, 4.5), 106)

    def test_already_at_target(self):
        self.assertEqual(reviews_needed_for(4.6, 100, 4.5), 0)

    def test_unreachable_target(self):
        self.assertIsNone(reviews_needed_for(3.0, 50, 5.0))

    def test_no_reviews(self):
        self.assertIsNone(reviews_needed_for(3.0, 0))


class TargetFilterTest(unittest.TestCase):
    def test_inside_range(self):
        self.assertTrue(is_target(make(rating=3.5, count=50)))

    def test_too_good_to_hurt(self):
        self.assertFalse(is_target(make(rating=4.7, count=200)))

    def test_too_bad_to_save(self):
        self.assertFalse(is_target(make(rating=2.4, count=200)))

    def test_too_few_reviews_is_noise(self):
        self.assertFalse(is_target(make(rating=3.5, count=4)))

    def test_boundaries_are_inclusive(self):
        self.assertTrue(is_target(make(rating=3.0, count=50)))
        self.assertTrue(is_target(make(rating=4.2, count=50)))

    def test_missing_rating_never_passes(self):
        self.assertFalse(is_target(make(rating=None, count=50)))

    def test_require_phone(self):
        self.assertFalse(is_target(make(phone=""), require_phone=True))
        self.assertTrue(is_target(make(phone=""), require_phone=False))


class ScoringTest(unittest.TestCase):
    def test_traffic_beats_a_slightly_worse_rating(self):
        busy = scoring.score_company(make(rating=3.9, count=250))
        quiet = scoring.score_company(make(rating=3.6, count=14))
        self.assertGreater(busy.score, quiet.score)

    def test_missing_phone_costs_priority(self):
        with_phone = scoring.score_company(make(phone="+7 900 000-00-00"))
        without = scoring.score_company(make(phone=""))
        self.assertGreater(with_phone.score, without.score)

    def test_no_phone_penalty_when_nobody_has_one(self):
        # Демо-ключ 2GIS контакты не отдаёт: одинаковый штраф всем просто
        # перекрашивает горячие лиды в холодные, ничего не ранжируя.
        nobody = scoring.rank([make(phone=""), make(rating=4.0, phone="")])
        somebody = scoring.rank([make(phone=""), make(phone="+7 900 000-00-00")])

        self.assertEqual(nobody[0].score, scoring.score_company(make(phone="+7 900 1")).score)
        self.assertLess(somebody[1].score, somebody[0].score)

    def test_score_stays_in_range(self):
        for rating in (3.0, 3.5, 4.0, 4.2):
            for count in (10, 100, 5000):
                company = scoring.score_company(make(rating=rating, count=count))
                self.assertGreaterEqual(company.score, 0)
                self.assertLessEqual(company.score, 100)

    def test_no_rating_gets_zero(self):
        company = scoring.score_company(make(rating=None))
        self.assertEqual(company.score, 0)
        self.assertEqual(company.verdict, "нет данных")

    def test_rank_sorts_descending(self):
        ranked = scoring.rank([
            make(rating=4.2, count=12, name="слабый"),
            make(rating=3.1, count=200, name="сильный"),
        ])
        self.assertEqual(ranked[0].name, "сильный")


class ParsingTest(unittest.TestCase):
    def test_extracts_rating_phone_and_website(self):
        raw = {
            "id": "700_1",
            "name": "Клиника",
            "address_name": "ул. Тестовая, 1",
            "point": {"lat": 55.0, "lon": 49.0},
            "reviews": {"general_rating": 3.7, "general_review_count": 88},
            "rubrics": [{"name": "Медцентр"}],
            "contact_groups": [{"contacts": [
                {"type": "phone", "value": "+7 900 123-45-67"},
                {"type": "website", "url": "https://example.test"},
            ]}],
        }
        company = dgis.parse_company(raw, city="Казань")
        self.assertEqual(company.rating, 3.7)
        self.assertEqual(company.review_count, 88)
        self.assertEqual(company.phone, "+7 900 123-45-67")
        self.assertEqual(company.website, "https://example.test")
        self.assertEqual(company.rubric, "Медцентр")
        self.assertEqual(company.url_2gis, "https://2gis.ru/firm/700")

    def test_survives_empty_organisation(self):
        company = dgis.parse_company({"id": "1", "name": "Пусто"})
        self.assertIsNone(company.rating)
        self.assertEqual(company.review_count, 0)
        self.assertEqual(company.phone, "")

    def test_falls_back_to_org_rating(self):
        raw = {"id": "2", "name": "Ф", "reviews": {"org_rating": 4.1, "org_review_count": 30}}
        company = dgis.parse_company(raw)
        self.assertEqual(company.rating, 4.1)
        self.assertEqual(company.review_count, 30)


class PresetsTest(unittest.TestCase):
    def test_expands_known_preset(self):
        self.assertIn("барбершоп", presets.resolve(["красота"]))

    def test_passes_through_free_text(self):
        self.assertEqual(presets.resolve(["ремонт обуви"]), ["ремонт обуви"])

    def test_deduplicates_across_presets(self):
        queries = presets.resolve(["красота", "красота", "барбершоп"])
        self.assertEqual(len(queries), len(set(q.casefold() for q in queries)))


class ApiLimitsTest(unittest.TestCase):
    """Оба предела 2GIS жёсткие: превышение — ошибка 400 и падение поиска
    целиком, а не просто укороченная выдача."""

    def test_page_size_within_limit(self):
        self.assertLessEqual(dgis.PAGE_SIZE, 10)

    def test_default_pages_within_limit(self):
        self.assertLessEqual(dgis.MAX_PAGES, dgis.PAGE_LIMIT)

    def test_search_clamps_excessive_pages(self):
        pages = []

        class Fake(dgis.DgisClient):
            def __init__(self):
                pass
            def _get(self, url, params):
                pages.append(params["page"])
                return {"result": {"items": [{"id": str(params["page"])}], "total": 10_000}}

        client = Fake()
        client.pause = 0
        list(client.search("тест", "1", max_pages=99))
        self.assertEqual(pages, list(range(1, dgis.PAGE_LIMIT + 1)))

    def test_search_floors_at_one_page(self):
        pages = []

        class Fake(dgis.DgisClient):
            def __init__(self):
                pass
            def _get(self, url, params):
                pages.append(params["page"])
                return {"result": {"items": [], "total": 0}}

        client = Fake()
        client.pause = 0
        list(client.search("тест", "1", max_pages=0))
        self.assertEqual(pages, [1])


class DemoPipelineTest(unittest.TestCase):
    """Сквозной прогон на встроенном примере — без сети."""

    def setUp(self):
        self.companies = load_demo()

    def test_fixture_loads(self):
        self.assertEqual(len(self.companies), 10)

    def test_filter_keeps_only_workable_leads(self):
        selected = [c for c in self.companies if is_target(c)]
        names = {c.name for c in selected}

        self.assertIn("Стоматология «Дента-Плюс»", names)      # 3.4 / 48
        self.assertIn("Автосервис «Мотор»", names)             # 3.8 / 214
        self.assertNotIn("Салон красоты «Аура»", names)        # 4.8 — не болит
        self.assertNotIn("Кофейня «Термос»", names)            # 2.6 — не вытянуть
        self.assertNotIn("Детский центр «Радуга»", names)      # 6 отзывов — шум
        self.assertNotIn("Ветклиника «Друг»", names)           # рейтинг без счётчика

    def test_pain_plus_traffic_ranks_first(self):
        selected = scoring.rank([c for c in self.companies if is_target(c)])

        # Фитнес 3.6/156 обгоняет автосервис 3.8/214: трафик почти тот же,
        # а просадка заметно больше — значит, у владельца болит сильнее.
        self.assertEqual(selected[0].name, "Фитнес-клуб «Атлант»")

        # Шиномонтаж 4.15/11 замыкает список: и не болит, и собирать не с кого.
        self.assertEqual(selected[-1].name, "Шиномонтаж на Кольцевой")

    def test_csv_roundtrip(self):
        selected = scoring.rank([c for c in self.companies if is_target(c)])
        with tempfile.TemporaryDirectory() as tmp:
            path = export.to_csv(selected, Path(tmp) / "leads.csv")
            raw = path.read_text(encoding="utf-8-sig")
            rows = list(csv.reader(raw.splitlines(), delimiter=";"))

        self.assertEqual(rows[0][0], "Приоритет")
        self.assertEqual(len(rows), len(selected) + 1)
        self.assertEqual(rows[1][2], selected[0].name)

    def test_csv_is_written_with_bom_for_excel(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = export.to_csv(self.companies[:2], Path(tmp) / "leads.csv")
            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_summary_handles_empty(self):
        self.assertIn("не попал никто", export.summary([]))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class YandexMatchTest(unittest.TestCase):
    """Чужой телефон хуже отсутствующего: по нему позвонят и попадут не туда."""

    def test_same_name_matches(self):
        self.assertTrue(yandex.looks_like_same("Анюта, салон красоты", "Салон красоты Анюта"))

    def test_punctuation_and_case_ignored(self):
        self.assertTrue(yandex.looks_like_same("Shagane, парикмахерская", "«SHAGANE» — парикмахерская"))

    def test_different_business_rejected(self):
        self.assertFalse(yandex.looks_like_same("Анюта, салон", "Пятёрочка"))

    def test_empty_never_matches(self):
        self.assertFalse(yandex.looks_like_same("", "Анюта"))
        self.assertFalse(yandex.looks_like_same("Анюта", ""))

    def test_short_words_do_not_create_false_match(self):
        # «и», «на» и прочая мелочь совпала бы у чего угодно
        self.assertFalse(yandex.looks_like_same("Кафе на углу", "Бар на пирсе"))

    def test_enrich_without_client_sets_search_link(self):
        company = make(name="Анюта")
        company.city = "Уфа"
        yandex.enrich([company], None)
        self.assertIn("yandex.ru/maps", company.url_yandex)
        self.assertEqual(company.phone_source, "")

    def test_enrich_fills_phone_from_yandex(self):
        class FakeClient:
            pause = 0
            def find(self, company):
                return yandex.YandexOrg(org_id="42", name=company.name,
                                        phone="+7 347 000-00-00", url="https://yandex.ru/maps/org/42/")

        company = make(name="Анюта", phone="")
        yandex.enrich([company], FakeClient())
        self.assertEqual(company.phone, "+7 347 000-00-00")
        self.assertEqual(company.phone_source, "yandex")
        self.assertEqual(company.yandex_org_id, "42")

    def test_existing_phone_is_not_overwritten(self):
        class FakeClient:
            pause = 0
            def find(self, company):
                return yandex.YandexOrg(org_id="42", phone="+7 347 111-11-11")

        company = make(name="Анюта", phone="+7 900 000-00-00")
        company.phone_source = "2gis"
        yandex.enrich([company], FakeClient())
        self.assertEqual(company.phone, "+7 900 000-00-00")
        self.assertEqual(company.phone_source, "2gis")

    def test_not_found_falls_back_to_search_link(self):
        class FakeClient:
            pause = 0
            def find(self, company):
                return None

        company = make(name="Анюта")
        yandex.enrich([company], FakeClient())
        self.assertIn("yandex.ru/maps/?text=", company.url_yandex)
        self.assertEqual(company.phone_source, "")


class MapLinkTest(unittest.TestCase):
    """Ссылка должна открывать конкретный филиал, а не результаты поиска."""

    def test_coords_open_exact_point(self):
        company = make(name="22, салон красоты")
        company.lat, company.lon = 55.823618, 49.104565
        url = yandex.maps_search_url(company)

        # ll обязателен — без него Яндекс покажет список тёзок по всему городу.
        self.assertIn("ll=49.104565%2C55.823618", url)   # долгота первой
        self.assertIn("z=18", url)

    def test_address_used_when_no_coords(self):
        company = make(name="Уют, салон")
        company.address = "ул. Мира, 5"
        url = yandex.maps_search_url(company)

        self.assertIn("yandex.ru/maps/?text=", url)
        self.assertNotIn("ll=", url)
        # Адрес в запросе — единственное, что отличает «Уют» от десятка тёзок.
        self.assertIn("%D0%9C%D0%B8%D1%80%D0%B0", url)   # «Мира» в urlencode

    def test_city_used_when_address_missing(self):
        company = make(name="Уют, салон")
        company.city = "Уфа"
        url = yandex.maps_search_url(company)
        self.assertIn("yandex.ru/maps/?text=", url)
        self.assertIn("%D0%A3%D1%84%D0%B0", url)         # «Уфа»

    def test_2gis_link_points_at_branch_card(self):
        # id филиала до подчёркивания — именно он открывает карточку точки
        company = dgis.parse_company({
            "id": "70000001006353769_bba2be4d",
            "name": "Анюта",
            "reviews": {"general_rating": 3.6, "general_review_count": 40},
        })
        self.assertEqual(company.url_2gis, "https://2gis.ru/firm/70000001006353769")

    def test_enrich_without_client_uses_coords(self):
        company = make(name="Анюта")
        company.lat, company.lon = 54.7388, 55.9721
        yandex.enrich([company], None)
        self.assertIn("ll=55.9721%2C54.7388", company.url_yandex)

    def test_coords_land_in_csv(self):
        company = make()
        company.lat, company.lon = 54.738762, 55.972055
        self.assertEqual(company.coords, "54.738762, 55.972055")
        self.assertEqual(make().coords, "")

        with tempfile.TemporaryDirectory() as tmp:
            path = export.to_csv([company], Path(tmp) / "leads.csv")
            with path.open(encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle, delimiter=";"))
        self.assertEqual(row["Координаты"], "54.738762, 55.972055")


class RatingSourceTest(unittest.TestCase):
    """Выбор площадки: по какому рейтингу идёт отбор.

    Цифры у площадок и охватов разные, и это не мелочь: сетевая точка
    с 3.1 на карточке может иметь 3.8 по организации — попадёт она
    в выдачу или нет, зависит от того, что выбрали.
    """

    RAW = {
        "id": "70000001_x",
        "name": "Шаверма по-питерски",
        "reviews": {
            "general_rating": 3.1, "general_review_count": 318,
            "org_rating": 4.4, "org_review_count": 4200,
        },
    }

    def test_branch_is_the_default(self):
        company = dgis.parse_company(self.RAW)
        self.assertEqual(company.rating, 3.1)
        self.assertEqual(company.review_count, 318)
        self.assertEqual(company.rating_source, sources.DEFAULT)

    def test_org_scope_changes_the_working_number(self):
        company = dgis.parse_company(self.RAW, rating_source="2gis_org")
        self.assertEqual(company.rating, 4.4)
        self.assertEqual(company.review_count, 4200)

    def test_both_scopes_stay_available_either_way(self):
        # Расхождение между охватами — готовый аргумент на встрече,
        # поэтому вторая цифра не теряется при любом выборе.
        for key in ("2gis", "2gis_org"):
            company = dgis.parse_company(self.RAW, rating_source=key)
            self.assertEqual((company.rating_branch, company.review_count_branch), (3.1, 318))
            self.assertEqual((company.rating_org, company.review_count_org), (4.4, 4200))

    def test_choice_decides_whether_the_lead_passes(self):
        # 3.1 — в рабочем диапазоне, 4.4 — уже нет
        self.assertTrue(is_target(dgis.parse_company(self.RAW)))
        self.assertFalse(is_target(dgis.parse_company(self.RAW, rating_source="2gis_org")))

    def test_missing_scope_falls_back_instead_of_losing_the_lead(self):
        raw = {"id": "1", "name": "Одиночка",
               "reviews": {"general_rating": 3.4, "general_review_count": 48}}
        company = dgis.parse_company(raw, rating_source="2gis_org")
        self.assertEqual(company.rating, 3.4)   # org-данных нет — взяли филиал

    def test_unknown_key_does_not_break_the_search(self):
        # Ключ приезжает из callback_data: кнопка из старой версии бота
        # не должна ронять поиск.
        self.assertEqual(sources.get("телепортация").key, sources.DEFAULT)
        company = dgis.parse_company(self.RAW, rating_source="ерунда")
        self.assertEqual(company.rating, 3.1)

    def test_csv_says_where_the_number_came_from(self):
        company = dgis.parse_company(self.RAW, city="Уфа", rating_source="2gis_org")
        with tempfile.TemporaryDirectory() as tmp:
            path = export.to_csv([company], Path(tmp) / "leads.csv")
            with path.open(encoding="utf-8-sig") as handle:
                row = next(csv.DictReader(handle, delimiter=";"))

        self.assertEqual(row["Где смотрели"], "2ГИС, организация")
        self.assertEqual(row["Рейтинг"], "4.4")
        self.assertEqual(row["Рейтинг филиала"], "3.1")
        self.assertEqual(row["Рейтинг организации"], "4.4")
