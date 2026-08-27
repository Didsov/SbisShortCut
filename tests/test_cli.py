import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from cli import main
from lookup import KKTInfo, KKTLookupResult


class CliTests(unittest.TestCase):
    @patch.dict("os.environ", {"SBIS_COOKIES": "test-cookie"})
    @patch("cli.find_all_kkt_by_owner_inn")
    def test_prints_all_kkt_in_telegram_order(self, lookup) -> None:
        lookup.return_value = KKTLookupResult(
            owner_inn="253900152591",
            accounts_count=1,
            available_kkt_count=2,
            foreign_kkt_count=0,
            expired_kkt_count=0,
            cash_registers=(
                self._item("RNM-1", "5.8.100"),
                self._item("RNM-2", None),
            ),
            errors=(),
        )
        output = StringIO()

        with redirect_stdout(output):
            exit_code = main(["253900152591"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(lookup.call_args.args, ("253900152591",))
        text = output.getvalue()
        self.assertIn("**Касса №1**", text)
        self.assertIn("**Касса №2**", text)
        self.assertIn("**Версия ПО:** `5.8.100`", text)
        self.assertIn("**Версия ПО:** `—`", text)
        self.assertNotIn("<b>", text)
        self.assertLess(text.index("RNM-1"), text.index("RNM-2"))

    @patch.dict("os.environ", {}, clear=True)
    def test_requires_sbis_cookies(self) -> None:
        errors = StringIO()

        with redirect_stderr(errors):
            exit_code = main(["253900152591"])

        self.assertEqual(exit_code, 1)
        self.assertIn("SBIS_COOKIES", errors.getvalue())

    @staticmethod
    def _item(reg_number: str, software_version: str | None) -> KKTInfo:
        return KKTInfo(
            owner_inn="253900152591",
            owner_name="Голованова Наталия Леонидовна",
            model="АТОЛ 30Ф",
            reg_number=reg_number,
            manufacturer_number="00106109551068",
            fn_end_date="2027-04-17",
            ofd_end_date="2026-09-07",
            sales_point_address="Приморский край, г. Артем",
            account_id=5588947,
            account_name="Голованова Наталия Леонидовна, ИП",
            software_version=software_version,
        )


if __name__ == "__main__":
    unittest.main()
