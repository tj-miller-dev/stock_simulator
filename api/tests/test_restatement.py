"""as_of and the restatement tickers.

The promise being tested is two-sided, and both halves matter. Pin `as_of` and
history is frozen forever, exactly as V1 promised. Leave it out and a restating
symbol answers as of today -- which for a month-old window is not what it
answered last month. That second half is the thing real feeds do and mocks
never do, and it is what a reconciliation job exists to catch.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import api as api_module
from api import app
from engine import actions_for, bars_range, parse_timeframe
from engine.corporate_actions import DIVIDEND_YIELD, SPLIT_RATIO

client = TestClient(app)

NOW = datetime(2026, 8, 19, 21, 0, tzinfo=timezone.utc)
JUNE = (datetime(2026, 6, 1, tzinfo=timezone.utc), datetime(2026, 6, 30, tzinfo=timezone.utc))


def setup_function():
    api_module.limiter.reset()


def at(day: str) -> datetime:
    return datetime.fromisoformat(day + "T00:00:00+00:00")


def june(symbol, as_of, adjustment="all"):
    bars, _ = bars_range(symbol, parse_timeframe("1Day"), *JUNE, now=NOW,
                         as_of=at(as_of), adjustment=adjustment)
    return bars


# --- the contract ---------------------------------------------------------


def test_pinned_as_of_is_byte_stable():
    """The V1 guarantee, unchanged: fix every input and the bytes never move."""
    assert june("SPLITS", "2026-07-09") == june("SPLITS", "2026-07-09")
    assert june("DIVVY", "2026-08-01") == june("DIVVY", "2026-08-01")


def test_same_window_restates_across_the_announcement():
    """...and the half that is new: the same request, either side of a
    processed action, hands back different numbers."""
    before = june("SPLITS", "2026-07-09")   # the split goes ex on the 10th
    after = june("SPLITS", "2026-07-13")
    assert before != after
    assert [b["t"] for b in before] == [b["t"] for b in after]  # same bars, new values


def test_ordinary_symbols_never_restate():
    assert june("AAPL", "2026-07-09") == june("AAPL", "2026-08-19")


# --- splits ---------------------------------------------------------------


def test_split_adjustment_is_exactly_the_ratio():
    before = june("SPLITS", "2026-07-09")
    after = june("SPLITS", "2026-07-13")
    for old, new in zip(before, after):
        assert new["c"] == pytest.approx(old["c"] / SPLIT_RATIO, rel=1e-3)
        assert new["v"] == old["v"] * SPLIT_RATIO      # volume scales inversely
        assert new["n"] == old["n"]                    # trade count is untouched


def test_raw_is_unchanged_across_the_announcement():
    """adjustment=raw means as-traded, and as-traded is what actually happened
    -- no restatement can rewrite it."""
    assert june("SPLITS", "2026-07-09", "raw") == june("SPLITS", "2026-07-13", "raw")


def test_adjustment_modes_select_their_own_actions():
    settled = "2026-08-19"
    assert june("SPLITS", settled, "dividend") == june("SPLITS", settled, "raw")
    assert june("SPLITS", settled, "split") == june("SPLITS", settled, "all")


# --- the late dividend, which is the one that was actually asked for ------


def test_dividend_adjustment_lands_after_the_ex_date():
    (action,) = [
        a for a in actions_for("DIVVY", at("2026-08-19").date())
        if a.ex_date.isoformat() == "2026-07-20"
    ]
    assert action.process_date > action.ex_date, "a late adjustment that isn't late"
    # Still unrestated the day after it went ex...
    assert june("DIVVY", "2026-07-21") == june("DIVVY", "2026-07-20")
    # ...and restated only once it processes.
    assert june("DIVVY", "2026-07-30") != june("DIVVY", "2026-07-21")


def test_dividend_restatement_is_small_enough_to_be_missed():
    """The cruelty of this one is the size: big enough to corrupt a stored
    series, small enough to slip past a "did anything move 10%" check."""
    before = june("DIVVY", "2026-07-21")
    after = june("DIVVY", "2026-07-30")
    moves = [abs(a["c"] / b["c"] - 1.0) for b, a in zip(before, after)]
    assert 0 < min(moves)
    assert max(moves) < 0.03
    assert max(moves) == pytest.approx(DIVIDEND_YIELD, abs=0.005)


# --- the busted trade -----------------------------------------------------


def test_revised_rewrites_exactly_one_session():
    before = june("REVISED", "2026-06-09")
    after = june("REVISED", "2026-06-15")
    changed = [b["t"] for b, a in zip(before, after) if b != a]
    assert len(changed) == 1, f"a bad print should touch one session, touched {changed}"
    assert changed[0][:10] == "2026-06-08"


def test_revised_bad_print_is_the_high_one():
    before = june("REVISED", "2026-06-09")
    after = june("REVISED", "2026-06-15")
    (old,) = [b for b in before if b["t"][:10] == "2026-06-08"]
    (new,) = [b for b in after if b["t"][:10] == "2026-06-08"]
    assert new["c"] < old["c"], "the bust should remove the inflated print"


# --- the ledger -----------------------------------------------------------


def test_corporate_actions_endpoint_explains_the_change():
    r = client.get("/api/v1/corporate-actions?symbols=SPLITS,DIVVY"
                   "&start=2026-07-01&end=2026-07-31")
    body = r.json()
    assert r.status_code == 200 and body["synthetic"] is True
    kinds = {a["symbol"]: a for a in body["actions"]}
    assert kinds["SPLITS"]["type"] == "split" and kinds["SPLITS"]["ratio"] == 2.0
    divvy = kinds["DIVVY"]
    assert divvy["type"] == "dividend"
    # The three dates are the reason this endpoint exists.
    assert divvy["announce_date"] < divvy["ex_date"] < divvy["process_date"]


def test_ordinary_symbols_have_no_actions():
    r = client.get("/api/v1/corporate-actions?symbols=AAPL&start=2026-01-01&end=2026-08-01")
    assert r.json()["actions"] == []


# --- HTTP surface ---------------------------------------------------------


BARS = "/api/v1/alpaca/v2/stocks/bars?symbols=SPLITS&timeframe=1Day&start=2026-06-01&end=2026-06-30"


def test_as_of_flows_through_the_alpaca_surface():
    before = client.get(BARS + "&as_of=2026-07-09").json()["bars"]["SPLITS"]
    after = client.get(BARS + "&as_of=2026-07-13").json()["bars"]["SPLITS"]
    assert before != after


def test_alpacas_own_asof_is_a_different_parameter():
    """Alpaca's `asof` is a symbol-mapping date. Ours is `as_of`. Conflating
    them would silently break the mimicry."""
    pinned = client.get(BARS + "&as_of=2026-07-09").json()["bars"]["SPLITS"]
    ignored = client.get(BARS + "&asof=2026-07-09").json()["bars"]["SPLITS"]
    assert ignored != pinned
    assert ignored == client.get(BARS).json()["bars"]["SPLITS"]


def test_restating_symbols_are_not_cached_forever_without_an_as_of():
    """A year-long immutable header would serve pre-split prices long after
    the split (V1_1_SPEC 3.4)."""
    loose = client.get(BARS)
    assert "immutable" not in loose.headers.get("Cache-Control", "")

    pinned = client.get(BARS + "&as_of=2026-07-13")
    assert "immutable" in pinned.headers["Cache-Control"]

    ordinary = client.get(BARS.replace("SPLITS", "AAPL"))
    assert "immutable" in ordinary.headers["Cache-Control"]


def test_bad_adjustment_teaches():
    r = client.get(BARS + "&adjustment=sideways")
    assert r.status_code == 400
    assert "raw, split, dividend or all" in r.json()["message"]


# --- saying out loud what as_of did ---------------------------------------
#
# Both of these reproduce a real debugging session: a restatement that changed
# nothing looked exactly like a broken feature, because the response is a clean
# 200 full of plausible bars either way.


def test_restated_header_names_the_actions_that_applied():
    r = client.get(BARS + "&as_of=2026-07-13")
    assert r.headers["X-Cuckoo-As-Of"].startswith("2026-07-13")
    restated = r.headers["X-Cuckoo-Restated"]
    assert restated.startswith("2 actions applied")
    assert "SPLITS split ex 2026-06-10" in restated
    assert "SPLITS split ex 2026-07-10" in restated


def test_window_in_front_of_every_action_reports_zero():
    """The wrong-window mistake: August bars sit after every ex-date, so no
    action can rewrite them and as_of appears to do nothing."""
    august = ("/api/v1/alpaca/v2/stocks/bars?symbols=SPLITS&timeframe=1Day"
              "&start=2026-08-11&end=2026-08-18")
    r = client.get(august + "&as_of=2026-08-19")
    assert r.headers["X-Cuckoo-Restated"].startswith("0 actions applied")
    assert "before their ex_date" in r.headers["X-Cuckoo-Restated"]


def test_unreserved_symbol_reports_zero_without_the_hint():
    """The typo mistake: SPLIT is not SPLITS, so it falls through to an
    ordinary hash-derived personality and quietly never restates."""
    r = client.get(BARS.replace("symbols=SPLITS", "symbols=SPLIT") + "&as_of=2026-07-13")
    assert r.headers["X-Cuckoo-Restated"] == "0 actions applied"


def test_adjustment_raw_reports_zero():
    r = client.get(BARS + "&as_of=2026-07-13&adjustment=raw")
    assert r.headers["X-Cuckoo-Restated"].startswith("0 actions applied")


def test_pending_late_dividend_is_not_counted_as_applied():
    """DIVVY's June dividend goes ex on the 22nd and processes on the 29th.
    In between it is known but has changed nothing, and the header must say so
    rather than implying the adjustment already landed."""
    divvy = ("/api/v1/alpaca/v2/stocks/bars?symbols=DIVVY&timeframe=1Day"
             "&start=2026-06-01&end=2026-06-05")
    assert client.get(divvy + "&as_of=2026-06-26").headers[
        "X-Cuckoo-Restated"].startswith("0 actions applied")
    assert client.get(divvy + "&as_of=2026-06-30").headers[
        "X-Cuckoo-Restated"].startswith("1 actions applied")


def test_latest_bars_carry_the_headers_too():
    r = client.get("/api/v1/alpaca/v2/stocks/bars/latest?symbols=SPLITS")
    assert r.status_code == 200 and "bars" in r.json()
    assert "X-Cuckoo-As-Of" in r.headers and "X-Cuckoo-Restated" in r.headers


def test_index_lists_every_reserved_ticker():
    """The root cause of the typo: the one machine-readable list of special
    symbols was missing three of them."""
    magic = client.get("/api").json()["magic_tickers"]
    for ticker in ("SPLITS", "DIVVY", "REVISED", "STALE", "CRASH"):
        assert ticker in magic, f"{ticker} missing from the /api index"
