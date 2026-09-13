"""Exchange rate utility layer for the HackerRank 'Buy or Wait?' financial agent.

Provides deterministic, dated foreign-currency conversions based exclusively
on the dataset/exchange_rates.csv dataset provided by the challenge.
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

DEFAULT_CSV_PATH = (
    Path(__file__).resolve().parent.parent.parent / "dataset" / "exchange_rates.csv"
)

Numeric = Union[int, float, Decimal]


class ExchangeRateError(Exception):
    """Base exception for exchange-rate errors."""
    pass


class ExchangeRateNotFoundError(ExchangeRateError, KeyError):
    """Raised when an exchange rate is not available for a given date and currency pair."""

    def __init__(
        self,
        rate_date: str,
        from_currency: str,
        to_currency: str,
        message: Optional[str] = None,
    ) -> None:
        self.rate_date = rate_date
        self.from_currency = from_currency
        self.to_currency = to_currency
        self.message = (
            message
            or f"Exchange rate not found for {from_currency} -> {to_currency} on {rate_date}."
        )
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


def _normalize_date(date_val: Union[str, date, datetime]) -> str:
    """Normalize date to YYYY-MM-DD string format."""
    if isinstance(date_val, (date, datetime)):
        return date_val.strftime("%Y-%m-%d")
    s = str(date_val).strip()
    if not s:
        raise ValueError("rate_date cannot be empty.")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s


def _normalize_currency(curr: str) -> str:
    """Normalize currency code to uppercase stripped string."""
    cleaned = str(curr).strip().upper()
    if not cleaned:
        raise ValueError("Currency code cannot be empty.")
    return cleaned


class ExchangeRateService:
    """Service for loading and querying dated exchange rates.

    Loads and parses dataset/exchange_rates.csv into an in-memory dictionary
    keyed by (rate_date, from_currency, to_currency) for O(1) lookups.
    """

    def __init__(self, csv_path: Optional[Union[str, Path]] = None) -> None:
        """Initialize the service and load exchange rates.

        :param csv_path: Path to exchange_rates.csv. Defaults to dataset/exchange_rates.csv.
        """
        self._rates: Dict[Tuple[str, str, str], float] = {}
        self.csv_path = Path(csv_path) if csv_path is not None else DEFAULT_CSV_PATH
        self.load_csv(self.csv_path)

    def load_csv(self, csv_path: Union[str, Path]) -> None:
        """Load dated exchange rates from CSV into memory.

        CSV schema expected: rate_date,from_currency,to_currency,rate

        :param csv_path: Path to the exchange rates CSV file.
        :raises FileNotFoundError: If the CSV file does not exist.
        :raises ValueError: If the CSV file is missing required columns.
        """
        path = Path(csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"Exchange rate file not found at: {path}")

        new_rates: Dict[Tuple[str, str, str], float] = {}
        with open(path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required_cols = {"rate_date", "from_currency", "to_currency", "rate"}
            if not reader.fieldnames or not required_cols.issubset(set(reader.fieldnames)):
                raise ValueError(
                    f"Invalid exchange rates CSV format in {path}. "
                    f"Expected columns: {required_cols}"
                )

            for row in reader:
                d = _normalize_date(row["rate_date"])
                fc = _normalize_currency(row["from_currency"])
                tc = _normalize_currency(row["to_currency"])
                rate_val = float(row["rate"])
                new_rates[(d, fc, tc)] = rate_val

        self._rates = new_rates
        self.csv_path = path

    def get_rate(
        self,
        rate_date: Union[str, date, datetime],
        from_currency: str,
        to_currency: str,
    ) -> float:
        """Retrieve the exact exchange rate for a given date and currency pair.

        Rules:
        - If from_currency == to_currency, returns 1.0.
        - Lookups must match the exact date, from_currency, and to_currency.
        - Automatic inversion (1 / rate) or indirect conversions (A -> B -> C)
          are explicitly NOT performed.
        - Raises ExchangeRateNotFoundError if the exact dated pair is unavailable.

        :param rate_date: Date of the exchange rate (YYYY-MM-DD or date/datetime).
        :param from_currency: Source currency code (e.g. 'USD').
        :param to_currency: Target currency code (e.g. 'INR').
        :return: Exchange rate as a float.
        """
        fc = _normalize_currency(from_currency)
        tc = _normalize_currency(to_currency)
        d = _normalize_date(rate_date)

        if fc == tc:
            return 1.0

        key = (d, fc, tc)
        if key in self._rates:
            return self._rates[key]

        raise ExchangeRateNotFoundError(rate_date=d, from_currency=fc, to_currency=tc)

    def convert_amount(
        self,
        amount: Numeric,
        from_currency: str,
        to_currency: str,
        rate_date: Union[str, date, datetime],
    ) -> Numeric:
        """Convert an amount from one currency to another using the dated exchange rate.

        Rules:
        - If from_currency == to_currency, returns the original amount unchanged.
        - Otherwise, retrieves the dated rate and computes amount * rate.
        - Preserves Decimal precision if amount is a Decimal.

        :param amount: Amount to convert (int, float, or Decimal).
        :param from_currency: Source currency code.
        :param to_currency: Target currency code.
        :param rate_date: Date for the exchange rate.
        :return: Converted amount.
        """
        fc = _normalize_currency(from_currency)
        tc = _normalize_currency(to_currency)

        if fc == tc:
            return amount

        rate = self.get_rate(rate_date, fc, tc)

        if isinstance(amount, Decimal):
            return amount * Decimal(str(rate))

        return amount * rate

    def has_rate(
        self,
        rate_date: Union[str, date, datetime],
        from_currency: str,
        to_currency: str,
    ) -> bool:
        """Check whether an exchange rate exists for the given date and pair.

        :param rate_date: Date of the exchange rate.
        :param from_currency: Source currency code.
        :param to_currency: Target currency code.
        :return: True if rate is available (or currencies are identical), False otherwise.
        """
        fc = _normalize_currency(from_currency)
        tc = _normalize_currency(to_currency)
        if fc == tc:
            return True
        d = _normalize_date(rate_date)
        return (d, fc, tc) in self._rates

    def __len__(self) -> int:
        """Return the number of rate entries loaded."""
        return len(self._rates)

    convert = convert_amount


_default_service: Optional[ExchangeRateService] = None


def get_default_service(csv_path: Optional[Union[str, Path]] = None) -> ExchangeRateService:
    """Get or initialize the default cached ExchangeRateService instance.

    Ensures the CSV is loaded only once across multiple calls.
    """
    global _default_service
    if _default_service is None or (csv_path is not None and _default_service.csv_path != Path(csv_path)):
        _default_service = ExchangeRateService(csv_path=csv_path)
    return _default_service


def get_rate(
    rate_date: Union[str, date, datetime],
    from_currency: str,
    to_currency: str,
    csv_path: Optional[Union[str, Path]] = None,
) -> float:
    """Retrieve the exchange rate using the default service.

    :param rate_date: Date of the exchange rate (YYYY-MM-DD).
    :param from_currency: Source currency code (e.g. 'USD').
    :param to_currency: Target currency code (e.g. 'INR').
    :param csv_path: Optional override for the CSV file path.
    :return: Exchange rate as a float.
    """
    service = get_default_service(csv_path=csv_path) if csv_path is not None else get_default_service()
    return service.get_rate(rate_date, from_currency, to_currency)


def convert_amount(
    amount: Numeric,
    from_currency: str,
    to_currency: str,
    rate_date: Union[str, date, datetime],
    csv_path: Optional[Union[str, Path]] = None,
) -> Numeric:
    """Convert an amount using the default service.

    :param amount: Amount to convert.
    :param from_currency: Source currency code.
    :param to_currency: Target currency code.
    :param rate_date: Date for the exchange rate (YYYY-MM-DD).
    :param csv_path: Optional override for the CSV file path.
    :return: Converted amount.
    """
    service = get_default_service(csv_path=csv_path) if csv_path is not None else get_default_service()
    return service.convert_amount(amount, from_currency, to_currency, rate_date)


convert = convert_amount
