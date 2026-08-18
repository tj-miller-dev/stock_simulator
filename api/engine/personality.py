"""Per-symbol personality: stable traits every universe shares.

Traits are a function of the symbol ONLY -- not of the universe seed -- so
AAPL is recognizably AAPL in every alternate universe; the seed remixes the
*path*, not the identity. Curated entries anchor famous tickers to a plausible
price level at the reference epoch (start of 2026); everything else gets
hash-derived traits, so any string is a valid symbol with a permanent
personality.

Prices here are deliberately "plausible ballpark", not quotes: the entire
product is openly synthetic. They exist so the first thing every visitor tries
("show me AAPL") doesn't return a $147 BRK.A.
"""

from dataclasses import dataclass, replace
from math import exp, log

from .hashing import hash_float


@dataclass(frozen=True)
class Personality:
    base_price: float     # anchor price at the reference epoch (Jan 1 2026)
    annual_vol: float     # annualized volatility of daily returns
    annual_drift: float   # expected log-return per year
    daily_volume: float   # typical shares/day


# symbol: (anchor price, annual vol, typical daily volume)
_CURATED_RAW: dict[str, tuple[float, float, float]] = {
    # Mega-cap tech
    "AAPL": (230, 0.26, 55e6), "MSFT": (430, 0.24, 22e6), "GOOGL": (190, 0.28, 28e6),
    "GOOG": (192, 0.28, 20e6), "AMZN": (220, 0.30, 40e6), "NVDA": (140, 0.45, 250e6),
    "META": (600, 0.32, 15e6), "TSLA": (400, 0.55, 95e6), "AVGO": (230, 0.35, 20e6),
    # Big tech / software
    "ORCL": (180, 0.30, 9e6), "CRM": (330, 0.32, 6e6), "ADBE": (450, 0.32, 3e6),
    "NFLX": (900, 0.35, 3e6), "AMD": (120, 0.48, 45e6), "INTC": (21, 0.42, 75e6),
    "IBM": (220, 0.24, 4e6), "CSCO": (58, 0.24, 18e6), "QCOM": (155, 0.34, 8e6),
    "TXN": (190, 0.27, 5e6), "NOW": (1000, 0.34, 1.5e6), "SHOP": (105, 0.48, 7e6),
    "PLTR": (75, 0.60, 60e6), "SNOW": (160, 0.48, 5e6), "DDOG": (130, 0.44, 4e6),
    "NET": (110, 0.48, 3e6), "CRWD": (350, 0.42, 3e6), "ZS": (200, 0.44, 2e6),
    "PANW": (180, 0.36, 5e6), "FTNT": (95, 0.36, 6e6), "MDB": (270, 0.48, 2e6),
    "UBER": (75, 0.38, 20e6), "ABNB": (135, 0.38, 5e6), "COIN": (250, 0.70, 10e6),
    "SPOT": (480, 0.38, 1.5e6), "SQ": (70, 0.50, 8e6), "PYPL": (85, 0.38, 12e6),
    "ZM": (75, 0.40, 3e6), "SNAP": (11, 0.60, 25e6), "PINS": (32, 0.45, 10e6),
    "ROKU": (70, 0.55, 5e6), "EA": (145, 0.26, 3e6), "HOOD": (40, 0.60, 25e6),
    "MSTR": (300, 0.85, 15e6), "SMCI": (35, 0.80, 40e6), "ARM": (130, 0.50, 8e6),
    # Financials
    "BRK.A": (700000, 0.18, 1.5e3), "BRK.B": (460, 0.18, 3e6),
    "JPM": (240, 0.24, 9e6), "V": (310, 0.20, 6e6), "MA": (520, 0.20, 2.5e6),
    "GS": (560, 0.26, 2e6), "MS": (130, 0.28, 7e6), "BAC": (45, 0.28, 35e6),
    "WFC": (75, 0.28, 15e6), "C": (70, 0.28, 12e6), "SCHW": (75, 0.30, 8e6),
    "BLK": (1000, 0.24, 0.6e6), "SPGI": (500, 0.22, 1.2e6), "AXP": (290, 0.26, 2.5e6),
    "USB": (48, 0.28, 8e6), "PNC": (190, 0.28, 2e6),
    # Health care
    "UNH": (520, 0.26, 3e6), "JNJ": (150, 0.18, 7e6), "LLY": (780, 0.32, 3e6),
    "ABBV": (175, 0.22, 6e6), "MRK": (100, 0.24, 10e6), "PFE": (27, 0.26, 35e6),
    "TMO": (550, 0.24, 1.5e6), "DHR": (240, 0.24, 3e6), "BMY": (55, 0.24, 10e6),
    "AMGN": (290, 0.24, 2.5e6), "GILD": (95, 0.24, 6e6), "CVS": (60, 0.30, 9e6),
    "MDT": (85, 0.22, 6e6), "ISRG": (550, 0.30, 1.5e6), "ABT": (115, 0.20, 5e6),
    # Consumer / industrials / energy
    "WMT": (95, 0.20, 18e6), "PG": (165, 0.16, 6e6), "KO": (62, 0.16, 12e6),
    "PEP": (155, 0.18, 5e6), "COST": (950, 0.22, 2e6), "HD": (400, 0.22, 3e6),
    "MCD": (290, 0.18, 2.5e6), "SBUX": (95, 0.28, 8e6), "NKE": (75, 0.30, 8e6),
    "DIS": (110, 0.28, 8e6), "CMCSA": (38, 0.24, 18e6), "T": (22, 0.22, 30e6),
    "VZ": (40, 0.20, 18e6), "TMUS": (220, 0.24, 4e6), "XOM": (110, 0.24, 14e6),
    "CVX": (155, 0.24, 7e6), "BA": (175, 0.36, 6e6), "CAT": (360, 0.26, 2.5e6),
    "GE": (180, 0.28, 4e6), "MMM": (130, 0.24, 3e6), "HON": (210, 0.20, 3e6),
    "UPS": (130, 0.26, 3.5e6), "FDX": (280, 0.30, 1.8e6), "LMT": (480, 0.20, 1.2e6),
    "RTX": (120, 0.22, 4e6), "DE": (420, 0.26, 1.2e6), "UNP": (240, 0.22, 2.5e6),
    "F": (10, 0.38, 50e6), "GM": (50, 0.34, 9e6),
    # ETFs (index trackers run much cooler than single names)
    "SPY": (600, 0.15, 70e6), "VOO": (550, 0.15, 5e6), "VTI": (300, 0.15, 4e6),
    "QQQ": (520, 0.20, 35e6), "IWM": (220, 0.22, 30e6), "DIA": (440, 0.14, 3e6),
    "GLD": (240, 0.14, 7e6), "SLV": (27, 0.24, 15e6), "TLT": (90, 0.15, 30e6),
    "HYG": (79, 0.08, 40e6), "EEM": (43, 0.16, 30e6), "EFA": (82, 0.13, 15e6),
    "XLF": (48, 0.16, 40e6), "XLE": (90, 0.20, 15e6), "XLK": (230, 0.20, 6e6),
    "ARKK": (55, 0.40, 10e6),
    # High-vol favorites
    "GME": (25, 0.90, 8e6), "AMC": (4, 0.95, 30e6), "RIVN": (12, 0.60, 25e6),
    "LCID": (2.5, 0.65, 30e6), "NIO": (4.5, 0.60, 40e6), "MARA": (17, 0.85, 30e6),
    "RIOT": (10, 0.85, 25e6),
}


def _drift_for(symbol: str, vol: float) -> float:
    # Mild positive drift, hash-jittered so symbols differ; high-vol names get
    # no free upward bias (their drama is the vol, not the trend).
    u = hash_float(f"personality:{symbol}:drift")
    return max(0.0, 0.07 - 0.05 * vol) + 0.08 * (u - 0.5)


def personality(symbol: str) -> Personality:
    curated = _CURATED_RAW.get(symbol)
    if curated is not None:
        price, vol, volume = curated
        return Personality(price, vol, _drift_for(symbol, vol), volume)

    u_price = hash_float(f"personality:{symbol}:price")
    u_vol = hash_float(f"personality:{symbol}:vol")
    u_volume = hash_float(f"personality:{symbol}:volume")
    base_price = exp(log(2.0) + u_price * (log(2000.0) - log(2.0)))
    annual_vol = 0.12 + 0.78 * (u_vol**1.5)  # skewed toward calm
    daily_volume = exp(log(2e5) + u_volume * (log(8e7) - log(2e5)))
    return Personality(base_price, annual_vol, _drift_for(symbol, annual_vol), daily_volume)


def override(base: Personality, **changes) -> Personality:
    return replace(base, **changes)
