import unittest
from unittest.mock import patch

from bot import WhitelistStore, format_kkt, normalize_inn
from lookup import KKTInfo, find_all_kkt_by_owner_inn


class BotTests(unittest.TestCase):
    def test_normalize_inn(self) -> None:
        self.assertEqual(normalize_inn("2537 0108 4668"), "253701084668")
        with self.assertRaises(ValueError):
            normalize_inn("123")

    def test_whitelist_from_environment(self) -> None:
        store = WhitelistStore(frozenset({1, 2}))
        self.assertTrue(store.is_allowed(2))
        self.assertFalse(store.is_allowed(3))

    def test_format_kkt_uses_html_and_copyable_values(self) -> None:
        item = KKTInfo(
            owner_inn="1234567890",
            owner_name="Иванов <И.И.>",
            model="Модель",
            reg_number="0001",
            manufacturer_number="9999",
            fn_end_date="2020-01-01",
            ofd_end_date=None,
        )
        text = format_kkt(item, 1)
        self.assertIn("<b>Касса №1</b>", text)
        self.assertIn("<code>1234567890</code>", text)
        self.assertIn("Иванов &lt;И.И.&gt;", text)

    @patch("lookup.collect_kkt_by_inn")
    def test_old_kkt_is_not_filtered(self, collector) -> None:
        collector.return_value = {
            "accounts": [{}],
            "kkt": [
                {
                    "kkt_inn": "1234567890",
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


if __name__ == "__main__":
    unittest.main()
