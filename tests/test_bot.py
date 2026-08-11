import tempfile
import unittest
from pathlib import Path

from bot import WhitelistStore, normalize_inn


class BotTests(unittest.TestCase):
    def test_normalize_inn(self) -> None:
        self.assertEqual(normalize_inn("2537 0108 4668"), "253701084668")
        with self.assertRaises(ValueError):
            normalize_inn("123")

    def test_whitelist_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "whitelist.json"
            store = WhitelistStore(path, frozenset({1}))
            self.assertTrue(store.add(2))
            self.assertTrue(WhitelistStore(path, frozenset({1})).is_allowed(2))


if __name__ == "__main__":
    unittest.main()
