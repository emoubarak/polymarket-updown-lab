"""Journal-reading primitives shared by every consumer of a runner's journal.csv.

ONE definition of "what is an entry / a close / a win / which window" so the
dashboard's per-strategy metrics (webdash.live_metrics) and the cross-strategy
correlation matrix (correlation.py) can never drift on it (cf. always-modularize:
the entry gate already burned us by living in two code paths). Pure, stdlib-only.

Journal kinds (paper.py / events / scalp):
  BUY[tag] / BUY+[tag]  taker entry (and top-up toward target stake)
  REST_BUY[tag]         a maker bid only PLACED — no cash moved, NOT an entry
  FILL_BUY[tag]         that resting bid actually filled — an entry
  SETTLE_WIN/LOSS       outcome-bet resolution
  WIN / LOSS            legacy settle alias
  SELL / FILL_SELL      a scalp round-trip exit (taker bail / maker revert) — a close
"""

from __future__ import annotations

from pathlib import Path

# The journal.csv SCHEMA — ONE column list so every writer (paper.MultiBroker, events,
# copy-mirror) and reader agrees and can't drift.
JOURNAL_COLUMNS = ("ts", "kind", "slug", "direction", "shares", "price", "pnl", "fee", "cash")
JOURNAL_HEADER = ",".join(JOURNAL_COLUMNS)


def append_row(path, row: dict) -> None:
    """Append one journal row from a {column: value} dict (missing keys → blank), writing the
    header first if the file is new. The shared dict-writer for the events + copy-mirror
    engines (paper.MultiBroker keeps its own csv.writer for float-precision formatting)."""
    new = not Path(path).exists()
    with open(path, "a") as fh:
        if new:
            fh.write(JOURNAL_HEADER + "\n")
        fh.write(",".join(str(row.get(k, "")) for k in JOURNAL_COLUMNS) + "\n")


def is_entry(kind: str) -> bool:
    """A row that OPENED or added exposure: a taker buy or a FILLED resting maker
    bid. A bare REST_BUY is only a placed order (moves no cash) and must NOT count."""
    return kind.startswith("BUY") or kind.startswith("FILL_BUY")


def is_settle(kind: str) -> bool:
    """An outcome-bet settlement (the window resolved Up/Down)."""
    return kind.startswith("SETTLE") or kind in ("WIN", "LOSS")


def is_close(kind: str) -> bool:
    """A row that REALIZED P&L: an outcome-bet settlement OR a scalp round-trip
    exit (taker SELL / maker FILL_SELL). The scalp never settles — it exits
    intra-window — so its sells must count as closes or its money is invisible."""
    return is_settle(kind) or kind.startswith("SELL") or kind.startswith("FILL_SELL")


def won(kind: str, pnl: float) -> bool:
    """An outcome bet is won by its kind; a scalp round-trip is won iff it realized
    a positive P&L (the right tripwire for a revert harvester)."""
    return kind.endswith("WIN") if is_settle(kind) else (pnl > 0)


def window_ts(slug: str) -> int | None:
    """The window-start unix ts encoded in a deterministic crypto slug
    ({coin}-updown-{frame}-{ts}). Coin-AGNOSTIC by construction, so btc-updown-15m-T
    and eth-updown-15m-T return the same T — that's what lets same-frame coins align
    in the correlation matrix. None if the slug carries no trailing timestamp (e.g.
    an event market), so the caller can skip it rather than mis-bucket."""
    if not slug:
        return None
    tail = slug.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else None


def window_pnl(trades: list) -> dict:
    """Realized P&L per RESOLVED window, keyed by window-start ts (from the slug).

    Sums every close in a window: a top-up settles once, but a scalp may round-trip
    several times in one window — all of it is that window's realized money. Open
    (unsettled) positions contribute nothing, which is correct: the matrix correlates
    only windows that actually paid out."""
    out: dict[int, float] = {}
    for t in trades:
        kind = t.get("kind", "")
        if not is_close(kind):
            continue
        ts = window_ts(t.get("slug", ""))
        if ts is None:
            continue
        try:
            pnl = float(t.get("pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        out[ts] = out.get(ts, 0.0) + pnl
    return out
