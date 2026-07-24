#!/usr/bin/env python3
"""Live web dashboard for pmlab paper trading — stdlib only.

One tab per strategy plus an overview tab. Each strategy maps to a state
dir and a log file:

    python3 webdash.py --port 8420 \
        --strat favorite-15m:state_rubedo:favorite.log \
        --strat favorite_vol-15m:state_favorite_vol:favorite_vol.log

Built for the OPERATOR (the person running the race): light neo-brutalist
theme, LIVE paper-trading results first, simulation-vs-real and the explainer
below. Backtest result shown in DOLLARS. Jargon is kept — the reader knows it.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ----------------------------------------------------------- live pilot ---
# The real-money pilot. Several can run IN PARALLEL now (one run_live.py process
# per config), each in its OWN state dir/log, tracked in pilot_registry.json.
# Launched/stopped from the dashboard; armed only when POLY_LIVE is passed to the
# child (the key must be in the dashboard's OWN env, never in code). NOTE: all
# armed pilots share the one wallet (POLY_PRIVATE_KEY), so each reads the FULL
# pUSD balance as "its" capital — parallel real pilots double-count the wallet
# (accounting is approximate by design; the UI warns). DRY-RUN pilots are fully
# isolated (each carries its own model bankroll).
PILOT_REGISTRY = "pilot_registry.json"
# Legacy single-pilot files, migrated into the registry on first read (the VPS
# carries a seasoned real run in live_state/ — preserve it, never reset).
PILOT_DIR = "live_state"
PILOT_STATUS = "pilot_status.json"
PILOT_LOG = "live_pilot.log"
# Serialize every registry read-modify-write: the ThreadingHTTPServer runs each request
# in its own thread AND the pilot-supervisor runs in a daemon thread, so a supervisor
# revive must never interleave with a user Stop and resurrect a paused REAL pilot.
_REGISTRY_LOCK = threading.RLock()
# Distributed control plane (the AWS/Morocco split): this dashboard either OWNS the pilots
# (--pilot-api: runs the supervisor + arms locally — the AWS engine, sole holder of the key)
# or PROXIES the pilot tab to the owner (--pilot-remote URL: the Morocco display dashboard
# forwards /pilot-data + POST /pilot to AWS over the private channel). The key lives only on
# the owner ⇒ only the owner can arm ⇒ the proxy host can NEVER double-run.
PILOT_API = False
PILOT_REMOTE = None
_PILOT_REMOTE_CACHE = {"body": None, "ts": 0.0}
# ----------------------------------------------------------- rewards-MM ---
# The std0 liquidity-rewards harvester (pmlab.rewardmm), a SEPARATE control plane
# from the zlead pilots: a different engine (run_rewardmm.py), a different telemetry shape
# (two-sided inventory Up/Down, maker fills, rebate, marked P&L vs cash) and — crucially —
# kept apart so it can NEVER touch the armed zlead pilot registry. Same two-host model as
# the pilots: the AWS engine OWNS it (--pilot-api → supervises + arms), the Morocco display
# PROXIES /mm-data + POST /mm. Same shared real wallet ⇒ real arming gated by POLY_PRIVATE_KEY.
MM_REGISTRY = "mm_registry.json"
_MM_REMOTE_CACHE = {"body": None, "ts": 0.0}
# Strategies the pilot can arm + the full preset table — MUTUALIZED from
# pmlab.presets (the single source of truth), so the dropdown only ever offers
# the survivors (a dead brain can't be armed) and /config can serve each gate's params
# (band, lead floor, entry slot) to the front-end with no parallel hand-kept list.
from pmlab.presets import (LIVE_STRATEGIES as FAMILY, ALL_PRESETS as _PRESETS,
                                   COIN_SLOTS as _COIN_SLOTS, COIN_BET_MAX as _COIN_BET_MAX,
                                   COIN_DEPTH as _COIN_DEPTH, bet_max_for,
                                   WEIGHT_PCT as _WEIGHT_PCT, START_CAPITAL as _START_CAPITAL)
# Journal classification (entry/close/win) + the cross-strategy correlation matrix
# live in their own modules — the SINGLE definition both this file and the matrix read
# (cf. always-modularize).
from pmlab.journal import is_entry, is_close, won as won_of
from pmlab.correlation import correlation_matrix, pearson as _pearson, MIN_OVERLAP as _MIN_OVERLAP
from pmlab import feeds
from pmlab.coins import COIN_KEYS as _COIN_KEYS, SYMBOL as _SYMBOL

STRATS: dict[str, dict] = {}        # crypto runners (the favorite race) -> {state_dir, log}
EVENT_STRATS: dict[str, dict] = {}  # event-market harvesters -> {state_dir, log}
COPY_STRATS: dict[str, dict] = {}   # wallet copy-mirror runners -> {state_dir, log}

# Legacy flat reference stake (fallback for the front-end when a coin has no measured
# cap). Since 2026-06-26 the paper runners size WEIGHTED (WEIGHT_PCT of capital, capped
# at COIN_BET_MAX[coin]) from a $100 START — the dashboard reads those per-coin numbers
# from /config and the recomputed states/curves; this flat value is just the fallback.
STAKE_REF = 25.0

# One-paragraph TECHNICAL summary per brain (base name; the JS strips any -5m/
# -15m frame suffix to find it). Tucked into "détails techniques". Keep in sync
# with pmlab/engine.py.
DESCRIPTIONS = {
    "favorite":
        "EDGE VALIDÉ HORS-ÉCHANTILLON — favori-longshot sur 15 m. La foule "
        "surpaie le côté quasi-mort (longshot) ; on achète le favori déjà extrême "
        "(prix 0,85–0,95) à ~6 min de la fin, frais taker, portage au règlement, "
        "AUCUNE sortie. C'est un pari sur la calibration des PRIX, pas sur la "
        "direction de BTC. Payoff brutalement asymétrique (gain +~0,11 / perte "
        "−1,00, break-even = le prix lui-même) → un cluster de flips fait mal. "
        "Tripwire : si le win réalisé colle au prix d'entrée sur ~150 trades, "
        "l'edge n'est pas réel et on retire (la mort de coagula).",
    "favorite_vol":
        "RUBEDO + FILTRE DE VOLATILITÉ. Le favori en haute vol est un "
        "« favori-tempête » sur le point de basculer — l'angle mort que le prix "
        "seul ne montre pas. On saute les fenêtres dont la vol BTC 1 m dépasse "
        "~0,00056 (66ᵉ pct). Le filtre AJOUTE de l'edge, ce n'est pas qu'un "
        "garde-fou.",
    "favorite_wide":
        "RUBEDO + FILTRE VOL + PLANCHER ABAISSÉ À 0,80. Plus de fenêtres "
        "qualifient (volume), au prix d'un edge par trade plus faible "
        "(OOS +0,018/$). Le pari : la prime favori existe encore à 0,80–0,85.",
    "favorite_lead":
        "RUBEDO + PLANCHER DE LEAD. L'autopsie live (20/06) : les pertes de "
        "favorite se concentrent sur les favoris MOUS — prix 0,85–0,91 au carnet "
        "mais BTC à peine bougé à l'entrée (12–56 $ de mouvement), des quasi-pile-"
        "ou-face que le carnet surcote (la zone 2–6 bps ne gagne que 0,92 vs ~0,97 "
        "au-delà). On exige que le mouvement du favori soit ÉTABLI : lead ≥ 6 bps "
        "de l'open de fenêtre, du bon côté. RECKONING 40 j (20/06) : sur BTC le "
        "plancher n'ajoute presque rien au favori nu (OOS +0,003/$) — la fenêtre "
        "10 j était favorable. MAIS le plancher se VALIDE hors-échantillon sur "
        "ETH (underlying indépendant où le favori nu est MORT) : +0,042/$ — preuve "
        "que le mécanisme est réel, pas un artefact BTC. Et il RESSUSCITE le 5m "
        "(OOS +0,060/$, ~3× le volume). Le filtre tient ; c'est le favori nu qui "
        "est mince.",
    "zlead":
        "FAVORI EXTRÊME À SEUIL DE LEAD VOL-NORMALISÉ (z-score). Au lieu d'un "
        "plancher de lead fixe en bps, on exige que le mouvement soit grand "
        "RELATIVEMENT à la vol (lead / (σ·√τ) ≥ 1) — le SEUL edge qui survit à "
        "l'audit multi-mois (tout ce qui est prix-seul est mort) et qui généralise "
        "cross-actifs (BTC/ETH robustes, alts en forward-test). FENÊTRE D'ENTRÉE "
        "ÉLARGIE 2026-06-25 : enter_lo 0,35 → 0,27 (entrée 4–6,75 min restantes au "
        "lieu de 5,25–6,75). Le backtest marginal (objectif = cumul $/jour, pas "
        "EV/pari) montre que le profit cumulé monte jusqu'à ~4 min restantes puis "
        "RETOMBE : les cohortes 2-3 et 1-2 min sont EV-négatives et le slippage réel "
        "s'aggrave tard. 0,27 est le bound robuste au slippage qui maximise le PnL "
        "du jour ; élargir vers le HAUT (entrées plus tôt) est strictement pire. "
        "Tous les paramètres vivent dans pmlab/presets.py (un preset = une "
        "stratégie, partagé par le paper, le pilote réel et ce dashboard).",
    "zleadx":
        "FIXATIOZ À SEUIL z RENFORCÉ (z≥1.5 au lieu de 1.0). Le sweep profond montre "
        "que monter le seuil bat z≥1.0 en EV/$ ET en stabilité jour partout, surtout "
        "en 5m : btc-5m +0.039 / eth-5m +0.043, jours+ 90% (vs ~85% à z≥1.0). Moins "
        "de paris (gate plus strict). Forward-test pour confirmer le gain net en live.",
    "zleadn":
        "FIXATIOZ À BANDE RESSERRÉE 0,85–0,90. Même moteur que zlead (favori extrême + "
        "plancher de lead vol-normalisé z≥1, fenêtre d'entrée élargie 4–6,75 min), mais "
        "le plafond de favori descend de 0,95 à 0,90 : le sweep de bande par coin montre "
        "que la prime favori-longshot se CONCENTRE sur le favori 0,85–0,90 — les favoris "
        "profonds 0,90+ sont morts (on surpaie un côté quasi déjà réglé). La bande "
        "0,85–0,90 bat 0,85–0,95 hors-échantillon sur BTC comme sur ETH en 15m. Même "
        "plancher de lead, bande plus étroite → moins de paris mais mieux ciblés. "
        "Forward-test paper.",
    "favorite_cheap":
        "RETIRÉ 20/06 — bande resserrée 0,85–0,88. Semblait le plus rentable "
        "(10 j : +0,121/$) mais c'était un artefact de 64 échantillons : sur 40 j "
        "il est MORT (OOS −0,007/$, win = prix) ET mort sur ETH (−0,008). La "
        "preuve vivante que le tripwire à grand n est le seul juge — la mort de "
        "coagula, attrapée avant de saigner.",
    "favorite_vollead":
        "FIXATIO + FILTRE VOL — l'union des deux angles morts du moissonneur par "
        "le prix seul : le favori-tempête (haute vol, sur le point de basculer) "
        "ET le favori mou (mouvement non établi). Meilleure EV/$ OOS du backtest, "
        "au prix d'un volume moindre. La conjonction des deux purifications.",
    "favorite_conviction":
        "Mêmes paris que favorite_vollead (favori net, Bitcoin déjà engagé, marché "
        "calme), mais la MISE varie : plus grosse quand le pari est plus sûr "
        "(favori moins cher = prime plus grasse, mouvement bien établi) et plus "
        "petite sinon. Le sizing est backtesté ; il améliore la courbe en dollars "
        "sans changer l'avantage par dollar de favorite_vollead.",
    "favorite_mk":
        "RUBEDO À ENTRÉE MAKER. Au lieu de traverser l'ask (taker : haircut 2 ¢ + "
        "frais), pose un bid passif au prix du favori ; le carnet le remplit (0,7 "
        "de la taille, sans frais) quand le marché traverse, sinon on croise en "
        "taker tard dans la fenêtre — ainsi on ne RATE jamais un favori parti tout "
        "droit (côté raté = 100 % de gagnantes, le piège du maker pur). Backtest "
        "26/06 : per-$ EV bat le jumeau taker dans toutes les configs, y compris la "
        "tranche OOS 15m ; pire-cas = taker. Forward-test paper — la magnitude n'est "
        "pas validable OOS sur la tape en cache (régime sans perdant en 15m, IS pur "
        "en 5m). research/backtest_maker_entry.py.",
    "favorite_vollead_mk":
        "CONIUNCTIO À ENTRÉE MAKER. Les gates validés (favori net + lead 6 bps + "
        "filtre vol) avec la même entrée maker+fallback que favorite_mk. Teste le gain "
        "d'exécution (~2,5 ¢/$) sur l'edge réellement déployé. Le gate sélectif "
        "capte un peu MOINS du gain maker (favoris très établis = peu de wobble "
        "sous le prix d'entrée → moins de fills passifs), mais reste ≥ taker.",
    "zleadmk":
        "FIXATIOZ À ENTRÉE MAKER. Le seuil de lead vol-normalisé (z ≥ 1, le "
        "généralisateur cross-actif qui pilote la course) avec l'entrée maker+"
        "fallback de favorite_mk : bid passif au prix du favori (0,7 de la taille, "
        "sans frais), traversée taker tard dans la fenêtre si non rempli — jamais "
        "rater un favori parti tout droit. Forward-test paper sur BTC+ETH du gain "
        "d'exécution (~2,5 ¢/$) sur l'edge déployé ; jumeau taker = zlead. La "
        "magnitude n'est pas validable OOS sur la tape en cache (FINDINGS) — le "
        "forward-test réglé à l'oracle est le juge. research/backtest_maker_entry.py.",
    "scalp":
        "PROVISION DE LIQUIDITÉ — pas un pari sur l'issue, un pari sur le PRIX. Le "
        "carnet mince SUR-RÉAGIT au flux pressé : après que le mid plonge ~5 ¢ sous "
        "sa réf EWMA courte, il REMONTE de +6,5 ¢ sur 30 s en 5m (recherche 2026-06-25, "
        "n=4082, ~9σ, FILL-INDEPENDENT sur la tape exécutée ; +3,5-5,5 ¢ en 15m) vs "
        "une baseline ≈0. On pose un bid passif sous la réf des DEUX côtés (un creux "
        "Down = un pop Up) ; quand un vendeur pressé traverse (0,7 de la taille, sans "
        "frais maker — le MÊME modèle d'anti-sélection qui a démasqué le maker "
        "directionnel à −0,015 en live), on est long le creux. On revend au rebond "
        "(maker, sans frais) ; si pas de revert avant MAX_HOLD ou la clôture, on coupe "
        "en taker — jamais porté au règlement. POURQUOI CE N'EST PAS LE MAKER "
        "DIRECTIONNEL TUÉ : celui-là achetait-et-tenait le favori (fills adverses au "
        "moment où il faiblit) ; ici on SORT en quelques secondes, l'anti-sélection de "
        "règlement ne s'applique pas. RISQUE HONNÊTE : le +6,5 ¢ est réel et "
        "fill-independent, mais la CAPTURE ne l'est pas — il faut des fills maker sur "
        "un carnet mince au poll ~8 s (les creux rapides qui revertent en un tick sont "
        "ratés). Seul le paper live, qui remplit contre le vrai exec_book, dit si le "
        "revert est capturable. Tripwire : si les aller-retours nets ≤ 0 sur ~150 "
        "clôtures, le signal ne survit pas à l'exécution — on retire. pmlab/scalp.py.",
    "scalpx":
        "SCALP À CREUX PLUS PROFONDS (enter 6 ¢ / target 4 ¢ au lieu de 4/3). Vise "
        "les sur-réactions plus marquées : moins d'aller-retours, plus gros rebond "
        "visé chacun. Même mécanique, même tripwire (P&L réalisé net sur ~150 clôtures).",
}

# Per-strategy priors from the OOS backtest (research/backtest_engine.py +
# backtest_lead.py + backtest_eth.py): net EV/$ and qualifying windows/day. The
# dashboard's projection shows this as the "📊 backtest" lens, beside the "🟡 en
# direct" lens (the live-realized EV) — two separate lenses, no shrinkage/blend.
# ev == the OOS EV/$ in BACKTEST below, so the page is self-consistent.
# Resolved by match_keyed (full -> brain-token -> brain-frame -> brain), so a
# deployed strat-token-frame name finds its specific backtest. A strat with no
# matching key has NO backtest (has_backtest=False) — no fabricated DEFAULT.
PRIORS = {
    # DEEP RE-AUDIT 2026-06-24 (research/backtest_eth.py): re-fetched the full free
    # history — BTC 15m 145 DAYS (01-29..06-23, n~14000), ETH 15m 91 DAYS — fixed the
    # 60s spot LOOKAHEAD (spot_at now = price AT t, what live sees), balanced OOS split
    # (btc @ 04-23, eth @ 05-08). The depth is DECISIVE: on multi-month data the bare
    # favorite, the vol gate AND the bps lead floor are all DEAD (regime artifacts);
    # ONLY the vol-normalised z-lead floor (zlead) survives — thin but stable (eth
    # the robust leg, btc thin). ev = deep OOS EV/$. SOL/5m are still shallow (not
    # deep-fetched) — noted. (The earlier 40-day numbers were already lookahead-fixed
    # but too short; the deep sample is the honest verdict.)
    "favorite":      {"ev": -0.0058, "tpd": 23.5},   # DEAD on 145d (win==price: efficient)
    "favorite_vol":  {"ev": -0.0040, "tpd": 15.2},   # DEAD on 145d (vol gate = regime)
    "favorite_wide":  {"ev": 0.0149, "tpd": 20.7},    # not deep-fetched (not deployed); 40d-era, likely dead
    "favorite_lead":     {"ev": 0.0014, "tpd": 21.4},    # ~zero on 145d (bps floor)
    "favorite_vollead":  {"ev": 0.0178, "tpd": 13.2},    # not deep-fetched (not deployed); 40d-era
    "favorite_conviction":       {"ev": 0.0178, "tpd": 13.2},    # not deep-fetched (not deployed); 40d-era
    "favorite_cheap":       {"ev": -0.0067, "tpd": 5.8},
    "favorite_lead-5m":   {"ev": 0.0052, "tpd": 42.7},   # SHALLOW 22d (5m not deep-fetched) ~zero
    "favorite_lead-eth":  {"ev": 0.0006, "tpd": 17.9},   # ~zero on 91d (bps floor dead off btc)
    "zlead-sol": {"ev": -0.0293, "tpd": 12.3},  # DEAD; SHALLOW 11d (sol not deep-fetched)
    "zlead-btc": {"ev": 0.0109, "tpd": 18.3},   # thin REAL on 145d — the survivor
    "zlead-eth": {"ev": 0.0224, "tpd": 17.7},   # REAL & robust on 91d (days+ 74%) — best survivor
    # z-floor on the 5m FRAME (btc) — DEEP-CONFIRMED 45d (n=1593): +0.0270 days+ 85%,
    # the STRONGEST + most stable edge, ~3x the volume of 15m -> compounds fastest. The
    # z floor, NOT the dead bps one. Keyed by FULL name so it isn't shadowed by
    # zlead-btc (15m). Top pilot candidate.
    "zlead-btc-5m": {"ev": 0.0270, "tpd": 50.0},
    "zlead-eth-5m": {"ev": 0.0387, "tpd": 50.0},   # 18d, strongest edge/$ but carnet $57
    # sol/xrp ALIVE on 5m (DEAD on 15m!) — the z-floor generalizes on the fast frame. 18d.
    "zlead-sol-5m": {"ev": 0.0298, "tpd": 50.0},
    "zlead-xrp-5m": {"ev": 0.0372, "tpd": 50.0},
    # zleadx = stricter z>=1.5 floor — forward-test (z>=1.5 beats z>=1.0 on the sweep).
    "zleadx-btc-5m": {"ev": 0.0391, "tpd": 32.0},  # deep 45d, days+ 90%
    "zleadx-eth-5m": {"ev": 0.0431, "tpd": 32.0},  # 18d, days+ 90%, carnet mince
    # zleadn = z-floor + TIGHT band 0.85-0.90 (deep 15m sweep 2026-06-25): the longshot
    # premium concentrates in 0.85-0.90; deep favs 0.90+ DEAD. OOS beats the 0.85-0.95 zlead.
    "zleadn-btc": {"ev": 0.0362, "tpd": 7.1},   # 145d, OOS win 0.938/px 0.898
    "zleadn-eth": {"ev": 0.0375, "tpd": 6.2},   # 91d, OOS win 0.936/px 0.896
    # zleada = BTC-align veto (alts) ; zleadp = veto + longshot-flow size-tilt. SAME GATE, so the
    # same per-window backtest (the tilt only lifts the $-curve). Corpus-derived (entry-faithful px
    # @frac0.45, REAL settlement, alts only ; short sample 2-7d). Keyed by frame (no-op on btc).
    "zleada-15m": {"ev": 0.0954, "tpd": 8.0}, "zleada-5m": {"ev": 0.0920, "tpd": 24.0},
    "zleadp-15m": {"ev": 0.0954, "tpd": 8.0}, "zleadp-5m": {"ev": 0.0920, "tpd": 24.0},
    # maker brains (…mk) get NO prior — the maker EXECUTION gain isn't OOS-backtestable;
    # the dashboard shows the GATE's backtest (the taker twin) as a labelled reference.
}

# Full OOS backtest validation per strategy. DEEP RE-AUDIT 2026-06-24: one harness
# (research/backtest_eth.py), +2c haircut, real taker fee, ORACLE label, hold to settle,
# spot_at LOOKAHEAD-FREE, on the DEEP cache (BTC 145d / ETH 91d), balanced OOS split.
# ev == PRIORS ev. verdict = tripwire (OOS win-rate beat the avg price paid).
# VERDICT: depth kills the regime artifacts — bare favorite, vol gate, bps floor all
# DEAD on multi-month data; only the z-lead floor (zlead) survives, thin (eth robust,
# btc thin, sol dead). n here = the deep OOS tranche. SOL/5m still shallow (not deep-fetched).
BACKTEST = {
    "favorite":      {"ev": -0.0058, "n": 1500, "daysp": 0.50, "win": 0.924, "px": 0.924},
    "favorite_vol":  {"ev": -0.0040, "n": 1159, "daysp": 0.54, "win": 0.925, "px": 0.924},
    "favorite_wide":  {"ev": 0.0149, "n": 369, "daysp": 0.68, "win": 0.924, "px": 0.905},
    "favorite_lead":     {"ev": 0.0014, "n": 1382, "daysp": 0.60, "win": 0.932, "px": 0.926},
    "favorite_vollead":  {"ev": 0.0178, "n": 243, "daysp": 0.67, "win": 0.947, "px": 0.926},
    "favorite_conviction":       {"ev": 0.0178, "n": 243, "daysp": 0.67, "win": 0.947, "px": 0.926},
    "favorite_cheap":       {"ev": -0.0067, "n": 131, "daysp": 0.67, "win": 0.885, "px": 0.885},
    "favorite_lead-5m":   {"ev": 0.0052, "n": 992, "daysp": 0.71, "win": 0.933, "px": 0.924},
    "favorite_lead-eth":  {"ev": 0.0006, "n": 1085, "daysp": 0.55, "win": 0.932, "px": 0.927},
    "zlead-sol": {"ev": -0.0293, "n": 83,  "daysp": 0.33, "win": 0.904, "px": 0.926},
    "zlead-btc": {"ev": 0.0109, "n": 1148, "daysp": 0.68, "win": 0.943, "px": 0.929},
    "zlead-eth": {"ev": 0.0224, "n": 857, "daysp": 0.74, "win": 0.956, "px": 0.931},
    "zlead-btc-5m": {"ev": 0.0270, "n": 1593, "daysp": 0.85, "win": 0.953, "px": 0.924},  # DEEP 45d — confirmed
    "zlead-eth-5m": {"ev": 0.0387, "n": 325, "daysp": 0.90, "win": 0.960, "px": 0.920},   # 18d (deeper pending)
    "zlead-sol-5m": {"ev": 0.0298, "n": 388, "daysp": 0.90, "win": 0.954, "px": 0.922},   # 18d — alive on 5m
    "zlead-xrp-5m": {"ev": 0.0372, "n": 386, "daysp": 0.80, "win": 0.961, "px": 0.922},   # 18d — alive on 5m
    "zleadx-btc-5m": {"ev": 0.0391, "n": 1042, "daysp": 0.90, "win": 0.967, "px": 0.927}, # z>=1.5, deep 45d
    "zleadx-eth-5m": {"ev": 0.0431, "n": 216, "daysp": 0.90, "win": 0.968, "px": 0.923},  # z>=1.5, 18d
    "zleadn-btc": {"ev": 0.0362, "n": 64, "daysp": 0.70, "win": 0.938, "px": 0.898},  # band 0.85-0.90, deep 145d
    "zleadn-eth": {"ev": 0.0375, "n": 47, "daysp": 0.67, "win": 0.936, "px": 0.896},  # band 0.85-0.90, 91d
    # zleada/zleadp = BTC-align veto (+ flow-tilt for zleadp). Corpus-derived (entry px @frac0.45,
    # REAL settlement, ALTS only, SHORT sample). Same gate → same record; the tilt lifts the curve.
    "zleada-15m": {"ev": 0.0954, "n": 905, "daysp": 1.0, "win": 0.950, "px": 0.868},  # veto-alt, 7d (shallow)
    "zleada-5m": {"ev": 0.0920, "n": 586, "daysp": 1.0, "win": 0.951, "px": 0.871},   # veto-alt, ~2d (shallow)
    "zleadp-15m": {"ev": 0.0954, "n": 905, "daysp": 1.0, "win": 0.950, "px": 0.868},  # veto+tilt, 7d (shallow)
    "zleadp-5m": {"ev": 0.0920, "n": 586, "daysp": 1.0, "win": 0.951, "px": 0.871},   # veto+tilt, ~2d (shallow)
    # NO maker (…mk) entries — a maker has no own OOS backtest; the dashboard shows its
    # GATE's backtest (the taker twin) as a labelled reference instead.
}

# FEE: the PRIORS/BACKTEST ev above were computed WITH the 0.07 taker fee, and that fee IS
# really charged on-chain (re-verified 2026-06-27 from raw tx receipts — pUSD relayer 0xe111…
# forwards 0.07×p×(1−p)×shares to collector 0x115f48dc at entry, BOTH 5m & 15m). So the ev is
# shown as-is, fee included — NO add-back. The 2026-06-26 "fee=0 phantom" add-back was the
# error (it tracked USDC.e and missed the pUSD fee leg). The +2c slippage haircut also stays
# (real, just smaller than the backtest models → conservative).


# Deployed runners are named brain[-token][-frame] (zlead-sol-15m, favorite_lead-btc-5m).
# The backtest tables (PRIORS/BACKTEST) are keyed by whatever specificity the backtest
# had: brain ("favorite"), brain-frame ("favorite_lead-5m"), or brain-token ("zlead-sol").
# Match most- to least-specific so a deployed name finds ITS backtest instead of
# silently collapsing to the generic 0.03 DEFAULT (the old split("-")[0] bug:
# zlead-sol-15m -> "zlead" -> no key -> DEFAULT; favorite_lead-btc-5m -> "favorite_lead" ->
# the 15m generic, not the 5m number).
_TOKENS = set(_COIN_BET_MAX)    # the canonical coin set (single registry coins.py) — was a
_FRAMES = ("5m", "15m")         # stale 4-coin literal that broke doge/bnb name resolution


def name_parts(name: str) -> tuple:
    seg = name.split("-")
    return (seg[0],
            next((s for s in seg if s in _TOKENS), None),
            next((s for s in seg if s in _FRAMES), None))


def match_keyed(d: dict, name: str):
    """Best value in a name-keyed table for a deployed runner, trying
    full -> brain-token -> brain-frame -> brain. None if nothing matches, so the
    caller can tell 'no backtest' apart from a real one (vs a silent DEFAULT)."""
    brain, token, frame = name_parts(name)
    cands = [name]
    if token:
        cands.append(f"{brain}-{token}")
    if frame:
        cands.append(f"{brain}-{frame}")
    cands.append(brain)
    for k in cands:
        if k in d:
            return d[k]
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion — honest small-sample bounds
    on the win-rate, so the tripwire isn't read off a point estimate alone."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return ((c - h) / d, (c + h) / d)


def max_drawdown(equity: list) -> float:
    """Worst peak-to-trough on the equity curve, in dollars (>= 0)."""
    peak = -1e18
    dd = 0.0
    for _ts, v in equity:
        peak = max(peak, v)
        dd = max(dd, peak - v)
    return dd


def live_metrics(name: str, state: dict | None, trades: list, equity: list) -> dict:
    """Per-strategy live edge metrics, read straight from the journal. EV/$ =
    realized P&L per dollar actually staked on SETTLED positions (matched
    BUY→SETTLE by slug). Also a ROLLING window (catches a regime turn before the
    cumulative figure does) and a Wilson CI on the win-rate (honest small-n)."""
    matched_prior = match_keyed(PRIORS, name)
    has_backtest = matched_prior is not None
    open_stake: dict[str, float] = {}
    open_px: dict[str, float] = {}
    settled_stake = settled_pnl = fees = 0.0
    wins = losses = 0
    entry_px: list[float] = []
    win_amt: list[float] = []
    loss_amt: list[float] = []
    seq: list[dict] = []                       # ordered settled records
    for t in trades:
        kind = t.get("kind", "")
        slug = t.get("slug", "")
        # Entry/close/win classification lives in pmlab.journal — the SINGLE
        # definition shared with the correlation matrix (cf. always-modularize). It was a
        # maker-fill bug magnet: FILL_BUY doesn't start with "BUY", REST_BUY must not count.
        if is_entry(kind):
            try:
                stk = float(t["shares"]) * float(t["price"])
                px = float(t["price"])
                fees += float(t.get("fee") or 0.0)
            except (KeyError, ValueError):
                continue
            # a top-up ("BUY+", or a 2nd taker BUY) ADDS to the same window's stake —
            # accumulate, don't overwrite, or the BUY→SETTLE-by-slug denominator drops
            # the first fill and inflates EV/$. Entry price is recorded once per window
            # (the first fill) so the win<=price tripwire stays one row per trade.
            if slug in open_stake:
                open_stake[slug] += stk
            else:
                open_stake[slug] = stk
                open_px[slug] = px
                entry_px.append(px)
        # A close is an outcome-bet SETTLE *or* a scalp round-trip exit (SELL =
        # taker bail, FILL_SELL = the resting maker exit that caught the revert).
        # The scalp (liquidity provision) NEVER settles — it always exits intra-
        # window — so without counting its sells n_settled stays 0 and the whole
        # dashboard reads "en attente" while only $/jour shows its realized money.
        elif is_close(kind):
            stk = open_stake.pop(slug, None)
            px = open_px.pop(slug, None)
            try:
                pnl = float(t.get("pnl") or 0.0)
            except ValueError:
                pnl = 0.0
            fees += float(t.get("fee") or 0.0)     # taker bail fee (0 for a settle/maker exit)
            won_flag = won_of(kind, pnl)   # outcome bet by kind; scalp round-trip by sign
            if stk:
                settled_stake += stk
                settled_pnl += pnl
            (win_amt if won_flag else loss_amt).append(pnl)
            wins, losses = wins + won_flag, losses + (not won_flag)
            seq.append({"won": won_flag, "px": px or 0.0, "pnl": pnl, "stake": stk or 0.0})
    n = wins + losses
    run_days = ((equity[-1][0] - equity[0][0]) / 86400) if len(equity) >= 2 else 0.0
    recent = seq[-30:]
    r_stake = sum(s["stake"] for s in recent)
    r_wins = sum(s["won"] for s in recent)
    ev_live = (settled_pnl / settled_stake) if settled_stake > 0 else 0.0
    lo, hi = wilson(wins, n)
    avg_price = (sum(entry_px) / len(entry_px)) if entry_px else 0.0
    fee_per_stake = (fees / settled_stake) if settled_stake > 0 else 0.0
    # result windows off the equity curve (equity = cash + settled, never reserved)
    def _eq_at(ts: float) -> float:
        val = equity[0][1]
        for t, v in equity:
            if t <= ts:
                val = v
            else:
                break
        return val
    pnl_day = pnl_hour = per_hour = 0.0
    if len(equity) >= 2:
        now_ts, cur_eq = equity[-1][0], equity[-1][1]
        lt = time.localtime(now_ts)
        midnight = now_ts - (lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec)
        pnl_day = cur_eq - _eq_at(midnight)            # since local midnight
        pnl_hour = cur_eq - _eq_at(now_ts - 3600)      # rolling last hour
        span_h = (equity[-1][0] - equity[0][0]) / 3600.0
        per_hour = ((cur_eq - equity[0][1]) / span_h) if span_h > 0 else 0.0
    return {
        "ev_live": ev_live,
        "ev_prior": matched_prior["ev"] if matched_prior else None,   # None => no real backtest (e.g. maker)
        "tpd_prior": matched_prior["tpd"] if matched_prior else None,
        "has_backtest": has_backtest,
        "tpd_live": (n / run_days) if run_days > 0 else 0.0,
        "n_settled": n,
        "win_rate": (wins / n) if n else 0.0,
        "win_lo": lo, "win_hi": hi,            # 95% Wilson on the win-rate
        "avg_price": avg_price,
        "avg_win": (sum(win_amt) / len(win_amt)) if win_amt else 0.0,
        "avg_loss": (sum(loss_amt) / len(loss_amt)) if loss_amt else 0.0,
        "run_days": run_days,
        # rolling last-30
        "n_recent": len(recent),
        "win_recent": (r_wins / len(recent)) if recent else 0.0,
        "px_recent": (sum(s["px"] for s in recent) / len(recent)) if recent else 0.0,
        "ev_recent": (sum(s["pnl"] for s in recent) / r_stake) if r_stake > 0 else 0.0,
        # risk / cost
        "max_dd": max_drawdown(equity),
        "fees": fees,
        "fee_share": (fee_per_stake / (ev_live + fee_per_stake))
                     if (ev_live + fee_per_stake) > 0 else 0.0,
        # result windows (realized equity deltas)
        "pnl_day": pnl_day, "pnl_hour": pnl_hour, "per_hour": per_hour,
    }


# --------------------------------------------------------------- assets ---
# The front-end is a small Preact SPA living in webdash_assets/ (no build step):
#   index.html  shell + import map     style.css  the design system
#   vendor/     preact · htm · chart.js (ESM/UMD, vendored — never a pip dep)
#   fonts/      Fraunces · IBM Plex Mono (woff2)
#   app/        main.js, api.js, format.js, components/*, pages/*
# Static files are served under /a/<path>; the SPA fetches its static config
# from /config and live data from the JSON endpoints below.
ASSETS = Path(__file__).resolve().parent / "webdash_assets"

# boot-critical assets — the dashboard refuses to start without these
REQUIRED_ASSETS = ("index.html", "style.css", "app/main.js",
                   "vendor/preact.mjs", "vendor/htm.mjs", "vendor/hooks.mjs",
                   "vendor/chart.umd.js")

_CTYPES = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json", ".woff2": "font/woff2", ".svg": "image/svg+xml",
    ".png": "image/png", ".ico": "image/x-icon", ".map": "application/json",
}


# Per-strategy BACKTEST EQUITY CURVES, keyed by deployed strat name
# (research/gen_backtest_curves.py → JSON). A faithful replay of each deployed
# gate over the cached tape: cumulative $ P&L at STAKE_REF, IS/OOS split marked so
# the dashboard can colour the out-of-sample tranche. Absent file → no curves
# shown (graceful: the dashboard runs fine without it; regenerate offline).
def _load_curves() -> dict:
    p = Path(__file__).resolve().parent / "research" / "backtest_curves.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


BTCURVES = _load_curves()


def site_config() -> dict:
    """The static config the SPA reads once from /config (window.CONFIG)."""
    return {
        "STRATS": list(STRATS), "DESCS": DESCRIPTIONS,
        "BACKTEST": BACKTEST, "STAKE": STAKE_REF,
        "FAMILY": FAMILY, "EVENTS": list(EVENT_STRATS), "COPY": list(COPY_STRATS),
        "BTCURVE": BTCURVES,
        # preset gate params (band, lead floor, entry slot enter_lo/hi) — the front-end
        # renders the human "fenêtre d'entrée" + gate summary from this, no JS duplicate.
        "PRESETS": {k: p.config() for k, p in _PRESETS.items()},
        # per-coin entry-slot (enter_lo) overrides — so the UI shows the REAL slot per coin
        "COIN_SLOTS": _COIN_SLOTS,
        # book-depth bet ceiling + the weighted-sizing model the paper runners use (WEIGHT_PCT of
        # capital, capped at the per-(coin,frame) depth, START starting capital). COIN_DEPTH =
        # {coin:{frame:$}} (the real per-frame ceilings) ; COIN_BET_MAX = conservative min-across-
        # frames scalar (legacy, frame-agnostic fallback).
        "COIN_DEPTH": _COIN_DEPTH, "COIN_BET_MAX": _COIN_BET_MAX,
        "WEIGHT_PCT": _WEIGHT_PCT, "START": _START_CAPITAL,
        # the canonical coin + frame lists (single registry) — the front-end derives its
        # dropdowns/orders from these instead of re-hardcoding TOKENS/FRAMES/COIN_ORDER.
        "COINS": list(_COIN_BET_MAX), "FRAMES": list(_FRAMES),
    }


def serve_asset(rel: str):
    """Resolve a /a/<rel> request to a file under ASSETS. Returns (body, ctype,
    cacheable) or None on miss / path-traversal attempt. Vendored libs and fonts
    are cacheable; app code and the shell are not (so a redeploy shows at once)."""
    rel = rel.lstrip("/") or "index.html"
    target = (ASSETS / rel).resolve()
    if not str(target).startswith(str(ASSETS.resolve()) + os.sep) or not target.is_file():
        return None
    ctype = _CTYPES.get(target.suffix.lower(), "application/octet-stream")
    cacheable = rel.startswith(("vendor/", "fonts/"))
    return target.read_bytes(), ctype, cacheable


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _pilot_id(cfg: dict) -> str:
    """Stable per-config identity = the registry key, the state-dir suffix AND the
    journal slug prefix. One id per (strategy, underlying, interval)."""
    return (f"{cfg.get('strategy', 'favorite_lead')}-{cfg.get('underlying', 'btc')}-"
            f"{cfg.get('interval', '5m')}")


def _save_registry(reg: dict) -> None:
    Path(PILOT_REGISTRY).write_text(json.dumps(reg))


def _load_registry() -> dict:
    """id -> {mode, pid, started, config, state_dir, log}. Migrates the legacy
    single-pilot files (pilot_status.json + live_state/) into the registry once,
    so the VPS's seasoned real run survives the switch as a normal entry."""
    f = Path(PILOT_REGISTRY)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    reg: dict = {}
    legacy = Path(PILOT_STATUS)
    if legacy.exists():
        try:
            st = json.loads(legacy.read_text())
        except (json.JSONDecodeError, OSError):
            st = {}
        cfg = st.get("config")
        if cfg:                                    # a real run was configured here
            reg[_pilot_id(cfg)] = {"mode": st.get("mode", "stopped"),
                                   "pid": st.get("pid"), "started": st.get("started"),
                                   "config": cfg, "state_dir": PILOT_DIR, "log": PILOT_LOG}
            _save_registry(reg)
    return reg


