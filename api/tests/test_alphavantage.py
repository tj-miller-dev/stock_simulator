"""Alpha Vantage-compatible surface, checked against live www.alphavantage.co
response captures (Aug 2026): meta-data numbering per function, stringified
values, newest-first ordering, GLOBAL_QUOTE's zero-padded keys, and errors as
HTTP 200 with an "Error Message" body."""

from datetime import datetime

from fastapi.testclient import TestClient

import api as api_module
from api import app

client = TestClient(app)

AV = "/api/v1/alphavantage/query"


def setup_function():
    api_module.limiter.reset()


def test_daily_shape_matches_alpha_vantage():
    r = client.get(f"{AV}?function=TIME_SERIES_DAILY&symbol=IBM&apikey=anything")
    assert r.status_code == 200
    assert r.headers["X-Cuckoo-Synthetic"] == "true"
    body = r.json()
    assert list(body) == ["Meta Data", "Time Series (Daily)"]
    meta = body["Meta Data"]
    assert list(meta) == [
        "1. Information", "2. Symbol", "3. Last Refreshed",
        "4. Output Size", "5. Time Zone",
    ]
    assert meta["1. Information"] == "Daily Prices (open, high, low, close) and Volumes"
    assert meta["2. Symbol"] == "IBM"
    assert meta["4. Output Size"] == "Compact"
    assert meta["5. Time Zone"] == "US/Eastern"

    series = body["Time Series (Daily)"]
    assert len(series) == 100  # compact
    labels = list(series)
    assert labels == sorted(labels, reverse=True)  # newest first
    assert meta["3. Last Refreshed"] == labels[0]
    first = series[labels[0]]
    assert list(first) == ["1. open", "2. high", "3. low", "4. close", "5. volume"]
    # Alpha Vantage stringifies: prices with 4 decimals, integer volume.
    assert first["1. open"].count(".") == 1 and len(first["1. open"].split(".")[1]) == 4
    assert first["5. volume"].isdigit()


def test_daily_full_and_determinism():
    url = f"{AV}?function=TIME_SERIES_DAILY&symbol=AAPL&outputsize=full"
    a, b = client.get(url), client.get(url)
    assert a.json() == b.json()
    body = a.json()
    assert body["Meta Data"]["4. Output Size"] == "Full size"
    assert len(body["Time Series (Daily)"]) > 4000  # ~20 years of sessions


def test_weekly_and_monthly_shapes():
    weekly = client.get(f"{AV}?function=TIME_SERIES_WEEKLY&symbol=IBM").json()
    assert list(weekly) == ["Meta Data", "Weekly Time Series"]
    # Weekly/monthly meta has no Output Size row (verified live).
    assert list(weekly["Meta Data"]) == [
        "1. Information", "2. Symbol", "3. Last Refreshed", "4. Time Zone",
    ]
    for label in list(weekly["Weekly Time Series"])[:10]:
        # Labels are the period's last trading day, i.e. never a weekend.
        assert datetime.strptime(label, "%Y-%m-%d").weekday() < 5

    monthly = client.get(f"{AV}?function=TIME_SERIES_MONTHLY&symbol=IBM").json()
    assert "Monthly Time Series" in monthly
    labels = list(monthly["Monthly Time Series"])
    assert labels == sorted(labels, reverse=True)
    # Consecutive labels land in different months.
    assert labels[0][:7] != labels[1][:7]


def test_intraday_shape_and_rth_labels():
    r = client.get(f"{AV}?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=5min")
    body = r.json()
    meta = body["Meta Data"]
    assert list(meta) == [
        "1. Information", "2. Symbol", "3. Last Refreshed",
        "4. Interval", "5. Output Size", "6. Time Zone",
    ]
    assert meta["1. Information"] == "Intraday (5min) open, high, low, close prices and volume"
    assert meta["4. Interval"] == "5min"
    series = body["Time Series (5min)"]
    assert len(series) == 100
    for label in series:
        stamp = datetime.strptime(label, "%Y-%m-%d %H:%M:%S")
        # End-of-interval labels within the RTH session: 09:35 .. 16:00 ET.
        minutes = stamp.hour * 60 + stamp.minute
        assert 9 * 60 + 35 <= minutes <= 16 * 60


def test_intraday_60min_labels_align_to_session():
    body = client.get(f"{AV}?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=60min").json()
    labels = list(body["Time Series (60min)"])
    # Session-anchored hourly buckets end at :30 past the hour, or 16:00.
    for label in labels[:20]:
        assert label.endswith(":30:00") or label.endswith("16:00:00")


def test_global_quote_shape_and_arithmetic():
    body = client.get(f"{AV}?function=GLOBAL_QUOTE&symbol=IBM").json()
    quote = body["Global Quote"]
    assert list(quote) == [
        "01. symbol", "02. open", "03. high", "04. low", "05. price",
        "06. volume", "07. latest trading day", "08. previous close",
        "09. change", "10. change percent",
    ]
    change = float(quote["05. price"]) - float(quote["08. previous close"])
    assert abs(float(quote["09. change"]) - round(change, 4)) < 1e-9
    assert quote["10. change percent"].endswith("%")


def test_scenario_ticker_flows_through():
    body = client.get(f"{AV}?function=TIME_SERIES_DAILY&symbol=CRASH").json()
    closes = [float(v["4. close"]) for v in body["Time Series (Daily)"].values()]
    assert min(closes) / max(closes) < 0.82  # the scripted crash survives reshaping


def test_errors_are_200_with_error_message_key():
    # That is Alpha Vantage's actual error style; clients sniff for the key.
    cases = [
        f"{AV}?function=NOT_A_FUNCTION&symbol=IBM",
        f"{AV}?symbol=IBM",
        f"{AV}?function=TIME_SERIES_DAILY",
        f"{AV}?function=TIME_SERIES_DAILY&symbol=IBM&datatype=csv",
        f"{AV}?function=TIME_SERIES_INTRADAY&symbol=IBM",           # missing interval
        f"{AV}?function=TIME_SERIES_INTRADAY&symbol=IBM&interval=7min",
        f"{AV}?function=TIME_SERIES_DAILY&symbol=IBM&outputsize=huge",
    ]
    for url in cases:
        r = client.get(url)
        assert r.status_code == 200, url
        assert "Error Message" in r.json(), url


def test_seed_selects_a_different_dataset():
    base = f"{AV}?function=TIME_SERIES_DAILY&symbol=AAPL"
    canonical = client.get(base).json()["Time Series (Daily)"]
    seeded = client.get(base + "&seed=alt").json()["Time Series (Daily)"]
    assert canonical != seeded
    assert client.get(base + "&seed=alt").json()["Time Series (Daily)"] == seeded
