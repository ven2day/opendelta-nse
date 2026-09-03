import tempfile
import unittest
from pathlib import Path

import pandas as pd

from backend.data.symbol_registry import (
    MarketSymbolRegistry,
    SymbolAlreadyExistsError,
    SymbolNotFoundError,
)


class MarketSymbolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.seed = root / "seed.csv"
        self.target = root / "persistent" / "symbols.csv"
        self.seed.write_text("symbol\nALPHA\nBETA\n", encoding="utf-8")
        self.instruments = pd.DataFrame(
            [
                {
                    "symbol": "ALPHA",
                    "SM_SYMBOL_NAME": "ALPHA INDUSTRIES LIMITED",
                    "SEM_CUSTOM_SYMBOL": "Alpha Industries",
                },
                {
                    "symbol": "GAMMA",
                    "SM_SYMBOL_NAME": "GAMMA FINANCIAL SERVICES",
                    "SEM_CUSTOM_SYMBOL": "Gamma Finance",
                },
            ]
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_add_seeds_persistent_registry_and_keeps_new_symbol(self) -> None:
        registry = MarketSymbolRegistry(self.target, self.seed)

        addition = registry.add(" gamma.ns ", self.instruments)

        self.assertEqual(addition.symbol, "GAMMA")
        self.assertEqual(addition.company_name, "Gamma Financial Services")
        self.assertEqual(addition.symbol_count, 3)
        self.assertEqual(registry.symbols(), ["ALPHA", "BETA", "GAMMA"])

    def test_duplicate_does_not_rewrite_or_increment_registry(self) -> None:
        registry = MarketSymbolRegistry(self.target, self.seed)
        registry.symbols()
        before = self.target.read_bytes()

        with self.assertRaisesRegex(SymbolAlreadyExistsError, "already"):
            registry.add("ALPHA", self.instruments)

        self.assertEqual(self.target.read_bytes(), before)
        self.assertEqual(registry.symbols(), ["ALPHA", "BETA"])

    def test_unknown_symbol_is_rejected_without_changing_registry(self) -> None:
        registry = MarketSymbolRegistry(self.target, self.seed)

        with self.assertRaisesRegex(SymbolNotFoundError, "Dhan's instrument master"):
            registry.add("UNKNOWN", self.instruments)

        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