def collect_pilots() -> dict:
    """Read-only snapshot of EVERY registered pilot (the /pilot-data payload)."""
    reg = _load_registry()
    pilots = [_collect_one_pilot(pid, e) for pid, e in reg.items()]
    pilots.sort(key=lambda p: (not p["alive"], p["id"]))   # running ones first
    return {"pilots": pilots, "armed_env": bool(os.environ.get("POLY_PRIVATE_KEY")),
            "wallet": _wallet_summary(pilots)}


# The total deposited into the REAL shared wallet — the baseline the real-time P&L is
# measured against (capital − baseline). NOT the per-pilot --start (=20, the dry-run model
# bankroll); this is the actual money put in. Override via POLY_WALLET_BASELINE in .live_env
# if you deposit/withdraw, otherwise capital−baseline silently counts a deposit as profit.
WALLET_BASELINE = float(os.environ.get("POLY_WALLET_BASELINE", "200") or 200)


def _wallet_summary(pilots: list) -> dict:
    """Real-time figures for the ONE shared real wallet, derived from the armed pilots.
    Every live pilot reports the SAME true total (liquid pUSD + ALL open positions), each
    as a snapshot at ITS last sync. The wallet capital is the bankroll of the **freshest**
    pilot (most recent posmark.json) — NOT the max-of-all, which picked the stalest-HIGHEST
    snapshot and over-stated the wallet during position churn (a just-resolved position
    lingered in an un-synced pilot's number → e.g. $337 shown while the wallet was $324).
    The real-time P&L = that mark-to-market capital − the deposit baseline (so it MOVES with
    open positions, unlike the per-pilot settled P&L). 0 when nothing real is armed."""
    now = time.time()
    real = [p for p in pilots if (p.get("status") or {}).get("mode") == "live"]
    fresh = [p for p in real if now - p.get("posmark_ts", 0) <= POSMARK_FRESH_S]
    if fresh:                                   # freshest sync = most up-to-date wallet
        capital = round(max(fresh, key=lambda p: p["posmark_ts"])["bankroll"], 2)
    else:                                       # no fresh posmark (all stopped): last-known max
        capital = round(max((p.get("bankroll", 0.0) for p in real), default=0.0), 2)
    return {"capital": capital, "baseline": WALLET_BASELINE,
            "pnl": round(capital - WALLET_BASELINE, 2) if capital > 0 else 0.0,
            "live": any(p.get("alive") for p in real),
            "n_live": sum(1 for p in real if p.get("alive"))}


