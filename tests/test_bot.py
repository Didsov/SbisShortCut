import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bot import (
    WhitelistStore,
    format_account_statistics,
    format_kkt,
    group_kkt_by_account,
    normalize_inn,
)
from datetime import date

from lookup import (
    KKTInfo,
    display_sort_key,
    find_all_kkt_by_owner_inn,
    fn_replacement_status,
    replacement_sort_key,
)
from services.live_collector import collect_kkt_by_inn


class BotTests(unittest.TestCase):
    def test_normalize_inn(self) -> None:
        self.assertEqual(normalize_inn("2537 0108 4668"), "253701084668")
        with self.assertRaises(ValueError):
            normalize_inn("123")

    def test_whitelist_from_environment(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "whitelist.json"
            store = WhitelistStore(
                path,
                admin_user_ids=frozenset({1}),
                configured_user_ids=frozenset({2}),
            )
            self.assertTrue(store.is_allowed(1))
            self.assertTrue(store.is_allowed(2))
            self.assertFalse(store.is_allowed(3))
            self.assertTrue(store.add(3))
            self.assertTrue(WhitelistStore(
                path,
                admin_user_ids=frozenset({1}),
            ).is_allowed(3))

    def test_admin_cannot_be_removed_from_whitelist(self) -> None:
        with TemporaryDirectory() as directory:
            store = WhitelistStore(
                Path(directory) / "whitelist.json",
                admin_user_ids=frozenset({1}),
            )
            with self.assertRaises(PermissionError):
                store.remove(1)

    def test_format_kkt_uses_html_and_copyable_values(self) -> None:
        item = KKTInfo(
            owner_inn="1234567890",
            owner_name="Иванов <И.И.>",
            model="Модель",
            reg_number="0001",
            manufacturer_number="9999",
            fn_end_date="2020-01-01",
            ofd_end_date=None,
            account_id=42,
            account_name="Основной",
        )
        text = format_kkt(item, 1)
        self.assertIn("<b>Касса №1</b>", text)
        self.assertIn("<code>1234567890</code>", text)
        self.assertIn("Иванов &lt;И.И.&gt;", text)
        self.assertIn("<code>42 — Основной</code>", text)

    def test_kkt_are_grouped_by_account(self) -> None:
        def item(account_id: int, reg_number: str) -> KKTInfo:
            return KKTInfo(
                owner_inn="1234567890",
                owner_name=None,
                model=None,
                reg_number=reg_number,
                manufacturer_number=None,
                fn_end_date="2027-01-01",
                ofd_end_date=None,
                account_id=account_id,
                account_name=f"Аккаунт {account_id}",
            )

        groups = group_kkt_by_account((item(10, "A"), item(20, "B"), item(10, "C")))
        self.assertEqual([label for label, _items in groups], ["10 — Аккаунт 10", "20 — Аккаунт 20"])
        self.assertEqual([[value.reg_number for value in values] for _label, values in groups], [["A", "C"], ["B"]])

    def test_format_account_statistics(self) -> None:
        text = format_account_statistics(
            {
                "account_id": 10,
                "account_name": "Точка <1>",
                "registry_kkt_count": 3,
                "loaded_kkt_count": 2,
                "error_count": 1,
            },
            1,
        )
        self.assertIn("<b>Аккаунт №1</b>", text)
        self.assertIn("Точка &lt;1&gt;", text)
        self.assertIn("<code>3</code>", text)

    def test_replacement_sort_uses_nearest_fn_date(self) -> None:
        def item(fn_end_date: str, reg_number: str) -> KKTInfo:
            return KKTInfo(
                owner_inn="1234567890",
                owner_name=None,
                model=None,
                reg_number=reg_number,
                manufacturer_number=None,
                fn_end_date=fn_end_date,
                ofd_end_date=None,
            )

        items = [
            item("2018-01-01", "old"),
            item("2026-08-12", "near"),
            item("2026-09-01", "later"),
        ]
        ordered = sorted(
            items,
            key=lambda value: replacement_sort_key(
                value,
                today=date(2026, 8, 11),
            ),
        )
        self.assertEqual([value.reg_number for value in ordered], ["near", "later", "old"])

    def test_display_sort_and_replacement_colors(self) -> None:
        def item(fn_end_date: str, reg_number: str) -> KKTInfo:
            return KKTInfo(
                owner_inn="1234567890",
                owner_name=None,
                model=None,
                reg_number=reg_number,
                manufacturer_number=None,
                fn_end_date=fn_end_date,
                ofd_end_date=None,
            )

        values = [
            item("2026-01-01", "expired"),
            item("2026-08-20", "red"),
            item("2026-11-01", "yellow"),
            item("2027-02-01", "green"),
        ]
        ordered = sorted(values, key=display_sort_key, reverse=True)
        self.assertEqual(
            [value.reg_number for value in ordered],
            ["green", "yellow", "red", "expired"],
        )
        report_date = date(2026, 8, 11)
        self.assertEqual(fn_replacement_status(values[0], today=report_date)[0], "⚫")
        self.assertEqual(fn_replacement_status(values[1], today=report_date)[0], "🔴")
        self.assertEqual(fn_replacement_status(values[2], today=report_date)[0], "🟡")
        self.assertEqual(fn_replacement_status(values[3], today=report_date)[0], "🟢")

    @patch("lookup.collect_kkt_by_inn")
    def test_old_kkt_is_not_filtered(self, collector) -> None:
        collector.return_value = {
            "accounts": [{}],
            "kkt": [
                {
                    "kkt_inn": "1234567890",
                    "account_id": 10,
                    "account_name": "Основной аккаунт",
                    "sales_point_address": "Адрес магазина",
                    "registry": {"KKTRegId": "0001", "INN": "1234567890"},
                    "kkt": {"ИНН": "1234567890", "FSEndDate": "2018-01-01"},
                }
            ],
            "errors": [],
        }
        result = find_all_kkt_by_owner_inn("1234567890")
        self.assertEqual(len(result.cash_registers), 1)
        self.assertEqual(result.cash_registers[0].fn_end_date, "2018-01-01")
        self.assertEqual(result.cash_registers[0].sales_point_address, "Адрес магазина")
        self.assertEqual(result.cash_registers[0].account_id, 10)
        self.assertEqual(result.cash_registers[0].account_name, "Основной аккаунт")

    @patch("lookup.collect_kkt_by_inn")
    def test_kkt_without_fn_date_is_filtered(self, collector) -> None:
        collector.return_value = {
            "accounts": [{}],
            "kkt": [
                {
                    "kkt_inn": "1234567890",
                    "registry": {"KKTRegId": "0001", "INN": "1234567890"},
                    "kkt": {"ИНН": "1234567890"},
                }
            ],
            "errors": [],
        }
        result = find_all_kkt_by_owner_inn("1234567890")
        self.assertEqual(result.cash_registers, ())

    @patch("services.live_collector.extract_ofd_end_date", return_value=None)
    @patch("services.live_collector.get_kkt")
    @patch("services.live_collector.get_all_kkt_registry")
    @patch("services.live_collector.get_contractor_accounts")
    @patch("services.live_collector.get_contractor_by_inn")
    @patch("services.live_collector.SBISClient")
    def test_collector_processes_every_account(
        self,
        client_class,
        contractor,
        accounts,
        registry,
        get_kkt,
        _extract_ofd,
    ) -> None:
        client_class.return_value.__enter__.return_value = object()
        contractor.return_value = {"@Лицо": 100}
        accounts.return_value = [
            {"AccountId": 1, "AccountName": "Первая"},
            {"AccountId": 2, "AccountName": "Вторая"},
        ]
        registry.side_effect = [
            [{"KKTId": 11, "KKTRegId": "RNM-1", "INN": "1234567890"}],
            [{"KKTId": 22, "KKTRegId": "RNM-2", "INN": "1234567890"}],
        ]
        get_kkt.side_effect = [
            {"ИНН": "1234567890", "FSEndDate": "2027-01-01"},
            {"ИНН": "1234567890", "FSEndDate": "2027-02-01"},
        ]

        statuses: list[str] = []
        result = collect_kkt_by_inn(
            "1234567890",
            status_callback=statuses.append,
        )

        self.assertEqual(registry.call_count, 2)
        self.assertEqual(len(result["account_statistics"]), 2)
        self.assertEqual(len(result["kkt"]), 2)
        self.assertEqual(
            [item["loaded_kkt_count"] for item in result["account_statistics"]],
            [1, 1],
        )
        self.assertTrue(any("Найдено активных аккаунтов: 2" in text for text in statuses))
        self.assertTrue(any("Аккаунт 1/2" in text for text in statuses))
        self.assertTrue(any("KKT.Read 1/1" in text for text in statuses))

    @patch("services.live_collector.extract_ofd_end_date", return_value=None)
    @patch("services.live_collector.get_kkt")
    @patch("services.live_collector.get_all_kkt_registry")
    @patch("services.live_collector.get_contractor_accounts")
    @patch("services.live_collector.get_contractor_by_inn")
    @patch("services.live_collector.SBISClient")
    def test_same_rnm_is_loaded_from_each_account(
        self,
        client_class,
        contractor,
        accounts,
        registry,
        get_kkt,
        _extract_ofd,
    ) -> None:
        client_class.return_value.__enter__.return_value = object()
        contractor.return_value = {"@Лицо": 100}
        accounts.return_value = [
            {"AccountId": 9908993, "AccountName": "Без срока"},
            {"AccountId": 4755172, "AccountName": "Со сроком"},
        ]
        registry_item = {
            "KKTId": 11,
            "KKTRegId": "0004306972061659",
            "INN": "252100376720",
        }
        registry.side_effect = [[registry_item], [registry_item]]
        get_kkt.side_effect = [
            {"ИНН": "252100376720", "FSEndDate": None},
            {"ИНН": "252100376720", "FSEndDate": "2027-06-28"},
        ]

        result = collect_kkt_by_inn("252100376720")

        self.assertEqual(get_kkt.call_count, 2)
        self.assertEqual(len(result["kkt"]), 2)
        self.assertEqual(
            [item["account_id"] for item in result["kkt"]],
            [9908993, 4755172],
        )

    @patch("lookup.collect_kkt_by_inn")
    def test_complete_duplicate_wins_after_missing_fn_version(self, collector) -> None:
        registry = {"KKTRegId": "0004306972061659", "INN": "252100376720"}
        collector.return_value = {
            "accounts": [{}, {}],
            "kkt": [
                {
                    "account_id": 9908993,
                    "registry": registry,
                    "kkt": {"ИНН": "252100376720", "FSEndDate": None},
                },
                {
                    "account_id": 4755172,
                    "registry": registry,
                    "kkt": {
                        "ИНН": "252100376720",
                        "FSEndDate": "2027-06-28",
                        "Действующая": True,
                    },
                },
            ],
            "errors": [],
            "skip_metrics": {},
        }

        result = find_all_kkt_by_owner_inn("252100376720")

        self.assertEqual(len(result.cash_registers), 1)
        self.assertEqual(result.cash_registers[0].account_id, 4755172)
        self.assertEqual(result.cash_registers[0].fn_end_date, "2027-06-28")
        self.assertEqual(dict(result.skip_metrics)["missing_fn_end_date"], 1)


if __name__ == "__main__":
    unittest.main()
