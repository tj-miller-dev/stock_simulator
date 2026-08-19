"""The scenario= parameter: opt-in, reproducible failure injection.

V1 gave callers data that always shows up. This is the other half -- sockets
that die, frames that arrive half-written, responses that hang or fail a few
times before they work. Real feeds do all of it; no vendor sells it on demand,
which is the same argument that made the magic tickers the headline feature.

Two rules hold the design together.

*It is a parameter, never a magic ticker.* V1_SPEC section 2 promises scripted
tickers never return malformed data, and a keyless public endpoint that serves
garbage to an agent who merely stumbled onto it is a brand problem rather than
a feature. A parameter appears in the URL that produced the failure, which
makes it self-documenting and impossible to hit by accident.

*Faults are deterministic.* drop:20s drops at twenty seconds, every time, for
everyone. The chaos tools people know are random; random failures make flaky
tests, and flaky tests are why nobody runs chaos in CI. These are meant to run
in CI.

flap is the one exception, and unavoidably so: "fail the first n attempts"
means remembering attempts. Its counter is per-pod and in-memory, exactly like
ratelimit.py, so across N replicas a flap:n can burn up to n*N failures before
it clears. Disclosed rather than papered over -- callers who need an exact
count run the container locally, which is what the GHCR image is for.

V1_SPEC section 2 reserves this same parameter for applying magic-ticker price
shapes to arbitrary symbols. Those names are recognised here and rejected with
a message pointing at the ticker, so the namespace stays theirs and nobody
burns a name we have already promised.
"""

import time
from dataclasses import dataclass, field

# Reserved for V1_SPEC section 2's "shapes on arbitrary symbols". Not built.
DATA_EFFECTS = (
    "crash", "moon", "flat", "gappy", "halts", "stale", "spikey", "penny", "choppy",
)

# name -> (takes an argument, low, high, surface)
_SPECS = {
    "flap":     (True,  1,   20,     "http"),
    "status":   (True,  400, 599,    "http"),
    "slow":     (True,  0,   10_000, "both"),
    "truncate": (False, 0,   0,      "both"),
    "drop":     (True,  0,   900,    "stream"),
    "garbage":  (True,  1,   50,     "stream"),
    "silent":   (True,  0,   900,    "stream"),
}

_EXAMPLES = {
    "http": "scenario=flap:2 (fail twice, then succeed)",
    "stream": "scenario=drop:20s (close the socket at twenty seconds)",
}


class EffectError(ValueError):
    """A malformed scenario= spec. The message teaches: it lists what is
    valid on this surface and shows something that works."""


@dataclass(frozen=True)
class Effect:
    name: str
    value: int = 0


def names_for(surface: str) -> list[str]:
    return sorted(n for n, spec in _SPECS.items() if spec[3] in ("both", surface))


def _parse_arg(name: str, arg: str, low: int, high: int) -> int:
    # Seconds and milliseconds may carry their unit: drop:20s reads better
    # than drop:20 and is what anyone would type first.
    digits = arg[:-2] if arg.endswith("ms") else arg[:-1] if arg.endswith("s") else arg
    if not digits.isdigit():
        raise EffectError(
            f"scenario={name}:{arg} -- {name} takes a whole number "
            f"between {low} and {high}, e.g. scenario={name}:{low}"
        )
    value = int(digits)
    if not low <= value <= high:
        raise EffectError(
            f"scenario={name}:{arg} is out of range -- {name} takes "
            f"{low} to {high}, e.g. scenario={name}:{low}"
        )
    return value


def parse_scenario(raw: str, surface: str) -> tuple[Effect, ...]:
    """Parse a comma-separated scenario= spec for 'http' or 'stream'.

    Raises EffectError with a message worth reading; callers render it in
    whichever provider's error shape owns the path.
    """
    effects: list[Effect] = []
    seen: set[str] = set()
    for token in (t.strip().lower() for t in raw.split(",")):
        if not token:
            continue
        name, _, arg = token.partition(":")
        if name in DATA_EFFECTS:
            raise EffectError(
                f"scenario={name} would apply {name.upper()}'s price shape to an "
                f"arbitrary symbol, which is not built yet. Request the ticker "
                f"itself in the meantime: symbols={name.upper()}"
            )
        if name not in _SPECS:
            raise EffectError(
                f"unknown scenario effect {name!r}: this surface accepts "
                f"{', '.join(names_for(surface))} -- e.g. {_EXAMPLES[surface]}"
            )
        takes_arg, low, high, where = _SPECS[name]
        if where not in ("both", surface):
            other = "the SSE stream" if where == "stream" else "the bar endpoints"
            raise EffectError(
                f"scenario={name} only applies to {other}; here you can use "
                f"{', '.join(names_for(surface))}"
            )
        if name in seen:
            raise EffectError(f"scenario={name} given twice: each effect takes one value")
        seen.add(name)
        if takes_arg:
            if not arg:
                raise EffectError(
                    f"scenario={name} needs a value between {low} and {high}, "
                    f"e.g. scenario={name}:{low}"
                )
            effects.append(Effect(name, _parse_arg(name, arg, low, high)))
        else:
            if arg:
                raise EffectError(f"scenario={name} takes no value; write scenario={name}")
            effects.append(Effect(name))
    return tuple(effects)


def value_of(effects: tuple[Effect, ...], name: str) -> int | None:
    for effect in effects:
        if effect.name == name:
            return effect.value
    return None


def has(effects: tuple[Effect, ...], name: str) -> bool:
    return any(effect.name == name for effect in effects)


@dataclass
class AttemptCounter:
    """How many times this caller has made this exact request. Only flap needs
    it, and only flap may: everything else here stays a pure function of the
    request. Per-pod and in-memory, with the same bounded table and the same
    honest caveat as the rate limiter."""

    ttl_seconds: float = 900.0
    _seen: dict[str, tuple[int, float]] = field(default_factory=dict)

    def bump(self, key: str) -> int:
        now = time.monotonic()
        count, _ = self._seen.get(key, (0, now))
        count += 1
        self._seen[key] = (count, now)
        if len(self._seen) > 20_000:
            self._prune(now)
        return count

    def _prune(self, now: float) -> None:
        self._seen = {k: v for k, v in self._seen.items() if now - v[1] < self.ttl_seconds}

    def reset(self) -> None:
        self._seen.clear()
