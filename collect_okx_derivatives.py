"""BTC v1.1 isolated OKX derivatives snapshot collector. Does not alter v1.0."""
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://www.okx.com"
SWAP = "BTC-USDT-SWAP"
INDEX = "BTC-USDT"
MAX_AGE_MS = 15 * 60 * 1000

ENDPOINTS = {
    "funding": ("/api/v5/public/funding-rate", {"instId": SWAP}, SWAP),
    "open_interest": ("/api/v5/public/open-interest", {"instType": "SWAP", "instId": SWAP}, SWAP),
    "mark_price": ("/api/v5/public/mark-price", {"instType": "SWAP", "instId": SWAP}, SWAP),
    "index_price": ("/api/v5/market/index-tickers", {"instId": INDEX}, INDEX),
}

def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

def positive_decimal(row, key, allow_zero=False, optional=False):
    value = row.get(key)
    if optional and (value is None or value == ""):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"invalid decimal in {key}: {value!r}")
    if not n.is_finite() or (n < 0 if allow_zero else n <= 0):
        raise ValueError(f"invalid range in {key}: {value!r}")
    return str(n)

def signed_decimal(row, key, optional=False):
    value = row.get(key)
    if optional and (value is None or value == ""):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"invalid decimal in {key}: {value!r}")
    if not n.is_finite():
        raise ValueError(f"non-finite {key}")
    return str(n)

def parse_ts(row, key, now_ms, max_age_ms=MAX_AGE_MS, optional=False):
    value = row.get(key)
    if optional and (value is None or value == ""):
        return None
    try:
        ms = int(value)
    except (ValueError, TypeError):
        raise ValueError(f"invalid timestamp: {key}")
    if max_age_ms is not None and not (-60000 <= now_ms - ms <= max_age_ms):
        raise ValueError(f"stale/future timestamp {key}: {iso(ms)}")
    return {"ms": ms, "utc": iso(ms)}

def request_one(name, path, params, expected_instrument, now_ms):
    url = BASE + path + "?" + urlencode(params)
    try:
        with urlopen(Request(url, headers={"User-Agent": "btc-market-data-v1.1/1.0"}), timeout=20) as response:
            response_code = response.status
            payload = json.load(response)
    except HTTPError as e:
        raise RuntimeError(f"{name}: HTTP {e.code}") from e
    except URLError as e:
        raise RuntimeError(f"{name}: NETWORK/DNS {e.reason}") from e
    if payload.get("code") != "0":
        raise ValueError(f"{name}: OKX code={payload.get('code')} msg={payload.get('msg')}")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError(f"{name}: invalid data array")
    matches = [r for r in rows if isinstance(r, dict) and r.get("instId") == expected_instrument]
    if len(matches) != 1:
        raise ValueError(f"{name}: expected exactly one {expected_instrument} row; got {len(matches)}")
    row = matches[0]
    if name in ("open_interest", "mark_price", "funding") and row.get("instType") != "SWAP":
        raise ValueError(f"{name}: not SWAP contract")
    # Always use OKX data-generation timestamp, not the HTTP request time.
    ts = parse_ts(row, "ts", now_ms)
    return row, ts, response_code, path

def main():
    now_ms = int(time.time() * 1000)
    output = {
        "schema_version": "1.1",
        "status": "verified_derivatives_snapshot",
        "source": "OKX",
        "instrument": SWAP,
        "index_instrument": INDEX,
        "contract_type": "USDT-margined perpetual swap",
        "checked_at_utc": iso(now_ms),
        "max_data_age_seconds": MAX_AGE_MS // 1000,
        "snapshots": {},
        "endpoints": {},
    }
    for name, (path, params, expected) in ENDPOINTS.items():
        row, ts, http_code, endpoint = request_one(name, path, params, expected, now_ms)
        output["endpoints"][name] = {"path": endpoint, "http_status": http_code}
        if name == "funding":
            output["snapshots"][name] = {
                "as_of": ts,
                "predicted_funding_rate": signed_decimal(row, "fundingRate"),
                "last_settled_funding_rate": signed_decimal(row, "settFundingRate", optional=True),
                "settlement_state": row.get("settState"),
                "funding_time": parse_ts(row, "fundingTime", now_ms, max_age_ms=None),
                "next_funding_time": parse_ts(row, "nextFundingTime", now_ms, max_age_ms=None, optional=True),
                "note": "predicted rate may change before settlement; not realized payment",
            }
        elif name == "open_interest":
            output["snapshots"][name] = {
                "as_of": ts,
                "contracts": positive_decimal(row, "oi", allow_zero=True),
                "btc": positive_decimal(row, "oiCcy", allow_zero=True),
                "usd": positive_decimal(row, "oiUsd", allow_zero=True),
            }
        elif name == "mark_price":
            output["snapshots"][name] = {
                "as_of": ts,
                "usdt_per_btc": positive_decimal(row, "markPx"),
            }
        elif name == "index_price":
            output["snapshots"][name] = {
                "as_of": ts,
                "usdt_per_btc": positive_decimal(row, "idxPx"),
            }
        print(f"VERIFIED {name}: instrument={expected}, ts={ts['utc']}, HTTP {http_code}")

    mark = Decimal(output["snapshots"]["mark_price"]["usdt_per_btc"])
    index = Decimal(output["snapshots"]["index_price"]["usdt_per_btc"])
    mark_ts = output["snapshots"]["mark_price"]["as_of"]["ms"]
    index_ts = output["snapshots"]["index_price"]["as_of"]["ms"]
    if abs(mark_ts - index_ts) > 10 * 60 * 1000:
        raise ValueError("mark/index timestamps too far apart")
    output["mark_minus_index_usdt"] = str(mark - index)
    output["mark_index_basis_pct"] = str((mark - index) / index * 100)
    target = Path("data/v1_1_derivatives_latest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("SUCCESS: 4/4 verified; data saved to", target)

if __name__ == "__main__":
    main()
