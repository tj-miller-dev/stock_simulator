"""Corporate actions: the second axis of determinism.

V1 promised that identical requests return identical bytes forever. Real feeds
do not work that way -- a split or a late dividend rewrites bars you already
stored, which is why anyone keeping history in a database has a reconciliation
job. Modelling that was the sharpest piece of launch feedback we got, and it
is a fidelity gap rather than a feature.

The fix strengthens the guarantee instead of weakening it. A bar is now a pure
function of (symbol, timestamp, generation, seed, as_of): pin `as_of` and
history is immutable forever exactly as before, so golden files and CI stay
reproducible. Omit it -- the default -- and you get what the feed would say
today, which for a restating symbol is not what it said last month.

Still no database. A schedule is derived from the calendar like every other
scenario, so "what did this look like in July" is a computation rather than a
lookup.

Two properties are load-bearing:

*Adjustments apply per day, before any aggregation.* That is what keeps
cross-timeframe coherence true by construction -- a weekly bar straddling an
ex-date is built from already-adjusted daily bars rather than patched
afterwards. Split ratios are whole numbers so volume scaling stays exact
integer arithmetic and V stays additive across timeframes.

*Actions have a horizon.* Only the last HORIZON_MONTHS of them are modelled as
restatements; anything older counts as already baked into history. Without
that bound, monthly actions compound without limit and adjusted prices from a
decade ago collapse to nothing. Six months keeps every realistic view sane
(one split inside a 30-day window, at most six across the horizon) and matches
what the reconciliation use case actually needs.

Scope, stated plainly: this models the *restatement*, not the ex-date price
discontinuity. The generated path is what traded; a split rewrites the history
in front of it, but you will not see the price halve on the ex-date itself.
"""

from dataclasses import dataclass
from datetime import date

from .market_calendar import is_trading_day, next_trading_day

HORIZON_MONTHS = 6

SPLIT_RATIO = 2               # 2:1 forward split
DIVIDEND_YIELD = 0.015        # ~1.5%, small enough to slip past a naive check
CORRECTION_ERROR = 0.08       # the size of the bad print, before it is busted
DIVIDEND_LATE_SESSIONS = 5    # the adjustment lands this long after the ex-date
CORRECTION_LATE_SESSIONS = 3

KINDS = ("split", "dividend", "correction")


@dataclass(frozen=True)
class Action:
    symbol: str
    kind: str
    ex_date: date
    announce_date: date
    process_date: date   # the day the restatement actually lands in history
    ratio: float         # split ratio (2.0 == 2:1); 1.0 otherwise
    cash: float          # dividend per share; 0.0 otherwise
    pending_factor: float   # price multiplier while as_of < process_date
    settled_factor: float   # ...and once as_of >= process_date
    volume_factor: int

    def touches(self, bar_day: date) -> bool:
        """Which bars this action rewrites. A split or dividend restates
        everything before the ex-date; a busted trade restates the single
        session it happened in."""
        if self.kind == "correction":
            return bar_day == self.ex_date
        return bar_day < self.ex_date

    def factor(self, as_of: date) -> float:
        return self.settled_factor if as_of >= self.process_date else self.pending_factor

    def as_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "type": self.kind,
            "ex_date": self.ex_date.isoformat(),
            "announce_date": self.announce_date.isoformat(),
            "process_date": self.process_date.isoformat(),
            "ratio": self.ratio,
            "cash_amount": round(self.cash, 4),
            "synthetic": True,
        }


def _session_on_or_after(d: date) -> date:
    return d if is_trading_day(d) else next_trading_day(d)


def _plus_sessions(d: date, n: int) -> date:
    for _ in range(n):
        d = next_trading_day(d)
    return d


