from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import struct
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

from backend.paths import data_file

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows development fallback.
    fcntl = None

BASE_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_SYMBOLS_FILE = data_file("symbols.csv")
DEFAULT_OUTPUT_FILE = data_file("nse_symbols_rsi_volume.csv")

RSI_PERIOD = 14
HISTORY_DAYS = 160
REQUESTS_PER_SECOND = 4.0
REQUEST_RETRIES = 3
SESSION_RETRY_PASSES = 2
MINIMUM_COVERAGE = 0.90
PIVOT_WINDOW = 2
IST = ZoneInfo("Asia/Kolkata")

OUTPUT_COLUMNS = [
    "rank",
    "symbol",
    "company_name",
    "trading_date",
    "previous_date",
    "previous_close",
    "entry_price",
    "change_percent",
    "previous_rsi_14",
    "rsi_14",
    "volume_24h",
    "support_1_price",
    "support_1_time",
    "support_2_price",
    "support_2_time",
    "resistance_1_price",
    "resistance_1_time",
    "resistance_2_price",
    "resistance_2_time",
]


class ConfigurationError(RuntimeError):
    """Raised when required Dhan configuration is missing or unsafe."""


class DhanAPIError(RuntimeError):
    """Raised for a sanitized Dhan API failure."""


@dataclass(frozen=True)
class DhanConfig:
    client_id: str
    pin: str
    totp_secret: str
    auth_base_url: str
    base_url: str
    exchange_segment: str
    instrument: str
    instrument_master_url: str
    token_cache_file: Path
    symbols_file: Path
    output_file: Path
    history_days: int
    requests_per_second: float
    request_retries: int
    session_retry_passes: int
    minimum_coverage: float

    @classmethod
    def from_environment(cls) -> DhanConfig:
        required = {
            "DHAN_CLIENT_ID": os.environ.get("DHAN_CLIENT_ID", "").strip(),
            "DHAN_PIN": os.environ.get("DHAN_PIN", "").strip(),
            "DHAN_TOTP_SECRET": os.environ.get("DHAN_TOTP_SECRET", "").strip(),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise ConfigurationError(
                "Missing required Dhan environment variables: " + ", ".join(missing)
            )

        if not required["DHAN_CLIENT_ID"].isdigit():
            raise ConfigurationError("DHAN_CLIENT_ID must contain digits only")
        if len(required["DHAN_PIN"]) != 6 or not required["DHAN_PIN"].isdigit():
            raise ConfigurationError("DHAN_PIN must be a six-digit value")

        token_cache_file = _absolute_path_from_environment(
            "DHAN_TOKEN_CACHE_FILE",
            "/var/lib/vento-nse/dhan/token_cache.json",
        )
        output_file = _absolute_path_from_environment(
            "NSE_DATA_FILE",
            str(DEFAULT_OUTPUT_FILE),
        )

        minimum_coverage = float(
            os.environ.get("DHAN_MINIMUM_COVERAGE", str(MINIMUM_COVERAGE))
        )
        if not 0 < minimum_coverage <= 1:
            raise ConfigurationError("DHAN_MINIMUM_COVERAGE must be between 0 and 1")

        return cls(
            client_id=required["DHAN_CLIENT_ID"],
            pin=required["DHAN_PIN"],
            totp_secret=required["DHAN_TOTP_SECRET"],
            auth_base_url=os.environ.get(
                "DHAN_AUTH_BASE_URL", "https://auth.dhan.co"
            ).rstrip("/"),
            base_url=os.environ.get("DHAN_BASE_URL", "https://api.dhan.co/v2").rstrip(
                "/"
            ),
            exchange_segment=os.environ.get("DHAN_EXCHANGE_SEGMENT", "NSE_EQ"),
            instrument=os.environ.get("DHAN_INSTRUMENT", "EQUITY"),
            instrument_master_url=os.environ.get(
                "DHAN_INSTRUMENT_MASTER_URL",
                "https://images.dhan.co/api-data/api-scrip-master.csv",
            ),
            token_cache_file=token_cache_file,
            symbols_file=Path(
                os.environ.get("SYMBOLS_FILE", str(DEFAULT_SYMBOLS_FILE))
            ).expanduser(),
            output_file=output_file,
            history_days=int(os.environ.get("DHAN_HISTORY_DAYS", str(HISTORY_DAYS))),
            requests_per_second=float(
                os.environ.get("DHAN_REQUESTS_PER_SECOND", str(REQUESTS_PER_SECOND))
            ),
            request_retries=int(
                os.environ.get("DHAN_REQUEST_RETRIES", str(REQUEST_RETRIES))
            ),
            session_retry_passes=int(
                os.environ.get("DHAN_SESSION_RETRY_PASSES", str(SESSION_RETRY_PASSES))
            ),
            minimum_coverage=minimum_coverage,
        )


def _absolute_path_from_environment(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    if not value.is_absolute():
        raise ConfigurationError(f"{name} must be an absolute path")
    return value


def generate_totp(
    secret: str,
    *,
    timestamp: float | None = None,
    interval: int = 30,
    digits: int = 6,
) -> str:
    """Generate an RFC 6238 TOTP without exposing the shared secret."""
    normalized = "".join(secret.split()).upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        key = base64.b32decode(normalized + padding, casefold=True)
    except (ValueError, TypeError) as error:
        raise ConfigurationError("DHAN_TOTP_SECRET is not valid base32") from error

    counter = int(time.time() if timestamp is None else timestamp) // interval
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def _safe_api_message(status: int, payload: Any) -> str:
    if isinstance(payload, dict):
        error_code = payload.get("errorCode") or payload.get("code")
        error_message = payload.get("errorMessage") or payload.get("message")
        details = " ".join(str(value) for value in (error_code, error_message) if value)
        if details:
            return f"Dhan API returned HTTP {status}: {details}"
    return f"Dhan API returned HTTP {status}"


def _is_invalid_access_token_error(error: DhanAPIError) -> bool:
    message = str(error).casefold()
    return any(
        marker in message
        for marker in ("dh-901", "dh-906", "invalid token", "token is invalid", "expired token")
    )


class DhanClient:
    def __init__(
        self,
        config: DhanConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._clock = clock
        self._last_data_request = 0.0
        self._last_option_chain_request = float("-inf")
        self._access_token: str | None = None

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        throttle_data_api: bool = False,
    ) -> Any:
        if query:
            url = f"{url}?{urlencode(query)}"

        encoded_body = None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        for attempt in range(self.config.request_retries + 1):
            if throttle_data_api:
                self._wait_for_data_slot()

            request = Request(
                url,
                data=encoded_body,
                headers=request_headers,
                method=method,
            )
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read()
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except HTTPError as error:
                raw = error.read()
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = {}

                if (
                    error.code in {408, 429, 500, 502, 503, 504}
                    and attempt < self.config.request_retries
                ):
                    self._sleep(min(2**attempt, 8))
                    continue
                raise DhanAPIError(_safe_api_message(error.code, payload)) from None
            except (TimeoutError, URLError) as error:
                if attempt < self.config.request_retries:
                    self._sleep(min(2**attempt, 8))
                    continue
                raise DhanAPIError(
                    f"Dhan API network request failed after {attempt + 1} attempts"
                ) from error

        raise DhanAPIError("Dhan API request failed")

    def _wait_for_data_slot(self) -> None:
        interval = 1 / max(self.config.requests_per_second, 0.1)
        elapsed = self._clock() - self._last_data_request
        if elapsed < interval:
            self._sleep(interval - elapsed)
        self._last_data_request = self._clock()

    def _wait_for_option_chain_slot(self) -> None:
        elapsed = self._clock() - self._last_option_chain_request
        if elapsed < 3.0:
            self._sleep(3.0 - elapsed)
        self._last_option_chain_request = self._clock()

    def _load_cached_token(self) -> str | None:
        cache_file = self.config.token_cache_file
        try:
            payload = json.loads(cache_file.read_text(encoding="utf-8"))
            access_token = str(payload.get("accessToken", "")).strip()
            expiry_time = _parse_dhan_expiry(str(payload.get("expiryTime", "")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

        if not access_token or expiry_time <= datetime.now(IST) + timedelta(minutes=5):
            return None
        return access_token

    def _generate_access_token(self) -> str:
        payload = self._request_json(
            "POST",
            f"{self.config.auth_base_url}/app/generateAccessToken",
            query={
                "dhanClientId": self.config.client_id,
                "pin": self.config.pin,
                "totp": generate_totp(self.config.totp_secret),
            },
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan token response was not a JSON object")

        access_token = str(payload.get("accessToken", "")).strip()
        expiry_time = str(payload.get("expiryTime", "")).strip()
        if not access_token or not expiry_time:
            raise DhanAPIError("Dhan token response omitted the access token or expiry")

        _write_json_atomically(
            self.config.token_cache_file,
            {"accessToken": access_token, "expiryTime": expiry_time},
            mode=0o600,
        )
        return access_token

    def access_token(self) -> str:
        # Dhan tokens expire after 24 hours. Always re-check the shared cache so
        # long-running workers do not keep an expired token in memory and so a
        # token refreshed by another worker is picked up immediately.
        self._access_token = self._load_cached_token() or self._generate_access_token()
        return self._access_token

    def _refresh_access_token(self, rejected_token: str) -> str:
        cached_token = self._load_cached_token()
        if cached_token and cached_token != rejected_token:
            self._access_token = cached_token
        else:
            self._access_token = self._generate_access_token()
        return self._access_token

    def _request_with_access_token(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        throttle_data_api: bool = False,
    ) -> Any:
        access_token = self.access_token()
        request_headers = {**(headers or {}), "access-token": access_token}
        try:
            return self._request_json(
                method,
                url,
                headers=request_headers,
                body=body,
                throttle_data_api=throttle_data_api,
            )
        except DhanAPIError as error:
            if not _is_invalid_access_token_error(error):
                raise

        refreshed_token = self._refresh_access_token(access_token)
        refreshed_headers = {**request_headers, "access-token": refreshed_token}
        return self._request_json(
            method,
            url,
            headers=refreshed_headers,
            body=body,
            throttle_data_api=throttle_data_api,
        )

    def profile(self) -> dict[str, Any]:
        payload = self._request_with_access_token(
            "GET",
            f"{self.config.base_url}/profile",
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan profile response was not a JSON object")
        return payload

    def market_quote(self, security_ids: list[str]) -> dict[str, dict[str, Any]]:
        normalized_ids = list(dict.fromkeys(str(value).strip() for value in security_ids))
        if not normalized_ids:
            return {}
        if len(normalized_ids) > 1_000 or any(
            not value.isdigit() for value in normalized_ids
        ):
            raise DhanAPIError(
                "Dhan market quote requires up to 1,000 numeric security IDs"
            )

        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/marketfeed/quote",
            headers={"client-id": self.config.client_id},
            body={
                self.config.exchange_segment: [int(value) for value in normalized_ids]
            },
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan market quote response was not a JSON object")
        data = payload.get("data")
        quotes = data.get(self.config.exchange_segment) if isinstance(data, dict) else None
        if not isinstance(quotes, dict):
            raise DhanAPIError("Dhan market quote response omitted NSE quote data")
        return {
            str(security_id): quote
            for security_id, quote in quotes.items()
            if isinstance(quote, dict)
        }

    def market_quote_segments(
        self,
        instruments: dict[str, list[str | int]],
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Fetch quote/OI snapshots for multiple exchange segments in one request."""
        body: dict[str, list[int]] = {}
        total = 0
        for segment, security_ids in instruments.items():
            normalized = list(dict.fromkeys(str(value).strip() for value in security_ids))
            if any(not value.isdigit() for value in normalized):
                raise DhanAPIError("Dhan quote security IDs must be numeric")
            if normalized:
                body[str(segment)] = [int(value) for value in normalized]
                total += len(normalized)
        if total == 0:
            return {}
        if total > 1_000:
            raise DhanAPIError("Dhan market quote supports at most 1,000 instruments per request")
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/marketfeed/quote",
            headers={"client-id": self.config.client_id},
            body=body,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise DhanAPIError("Dhan market quote response omitted quote data")
        return {
            str(segment): {
                str(security_id): dict(quote)
                for security_id, quote in segment_quotes.items()
                if isinstance(quote, dict)
            }
            for segment, segment_quotes in data.items()
            if isinstance(segment_quotes, dict)
        }

    def option_expiry_list(
        self,
        underlying_security_id: str | int,
        underlying_segment: str = "IDX_I",
    ) -> list[str]:
        self._wait_for_option_chain_slot()
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/optionchain/expirylist",
            headers={"client-id": self.config.client_id},
            body={
                "UnderlyingScrip": int(underlying_security_id),
                "UnderlyingSeg": underlying_segment,
            },
            throttle_data_api=False,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise DhanAPIError("Dhan option-expiry response omitted the expiry list")
        return [str(value) for value in data]

    def option_chain(
        self,
        underlying_security_id: str | int,
        expiry: str,
        underlying_segment: str = "IDX_I",
    ) -> dict[str, Any]:
        self._wait_for_option_chain_slot()
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/optionchain",
            headers={"client-id": self.config.client_id},
            body={
                "UnderlyingScrip": int(underlying_security_id),
                "UnderlyingSeg": underlying_segment,
                "Expiry": str(expiry),
            },
            throttle_data_api=False,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
            raise DhanAPIError("Dhan option-chain response omitted option data")
        return payload

    def rolling_option_history(
        self,
        underlying_security_id: str | int,
        *,
        interval: str,
        expiry_flag: Literal["WEEK", "MONTH"],
        expiry_code: int,
        strike: str,
        option_type: Literal["CALL", "PUT"],
        from_date: date,
        to_date: date,
    ) -> dict[str, Any]:
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/charts/rollingoption",
            body={
                "exchangeSegment": "NSE_FNO",
                "interval": str(interval),
                "securityId": int(underlying_security_id),
                "instrument": "OPTIDX",
                "expiryFlag": expiry_flag,
                "expiryCode": int(expiry_code),
                "strike": strike,
                "drvOptionType": option_type,
                "requiredData": ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"],
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            },
            throttle_data_api=True,
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan expired-options response was not a JSON object")
        return payload

    def historical_daily(
        self,
        security_id: str,
        from_date: date,
        to_date: date,
        *,
        exchange_segment: str | None = None,
        instrument: str | None = None,
    ) -> dict[str, Any]:
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/charts/historical",
            body={
                "securityId": security_id,
                "exchangeSegment": exchange_segment or self.config.exchange_segment,
                "instrument": instrument or self.config.instrument,
                "expiryCode": 0,
                "oi": False,
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            },
            throttle_data_api=True,
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan historical response was not a JSON object")
        return payload

    def historical_intraday(
        self,
        security_id: str,
        interval: str,
        from_time: datetime,
        to_time: datetime,
        *,
        exchange_segment: str | None = None,
        instrument: str | None = None,
        include_oi: bool = False,
    ) -> dict[str, Any]:
        payload = self._request_with_access_token(
            "POST",
            f"{self.config.base_url}/charts/intraday",
            body={
                "securityId": security_id,
                "exchangeSegment": exchange_segment or self.config.exchange_segment,
                "instrument": instrument or self.config.instrument,
                "interval": interval,
                "oi": bool(include_oi),
                "fromDate": from_time.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
                "toDate": to_time.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S"),
            },
            throttle_data_api=True,
        )
        if not isinstance(payload, dict):
            raise DhanAPIError("Dhan intraday response was not a JSON object")
        return payload


def _parse_dhan_expiry(value: str) -> datetime:
    if not value:
        raise ValueError("empty expiry")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _write_json_atomically(path: Path, payload: dict[str, Any], mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(payload, handle, separators=(",", ":"))
            handle.write("\n")
            temporary_path = Path(handle.name)
        temporary_path.chmod(mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def calculate_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    price_change = close.diff()
    gains = price_change.clip(lower=0)
    losses = -price_change.clip(upper=0)

    average_gain = gains.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()
    average_loss = losses.ewm(
        alpha=1 / period,
        min_periods=period,
        adjust=False,
    ).mean()

    relative_strength = average_gain / average_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + relative_strength))
    rsi = rsi.mask((average_loss == 0) & (average_gain > 0), 100)
    rsi = rsi.mask((average_gain == 0) & (average_loss > 0), 0)
    return rsi


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().removesuffix(".NS")


def load_symbols(file_path: str | Path) -> list[str]:
    symbols_df = pd.read_csv(file_path)
    if "symbol" not in symbols_df.columns:
        raise ValueError("symbols.csv must contain a column named 'symbol'")

    symbols = symbols_df["symbol"].dropna().astype(str).map(normalize_symbol)
    return symbols[symbols != ""].drop_duplicates().tolist()


def parse_instrument_master(csv_bytes: bytes) -> pd.DataFrame:
    instruments = pd.read_csv(io.BytesIO(csv_bytes), dtype=str).fillna("")
    required_columns = {
        "SEM_EXM_EXCH_ID",
        "SEM_SEGMENT",
        "SEM_SMST_SECURITY_ID",
        "SEM_INSTRUMENT_NAME",
        "SEM_TRADING_SYMBOL",
        "SEM_SERIES",
    }
    missing = required_columns.difference(instruments.columns)
    if missing:
        raise DhanAPIError(
            "Dhan instrument master is missing columns: " + ", ".join(sorted(missing))
        )

    eligible = instruments[
        (instruments["SEM_EXM_EXCH_ID"] == "NSE")
        & (instruments["SEM_SEGMENT"] == "E")
        & (instruments["SEM_INSTRUMENT_NAME"] == "EQUITY")
    ].copy()
    eligible["symbol"] = eligible["SEM_TRADING_SYMBOL"].map(normalize_symbol)
    eligible["series_priority"] = eligible["SEM_SERIES"].map(
        lambda value: 0 if value == "EQ" else 1
    )
    eligible = eligible.sort_values(["symbol", "series_priority"])
    return eligible.drop_duplicates("symbol", keep="first")


def instrument_company_name(instrument: pd.Series | dict[str, Any]) -> str:
    """Return the best human-readable company name supplied by Dhan."""
    for column in ("SM_SYMBOL_NAME", "SEM_CUSTOM_SYMBOL"):
        value = " ".join(str(instrument.get(column, "")).split())
        if value:
            return value.title() if value.isupper() else value
    return ""


def build_company_name_map(instruments: pd.DataFrame) -> dict[str, str]:
    return {
        str(instrument["symbol"]): company_name
        for _, instrument in instruments.iterrows()
        if (company_name := instrument_company_name(instrument))
    }


def download_instrument_master(url: str) -> pd.DataFrame:
    request = Request(url, headers={"User-Agent": "vento-nse-data/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            return parse_instrument_master(response.read())
    except (HTTPError, URLError, TimeoutError) as error:
        raise DhanAPIError("Unable to download the Dhan instrument master") from error


def build_security_map(
    symbols: list[str],
    instruments: pd.DataFrame,
) -> tuple[dict[str, str], list[str]]:
    lookup = dict(
        zip(
            instruments["symbol"],
            instruments["SEM_SMST_SECURITY_ID"],
            strict=False,
        )
    )
    security_map = {symbol: lookup[symbol] for symbol in symbols if symbol in lookup}
    missing = [symbol for symbol in symbols if symbol not in security_map]
    return security_map, missing


def historical_payload_to_frame(payload: dict[str, Any]) -> pd.DataFrame:
    required = ["timestamp", "close"]
    if any(not isinstance(payload.get(key), list) for key in required):
        return pd.DataFrame()

    lengths = [
        len(value)
        for key in ("timestamp", "open", "high", "low", "close", "volume")
        if isinstance((value := payload.get(key)), list)
    ]
    if not lengths or min(lengths) == 0:
        return pd.DataFrame()
    row_count = min(lengths)

    timestamps = pd.to_numeric(
        pd.Series(payload["timestamp"][:row_count]), errors="coerce"
    )
    timestamp_unit = "ms" if timestamps.dropna().median() > 10_000_000_000 else "s"
    index = pd.to_datetime(timestamps, unit=timestamp_unit, utc=True, errors="coerce")

    frame = pd.DataFrame(index=index)
    for source, target in (
        ("open", "Open"),
        ("high", "High"),
        ("low", "Low"),
        ("close", "Close"),
        ("volume", "Volume"),
        ("open_interest", "OpenInterest"),
        ("oi", "OpenInterest"),
    ):
        values = payload.get(source)
        if isinstance(values, list):
            frame[target] = pd.to_numeric(
                pd.Series(values[:row_count], index=index), errors="coerce"
            )

    frame = frame[~frame.index.isna()].sort_index()
    if not frame.empty:
        frame.index = frame.index.tz_convert(IST)
        frame = frame[~frame.index.duplicated(keep="last")]
    return frame


def merge_live_quote(
    daily: pd.DataFrame,
    quote: dict[str, Any] | None,
    expected_session: date,
) -> pd.DataFrame:
    if daily is None or daily.empty or not isinstance(quote, dict):
        return daily

    trade_time = pd.to_datetime(
        quote.get("last_trade_time"),
        format="%d/%m/%Y %H:%M:%S",
        errors="coerce",
    )
    if pd.isna(trade_time):
        return daily
    trade_time = pd.Timestamp(trade_time).tz_localize(IST)
    quote_session = trade_time.date()
    if quote_session > expected_session:
        return daily

    last_price = pd.to_numeric(quote.get("last_price"), errors="coerce")
    ohlc = quote.get("ohlc") if isinstance(quote.get("ohlc"), dict) else {}
    open_price = pd.to_numeric(ohlc.get("open"), errors="coerce")
    high_price = pd.to_numeric(ohlc.get("high"), errors="coerce")
    low_price = pd.to_numeric(ohlc.get("low"), errors="coerce")
    volume = pd.to_numeric(quote.get("volume"), errors="coerce")
    prices = (last_price, open_price, high_price, low_price)
    if any(pd.isna(value) or float(value) <= 0 for value in prices):
        return daily

    live_row = pd.DataFrame(
        {
            "Open": [float(open_price)],
            "High": [max(float(high_price), float(last_price))],
            "Low": [min(float(low_price), float(last_price))],
            "Close": [float(last_price)],
            "Volume": [int(volume) if pd.notna(volume) and float(volume) >= 0 else 0],
        },
        index=pd.DatetimeIndex([trade_time], name="Date"),
    )

    frame = daily.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        return daily
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(IST)
    else:
        frame.index = frame.index.tz_convert(IST)
    latest_daily_session = frame.index.max().date()
    if quote_session < latest_daily_session:
        return frame

    # Dhan daily history can lag the latest settled NSE session shortly after
    # midnight. Keep the quote's actual trade date instead of discarding it
    # merely because India has moved into the next calendar day.
    frame = frame[frame.index.date != quote_session]
    return pd.concat([frame, live_row]).sort_index()


def calculate_recent_levels(
    data: pd.DataFrame,
    target_session: date,
    pivot_window: int = PIVOT_WINDOW,
) -> dict[str, Any]:
    levels: dict[str, Any] = {
        "support_1_price": None,
        "support_1_time": None,
        "support_2_price": None,
        "support_2_time": None,
        "resistance_1_price": None,
        "resistance_1_time": None,
        "resistance_2_price": None,
        "resistance_2_time": None,
    }
    if data is None or data.empty or pivot_window < 1:
        return levels

    high_column = next(
        (column for column in data.columns if str(column).lower() == "high"), None
    )
    low_column = next(
        (column for column in data.columns if str(column).lower() == "low"), None
    )
    if high_column is None or low_column is None:
        return levels

    frame = data[[high_column, low_column]].copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        return levels
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize(IST)
    else:
        frame.index = frame.index.tz_convert(IST)
    frame = frame[frame.index.date <= target_session].sort_index().dropna()
    if len(frame) < (pivot_window * 2) + 1:
        return levels

    highs = pd.to_numeric(frame[high_column], errors="coerce")
    lows = pd.to_numeric(frame[low_column], errors="coerce")
    supports: list[tuple[pd.Timestamp, float]] = []
    resistances: list[tuple[pd.Timestamp, float]] = []

    for position in range(pivot_window, len(frame) - pivot_window):
        window_start = position - pivot_window
        window_end = position + pivot_window + 1
        low_window = lows.iloc[window_start:window_end]
        high_window = highs.iloc[window_start:window_end]
        low_value = float(lows.iloc[position])
        high_value = float(highs.iloc[position])

        if (
            pd.notna(low_value)
            and low_value == float(low_window.min())
            and (low_window.drop(low_window.index[pivot_window]) > low_value).any()
        ):
            supports.append((pd.Timestamp(frame.index[position]), low_value))
        if (
            pd.notna(high_value)
            and high_value == float(high_window.max())
            and (high_window.drop(high_window.index[pivot_window]) < high_value).any()
        ):
            resistances.append((pd.Timestamp(frame.index[position]), high_value))

    for level_number, (timestamp, price) in enumerate(reversed(supports[-2:]), start=1):
        levels[f"support_{level_number}_price"] = round(price, 2)
        levels[f"support_{level_number}_time"] = timestamp.date().isoformat()
    for level_number, (timestamp, price) in enumerate(
        reversed(resistances[-2:]), start=1
    ):
        levels[f"resistance_{level_number}_price"] = round(price, 2)
        levels[f"resistance_{level_number}_time"] = timestamp.date().isoformat()

    return levels


def process_symbol(symbol: str, data: pd.DataFrame) -> dict[str, Any] | None:
    if data is None or data.empty:
        return None

    close_column = next(
        (column for column in data.columns if str(column).lower() == "close"), None
    )
    if close_column is None:
        return None

    close = pd.to_numeric(data[close_column], errors="coerce").dropna()
    if len(close) < RSI_PERIOD + 2:
        return None

    rsi = calculate_rsi(close, RSI_PERIOD)
    latest_date = close.index[-1]
    previous_date = close.index[-2]
    latest_close = float(close.iloc[-1])
    previous_close = float(close.iloc[-2])
    latest_rsi = float(rsi.iloc[-1])
    previous_rsi = float(rsi.iloc[-2])

    if previous_close <= 0 or pd.isna(latest_rsi) or pd.isna(previous_rsi):
        return None

    volume_24h = None
    volume_column = next(
        (column for column in data.columns if str(column).lower() == "volume"), None
    )
    if volume_column is not None:
        volume = pd.to_numeric(data[volume_column], errors="coerce")
        latest_volume = volume.get(latest_date)
        if pd.notna(latest_volume):
            volume_24h = int(latest_volume)

    result = {
        "symbol": normalize_symbol(symbol),
        "trading_date": pd.Timestamp(latest_date).date(),
        "previous_date": pd.Timestamp(previous_date).date(),
        "previous_close": round(previous_close, 2),
        "entry_price": round(latest_close, 2),
        "change_percent": round(
            ((latest_close - previous_close) / previous_close) * 100,
            2,
        ),
        "previous_rsi_14": round(previous_rsi, 2),
        "rsi_14": round(latest_rsi, 2),
        "volume_24h": volume_24h,
    }
    result.update(calculate_recent_levels(data, result["trading_date"]))
    return result


def choose_target_session(results: dict[str, dict[str, Any]]) -> date | None:
    counts = Counter(
        result["trading_date"]
        for result in results.values()
        if result.get("trading_date") is not None
    )
    if not counts:
        return None
    return max(counts, key=lambda session: (counts[session], session))


def build_session_consistent_output(
    symbols: list[str],
    results: dict[str, dict[str, Any]],
    target_session: date,
    company_names: dict[str, str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for rank, symbol in enumerate(symbols, start=1):
        result = results.get(symbol)
        row: dict[str, Any] = {
            "rank": rank,
            "symbol": symbol,
            "company_name": (company_names or {}).get(symbol, symbol),
        }
        if result and result.get("trading_date") == target_session:
            row.update(result)
            row["rank"] = rank
        rows.append(row)
    return pd.DataFrame(rows).reindex(columns=OUTPUT_COLUMNS)


def write_csv_atomically(data: pd.DataFrame, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            dir=output_file.parent,
            prefix=f".{output_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            data.to_csv(handle, index=False, quoting=csv.QUOTE_MINIMAL)
            temporary_path = Path(handle.name)
        temporary_path.chmod(0o644)
        os.replace(temporary_path, output_file)
    finally:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()


def _fetch_result(
    client: DhanClient,
    symbol: str,
    security_id: str,
    start_date: date,
    end_date: date,
    live_quote: dict[str, Any] | None = None,
    live_session: date | None = None,
) -> dict[str, Any] | None:
    payload = client.historical_daily(security_id, start_date, end_date)
    frame = historical_payload_to_frame(payload)
    if live_session is not None:
        frame = merge_live_quote(frame, live_quote, live_session)
    return process_symbol(symbol, frame)


def _run_screener_unlocked(
    config: DhanConfig,
    progress_callback: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    symbols = load_symbols(config.symbols_file)
    print(f"Loaded {len(symbols)} symbols from {config.symbols_file}", flush=True)
    if progress_callback is not None:
        progress_callback(0, len(symbols))

    client = DhanClient(config)
    profile = client.profile()
    data_plan = str(profile.get("dataPlan", "")).strip().lower()
    if data_plan and data_plan != "active":
        raise DhanAPIError("The Dhan Data API subscription is not active")
    print("Dhan authentication and profile validation passed", flush=True)

    instruments = download_instrument_master(config.instrument_master_url)
    security_map, unmapped = build_security_map(symbols, instruments)
    company_names = build_company_name_map(instruments)
    if unmapped:
        print(
            f"Instrument mapping missing for {len(unmapped)} symbols: "
            + ", ".join(unmapped[:20]),
            flush=True,
        )
    print(f"Mapped {len(security_map)} of {len(symbols)} symbols", flush=True)

    india_today = datetime.now(IST).date()
    start_date = india_today - timedelta(days=config.history_days)
    end_date = india_today + timedelta(days=1)
    results: dict[str, dict[str, Any]] = {}
    live_quotes: dict[str, dict[str, Any]] = {}
    try:
        live_quotes = client.market_quote(list(security_map.values()))
        print(
            f"Loaded live NSE quotes for {len(live_quotes)} of {len(security_map)} symbols",
            flush=True,
        )
    except DhanAPIError as error:
        print(
            f"Live NSE quotes unavailable; using completed daily candles: {error}",
            flush=True,
        )

    for position, symbol in enumerate(symbols, start=1):
        security_id = security_map.get(symbol)
        if security_id:
            try:
                result = _fetch_result(
                    client,
                    symbol,
                    security_id,
                    start_date,
                    end_date,
                    live_quotes.get(security_id),
                    india_today,
                )
                if result:
                    results[symbol] = result
            except DhanAPIError as error:
                print(f"{symbol}: {error}", flush=True)

        if position % 25 == 0 or position == len(symbols):
            print(f"Downloaded {position}/{len(symbols)} symbols", flush=True)
        if progress_callback is not None:
            progress_callback(position, len(symbols))

    target_session = choose_target_session(results)
    if target_session is None:
        raise DhanAPIError("Dhan returned no usable daily candles")

    for retry_pass in range(1, config.session_retry_passes + 1):
        stale_symbols = [
            symbol
            for symbol in symbols
            if symbol in security_map
            and results.get(symbol, {}).get("trading_date") != target_session
        ]
        if not stale_symbols:
            break

        print(
            f"Retry pass {retry_pass}: {len(stale_symbols)} stale or missing symbols",
            flush=True,
        )
        for symbol in stale_symbols:
            try:
                result = _fetch_result(
                    client,
                    symbol,
                    security_map[symbol],
                    start_date,
                    end_date,
                    live_quotes.get(security_map[symbol]),
                    india_today,
                )
                if result:
                    results[symbol] = result
            except DhanAPIError as error:
                print(f"{symbol}: {error}", flush=True)
        target_session = choose_target_session(results) or target_session

    valid_count = sum(
        result.get("trading_date") == target_session for result in results.values()
    )
    coverage = valid_count / len(symbols) if symbols else 0
    if coverage < config.minimum_coverage:
        raise DhanAPIError(
            "Refusing to publish incomplete Dhan data: "
            f"{valid_count}/{len(symbols)} symbols match {target_session} "
            f"({coverage:.1%}, minimum {config.minimum_coverage:.1%})"
        )

    output = build_session_consistent_output(
        symbols,
        results,
        target_session,
        company_names,
    )
    write_csv_atomically(output, config.output_file)
    print(f"Published session: {target_session} (IST)", flush=True)
    print(f"Session-consistent symbols: {valid_count}/{len(symbols)}", flush=True)
    print(f"Output saved atomically to: {config.output_file}", flush=True)
    return output


@contextmanager
def market_data_refresh_lock(output_file: Path) -> Iterator[None]:
    """Prevent the scheduled collector and manual refresh from running together."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = output_file.parent / ".nse-market-data-refresh.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise DhanAPIError("An all-symbol market-data refresh is already running") from error
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_screener(
    config: DhanConfig | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> pd.DataFrame:
    resolved = config or DhanConfig.from_environment()
    with market_data_refresh_lock(resolved.output_file):
        return _run_screener_unlocked(resolved, progress_callback)


if __name__ == "__main__":
    try:
        run_screener()
    except (ConfigurationError, DhanAPIError, OSError, ValueError) as error:
        print(f"Collector failed: {error}", flush=True)
        raise SystemExit(1) from None
