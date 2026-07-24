"""Strategy presets — ONE base strategy (zlead) + composable TYPE modifiers.

The whole live family is `zlead`: buy the extreme favorite with a vol-normalized
lead floor, hold to settle. Everything else is a CUSTOMIZATION of zlead, expressed
as fields — never a separate hand-written strategy:

  • the entry slot (enter_lo/enter_hi)  — "plage de temps", a plain field override
  • a MAKER entry                       — type "mk"
  • a narrower band 0.85-0.90           — type "n"
  • a stricter z floor (>=1.5)          — type "x"

A deployed brain = zlead + a (possibly empty, possibly composed) set of types, plus
any per-deployment field overrides (e.g. a per-coin entry slot). So `zleadn` is just
`zlead` of type `n`; `zleadmk` is `zlead` type `mk`; they COMPOSE (`zleadnmk`). Add a
new dimension = add one TYPES entry (or a new field). Paper (engine.Engine), the real
pilot (run_live) and the dashboard (/config) all read the SAME generated Preset, so a
brain decides identically everywhere and enter_lo lives in ONE place.

THE WIDENING (2026-06-25): the base entry slot is enter_lo 0.27 / enter_hi 0.45 (entry
4-6.75 min left). The marginal-cohort backtest (objective = cumulative $/day, not
per-trade EV) showed profit/day keeps rising as the slot extends down to ~4 min left,
then the 2-3 / 1-2 min cohorts turn EV-negative and real slippage worsens; widening UP
(earlier) is strictly worse. Per-coin: eth/doge confirm 0.27, bnb wants ~0.33 (set it
with a field override), sol/xrp thin → judge live.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .coins import (COIN_BET_MAX, COIN_DEPTH, COIN_SLOTS, COIN_KEYS, DEFAULT_BET_MAX,  # noqa: F401 (re-export)
                    bet_max_for)
from .entry import EntryGate

ENTER_HI = 0.40     # early bound — TIGHTENED 0.45->0.40 (2026-06-26). The early cohort
#                     (6-6.75 min left in 15m, frac 0.40-0.45) is dead-to-negative on
#                     THREE independent datasets: backtest BTC OOS (n=14k), paper live, and
#                     REAL money (btc-15m n=197: 6-7min cohort EV/$ -0.009 vs 5-6min +0.083).
#                     A faithful prod-rule slot sim (enter_lo=0.27 fixed) peaks at enter_hi=0.40
#                     on BOTH OOS EV/$ (+0.0092->+0.0154) and cumulative $/day (+90.8->+137.4);
#                     0.38/0.36 lose volume without lifting edge. Widening UP stays strictly worse.
WIDE_LO = 0.27      # late bound — the slippage-robust profit-max slot (was 0.35 / ~6min)

# COIN_BET_MAX (measured favourite book depth @+2c = per-bet ceiling) and COIN_SLOTS
# (per-coin entry_lo override, bnb 0.33) now live in the single coin registry
# (pmlab/coins.py) and are re-exported above, so `presets.COIN_BET_MAX` etc. keep
# working for every importer. Each paper/pilot bet is min(WEIGHT_PCT × capital,
# COIN_BET_MAX[coin]) (staking.weighted_clip) — never more than the book holds. Add a coin
# = one Coin(...) line in coins.py.
WEIGHT_PCT = 0.10          # fraction of capital staked per bet (same as the real pilot)
START_CAPITAL = 100.0      # paper + backtest starting bankroll (rebased 2026-06-26)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str = ""
    tagline: str = ""
    glyph: str = "☿"
    base: str = "zlead"             # the strategy this is a customization of
    types: tuple = ()               # the composed type tags (e.g. ("n", "mk"))
    # --- entry gate: the SHARED decision (one-to-one with EntryGate) ---
    min_fav: float = 0.85           # favorite price floor — longshot premium lives >= 0.85
    max_fav: float = 0.95           # ceiling, enforced on the executable ask at fill time
    vol_cap: float | None = None    # skip storm-favorites (EWMA 1m vol gate); None = off
    min_lead_bps: float = 0.0       # established-move floor, bps of window open; 0 = off
    min_lead_z: float = 1.0         # VOL-NORMALIZED lead floor lead/(σ·√τ) — the zlead edge
    btc_align: bool = False         # BTC-align veto (alts): skip if BTC's move opposes the favorite
    ls_flow_cap: float = 0.0        # longshot-flow threshold (alts): longshot $vol/depth; veto or tilt
    ls_flow_tilt: bool = False      # True → stake-TILT on the flow signal (PnL-additive) instead of veto
    enter_lo: float = WIDE_LO       # window fraction REMAINING — LATE bound of the entry slot
    enter_hi: float = ENTER_HI      # EARLY bound. [0.27,0.45] == 4-6.75 min left in 15m
    # --- execution / sizing ---
    maker_entry: bool = False       # rest a passive bid (no fee), cross taker late if unfilled
    maker_fb_frac: float = 0.15     # cross taker once < this much of the window remains
    maker_recenter: bool = False    # COLO experiment: re-post the resting bid at the live favorite
    #                                 mid on drift (a stale bid fills only on a weakening favorite =
    #                                 adverse selection); only meaningful with maker_entry
    conviction_size: bool = False   # scale stake by extremity × excess-lead-z
    # --- deployment ---
    live: bool = False              # armable as a REAL pilot (webdash pilot dropdown reads this)

    def gate(self) -> EntryGate:
        """The shared EntryGate — the one object both paper (Engine) and real build."""
        return EntryGate(min_fav=self.min_fav, max_fav=self.max_fav, vol_cap=self.vol_cap,
                         min_lead_bps=self.min_lead_bps, min_lead_z=self.min_lead_z,
                         btc_align=self.btc_align, ls_flow_cap=self.ls_flow_cap,
                         ls_flow_tilt=self.ls_flow_tilt,
                         enter_lo=self.enter_lo, enter_hi=self.enter_hi)

    def customized(self, **overrides) -> "Preset":
        """A copy with per-deployment field overrides (e.g. enter_lo=0.33 for bnb). The
        key gets a '*' marker so the runner name still parses but reads as tuned."""
        if not overrides:
            return self
        return replace(self, **overrides)

    def config(self) -> dict:
        """Front-end-facing summary for /config: name, glyph, base+types, and the
        human-readable gate (band, lead floor, the timing slot as window fractions)."""
        return {
            "key": self.key, "label": self.label, "tagline": self.tagline,
            "glyph": self.glyph, "base": self.base, "types": list(self.types),
            "live": self.live, "min_fav": self.min_fav, "max_fav": self.max_fav,
            "vol_cap": self.vol_cap, "min_lead_bps": self.min_lead_bps,
            "min_lead_z": self.min_lead_z, "btc_align": self.btc_align,
            "ls_flow_cap": self.ls_flow_cap, "ls_flow_tilt": self.ls_flow_tilt,
            "enter_lo": self.enter_lo, "enter_hi": self.enter_hi,
            "maker_entry": self.maker_entry, "maker_recenter": self.maker_recenter,
            "conviction_size": self.conviction_size,
        }


# ----------------------------------------------------- the composable types ---
@dataclass(frozen=True)
class Type:
    """One dimension of customization on the base zlead, as a field delta."""
    desc: str
    fields: dict = field(default_factory=dict)


# Each TYPE tweaks specific gate fields. Compose freely: zlead('n','mk') = narrow maker.
# Add a dimension = add an entry here (its tag becomes part of the brain key).
TYPES: dict[str, Type] = {
    "n":  Type("bande resserrée 0,85–0,90 (la prime se concentre là ; favoris 0,90+ morts)",
               {"max_fav": 0.90}),
    "x":  Type("plancher de lead renforcé z≥1,5 (moins de paris, plus sûrs)",
               {"min_lead_z": 1.5}),
    "mk": Type("entrée maker : bid passif sans frais, traversée taker tardive si non rempli",
               {"maker_entry": True}),
    "a":  Type("veto BTC-align (alts) — DÉSORMAIS LE DÉFAUT de base zlead (autopsie pertes réelles "
               "2026-06-28 : opposé 20% de pertes vs aligné 7%) ; type conservé comme alias explicite",
               {"btc_align": True}),
    "f":  Type("veto flux-longshot (alts) : skip si l'accumulation longshot pré-entrée / profondeur "
               "≥ 2,5 (fade informé → favori plus à risque ; calibré 5m+15m, ADDITIF au type a, no-op btc)",
               {"ls_flow_cap": 2.5}),
    "p":  Type("FLAGSHIP optimal (alts) : veto BTC-align + TILT de mise flux-longshot (mise + sur les "
               "fenêtres propres, − sur les sales, exposition ≈ constante). Le meilleur config MESURÉ "
               "(90j : +~16% net vs base, à risque égal). no-op sur btc.",
               {"btc_align": True, "ls_flow_cap": 2.5, "ls_flow_tilt": True}),
}

_BASE_TAGLINE = ("Favori extrême + plancher de lead vol-normalisé (z≥1), fenêtre d'entrée "
                 "élargie (4–6,75 min) pour maximiser le PnL cumulé du jour.")


def zlead(*types: str, live: bool = False, **overrides) -> Preset:
    """Build a zlead preset: the base + composable TYPE modifiers + field overrides.
      zlead()             -> the flagship (band 0.85-0.95, z≥1, slot 0.27-0.45, taker)
      zlead('n')          -> zlead type n  (narrow band)         key 'zleadn'
      zlead('mk')         -> zlead type mk (maker)               key 'zleadmk'
      zlead('n','mk')     -> narrow maker                        key 'zleadnmk'
      zlead(enter_lo=.33) -> custom entry slot (e.g. bnb)        key 'zlead'
    """
    # GUARDRAIL (2026-06-28, from the real-money loss autopsy research/loss_enrich.py): the
    # BTC-align veto is now the BASE default for every zlead variant. On an ALT, a favorite the
    # book likes while BTC's move OPPOSES it lost 20% of the time on real money (n=30) vs 7% when
    # BTC aligned — and it held OOS on the 90d deep backtest. It's a no-op on BTC itself (runner
    # passes btc_lead=None when not an alt), rare (~6% of alt entries), and cuts a −EV tail, so it
    # protects the alt pilots (esp. plain zlead-eth-15m) without touching BTC or the core edge.
    # The 'a' type (and 'p') still set it explicitly — now redundant with the base, kept as aliases.
    fields: dict = {"btc_align": True}
    tags: list[str] = []
    for t in types:
        if t not in TYPES:
            raise KeyError(f"unknown zlead type {t!r}; known: {list(TYPES)}")
        fields.update(TYPES[t].fields)
        tags.append(t)
    fields.update(overrides)
    key = "zlead" + "".join(tags)
    label = "☿ Zlead" + ("·" + "·".join(tags) if tags else "")
    extra = " ".join(f"+ {TYPES[t].desc}." for t in tags)
    return Preset(key=key, label=label, tagline=(_BASE_TAGLINE + (" " + extra if extra else "")),
                  base="zlead", types=tuple(tags), live=live, **fields)


# ===================================================================== ACTIVE ===
# The live family — ALL generated from the base zlead + types. Add a variant = one
# zlead(...) call. The two armable survivors carry live=True.
_ACTIVE = [
    zlead(live=True),          # flagship — zlead for every coin
    zlead("mk", live=True),    # maker twin (same gate, execution-only forward-test)
    zlead("x"),                # stricter z floor — paper forward-test
    zlead("n"),                # narrow band — paper forward-test
    zlead("a"),                # BTC-align veto (alts) — paper forward-test (90d-OOS lead)
    zlead("f"),                # longshot-flow fade veto (alts) — paper forward-test (90d-OOS, additive)
    zlead("a", "f"),           # COMBO a+f veto — the cleanest alt subset (90d: 4.2% loss / +9.0 edge)
    zlead("p", live=True),     # FLAGSHIP — veto BTC-align + flow-TILT sizing; the measured-best, ARMABLE
]

# ==================================================================== ARCHIVE ===
# Falsified non-zlead ancestors (research/FINDINGS.md). Kept constructible so research/
# backtests can re-run them; NOT deployed, NOT armable, NOT in the dashboard lineup.
# NOTE HISTORIQUE : ces stratégies falsifiées portaient des noms de code alchimiques
# (favorite, favorite_vol, favorite_lead, favorite_vollead, favorite_cheap, favorite_conviction…) — renommés en clair pour la
# publication ; le mapping est documenté dans research/FINDINGS.md.
_ARCHIVE = [
    Preset("favorite", "Favori nu", "Favori extrême nu (0.85-0.95), hold. DEAD multi-mois.", "F",
           base="favorite", min_lead_z=0.0, enter_lo=0.35),
    Preset("favorite_vol", "Favori + filtre vol", "Favori nu + filtre volatilité. DEAD.", "F",
           base="favorite_vol", min_lead_z=0.0, vol_cap=0.00056, enter_lo=0.35),
    Preset("favorite_wide", "Favori élargi", "Favori + vol + plancher 0.80.", "F",
           base="favorite_wide", min_lead_z=0.0, min_fav=0.80, vol_cap=0.00056, enter_lo=0.35),
    Preset("favorite_lead", "Favori + lead bps", "Favori + plancher de lead 6bps. ~zéro.", "F",
           base="favorite_lead", min_lead_z=0.0, min_lead_bps=6.0, enter_lo=0.35),
    Preset("favorite_vollead", "Favori vol+lead", "Favori + lead 6bps + filtre vol.", "F",
           base="favorite_vollead", min_lead_z=0.0, vol_cap=0.00056, min_lead_bps=6.0, enter_lo=0.35),
    Preset("favorite_cheap", "Favori le moins cher", "Favori le moins cher 0.85-0.88. FRAGILE.", "F",
           base="favorite_cheap", min_lead_z=0.0, max_fav=0.88, enter_lo=0.35),
    Preset("favorite_conviction", "Favori mise-conviction", "Favori vol+lead + mise par conviction.", "F",
           base="favorite_conviction", min_lead_z=0.0, vol_cap=0.00056, min_lead_bps=6.0,
           conviction_size=True, enter_lo=0.35),
    Preset("favorite_mk", "Favori maker", "Favori nu + entrée maker.", "F",
           base="favorite_mk", min_lead_z=0.0, maker_entry=True, enter_lo=0.35),
    Preset("favorite_vollead_mk", "Favori vol+lead maker", "Favori vol+lead + entrée maker.", "F",
           base="favorite_vollead_mk", min_lead_z=0.0, vol_cap=0.00056, min_lead_bps=6.0,
           maker_entry=True, enter_lo=0.35),
]

PRESETS: dict[str, Preset] = {p.key: p for p in _ACTIVE}
ARCHIVE: dict[str, Preset] = {p.key: p for p in _ARCHIVE}
ALL_PRESETS: dict[str, Preset] = {**PRESETS, **ARCHIVE}

# Armable as REAL pilots = every ACTIVE zlead variant (the dashboard pilot dropdown
# reads this). ARCHIVE (dead non-zlead brains) stays excluded. The `live` field marks
# the audited flagships (zlead/zleadmk) for a badge, but all four can be armed/customized.
LIVE_STRATEGIES: list[str] = list(PRESETS)


# ---------------------------------------------- CLI resolution (paper + real) ---
# Both main.py (paper) and run_live.py (real) build their preset through THESE, so the
# customization interface (compose types, override fields per deployment) is identical
# on both paths — no drift, the [[always-modularize]] rule.
def add_preset_args(p) -> None:
    """Add the zlead customization flags to an argparse parser."""
    p.add_argument("--type", default="", metavar="TAGS",
                   help="compose zlead types ad-hoc, comma-sep (e.g. n,mk) — overrides --strategy")
    p.add_argument("--enter-lo", type=float, default=None,
                   help="entry slot LATE bound, window fraction remaining (e.g. 0.33 for bnb)")
    p.add_argument("--enter-hi", type=float, default=None, help="entry slot EARLY bound")
    p.add_argument("--max-fav", type=float, default=None, help="favorite price ceiling (n = 0.90)")
    p.add_argument("--min-lead-z", type=float, default=None, help="vol-normalized lead floor (x = 1.5)")
    p.add_argument("--maker", action="store_true", help="maker entry (mk)")
    p.add_argument("--recenter", action="store_true",
                   help="maker re-centering (colo): re-post the resting bid at the live favorite mid on drift")


def preset_from_args(strategy: str, a) -> Preset:
    """Resolve a deployment to a Preset: a named key (zlead/zleadn/…) or base zlead +
    ad-hoc --type tags, then per-deployment field overrides (--enter-lo, --maker, …)."""
    tags = [t.strip() for t in a.type.split(",") if t.strip()] if getattr(a, "type", "") else None
    base = zlead(*tags) if tags else ALL_PRESETS[strategy]
    ov: dict = {}
    for fld in ("enter_lo", "enter_hi", "max_fav", "min_lead_z"):
        v = getattr(a, fld, None)
        if v is not None:
            ov[fld] = v
    if getattr(a, "maker", False):
        ov["maker_entry"] = True
    if getattr(a, "recenter", False):           # colo: re-centering implies a maker bid to track
        ov["maker_entry"] = True
        ov["maker_recenter"] = True
    # per-coin entry-slot default (COIN_SLOTS), unless --enter-lo was given explicitly
    coin = getattr(a, "underlying", None)
    if "enter_lo" not in ov and coin in COIN_SLOTS:
        ov["enter_lo"] = COIN_SLOTS[coin]
    return base.customized(**ov)