def _month_action(symbol: str, year: int, month: int) -> Action | None:
    """This symbol's action for one calendar month, if it has one."""
    if symbol == "SPLITS":
        ex = _session_on_or_after(date(year, month, 10))
        return Action(
            symbol, "split", ex,
            announce_date=_session_on_or_after(date(year, month, 1)),
            process_date=ex,  # splits restate the moment they go ex
            ratio=float(SPLIT_RATIO), cash=0.0,
            pending_factor=1.0, settled_factor=1.0 / SPLIT_RATIO,
            volume_factor=SPLIT_RATIO,
        )
    if symbol == "DIVVY":
        ex = _session_on_or_after(date(year, month, 20))
        return Action(
            symbol, "dividend", ex,
            announce_date=_session_on_or_after(date(year, month, 5)),
            # The whole point of this ticker: the adjustment shows up days
            # after the ex-date, long after a naive job decided the month was
            # settled and stopped looking.
            process_date=_plus_sessions(ex, DIVIDEND_LATE_SESSIONS),
            ratio=1.0, cash=DIVIDEND_YIELD * 120.0,
            pending_factor=1.0, settled_factor=1.0 - DIVIDEND_YIELD,
            volume_factor=1,
        )
    if symbol == "REVISED":
        ex = _session_on_or_after(date(year, month, 8))
        return Action(
            symbol, "correction", ex,
            announce_date=ex,
            process_date=_plus_sessions(ex, CORRECTION_LATE_SESSIONS),
            ratio=1.0, cash=0.0,
            # Runs the other way round: the bad print is there first and the
            # bust removes it, so the *pending* state is the wrong one.
            pending_factor=1.0 + CORRECTION_ERROR, settled_factor=1.0,
            volume_factor=1,
        )
    return None


RESTATING_TICKERS = frozenset({"SPLITS", "DIVVY", "REVISED"})


def _months_back(anchor: date, count: int):
    year, month = anchor.year, anchor.month
    for _ in range(count):
        yield year, month
        month -= 1
        if month == 0:
            year, month = year - 1, 12


def actions_for(symbol: str, as_of: date) -> list[Action]:
    """Every action inside the horizon ending at `as_of`, oldest first.

    Anything further back counts as already incorporated into history, which
    is what stops monthly actions compounding into nonsense.
    """
    if symbol not in RESTATING_TICKERS:
        return []
    found = []
    for year, month in _months_back(as_of, HORIZON_MONTHS + 1):
        action = _month_action(symbol, year, month)
        # Nothing past the requested as_of exists yet, by definition.
        if action is not None and action.ex_date <= as_of:
            found.append(action)
    return sorted(found, key=lambda a: a.ex_date)


def in_window(symbol: str, start: date, end: date, as_of: date) -> list[Action]:
    """Actions whose ex-date falls in [start, end] -- what the
    corporate-actions endpoint serves."""
    return [a for a in actions_for(symbol, as_of) if start <= a.ex_date <= end]


def factors_for(symbol: str, as_of: date, adjustment: str):
    """A callable day -> (price_factor, volume_factor), or None when there is
    nothing to adjust.

    None is the common case -- every ordinary symbol -- and callers use it to
    skip the work entirely, so the overwhelming majority of requests pay
    nothing for this feature existing.
    """
    if adjustment == "raw" or symbol not in RESTATING_TICKERS:
        return None
    wanted = {
        "split": ("split", "all"),
        "dividend": ("dividend", "all"),
        # A busted trade is a vendor correction, not a corporate action; it is
        # not something you can ask to be left out of.
        "correction": ("split", "dividend", "all", "correction"),
    }
    applicable = [
        a for a in actions_for(symbol, as_of) if adjustment in wanted[a.kind]
    ]
    if not applicable:
        return None

    def factors(day: date) -> tuple[float, int]:
        price, volume = 1.0, 1
        for action in applicable:
            if not action.touches(day):
                continue
            price *= action.factor(as_of)
            if as_of >= action.process_date:
                volume *= action.volume_factor
        return price, volume

    return factors


def parse_adjustment(value: str | None) -> str:
    """Alpaca's grammar. Unlike Alpaca we default to `all`: the restatement
    tickers exist to be seen restating, and no ordinary symbol here has a
    corporate action for the default to change."""
    text = (value or "all").strip().lower()
    if text not in ("raw", "split", "dividend", "dividends", "all"):
        raise ValueError(
            f"invalid adjustment {value!r}: use raw, split, dividend or all "
            f"(default all) -- e.g. adjustment=raw for as-traded prices"
        )
    return "dividend" if text == "dividends" else text
