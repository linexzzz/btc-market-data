
import json
import socket
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

now = datetime.now(timezone.utc)
end = now.replace(
    hour=(now.hour // 4) * 4,
    minute=0, second=0, microsecond=0
)
start = end - timedelta(hours=4)
target_ms = int(start.timestamp() * 1000)

print("BTC PERPETUAL 4H API TEST")
print("Checked UTC:", now.isoformat())
print("Target start UTC:", start.isoformat())
print("Target close UTC:", end.isoformat())

sources = {
    "Binance": (
        "https://fapi.binance.com/fapi/v1/klines"
        "?symbol=BTCUSDT&interval=4h&limit=5"
    ),
    "Bybit": (
        "https://api.bybit.com/v5/market/kline"
        "?category=linear&symbol=BTCUSDT"
        "&interval=240&limit=5"
    ),
    "OKX": (
        "https://www.okx.com/api/v5/market/candles"
        "?instId=BTC-USDT-SWAP&bar=4H&limit=5"
    ),
}

successes = 0

for source, url in sources.items():
    print("\nSource:", source)

    try:
        request = Request(
            url,
            headers={"User-Agent": "btc-market-data/1.0"}
        )
        with urlopen(request, timeout=15) as response:
            data = json.load(response)

        if source == "Binance":
            rows = data
        elif source == "Bybit":
            if data.get("retCode") != 0:
                raise ValueError(str(data))
            rows = data["result"]["list"]
        else:
            if data.get("code") != "0":
                raise ValueError(str(data))
            rows = data["data"]

        row = next(
            (r for r in rows if int(r[0]) == target_ms),
            None
        )

        if row is None:
            raise ValueError("Target closed 4H candle missing")

        if source == "OKX" and row[8] != "1":
            raise ValueError("OKX candle not confirmed")

        o, h, l, c = (
            Decimal(row[i]) for i in (1, 2, 3, 4)
        )
        volume = Decimal(row[5])

        if not (0 < l <= min(o, c) <= max(o, c) <= h):
            raise ValueError("Invalid OHLC relationship")
        if volume < 0:
            raise ValueError("Invalid volume")

        unit = "contracts" if source == "OKX" else "BTC"
        print("Status: VERIFIED")
        print("Open:", o, "High:", h)
        print("Low:", l, "Close:", c)
        print("Volume:", volume, unit)
        print("Candle close UTC:", end.isoformat())
        successes += 1

    except HTTPError as exc:
        print("FAILED HTTP:", exc.code)
    except URLError as exc:
        kind = (
            "DNS" if isinstance(exc.reason, socket.gaierror)
            else "NETWORK"
        )
        print("FAILED", kind, ":", exc.reason)
    except Exception as exc:
        print("FAILED", type(exc).__name__, ":", exc)

print("\nVerified sources:", successes, "/ 3")
if successes == 0:
    raise SystemExit("No verified 4H exchange data")
