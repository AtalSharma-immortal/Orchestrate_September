"unit_tests for the exchange rate utility layer."""

from datetime import date
from decimal import Decimal
import tempfile
import unittest
from pathlib import Path
import sys

_code_dir = Path(__file__).resolve().parent.parent
if str(_code_dir) not in sys.path:
    sys.path.insert(0, str(_code_dir))

try:
    from finance.exchange_rates import (
        ExchangeRateError,
        ExchangeRateNotFoundError,
        ExchangeRateService,
        convert,
        convert_amount,
        get_rate,
    )
except ImportError:
    from code.finance.exchange_rates import (
        ExchangeRateError,
        ExchangeRateNotFoundError,
        ExchangeRateService,
        convert,
        convert_amount,
        get_rate,
    )


class TestExchangeRates(unittest.TestCase):
    """Test suite for exchange rate lookups, conversions, and dataset loading."""

    @classmethod
    def setUpClass(cls):
        cls.service = ExchangeRateService()

    def test_loading_csv_successfully(self):
        """Verify that the default CSV loads successfully and has valid entries."""
        self.assertGreater(len(self.service), 0)
        self.assertTrue(self.service.has_rate("2024-03-15", "USD", "INR"))
        self.assertTrue(self.service.has_rate("2024-03-15", "USD", "EUR"))
        self.assertTrue(self.service.has_rate("2024-03-15", "EUR", "ZAR"))

    def test_exact_rate_lookup(self):
        """Verify exact rate lookups matching records in dataset/exchange_rates.csv."""
        rate = self.service.get_rate("2024-03-15", "USD", "INR")
        self.assertEqual(rate, 83.33)

        rate_eur = self.service.get_rate("2024-03-15", "USD", "EUR")
        self.assertEqual(rate_eur, 0.92)

        rate_zar = self.service.get_rate("2024-03-15", "EUR", "ZAR")
        self.assertEqual(rate_zar, 20.0)

        self.assertEqual(get_rate("2024-03-15", "USD", "INR"), 83.33)

    def test_normal_conversion(self):
        """Verify currency conversions with float, int, and Decimal values."""
        converted = self.service.convert_amount(100, "USD", "INR", "2024-03-15")
        self.assertEqual(converted, 8333.0)

        self.assertEqual(convert_amount(100, "USD", "INR", "2024-03-15"), 8333.0)

        dec_amount = Decimal("100.00")
        dec_converted = self.service.convert_amount(dec_amount, "USD", "INR", "2024-03-15")
        self.assertIsInstance(dec_converted, Decimal)
        self.assertEqual(dec_converted, Decimal("8333.00"))

        self.assertEqual(self.service.convert_amount(50.0, "EUR", "ZAR", "2024-03-15"), 1000.0)

    def test_same_currency_conversion(self):
        """Verify that converting within the same currency returns the amount unchanged."""
        self.assertEqual(self.service.convert_amount(100, "INR", "INR", "2024-03-15"), 100)
        self.assertEqual(convert_amount(100, "INR", "INR", "2024-03-15"), 100)

        self.assertEqual(self.service.convert_amount(250.75, "USD", "USD", "2024-03-15"), 250.75)

        dec_val = Decimal("499.99")
        self.assertEqual(self.service.convert_amount(dec_val, "EUR", "EUR", "2024-03-15"), dec_val)

        self.assertEqual(self.service.get_rate("2024-03-15", "INR", "INR"), 1.0)
        self.assertEqual(get_rate("2024-03-15", "USD", "USD"), 1.0)

    def test_missing_currency_pair(self):
        """Verify that unlisted currency pairs raise ExchangeRateNotFoundError without reversing or triangulating."""
        with self.assertRaises(ExchangeRateNotFoundError) as ctx:
            self.service.get_rate("2024-03-15", "INR", "USD")
        self.assertIn("INR", str(ctx.exception))
        self.assertIn("USD", str(ctx.exception))
        self.assertIn("2024-03-15", str(ctx.exception))

        with self.assertRaises(ExchangeRateNotFoundError):
            self.service.convert_amount(100, "INR", "USD", "2024-03-15")

        with self.assertRaises(ExchangeRateNotFoundError):
            self.service.get_rate("2024-03-15", "EUR", "INR")


    def test_missing_date(self):
        """Verify that querying an unlisted date raises ExchangeRateNotFoundError."""
        with self.assertRaises(ExchangeRateNotFoundError) as ctx:
            self.service.get_rate("1995-01-01", "USD", "INR")
        self.assertIn("1995-01-01", str(ctx.exception))

        with self.assertRaises(ExchangeRateNotFoundError):
            self.service.convert_amount(100, "USD", "INR", "1995-01-01")

    def test_normalization(self):
        """Verify that currency codes and date formats are properly normalized."""
        self.assertEqual(self.service.get_rate("2024-03-15", "usd", "inr"), 83.33)
        self.assertEqual(self.service.get_rate("  2024-03-15  ", " Usd ", " Inr "), 83.33)

        d = date(2024, 3, 15)
        self.assertEqual(self.service.get_rate(d, "USD", "INR"), 83.33)

    def test_custom_csv_and_validation(self):
        """Verify that ExchangeRateService properly parses custom valid CSVs and validates errors."""
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as tf:
            tf.write("rate_date,from_currency,to_currency,rate\n")
            tf.write("2025-01-01,USD,GBP,0.78\n")
            temp_path = tf.name

        try:
            custom_service = ExchangeRateService(csv_path=temp_path)
            self.assertEqual(custom_service.get_rate("2025-01-01", "USD", "GBP"), 0.78)
            self.assertEqual(custom_service.convert_amount(100, "USD", "GBP", "2025-01-01"), 78.0)
        finally:
            Path(temp_path).unlink(missing_ok=True)

        with self.assertRaises(FileNotFoundError):
            ExchangeRateService(csv_path="non_existent_file.csv")

        with tempfile.NamedTemporaryFile("w", suffix=".csvad", delete=False, encoding="utf-8") as tf:
            tf.write("wrong_date,from_curr,to_curr,rate\n")
            temp_bad_path = tf.name

        try:
            with self.assertRaises(ValueError):
                ExchangeRateService(csv_path=temp_bad_path)
        finally:
            Path(temp_bad_path).unlink(missing_ok=True)


    def test_convert_alias_and_iso_timestamp(self):
        """Verify convert alias matches convert_amount and ISO timestamps normalize properly."""
        res1 = self.service.convert(100, "USD", "INR", "2024-03-15T00:00:00")
        res2 = self.service.convert_amount(100, "USD", "INR", "2024-03-15")
        self.assertEqual(res1, res2)
        self.assertEqual(convert(100, "USD", "INR", "2024-03-15"), res1)


if __name__ == "__main__":
    unittest.main()
