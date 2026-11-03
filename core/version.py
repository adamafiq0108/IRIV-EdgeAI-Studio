# The platform's version -- one number for the whole product, read from the
# VERSION file at the repo root, which is the single source of truth (the
# release workflow asserts the git tag matches it).
#
# VERSION is captured ONCE, at import time, on purpose: the OTA health gate
# asks "/api/health" what version is RUNNING, and a live file read would
# answer with whatever the updater just wrote to disk while the old process
# is still serving -- passing the gate before the restart actually happened.
# A constant frozen at process start proves the new code is really up.
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read() -> str:
    try:
        with open(os.path.join(_ROOT, "VERSION"), encoding="utf-8") as f:
            v = f.read().strip()
        return v or "0.0.0"
    except OSError:
        return "0.0.0"


VERSION = _read()


def parse(v) -> tuple:
    """'v2.10.1' -> (2, 10, 1). Tolerant: missing parts read 0, junk reads
    (0, 0, 0) -- comparisons must never crash the updater."""
    s = str(v or "").strip().lstrip("vV")
    parts = []
    for piece in s.split(".")[:3]:
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate, current) -> bool:
    """True when `candidate` is strictly newer. Numeric semver compare --
    never a string compare, or '1.10.0' would read older than '1.9.0'."""
    return parse(candidate) > parse(current)