POSMARK_FRESH_S = 90    # a pilot's posmark is "fresh" within this many seconds of its last sync


def _collect_one_pilot(pid: str, entry: dict) -> dict:
    """Read-only snapshot of ONE pilot (its state dir + registry entry), mirroring
    AdaptiveStake's gates so the dashboard shows the same phase."""
    sd = Path(entry.get("state_dir", PILOT_DIR))
    stake, trades, pos = {}, [], None
    status = {"mode": entry.get("mode", "stopped"), "pid": entry.get("pid"),
              "started": entry.get("started"), "config": entry.get("config"),
              "armed_mode": entry.get("armed_mode")}   # mode a paused pilot resumes into
    posmark_ts = 0                              # last _real_capital sync (wallet freshness)
    f = sd / "posmark.json"
    if f.exists():
        try:
            posmark_ts = int(json.loads(f.read_text()).get("ts", 0) or 0)
        except (json.JSONDecodeError, OSError, ValueError):
            posmark_ts = 0
    f = sd / "stake.json"
    if f.exists():
        try:
            stake = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            stake = {}
    f = sd / "journal.csv"
    if f.exists():
        with f.open() as fh:
            trades = list(csv.DictReader(fh))
    f = sd / "position.json"
    if f.exists():
        try:
            pos = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            pos = None
    # Restrict the track record to the pilot's CURRENT market family
    # ({underlying}-updown-{interval}-…) so KPIs, journal and curve reflect that
    # one strategy. This matters most for the migrated legacy live_state/, whose
    # journal still carries an abandoned favorite_lead-5m phase the favorite-15m run must
    # not be judged on; per-id state dirs are clean from the start, so the filter
    # is just a harmless guard there. Every settled stat below is derived from this
    # filtered journal (single source of truth, deposit-immune); stake.json feeds
    # only the live wallet figures (bankroll/peak). Disk state is left untouched.
    cfg = status.get("config") or {}
    prefix = f"{cfg.get('underlying', 'btc')}-updown-{cfg.get('interval', '15m')}-"
    trades = [t for t in trades if str(t.get("slug", "")).startswith(prefix)]

    def _num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    settled = [t for t in trades if (t.get("kind") or "").upper().endswith(("WIN", "LOSS"))]
    n = len(settled)
    wins = [1 if (t.get("kind") or "").upper().endswith("WIN") else 0 for t in settled]
    prices = [p for p in (_num(t.get("price")) for t in settled) if p is not None]
    wr = (sum(wins) / len(wins)) if wins else 0.0
    avgpx = (sum(prices) / len(prices)) if prices else 0.0
    ppd = []  # net per $1, mirrors AdaptiveStake._pnl (regime gate, last 40)
    for t in settled:
        p, sh, px = _num(t.get("pnl")), _num(t.get("shares")), _num(t.get("price"))
        if p is not None and sh and px:
            ppd.append(p / (sh * px))
    ppd = ppd[-40:]
    phase = ("STOP" if (len(wins) >= 150 and wr <= avgpx)
             else "PAUSE" if (len(ppd) >= 40 and sum(ppd) / len(ppd) <= 0)
             else "confirmed" if n >= 150 else "learning")
    bank = stake.get("bankroll", 20.0)
    # P&L = realized trading result (sum of settled pnl), NOT capital−start: the
    # wallet balance also moves on deposits/withdrawals, which are not gains. The
    # cumulative curve (l'évolution des résultats) and its drawdown are read off
    # the same settled pnl, so both are deposit-immune.
    realized = cum_peak = realized_dd = 0.0
    curve = []
    for t in settled:
        p = _num(t.get("pnl"))
        if p is None:
            continue
        realized += p
        cum_peak = max(cum_peak, realized)
        realized_dd = max(realized_dd, cum_peak - realized)
        ts = _num(t.get("ts"))
        curve.append([int(ts) if ts is not None else 0, round(realized, 2)])
    return {"id": pid, "status": status, "alive": _pid_alive(status.get("pid")),
            "bankroll": bank, "peak": stake.get("peak", bank), "n_trades": n,
            "realized_pnl": round(realized, 2), "realized_dd": round(realized_dd, 2),
            "win_rate": wr, "avg_price": avgpx, "phase": phase, "position": pos,
            "posmark_ts": posmark_ts, "trades": trades, "pnl_curve": curve}


