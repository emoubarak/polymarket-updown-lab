"""Cross-strategy correlation — how many INDEPENDENT bets the lineup really is, with
SIMULTANEOUS LOSSES as the primary lens.

The "strategy ETF" question: a row of runners can look like many bets while loading
on ONE risk factor. And what actually ruins such a book is its asymmetric payoff — a
favorite pays ~+0.11 to win but −1.00 to lose, so a CLUSTER of losses in the same window
hurts far more than the wins help. Full-P&L Pearson treats upside and downside the same
and hides that. So the lens that drives the ETF decision here is the **simultaneous-loss
correlation**: do two runners tend to lose in the SAME window?

Method — align by RESOLVED WINDOW, then two lenses over the overlap:
  • Each runner's realized P&L is bucketed per window by window-start ts
    (journal.window_pnl). The ts is coin-agnostic, so same-frame coins share the grid
    (btc-15m & eth-15m align on every 15m boundary; a 15m ts is also a 5m ts → 5m/15m
    overlap on 1/3 of windows). A window is a LOSS for a runner iff its net P&L there < 0.
  • LOSS (primary): phi = Pearson of the two 0/1 loss-indicators over the windows BOTH
    bet — the simultaneous-loss correlation. Reported with the raw co-loss counts (how
    often each lost, how often BOTH lost) + a lift vs independence, so the coefficient is
    never read in a vacuum. Favorites win ~90%, so losses are RARE: phi needs both enough
    overlap AND enough losses each side (MIN_LOSSES) or it's noise → None.
  • P&L (secondary): plain Pearson of the per-window P&L — the full co-movement, kept as a
    toggle in the UI.
  • STRUCTURAL (the leading indicator): the realized loss-phi fills SLOWLY — favorites win
    ~90 %, so a co-loss pair needs months to clear MIN_LOSSES. But the simultaneous-flip
    risk is DRIVEN by the underlyings' spot co-movement: when the market dumps in a window,
    correlated coins reverse together and their favorites flip together. That co-movement is
    measurable NOW from spot, with no need for losses to accumulate. So we also lift a
    coin×coin spot-return correlation (computed upstream — see webdash._coin_spot_corr) onto
    the strat grid: two strats on the SAME coin are one bet by construction (struct = 1.0,
    the conservative default for risk); two strats on different coins inherit those coins'
    spot rho. It is an HONEST PROXY — labelled as spot co-movement, an upper bound on the
    co-loss risk — not a substitute for the realized phi once that fills.
  • Redundancy + the effective number of independent groups are driven by LOSS phi (realized)
    AND, separately, by the STRUCTURAL spot rho (immediate). Two runners that crater together
    — or load on the same / a tightly-correlated underlying — are the duplicate risk you must
    not double-arm.

Pure, stdlib-only (no numpy) — paper stays dependency-free. The spot correlation is passed
IN (coin_of / coin_corr); this module never touches the network.
"""

from __future__ import annotations

import math

from .journal import window_pnl

# A correlation needs enough paired windows to mean anything; below this we report the
# overlap count but not a coefficient (honest small-n, like the win-rate tripwire).
MIN_OVERLAP = 10
# Loss phi additionally needs enough LOSSES on each side — favorites win ~90%, so a phi
# computed off 1-2 losses is pure noise. Thin even at 5; the co-loss counts are shown raw.
MIN_LOSSES = 5
# At/above this |phi| two runners lose together enough to be one bet — don't arm both.
REDUNDANT_RHO = 0.7


def pearson(xs: list, ys: list) -> float | None:
    """Pearson correlation of two equal-length series, or None when undefined: too few
    points, or one series is constant (no variance to correlate). Used for both the P&L
    co-movement and — on 0/1 loss indicators — the simultaneous-loss phi coefficient."""
    n = len(xs)
    if n < MIN_OVERLAP:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / (sx * sy)


def loss_stats(xs: list, ys: list) -> dict:
    """Simultaneous-loss stats for two aligned per-window P&L series (a window is a loss
    iff its net P&L < 0). Returns the phi correlation of the loss indicators plus the raw
    counts: losses each side (la/lb), windows BOTH lost (both), and the lift of co-losses
    over what independence would predict (>1 = losses cluster = dangerous for the book)."""
    la_ind = [1 if x < 0 else 0 for x in xs]
    lb_ind = [1 if y < 0 else 0 for y in ys]
    la, lb = sum(la_ind), sum(lb_ind)
    both = sum(1 for p, q in zip(la_ind, lb_ind) if p and q)
    n = len(xs)
    phi = (pearson(la_ind, lb_ind)
           if (n >= MIN_OVERLAP and la >= MIN_LOSSES and lb >= MIN_LOSSES) else None)
    exp = (la / n) * (lb / n) * n if n else 0.0   # co-losses expected if independent
    lift = (both / exp) if exp > 0 else None
    return {"loss": phi, "la": la, "lb": lb, "both": both, "lift": lift}


def _independent_groups(names: list, pairs: list) -> int:
    """Effective number of independent bets = connected components once every redundant
    pair (|phi| >= REDUNDANT_RHO, i.e. they LOSE together) is merged. Union-find; a runner
    with no redundant partner stays a singleton (the conservative, honest default — no
    measured co-loss means treat it as its own bet)."""
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    return len({find(n) for n in names})


