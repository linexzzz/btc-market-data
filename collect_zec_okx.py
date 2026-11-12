
import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SOURCE = "https://www.okx.com/api/v5/market/candles"
SYMBOL = "ZEC-USDT-SWAP"
INTERVALS = {
    "D1": ("1Dutc", 86400),
    "4H": ("4H", 14400),
    "1H": ("1H", 3600),
    "30m": ("30m", 1800),
    "15m": ("15m", 900),
}

def utc(ms):
    return datetime.fromtimestamp(
        ms / 1000, timezone.utc
    ).isoformat()

def collect(label, bar, seconds, now_ms):
    url = SOURCE + "?" + urlencode({
        "instId": SYMBOL,
        "bar": bar,
        "limit": 250
    })
    request = Request(
        url, headers={"User-Agent": "zec-data-check/1.0"}
    )

    with urlopen(request, timeout=20) as response:
        result = json.load(response)

    if result.get("code") != "0":
        raise ValueError(f"{label}: OKX API error {result}")

    duration = seconds * 1000
    expected_end = (now_ms // duration) * duration
    expected_start = expected_end - duration
    candles = []

    for row in result["data"]:
        if len(row) < 9:
            raise ValueError(f"{label}: incomplete row")

        ts = int(row[0])
        if row[8] != "1" or ts + duration > now_ms:
            continue

        o, h, l, c = [Decimal(x) for x in row[1:5]]
        volumes = [Decimal(x) for x in row[5:8]]

        if not all(x.is_finite() for x in
                   [o, h, l, c] + volumes):
            raise ValueError(f"{label}: nonfinite value")

        if not (0 < l <= min(o, c)
                <= max(o, c) <= h):
            raise ValueError(f"{label}: invalid OHLC")

        if any(v < 0 for v in volumes):
            raise ValueError(f"{label}: negative volume")

        if ts % duration != 0:
            raise ValueError(f"{label}: misaligned candle")

        candles.append({
            "open_ms": ts,
            "close_ms": ts + duration,
            "open": str(o),
            "high": str(h),
            "low": str(l),
            "close": str(c),
            "volume_contracts": str(volumes[0]),
            "volume_zec": str(volumes[1]),
            "volume_usdt": str(volumes[2])
        })

    candles.sort(key=lambda x: x["open_ms"])
    candles = [
        c for c in candles
        if c["open_ms"] <= expected_start
    ][-220:]

    if len(candles) < 220:
        raise ValueError(
            f"{label}: only {len(candles)} candles"
        )

    if candles[-1]["open_ms"] != expected_start:
        raise ValueError(
            f"{label}: latest closed candle missing"
        )

    for previous, current in zip(
        candles, candles[1:]
    ):
        if current["open_ms"] - previous["open_ms"] != duration:
            raise ValueError(f"{label}: missing/duplicate candle")

    last = candles[-1]
    previous_20 = candles[-21:-1]
    average = sum(
        Decimal(c["volume_zec"])
        for c in previous_20
    ) / 20

    rvol = (
        str(Decimal(last["volume_zec"]) / average)
        if average > 0 else None
    )

    print(
        f"VERIFIED {label}: 220 candles, "
        f"close={utc(last['close_ms'])}, "
        f"last_volume_zec={last['volume_zec']}, "
        f"RVOL={rvol}"
    )

    return {
        "api_bar": bar,
        "candle_count": len(candles),
        "latest_close_utc": utc(last["close_ms"]),
        "previous_20_avg_volume_zec": str(average),
        "latest_rvol_btc": rvol,
        "candles": candles
    }

def main():
    now_ms = int(time.time() * 1000)
    output = {
        "status": "verified_raw_ohlcv",
        "source": "OKX",
        "instrument": SYMBOL,
        "contract_type": "USDT-margined perpetual",
        "checked_at_utc": utc(now_ms),
        "timeframes": {}
    }

    for label, (bar, seconds) in INTERVALS.items():
        output["timeframes"][label] = collect(
            label, bar, seconds, now_ms
        )

    Path("data/zec").mkdir(parents=True, exist_ok=True)
    destination = Path("data/zec/latest.json")
    destination.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print("SUCCESS: 5/5 timeframes verified")
    print("Saved:", destination)

if __name__ == "__main__":
    main()