def _spawn_pilot(cmd: list, env: dict, log_path: str) -> int:
    log = open(log_path, "a")
    p = subprocess.Popen(cmd, cwd=os.getcwd(), env=env, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    return p.pid


def _pilot_cfg(params: dict) -> dict:
    """Validate POST params into the recorded pilot config (the run's identity)."""
    strat = params.get("strategy", "zlead")
    if strat not in FAMILY:
        strat = "zlead"
    iv = params.get("interval", "15m"); iv = iv if iv in _FRAMES else "15m"
    und = params.get("underlying", "btc")
    und = und if und in _TOKENS else "btc"        # _TOKENS = the single coin registry
    try:
        start = max(5.0, float(params.get("start", 20)))
    except (TypeError, ValueError):
        start = 20.0
    try:
        stake = max(1.0, float(params.get("stake", 5)))
    except (TypeError, ValueError):
        stake = 5.0
    weighted = str(params.get("weighted", "")) in ("1", "true", "on", "True")
    try:
        weight_pct = max(0.0, min(1.0, float(params.get("weight_pct", 0.10))))
    except (TypeError, ValueError):
        weight_pct = 0.10
    try:
        bet_max = max(1.0, float(params.get("bet_max")))
    except (TypeError, ValueError):
        bet_max = bet_max_for(und, iv)      # default = measured per-(coin,frame) book depth
    # optional entry-slot override (enter_lo, window fraction remaining). Empty = let
    # preset_from_args apply the per-coin COIN_SLOTS default. Clamp to a sane range.
    el = params.get("enter_lo", "")
    try:
        enter_lo = float(el) if str(el).strip() not in ("", "None") else None
        if enter_lo is not None and not (0.05 <= enter_lo <= 0.50):
            enter_lo = None
    except (TypeError, ValueError):
        enter_lo = None
    # poll cadence (s): the maker-recenter COLO experiment needs sub-second re-quoting to
    # track the favorite mid — the 8s default is what makes a resting maker bid stale and
    # adversely selected. Clamp 0.5–30 so a typo can't hammer the API or freeze a pilot.
    try:
        poll = float(params.get("poll", 8.0))
        poll = max(0.5, min(30.0, poll))
    except (TypeError, ValueError):
        poll = 8.0
    # maker re-centering (colo): keep the resting bid AT the live favorite mid (run_live
    # --recenter). Only meaningful for a maker strategy (zleadmk); a no-op for a taker one.
    recenter = str(params.get("recenter", "")) in ("1", "true", "on", "True")
    return {"strategy": strat, "interval": iv, "underlying": und, "start": start,
            "stake": stake, "weighted": weighted, "weight_pct": weight_pct,
            "bet_max": bet_max, "enter_lo": enter_lo, "poll": poll, "recenter": recenter}


def _pilot_cmd(cfg: dict, python_exec: str, state_dir: str) -> list:
    """Assemble the run_live.py command for a validated config + its state dir."""
    cmd = [python_exec, "-u", "run_live.py", "--strategy", cfg["strategy"],
           "--interval", cfg["interval"], "--underlying", cfg["underlying"],
           "--start", str(cfg["start"]), "--stake", str(cfg["stake"]),
           "--weight-pct", str(cfg["weight_pct"]), "--bet-max", str(cfg["bet_max"]),
           "--state-dir", state_dir, "--poll", str(cfg.get("poll", 8))]
    if cfg["weighted"]:
        cmd.append("--weighted")
    if cfg.get("enter_lo") is not None:        # explicit entry-slot override (else per-coin default)
        cmd += ["--enter-lo", str(cfg["enter_lo"])]
    if cfg.get("recenter"):                     # maker-recenter colo experiment (no-op for takers)
        cmd.append("--recenter")
    return cmd


def _kill_pilot(pid) -> None:
    try:
        os.killpg(os.getpgid(int(pid)), signal.SIGTERM)
    except OSError:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass


LIVE_ENV_FILE = ".live_env"


def load_live_env(path: str = LIVE_ENV_FILE) -> list:
    """Load the real-money secrets from .live_env into os.environ so the dashboard's
    Arm/Resume works regardless of HOW webdash was launched. Previously the live env
    was only inherited from the shell that started webdash (the watchdog sources
    .live_env, but a manual `python3 webdash.py` does NOT) → a keyless dashboard would
    silently fail to (re)arm a real pilot. Reading the file here decouples arming from
    the launch method. Parses `export KEY=value` / `KEY=value` lines (the shell format);
    only fills vars NOT already in the env, so an explicit env still wins. Returns the
    list of keys it set (for the boot log)."""
    p = Path(path)
    if not p.exists():
        return []
    loaded = []
    try:
        for raw in p.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            # A truly pre-existing env var wins (explicit env beats the file). But a
            # DUPLICATE key WITHIN .live_env must be last-wins, like shell `source`:
            # a stale early `POLY_SIG_TYPE=2` followed by the real `=3` once read 2
            # here (first-wins) while the watchdog's `source` read 3 → the AWS engine
            # queried the wrong account and saw $0. Override our OWN earlier write.
            if key and (key not in os.environ or key in loaded):
                os.environ[key] = val
                if key not in loaded:
                    loaded.append(key)
    except OSError:
        return loaded
    return loaded


def _launch_pilot(cfg: dict, mode: str, state_dir: str, log_path: str) -> tuple:
    """Spawn run_live.py for `cfg` in 'dry' or 'live' mode, reusing state_dir/log.
    Returns (pid, None) on success or (None, error_dict) when the live path isn't
    ready. The SINGLE place a pilot process is started — start/resume/edit all go
    through here so the dry vs live launch rules never drift (cf. always-modularize:
    the entry gate already burned us by living in two code paths)."""
    if mode == "live":
        if not os.environ.get("POLY_PRIVATE_KEY"):
            return None, {"ok": False, "msg": "POLY_PRIVATE_KEY absent de l'env du dashboard "
                          "— configure-le sur le VPS (voir docs/SETUP-LIVE.md)"}
        venv_py = os.path.join(os.getcwd(), ".venv-live", "bin", "python")
        if not os.path.exists(venv_py):
            return None, {"ok": False, "msg": ".venv-live absent — crée-le : python3 -m venv "
                          ".venv-live && .venv-live/bin/pip install -r requirements-live.txt"}
        env = os.environ.copy()
        env["POLY_LIVE"] = "1"
        env["POLY_CONFIRM"] = "I_UNDERSTAND_REAL_MONEY"
        return _spawn_pilot(_pilot_cmd(cfg, venv_py, state_dir), env, log_path), None
    return _spawn_pilot(_pilot_cmd(cfg, sys.executable, state_dir),
                        os.environ.copy(), log_path), None


def _pilot_proc_alive(entry: dict) -> bool:
    """True only if the recorded pid is alive AND is the run_live.py process for THIS
    state dir. A bare pid check is fooled by pid REUSE after a reboot (the registry pids
    predate it), which would leave a dead pilot un-revived — the exact silent-coverage
    failure this supervisor exists to kill."""
    pid = entry.get("pid")
    if not _pid_alive(pid):
        return False
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    return "run_live.py" in cmd and f"--state-dir {entry.get('state_dir', '')} " in cmd


# Pilot supervisor — the watchdog revives the PAPER runners + the dashboard but NEVER
# run_live.py (by design), so a crashed/rebooted armed pilot stays dead and stops trading
# SILENTLY. Measured cost of its absence: an armed pilot once sat dead ~50h while its
# always-on paper twin kept trading — that coverage gap, not any prediction edge, is the
# whole of the "paper beats the pilot" illusion. This daemon thread (started by main())
# relaunches any armed (mode dry/live) pilot whose process died, through the SAME
# _launch_pilot path the dashboard uses (no drift). A user-PAUSED pilot is mode='stopped'
# and left untouched. A crash-loop is braked to ONE pause+alert, never a relaunch storm.
PILOT_SUPERVISE_EVERY = 25          # s between liveness sweeps
_RESTART_WINDOW = 900               # s — crash-loop observation window
_RESTART_MAX = 5                    # revives within that window before we pause + alert
_restart_log: dict = {}             # id -> [recent revive unix ts] (in-memory; resets on boot)


def _supervise_pilots_once() -> None:
    """One liveness sweep — revive dead armed pilots, brake crash-loops."""
    from pmlab.notify import notify
    now = int(time.time())
    with _REGISTRY_LOCK:
        reg = _load_registry()
        changed = False
        for pid_id, entry in reg.items():
            if entry.get("mode") not in ("dry", "live") or not entry.get("config"):
                continue                                  # stopped/forgotten/blank: respect it
            if _pilot_proc_alive(entry):
                continue                                  # healthy
            hist = [t for t in _restart_log.get(pid_id, []) if now - t < _RESTART_WINDOW]
            if len(hist) >= _RESTART_MAX:                 # crash-loop: pause + alert, stop hammering
                entry["armed_mode"] = entry.get("mode")   # a manual Resume restores it
                entry["mode"], entry["pid"] = "stopped", None
                _restart_log[pid_id] = []
                changed = True
                m = (f"🛑 pilote {pid_id} en CRASH-LOOP (≥{_RESTART_MAX}×/"
                     f"{_RESTART_WINDOW // 60}min) → mis en PAUSE par le superviseur, à investiguer")
                print(f"[supervise] {m}")
                try: notify(m)
                except Exception: pass
                continue
            mode = entry["mode"]
            pid, err = _launch_pilot(entry["config"], mode, entry["state_dir"], entry["log"])
            if err:                                       # live path not ready (no key / no venv)
                print(f"[supervise] {pid_id}: relance impossible — {err.get('msg')}")
                continue
            entry["pid"], entry["started"] = pid, now
            hist.append(now); _restart_log[pid_id] = hist
            changed = True
            tag = "🔴 RÉEL" if mode == "live" else "DRY"
            m = f"♻️ pilote {pid_id} ({tag}) était mort → relancé par le superviseur (pid {pid})"
            print(f"[supervise] {m}")
            try: notify(m)
            except Exception: pass
        if changed:
            _save_registry(reg)


def _supervise_pilots_loop() -> None:
    while True:
        time.sleep(PILOT_SUPERVISE_EVERY)
        try:
            _supervise_pilots_once()
        except Exception as e:                            # a bad sweep must never kill the thread
            print(f"[supervise] sweep error: {type(e).__name__}: {e}")
        try:
            _supervise_mm_once()                          # same sweep revives crashed rewardMM runners
        except Exception as e:
            print(f"[supervise-mm] sweep error: {type(e).__name__}: {e}")


def pilot_control(action: str, params: dict | None = None) -> dict:
    """Serialize every registry mutation (request threads + the supervisor thread) so a
    revive can never race a user Stop and resurrect a paused real pilot."""
    with _REGISTRY_LOCK:
        return _pilot_control_impl(action, params)


def _pilot_control_impl(action: str, params: dict | None = None) -> dict:
    """Per-pilot lifecycle: start_dry | start_live | stop | resume | edit | forget.
    Several pilots run in parallel — one process / state dir / log each, keyed by
    config identity ({strategy}-{underlying}-{interval}). stop/resume/edit/forget take
    an `id`; start derives it from the config. Arming requires POLY_PRIVATE_KEY in the
    dashboard env + a .venv-live — the key is never taken from the web.

    Pause/resume/edit replace the old delete-and-re-arm dance: a paused pilot keeps
    its registry entry + on-disk state and remembers the mode it was armed in
    (`armed_mode`), so resume relaunches into the SAME mode and edit just restarts it
    with new staking params. Identity (strategy/underlying/interval) is immutable —
    changing it would strand the seasoned state dir, so that's a fresh 'Ajouter'."""
    params = params or {}
    reg = _load_registry()

    if action in ("stop", "resume", "edit", "forget"):
        pid_id = params.get("id", "")
        entry = reg.get(pid_id)
        if not entry:
            return {"ok": False, "msg": "pilote introuvable"}
        alive = _pid_alive(entry.get("pid"))

        if action == "stop":                       # pause: kill the process, keep the entry
            if alive:
                _kill_pilot(entry.get("pid"))
            if entry.get("mode") in ("dry", "live"):   # remember what resume should relaunch into
                entry["armed_mode"] = entry["mode"]
            entry["mode"], entry["pid"] = "stopped", None
            _save_registry(reg)
            return {"ok": True, "msg": f"pilote en pause : {pid_id}"}

        if action == "resume":                     # relaunch a paused pilot, same config + mode
            if alive:
                return {"ok": False, "msg": f"ce pilote tourne déjà : {pid_id}"}
            mode = entry.get("armed_mode") or "dry"     # absent (legacy/migrated) -> safe DRY default
            pid, err = _launch_pilot(entry["config"], mode, entry["state_dir"], entry["log"])
            if err:
                return err
            entry.update(mode=mode, pid=pid, started=int(time.time()))
            _save_registry(reg)
            tag = "🔴 RÉEL" if mode == "live" else "DRY-RUN"
            return {"ok": True, "msg": f"pilote repris ({tag}) : {pid_id}"}

        if action == "edit":                       # change staking params in place (identity locked)
            old = entry.get("config") or {}
            newcfg = _pilot_cfg({**params, "strategy": old.get("strategy"),
                                 "interval": old.get("interval"),
                                 "underlying": old.get("underlying")})
            entry["config"] = newcfg
            if alive:                              # restart so the new flags take effect, same mode+state
                mode = entry["mode"] if entry.get("mode") in ("dry", "live") else (entry.get("armed_mode") or "dry")
                _kill_pilot(entry.get("pid"))
                pid, err = _launch_pilot(newcfg, mode, entry["state_dir"], entry["log"])
                if err:                            # live path not ready: keep the new config but stay paused
                    if entry.get("mode") in ("dry", "live"):
                        entry["armed_mode"] = entry["mode"]
                    entry["mode"], entry["pid"] = "stopped", None
                    _save_registry(reg)
                    return err
                entry.update(mode=mode, pid=pid, started=int(time.time()))
            _save_registry(reg)
            return {"ok": True, "msg": f"pilote mis à jour : {pid_id}"
                    + ("" if alive else " (appliqué à la reprise)")}

        # forget: drop a stopped pilot from the list. Its state dir is left on disk,
        # so restarting the same config re-adopts the track record (clean declutter).
        if alive:
            return {"ok": False, "msg": "mets-le en pause d'abord, puis supprime-le"}
        del reg[pid_id]
        _save_registry(reg)
        return {"ok": True, "msg": f"pilote supprimé : {pid_id}"}

    if action not in ("start_dry", "start_live"):
        return {"ok": False, "msg": "action inconnue"}

    cfg = _pilot_cfg(params)
    pid_id = _pilot_id(cfg)
    existing = reg.get(pid_id)
    if existing and _pid_alive(existing.get("pid")):
        return {"ok": False, "msg": f"ce pilote tourne déjà : {pid_id} — arrête-le d'abord"}
    # Reuse a seasoned state dir/log on restart; a brand-new config gets its own
    # (the legacy live_state/ is only ever reused via its migrated entry).
    state_dir = existing["state_dir"] if existing else f"live_state_{pid_id}"
    log_path = existing["log"] if existing else f"live_pilot_{pid_id}.log"

    mode = "live" if action == "start_live" else "dry"
    pid, err = _launch_pilot(cfg, mode, state_dir, log_path)
    if err:
        return err
    reg[pid_id] = {"mode": mode, "pid": pid, "started": int(time.time()),
                   "config": cfg, "state_dir": state_dir, "log": log_path,
                   "armed_mode": mode}
    _save_registry(reg)
    if mode == "dry":
        return {"ok": True, "msg": f"DRY-RUN : {cfg['strategy']} {cfg['underlying'].upper()} {cfg['interval']}"}
    sz = (f"pondéré {cfg['weight_pct']*100:.0f} % du capital (max ${cfg['bet_max']:.0f})"
          if cfg["weighted"] else f"flat ${cfg['stake']:.0f}/pari")
    return {"ok": True, "msg": f"🔴 RÉEL armé : {cfg['strategy']} {cfg['underlying'].upper()} "
            f"{cfg['interval']} · {sz} · capital = wallet réel partagé"}


# =================================================================== rewards-MM ===
# The std0 liquidity-rewards harvester control plane — a deliberate MIRROR of the pilot
# plane above (same registry/spawn/supervise shape, same DRY/RÉEL launch rules), but over a
# SEPARATE registry + the run_rewardmm.py engine, so it can never touch the armed zlead
# pilots. Reuses _spawn_pilot/_kill_pilot/_pid_alive/load_live_env/_REGISTRY_LOCK.

def _mm_id(cfg: dict) -> str:
    """Stable identity = registry key + state-dir suffix = mm-{underlying}-{interval}."""
    return f"mm-{cfg.get('underlying', 'btc')}-{cfg.get('interval', '5m')}"


def _save_mm_registry(reg: dict) -> None:
    Path(MM_REGISTRY).write_text(json.dumps(reg))


def _load_mm_registry() -> dict:
    f = Path(MM_REGISTRY)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _mm_cfg(params: dict) -> dict:
    """Validate POST params into a rewardMM config. Defaults track run_rewardmm.py; every
    numeric is clamped so a typo can't hammer the API, cross the book, or skip the kill."""
    und = params.get("underlying", "btc"); und = und if und in _TOKENS else "btc"
    iv = params.get("interval", "5m"); iv = iv if iv in _FRAMES else "5m"   # btc-5m = the real target

    def _f(key, default, lo, hi):
        try:
            return max(lo, min(hi, float(params.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _bool(key):
        return str(params.get(key, "")) in ("1", "true", "on", "True")

    return {
        "underlying": und, "interval": iv,
        "mint_usd": _f("mint_usd", 15.0, 0.0, 5000.0),   # $ of sets minted as ask ammo (recovered)
        "clip": _f("clip", 5.0, 5.0, 2000.0),            # shares/order — CLOB min is 5
        "quote_dist": _f("quote_dist", 0.01, 0.002, 0.05),
        "max_band": _f("max_band", 0.10, 0.01, 0.49),
        "spot_anchor": _bool("spot_anchor"),
        "beta": _f("beta", 0.5, 0.0, 1.0),
        "back_off": _f("back_off", 0.0, 0.0, 0.05),
        "max_inv": _f("max_inv", 10.0, 5.0, 5000.0),
        "min_quote": _f("min_quote", 0.30, 0.05, 0.5),
        "max_quote": _f("max_quote", 0.70, 0.5, 0.95),
        "recenter_eps": _f("recenter_eps", 0.01, 0.002, 0.10),
        "flatten_buf": _f("flatten_buf", 45.0, 5.0, 300.0),
        "kill_loss": _f("kill_loss", 8.0, 0.5, 10000.0),
        "poll": _f("poll", 3.0, 0.5, 30.0),
    }


def _mm_cmd(cfg: dict, python_exec: str, state_dir: str) -> list:
    """Assemble the run_rewardmm.py command for a validated config + its state dir."""
    cmd = [python_exec, "-u", "run_rewardmm.py",
           "--interval", cfg["interval"], "--underlying", cfg["underlying"],
           "--mint-usd", str(cfg["mint_usd"]), "--clip", str(cfg["clip"]),
           "--quote-dist", str(cfg["quote_dist"]), "--max-band", str(cfg["max_band"]),
           "--beta", str(cfg["beta"]), "--back-off", str(cfg["back_off"]),
           "--max-inv", str(cfg["max_inv"]), "--min-quote", str(cfg["min_quote"]),
           "--max-quote", str(cfg["max_quote"]), "--recenter-eps", str(cfg["recenter_eps"]),
           "--flatten-buf", str(cfg["flatten_buf"]), "--kill-loss", str(cfg["kill_loss"]),
           "--state-dir", state_dir, "--poll", str(cfg["poll"])]
    if cfg["spot_anchor"]:
        cmd.append("--spot-anchor")
    return cmd


def _launch_mm(cfg: dict, mode: str, state_dir: str, log_path: str) -> tuple:
    """Spawn run_rewardmm.py for `cfg` in 'dry' or 'live' mode. Mirrors _launch_pilot: the
    live path needs the key + .venv-live (the on-chain mint/merge run via ~/mint/setops.js +
    POLY_BUILDER_* in env). Returns (pid, None) or (None, error_dict)."""
    if mode == "live":
        if not os.environ.get("POLY_PRIVATE_KEY"):
            return None, {"ok": False, "msg": "POLY_PRIVATE_KEY absent de l'env du dashboard "
                          "— configure-le sur le VPS (voir docs/SETUP-LIVE.md)"}
        venv_py = os.path.join(os.getcwd(), ".venv-live", "bin", "python")
        if not os.path.exists(venv_py):
            return None, {"ok": False, "msg": ".venv-live absent — crée-le : python3 -m venv "
                          ".venv-live && .venv-live/bin/pip install -r requirements-live.txt"}
        env = os.environ.copy()
        env["POLY_LIVE"] = "1"
        env["POLY_CONFIRM"] = "I_UNDERSTAND_REAL_MONEY"
        return _spawn_pilot(_mm_cmd(cfg, venv_py, state_dir), env, log_path), None
    return _spawn_pilot(_mm_cmd(cfg, sys.executable, state_dir),
                        os.environ.copy(), log_path), None


def _mm_proc_alive(entry: dict) -> bool:
    """True only if the recorded pid is alive AND is the run_rewardmm.py process for THIS
    state dir — pid-reuse-proof, mirroring _pilot_proc_alive."""
    pid = entry.get("pid")
    if not _pid_alive(pid):
        return False
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as fh:
            cmd = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except (OSError, ValueError):
        return False
    return "run_rewardmm.py" in cmd and f"--state-dir {entry.get('state_dir', '')} " in cmd


MM_FRESH_S = 60   # a rewardMM state snapshot is "fresh" within this many seconds of its last tick


def _collect_one_mm(mid: str, entry: dict) -> dict:
    """Read-only snapshot of ONE rewardMM runner from its state dir (rewardmm_state.json +
    rewardmm_journal.csv). Telemetry: marked P&L (true, incl. held inventory), cash-flow,
    estimated maker rebate, fill count, inventory + neutrality (the std0 invariant), resting
    quotes, recent journal + a cash-flow curve."""
    sd = Path(entry.get("state_dir", ""))
    status = {"mode": entry.get("mode", "stopped"), "pid": entry.get("pid"),
              "started": entry.get("started"), "config": entry.get("config"),
              "armed_mode": entry.get("armed_mode")}
    st, journal = {}, []
    f = sd / "rewardmm_state.json"
    if f.exists():
        try:
            st = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            st = {}
    f = sd / "rewardmm_journal.csv"
    if f.exists():
        with f.open() as fh:
            journal = list(csv.DictReader(fh))

    def _num(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    inv_up, inv_dn = _num(st.get("inv_up")), _num(st.get("inv_dn"))
    spent, received = _num(st.get("spent")), _num(st.get("received"))
    fill_pq = _num(st.get("fill_pq"))
    realized = received - spent                              # cash-flow (misleading mid-window)
    mark = _num(st.get("mark"), realized)                   # true P&L incl. held inventory
    rebate = _num(st.get("rebate"), 0.014 * fill_pq)        # ≈ 0.014·Σ p(1−p)·filled shares
    snap_ts = int(_num(st.get("ts")))
    # cash-flow curve from the journal `realized` column over time (what we have historically;
    # the marked P&L needs the live book, so it's a point-in-time snapshot only).
    curve = []
    for r in journal:
        ts, rl = _num(r.get("ts")), r.get("realized")
        if rl not in (None, "") and ts:
            curve.append([int(ts), round(_num(rl), 2)])
    return {"id": mid, "status": status, "alive": _pid_alive(status.get("pid")),
            "mark": round(mark, 2), "realized": round(realized, 2),
            "rebate": round(rebate, 3), "rebate_with_mark": round(mark + rebate, 2),
            "fills": int(_num(st.get("fills"))), "inv_up": round(inv_up, 1),
            "inv_dn": round(inv_dn, 1), "neutrality": round(inv_up - inv_dn, 1),
            "minted": round(_num(st.get("minted")), 1), "spent": round(spent, 2),
            "received": round(received, 2), "resting": len(st.get("orders", {}) or {}),
            "killed": bool(st.get("killed")), "cur_slug": st.get("cur_slug"),
            "snap_ts": snap_ts, "fresh": bool(snap_ts and time.time() - snap_ts <= MM_FRESH_S),
            "journal": journal[-40:], "curve": curve}


def collect_mm() -> dict:
    """The /mm-data payload: snapshot of every registered rewardMM runner + a fleet aggregate."""
    reg = _load_mm_registry()
    runners = [_collect_one_mm(mid, e) for mid, e in reg.items()]
    runners.sort(key=lambda r: (not r["alive"], r["id"]))
    live = [r for r in runners if r["alive"] and r["status"].get("mode") == "live"]
    agg = {"n_live": sum(1 for r in runners if r["alive"]),
           "n_real": len(live),
           "mark": round(sum(r["mark"] for r in live), 2),
           "rebate": round(sum(r["rebate"] for r in live), 3),
           "fills": sum(r["fills"] for r in live)}
    return {"runners": runners, "armed_env": bool(os.environ.get("POLY_PRIVATE_KEY")),
            "agg": agg}


def mm_control(action: str, params: dict | None = None) -> dict:
    """Serialize rewardMM registry mutations against the same lock as the pilots/supervisor."""
    with _REGISTRY_LOCK:
        return _mm_control_impl(action, params)


def _mm_control_impl(action: str, params: dict | None = None) -> dict:
    """rewardMM lifecycle: start_dry | start_live | stop | resume | forget. One process /
    state dir / log per identity (mm-{underlying}-{interval}). Arming needs POLY_PRIVATE_KEY
    in the dashboard env + a .venv-live — never taken from the web. Re-arming a STOPPED runner
    via start re-adopts its seasoned state dir, with the freshly-submitted knobs."""
    params = params or {}
    reg = _load_mm_registry()

    if action in ("stop", "resume", "forget"):
        mid = params.get("id", "")
        entry = reg.get(mid)
        if not entry:
            return {"ok": False, "msg": "runner introuvable"}
        alive = _pid_alive(entry.get("pid"))
        if action == "stop":
            if alive:
                _kill_pilot(entry.get("pid"))
            if entry.get("mode") in ("dry", "live"):
                entry["armed_mode"] = entry["mode"]
            entry["mode"], entry["pid"] = "stopped", None
            _save_mm_registry(reg)
            return {"ok": True, "msg": f"rewardMM en pause : {mid}"}
        if action == "resume":
            if alive:
                return {"ok": False, "msg": f"ce runner tourne déjà : {mid}"}
            mode = entry.get("armed_mode") or "dry"
            pid, err = _launch_mm(entry["config"], mode, entry["state_dir"], entry["log"])
            if err:
                return err
            entry.update(mode=mode, pid=pid, started=int(time.time()))
            _save_mm_registry(reg)
            return {"ok": True, "msg": f"rewardMM repris ({'🔴 RÉEL' if mode == 'live' else 'DRY'}) : {mid}"}
        # forget: drop a stopped runner; its state dir stays on disk (re-armable).
        if alive:
            return {"ok": False, "msg": "mets-le en pause d'abord, puis supprime-le"}
        del reg[mid]
        _save_mm_registry(reg)
        return {"ok": True, "msg": f"rewardMM supprimé : {mid}"}

    if action not in ("start_dry", "start_live"):
        return {"ok": False, "msg": "action inconnue"}

    cfg = _mm_cfg(params)
    mid = _mm_id(cfg)
    existing = reg.get(mid)
    if existing and _pid_alive(existing.get("pid")):
        return {"ok": False, "msg": f"ce runner tourne déjà : {mid} — arrête-le d'abord"}
    state_dir = existing["state_dir"] if existing else f"rewardmm_{cfg['underlying']}_{cfg['interval']}"
    log_path = existing["log"] if existing else f"rewardmm_{cfg['underlying']}_{cfg['interval']}.log"
    mode = "live" if action == "start_live" else "dry"
    pid, err = _launch_mm(cfg, mode, state_dir, log_path)
    if err:
        return err
    reg[mid] = {"mode": mode, "pid": pid, "started": int(time.time()), "config": cfg,
                "state_dir": state_dir, "log": log_path, "armed_mode": mode}
    _save_mm_registry(reg)
    if mode == "dry":
        return {"ok": True, "msg": f"DRY-RUN rewardMM : {cfg['underlying'].upper()} {cfg['interval']} "
                f"· mint ${cfg['mint_usd']:.0f} clip {cfg['clip']:.0f}"}
    return {"ok": True, "msg": f"🔴 RÉEL armé : rewardMM {cfg['underlying'].upper()} {cfg['interval']} "
            f"· mint ${cfg['mint_usd']:.0f} clip {cfg['clip']:.0f} — argent réel (wallet partagé)"}


# rewardMM supervisor — revive a crashed/rebooted armed harvester, same crash-loop brake as
# the pilots. Runs in the SAME daemon sweep (_supervise_pilots_loop), gated to the AWS engine.
_mm_restart_log: dict = {}


def _supervise_mm_once() -> None:
    from pmlab.notify import notify
    now = int(time.time())
    with _REGISTRY_LOCK:
        reg = _load_mm_registry()
        changed = False
        for mid, entry in reg.items():
            if entry.get("mode") not in ("dry", "live") or not entry.get("config"):
                continue
            if _mm_proc_alive(entry):
                continue
            hist = [t for t in _mm_restart_log.get(mid, []) if now - t < _RESTART_WINDOW]
            if len(hist) >= _RESTART_MAX:
                entry["armed_mode"] = entry.get("mode")
                entry["mode"], entry["pid"] = "stopped", None
                _mm_restart_log[mid] = []
                changed = True
                m = (f"🛑 rewardMM {mid} en CRASH-LOOP (≥{_RESTART_MAX}×/"
                     f"{_RESTART_WINDOW // 60}min) → mis en PAUSE par le superviseur")
                print(f"[supervise-mm] {m}")
                try: notify(m)
                except Exception: pass
                continue
            mode = entry["mode"]
            pid, err = _launch_mm(entry["config"], mode, entry["state_dir"], entry["log"])
            if err:
                print(f"[supervise-mm] {mid}: relance impossible — {err.get('msg')}")
                continue
            entry["pid"], entry["started"] = pid, now
            hist.append(now); _mm_restart_log[mid] = hist
            changed = True
            tag = "🔴 RÉEL" if mode == "live" else "DRY"
            m = f"♻️ rewardMM {mid} ({tag}) était mort → relancé par le superviseur (pid {pid})"
            print(f"[supervise-mm] {m}")
            try: notify(m)
            except Exception: pass
        if changed:
            _save_mm_registry(reg)


def collect(name: str, light: bool = False) -> dict:
    """Snapshot a runner's state for the dashboard.

    light=True (used by /all, polled every 10 s for the sidebar + overview) keeps
    state / metrics / tick / equity but DROPS the bulky journal, decisions and log
    arrays — those are only read by the per-strategy page (/data, full collect),
    not by /all's consumers. With 13+ runners this cut the /all payload ~10× (it
    was ~0.5 MB/poll). Trades are still READ here (live_metrics needs them); they
    are simply not serialised into the light response.
    """
    meta = STRATS.get(name) or EVENT_STRATS.get(name) or COPY_STRATS[name]
    sd = Path(meta["state_dir"])
    state, trades, equity, log = None, [], [], []
    f = sd / "state.json"
    if f.exists():
        state = json.loads(f.read_text())
    f = sd / "journal.csv"
    if f.exists():
        with f.open() as fh:
            trades = list(csv.DictReader(fh))
            for t in trades:
                t["ts"] = int(t["ts"])
    f = sd / "equity.csv"
    if f.exists():
        rows = f.read_text().strip().splitlines()
        step = max(1, len(rows) // 600)            # cap points sent to the page
        equity = [[int(a), float(b)] for a, b in
                  (r.split(",") for r in rows[::step])]
    tick = None
    f = sd / "tick.json"
    if f.exists():
        try:
            tick = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            tick = None
    metrics = live_metrics(name, state, trades, equity)
    if light:
        return {"state": state, "equity": equity, "tick": tick, "metrics": metrics}
    f = Path(meta["log"])
    if f.exists():
        log = f.read_text().splitlines()[-25:]
    decisions = []
    f = sd / "decisions.csv"
    if f.exists():
        with f.open() as fh:
            decisions = list(csv.DictReader(fh))
    return {"state": state, "trades": trades, "equity": equity, "log": log,
            "tick": tick, "decisions": decisions, "metrics": metrics}


def collect_events() -> list[dict]:
    """Per event-harvester: settled metrics + open positions + recent settlements."""
    out = []
    for name in EVENT_STRATS:
        d = collect(name)
        st = d["state"] or {}
        m = d["metrics"]
        pos = st.get("positions", {}) or {}
        positions = [{"slug": p.get("slug"), "fav": p.get("fav_outcome"),
                      "price": p.get("price"), "shares": p.get("shares"),
                      "opened": p.get("opened")} for p in pos.values()]
        settled = [t for t in d["trades"]
                   if str(t.get("kind", "")).startswith("SETTLE")][-20:]
        out.append({
            "name": name,
            "realized_pnl": float(st.get("realized_pnl", 0) or 0),
            "cash": float(st.get("cash", 0) or 0),
            "n_settled": m["n_settled"], "win_rate": m["win_rate"],
            "avg_price": m["avg_price"], "ev_live": m["ev_live"],
            "open": len(positions),
            "deployed": sum((p.get("price") or 0) * (p.get("shares") or 0)
                            for p in pos.values()),
            "positions": positions, "settled": settled})
    return out


def collect_copy() -> list[dict]:
    """Per copy-mirror runner: settled metrics straight from state, open mirrored
    positions, recent settlements, and how many of the target's fills we've replicated.
    win/n/PnL come from state.json (not slug-matched live_metrics) because several fills
    average into one position — slug matching would undercount the staked dollars."""
    out = []
    for name in COPY_STRATS:
        d = collect(name)
        st = d["state"] or {}
        pos = st.get("positions", {}) or {}
        n = int(st.get("n_trades", 0) or 0)
        wins = int(st.get("n_wins", 0) or 0)
        scost = float(st.get("settled_cost", 0) or 0)
        realized = float(st.get("realized_pnl", 0) or 0)
        positions = [{"slug": p.get("slug"), "title": p.get("title"),
                      "outcome": p.get("outcome"),
                      "price": (p.get("cost", 0) / p["shares"]) if p.get("shares") else 0,
                      "cost": p.get("cost"), "shares": p.get("shares"),
                      "n_fills": p.get("n_fills"), "opened": p.get("opened")}
                     for p in pos.values()]
        settled = [t for t in d["trades"]
                   if str(t.get("kind", "")).startswith("SETTLE")][-20:]
        out.append({
            "name": name,
            "target": st.get("target_name") or st.get("target", ""),
            "realized_pnl": realized,
            "cash": float(st.get("cash", 0) or 0),
            "initial": float(st.get("initial", 0) or 0),
            "n_settled": n, "win_rate": (wins / n) if n else 0.0,
            "roi": (realized / scost) if scost else 0.0,    # P&L per settled $ — vs their +24%
            "n_mirrored": int(st.get("n_mirrored", 0) or 0),
            "open": len(positions),
            "deployed": sum((p.get("cost") or 0) for p in pos.values()),
            "started": int(st.get("started", 0) or 0),
            "positions": positions, "settled": settled})
    return out


# Structural spot lens: the underlyings' co-movement DRIVES simultaneous favorite-flips, so
# it's the leading indicator of clustered losses — and unlike the realized loss-phi (which
# needs months of rare losses), it's measurable NOW from spot. Computed once per TTL (6
# klines fetches) and cached, since the SPA polls /correlation-data every few seconds.
_COIN_CORR_CACHE: dict = {"ts": 0.0, "corr": None}
_COIN_CORR_TTL = 300.0          # 5 min — coin betas drift slowly; don't hammer Binance per poll
_STRUCT_BUCKET = 900            # 15m return grid (the live-proven frame), aligned to epoch
_STRUCT_KLINES = 1000           # 1m candles per coin ≈ 16.6 h → ~66 aligned 15m anchors


def _coin_returns(symbol: str) -> dict:
    """{anchor_ts: simple return} of `symbol` over consecutive _STRUCT_BUCKET-second anchors
    aligned to the epoch grid (so every coin shares the same grid → comparable). Built from
    1m klines; returns {} on any fetch failure (that coin just drops out of the matrix)."""
    try:
        kl = feeds.btc_klines_1m(limit=_STRUCT_KLINES, symbol=symbol)
    except Exception:
        return {}
    close_at = {k["t"]: k["close"] for k in kl}
    anchors = sorted(t for t in close_at if t % _STRUCT_BUCKET == 0)
    out = {}
    for a0, a1 in zip(anchors, anchors[1:]):
        if a1 - a0 == _STRUCT_BUCKET and close_at[a0] > 0:   # consecutive, no gap
            out[a1] = close_at[a1] / close_at[a0] - 1.0
    return out


def _coin_spot_corr() -> dict:
    """Cached coin×coin spot-return correlation {coin: {coin: rho}} over the recent 15m grid.
    The STRUCTURAL co-movement that bounds simultaneous-flip risk (see correlation.py). Self
    rho omitted (the matrix forces same-coin struct to 1.0). Returns {} if <2 coins fetch."""
    now = time.time()
    if _COIN_CORR_CACHE["corr"] is not None and now - _COIN_CORR_CACHE["ts"] < _COIN_CORR_TTL:
        return _COIN_CORR_CACHE["corr"]
    rets = {c: _coin_returns(_SYMBOL[c]) for c in _COIN_KEYS if c in _SYMBOL}
    rets = {c: r for c, r in rets.items() if r}
    corr: dict = {c: {} for c in rets}
    coins = list(rets)
    for i, a in enumerate(coins):
        for b in coins[i + 1:]:
            common = sorted(set(rets[a]) & set(rets[b]))
            if len(common) >= _MIN_OVERLAP:
                rho = _pearson([rets[a][t] for t in common], [rets[b][t] for t in common])
                if rho is not None:
                    corr[a][b] = corr[b][a] = rho
    _COIN_CORR_CACHE.update(ts=now, corr=corr)
    return corr


def _coin_of(name: str) -> str | None:
    """Underlying coin of a crypto runner name 'variant-coin-frame' (e.g. zlead-btc-15m ->
    'btc'), or None if the middle token isn't a known coin (defensive — never guess)."""
    parts = name.split("-")
    return parts[1] if len(parts) >= 3 and parts[1] in _COIN_KEYS else None


def collect_correlation() -> dict:
    """Live cross-strategy correlation across the crypto race (the candidate pool a
    strategy-ETF would arm from), through three lenses: simultaneous-loss phi (primary,
    realized but slow), full-P&L rho (secondary), and the STRUCTURAL spot co-movement
    (immediate — coin betas lifted onto the strat grid). Reads each runner's journal and
    hands {name: trades} + the coin maps to correlation.correlation_matrix. Scoped to
    STRATS: the window-ts alignment is meaningful only for the deterministic
    {coin}-updown-{frame}-{ts} slugs (events use a different slug shape)."""
    runners: dict[str, list] = {}
    for name, meta in STRATS.items():
        f = Path(meta["state_dir"]) / "journal.csv"
        if f.exists():
            with f.open() as fh:
                runners[name] = list(csv.DictReader(fh))
        else:
            runners[name] = []
    coin_of = {name: _coin_of(name) for name in runners}
    return correlation_matrix(runners, coin_of=coin_of, coin_corr=_coin_spot_corr())


def _remote_get(path: str, cache: dict, empty: dict) -> bytes:
    """Morocco display: fetch a JSON endpoint from the AWS owner, degrading to the last good
    snapshot (flagged stale) if AWS is briefly unreachable — the proxied tab never 500s.
    Shared by the pilot tab (/pilot-data) and the rewards-MM tab (/mm-data)."""
    import urllib.request
    try:
        with urllib.request.urlopen(PILOT_REMOTE + path, timeout=5) as r:
            body = r.read()
        cache.update(body=body, ts=time.time())
        return body
    except Exception:
        if cache["body"] is not None:
            d = json.loads(cache["body"])
            d["stale"] = True
            d["stale_age"] = int(time.time() - cache["ts"])
            return json.dumps(d).encode()
        return json.dumps({**empty, "unreachable": True}).encode()


def _remote_post(path: str, raw: str) -> bytes:
    """Morocco display: forward an Arm/Stop/Resume POST to the AWS owner (the key-holder)."""
    import urllib.request
    try:
        req = urllib.request.Request(PILOT_REMOTE + path, data=raw.encode(), method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception as e:
        return json.dumps({"ok": False,
                           "msg": f"moteur AWS injoignable: {type(e).__name__}"}).encode()


def _pilot_proxy_get() -> bytes:
    return _remote_get("/pilot-data", _PILOT_REMOTE_CACHE,
                       {"pilots": [], "armed_env": False, "wallet": {}})


def _pilot_proxy_post(raw: str) -> bytes:
    return _remote_post("/pilot", raw)


def _mm_proxy_get() -> bytes:
    return _remote_get("/mm-data", _MM_REMOTE_CACHE, {"runners": [], "armed_env": False})


def _mm_proxy_post(raw: str) -> bytes:
    return _remote_post("/mm", raw)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body, ctype, cache=False):
        # The SPA polls every few seconds then navigates away / closes the tab,
        # resetting the socket mid-response. The stdlib would log a full traceback
        # for that benign disconnect — swallow it (see also DashServer.handle_error).
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            # live data + the shell never cache (a redeploy must show at once);
            # vendored libs + fonts are immutable, so let the browser keep them.
            self.send_header("Cache-Control",
                             "public, max-age=86400" if cache else "no-store, no-cache, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path
        # --- live JSON endpoints ---
        if path == "/data":
            name = parse_qs(url.query).get("strat", [next(iter(STRATS))])[0]
            if name not in STRATS:
                self.send_error(404)
                return
            return self._send(json.dumps(collect(name)).encode(), "application/json")
        if path == "/all":
            # light=True: sidebar + overview need state/metrics/tick/equity, not the
            # per-strat journal/decisions/log (those load on the /data page only).
            return self._send(json.dumps({n: collect(n, light=True) for n in STRATS}).encode(), "application/json")
        if path == "/history":
            f = Path("history/runs.json")
            return self._send(f.read_text().encode() if f.exists() else b"[]", "application/json")
        if path == "/pilot-data":
            if PILOT_REMOTE:                       # Morocco display: proxy to the AWS engine
                return self._send(_pilot_proxy_get(), "application/json")
            return self._send(json.dumps(collect_pilots()).encode(), "application/json")
        if path == "/mm-data":
            if PILOT_REMOTE:                       # Morocco display: proxy the rewards-MM tab to AWS
                return self._send(_mm_proxy_get(), "application/json")
            return self._send(json.dumps(collect_mm()).encode(), "application/json")
        if path == "/events-data":
            return self._send(json.dumps(collect_events()).encode(), "application/json")
        if path == "/copy-data":
            return self._send(json.dumps(collect_copy()).encode(), "application/json")
        if path == "/correlation-data":
            return self._send(json.dumps(collect_correlation()).encode(), "application/json")
        if path == "/config":
            return self._send(json.dumps(site_config()).encode(), "application/json")
        # --- static front-end ---
        if path == "/" or path == "/index.html":
            got = serve_asset("index.html")
        elif path.startswith("/a/"):
            got = serve_asset(path[3:])
        else:
            got = None
        if got is None:
            self.send_error(404)
            return
        body, ctype, cacheable = got
        return self._send(body, ctype, cacheable)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in ("/pilot", "/mm"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode() if length else ""
        params = {k: v[0] for k, v in parse_qs(raw).items()}
        if url.path == "/mm":                      # rewards-MM control plane (separate registry)
            if PILOT_REMOTE:                       # Morocco display: forward arming to AWS
                body = _mm_proxy_post(raw)
            else:
                body = json.dumps(mm_control(params.get("action", ""), params)).encode()
        elif PILOT_REMOTE:                         # Morocco display: forward arming to AWS
            body = _pilot_proxy_post(raw)
        else:
            body = json.dumps(pilot_control(params.get("action", ""), params)).encode()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *a):   # keep stdout quiet
        pass


class DashServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A polling client that resets the socket is normal here, not a fault;
        # the stdlib default dumps a traceback for it. Swallow connection resets,
        # surface anything genuinely unexpected.
        if not isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            super().handle_error(request, client_address)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--strat", action="append", default=[],
                   help="crypto runner name:state_dir:log_file (repeatable)")
    p.add_argument("--estrat", action="append", default=[],
                   help="event-market harvester name:state_dir:log_file (repeatable)")
    p.add_argument("--cstrat", action="append", default=[],
                   help="wallet copy-mirror runner name:state_dir:log_file (repeatable)")
    p.add_argument("--port", type=int, default=8420)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--pilot-api", action="store_true",
                   help="this host OWNS the pilots: run the supervisor + serve the control API "
                        "(the AWS engine, sole holder of the key). Default off = a paper/display "
                        "dashboard that NEVER supervises or arms.")
    p.add_argument("--pilot-remote", default=None,
                   help="proxy the pilot tab (/pilot-data + POST /pilot) to this owner URL — the "
                        "Morocco display dashboard → AWS engine. Mutually exclusive with --pilot-api.")
    a = p.parse_args()
    PILOT_API = a.pilot_api
    PILOT_REMOTE = a.pilot_remote
    # Real-money secrets: load .live_env ourselves so Arm/Resume works no matter how
    # webdash was started (the watchdog sources it; a manual launch would not) — the
    # cause of the "can't resume a paused real pilot" bug. No-op if the file is absent
    # (dashboard stays DRY-only). Vars already in the env are kept (explicit env wins).
    _armed_keys = load_live_env()
    if "POLY_PRIVATE_KEY" in os.environ:
        print(f"real-money env prête (clé présente{' — chargée depuis .live_env' if 'POLY_PRIVATE_KEY' in _armed_keys else ''})")
    # Fail loud if the front-end assets did not ship (e.g. an rsync that forgot
    # webdash_assets/) — better a clear boot error caught by the watchdog than a
    # blank dashboard serving 500s.
    missing = [] if PILOT_API else [f for f in REQUIRED_ASSETS if not (ASSETS / f).exists()]
    if missing:
        sys.exit(f"front-end assets manquants dans {ASSETS}: {', '.join(missing)} "
                 "— resynchronise webdash_assets/ (voir CLAUDE.md déploiement)")
    # The favorite race: the favorite-longshot harvester + backtest-derived
    # variants. The VPS passes explicit --strat; this is the local default.
    specs = a.strat or ([] if PILOT_API else      # the AWS engine serves pilots ONLY, no paper
                        ["favorite-15m:state_rubedo:favorite.log",
                         "favorite_vol-15m:state_favorite_vol:favorite_vol.log",
                         "favorite_wide-15m:state_favorite_wide:favorite_wide.log"])
    for spec in specs:
        name, sd, lg = spec.split(":")
        STRATS[name] = {"state_dir": sd, "log": lg}
    for spec in a.estrat:
        name, sd, lg = spec.split(":")
        EVENT_STRATS[name] = {"state_dir": sd, "log": lg}
    for spec in a.cstrat:
        name, sd, lg = spec.split(":")
        COPY_STRATS[name] = {"state_dir": sd, "log": lg}
    print(f"dashboard on http://{a.host}:{a.port}/ — strats: {', '.join(STRATS)}"
          + (f" | events: {', '.join(EVENT_STRATS)}" if EVENT_STRATS else "")
          + (f" | copy: {', '.join(COPY_STRATS)}" if COPY_STRATS else ""))
    # Pilot supervisor: ONLY on the owner host (--pilot-api = AWS engine). The watchdog
    # never touches run_live.py, so revive crashed/rebooted armed pilots here. Daemon thread
    # → dies with the dashboard, which the watchdog itself revives, so the supervision chain
    # survives reboots. Started AFTER load_live_env(), so a 'live' relaunch finds the key.
    # NEVER started on the Morocco display dashboard: it would see AWS's pids as dead and
    # relaunch the pilots LOCALLY → double-run on the shared wallet.
    if PILOT_API:
        threading.Thread(target=_supervise_pilots_loop, name="pilot-supervisor",
                         daemon=True).start()
        print(f"superviseur pilotes actif — sweep {PILOT_SUPERVISE_EVERY}s")
    elif PILOT_REMOTE:
        print(f"mode display: onglet pilotes proxifié vers {PILOT_REMOTE}")
    DashServer((a.host, a.port), Handler).serve_forever()