def _struct_rho(a, b, coin_of, coin_corr):
    """Structural spot co-movement between two strats: 1.0 if they bet the SAME coin (one
    bet by construction — the conservative risk default), else their coins' spot-return
    Pearson (or None if not measurable / no coin data). Symmetric; diagonal handled by the
    caller. coin_of = {name: coin}; coin_corr = {coin: {coin: rho}} (see webdash)."""
    if not coin_of:
        return None
    ca, cb = coin_of.get(a), coin_of.get(b)
    if not ca or not cb:
        return None
    if ca == cb:
        return 1.0
    return (coin_corr or {}).get(ca, {}).get(cb)


def correlation_matrix(runners: dict, coin_of: dict | None = None,
                       coin_corr: dict | None = None) -> dict:
    """Build the live correlation payload from {name: trades}.

    coin_of / coin_corr (optional) enable the STRUCTURAL spot lens: coin_of maps each
    runner to its underlying (e.g. 'zlead-btc-15m' -> 'btc'); coin_corr is the precomputed
    coin×coin spot-return correlation {coin: {coin: rho}} (webdash._coin_spot_corr). Absent
    (the default, e.g. unit tests), every struct field is None and the payload is unchanged.

    Returns:
      names    runner names, in input order (rows == cols)
      windows  per-runner count of resolved windows (the diagonal's n)
      losses   per-runner count of LOSING windows (the simultaneous-loss denominator)
      matrix   names×names of {rho, loss, struct, n, la, lb, both, lift}: rho = full-P&L
               Pearson (secondary), loss = simultaneous-loss phi (primary), struct = spot
               co-movement (structural, immediate), n = overlap, la/lb = losses each side,
               both = windows both lost. rho/loss/struct None when undefined.
      redundant  [{a, b, loss, both, la, lb, n}] pairs with |phi| >= REDUNDANT_RHO,
                 |phi| desc — the "lose together, don't double-arm" list.
      n_strats, n_groups   lineup size + effective independent groups (LOSS-driven).
      avg_abs_loss, n_pairs_loss   simultaneous-loss summary (primary).
      avg_abs_rho, n_pairs         full-P&L summary (secondary toggle).
      struct_groups        effective risk factors after merging same-coin / spot-correlated
                           strats (STRUCTURAL-driven; None when no coin data). The sobering
                           count: how few independent bets the lineup REALLY is, available now.
      avg_abs_struct, n_pairs_struct   structural summary over CROSS-coin pairs (same-coin
                           1.0s excluded — this is the spot beta BETWEEN distinct assets).
      min_overlap, min_losses, redundant_rho   thresholds, so the UI can explain itself.
    """
    series = {name: window_pnl(trades) for name, trades in runners.items()}
    names = list(series)
    losses = {n: sum(1 for v in series[n].values() if v < 0) for n in names}

    matrix = []
    redundant = []
    red_pairs = []
    struct_pairs = []
    abs_loss_sum = abs_rho_sum = abs_struct_sum = 0.0
    n_pairs_loss = n_pairs = n_pairs_struct = 0
    for i, a in enumerate(names):
        row = []
        for j, b in enumerate(names):
            if i == j:
                row.append({"rho": 1.0, "loss": 1.0, "struct": 1.0, "n": len(series[a]),
                            "la": losses[a], "lb": losses[a], "both": losses[a], "lift": None})
                continue
            common = sorted(set(series[a]) & set(series[b]))
            xs = [series[a][t] for t in common]
            ys = [series[b][t] for t in common]
            rho = pearson(xs, ys)
            ls = loss_stats(xs, ys)
            struct = _struct_rho(a, b, coin_of, coin_corr)
            row.append({"rho": rho, "struct": struct, "n": len(common), **ls})
            if j > i:                              # each unordered pair once (upper triangle)
                if rho is not None:
                    abs_rho_sum += abs(rho)
                    n_pairs += 1
                if ls["loss"] is not None:
                    abs_loss_sum += abs(ls["loss"])
                    n_pairs_loss += 1
                    if abs(ls["loss"]) >= REDUNDANT_RHO:
                        redundant.append({"a": a, "b": b, "loss": ls["loss"], "both": ls["both"],
                                          "la": ls["la"], "lb": ls["lb"], "n": len(common)})
                        red_pairs.append((a, b))
                if struct is not None:
                    same_coin = coin_of and coin_of.get(a) == coin_of.get(b)
                    if not same_coin:              # the spot beta BETWEEN distinct assets
                        abs_struct_sum += abs(struct)
                        n_pairs_struct += 1
                    if abs(struct) >= REDUNDANT_RHO:   # same-coin (1.0) or tightly co-moving
                        struct_pairs.append((a, b))
        matrix.append(row)

    redundant.sort(key=lambda r: -abs(r["loss"]))
    has_struct = coin_of is not None and coin_corr is not None
    return {
        "names": names,
        "windows": {n: len(series[n]) for n in names},
        "losses": losses,
        "matrix": matrix,
        "redundant": redundant,
        "n_strats": len(names),
        "n_groups": _independent_groups(names, red_pairs),
        "avg_abs_loss": (abs_loss_sum / n_pairs_loss) if n_pairs_loss else None,
        "n_pairs_loss": n_pairs_loss,
        "avg_abs_rho": (abs_rho_sum / n_pairs) if n_pairs else None,
        "n_pairs": n_pairs,
        "struct_groups": _independent_groups(names, struct_pairs) if has_struct else None,
        "avg_abs_struct": (abs_struct_sum / n_pairs_struct) if n_pairs_struct else None,
        "n_pairs_struct": n_pairs_struct,
        "min_overlap": MIN_OVERLAP,
        "min_losses": MIN_LOSSES,
        "redundant_rho": REDUNDANT_RHO,
    }
