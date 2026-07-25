// Pure formatters + domain helpers. No DOM, no Preact, NO api import — just data → display.
// This is the FOUNDATIONAL module (Node-runnable straight: see format.test.mjs), so it must
// stay dependency-free. The coin/frame/glyph registries are SERVED by /config
// (pmlab/coins.py + presets.py); api.loadConfig() PUSHES them here via setRegistry()
// once fetched — format never imports CONFIG (that would cycle with api.js and drag the
// browser-only Preact chain into the node test). The literals below are the fallback until then.

// fallback glyph dict — covers archived/legacy brains; LIVING brains override from presets.
export const GLYPH = {favorite:"🜍", favorite_vol:"🜔", favorite_wide:"🜍", favorite_lead:"🜨",
  favorite_vollead:"⚭", favorite_cheap:"🜚", zlead:"☿", zleadmk:"☿", zleadx:"☿", zleadn:"☿", favorite_conviction:"💎",
  scalp:"⚡", scalpx:"⚡"};   // ⚡ = the liquidity-provision paradigm (price revert, not outcome)
// equity-curve series colours, drawn from the metals palette
export const COLORS = ["#e8c264","#a3bac9","#5cba8d","#c77b4e","#e0573a","#9b7ec9","#d8b53a","#7fb0c9"];

// config-derived registries (pushed by api.loadConfig); literals = fallback before the push.
let _COINS  = ["btc","eth","sol","xrp","doge","bnb"];   // doge/bnb: breadth extension 2026-06-25
let _FRAMES = ["5m","15m"];
let _GLYPHS = {};                                       // {brain: glyph} from CONFIG.PRESETS
export function setRegistry({coins, frames, glyphs} = {}){
  if(coins  && coins.length)  _COINS  = coins;
  if(frames && frames.length) _FRAMES = frames;
  if(glyphs) _GLYPHS = glyphs;
}

// A runner key is brain[-token][-frame] — e.g. zlead-sol-15m, favorite-btc-15m,
// favorite_lead-btc-5m. Legacy keys (favorite-15m, favorite_lead-eth) and bare brains from
// history (gurdjieff) still parse: token/frame are matched by MEMBERSHIP, not
// position, so a middle token segment is never dropped.
export const partsOf = n => {
  const seg = String(n).split("-");
  return {brain: seg[0],
          token: seg.find(s => _COINS.includes(s)) || null,
          frame: seg.find(s => _FRAMES.includes(s)) || null};
};
// living-brain glyph (from presets) first, static dict fallback for archived/legacy brains.
export const glyphOf = n => { const b = partsOf(n).brain; return _GLYPHS[b] || GLYPH[b] || "●"; };
export const bkey = n => partsOf(n).brain;   // base brain, for per-brain descriptions/tags
export const isMakerName = n => bkey(n).endsWith("mk");
// the taker twin of a maker runner: drop the trailing "mk" from the brain only.
// zleadmk-btc-15m -> zlead-btc-15m ; favorite_mk -> favorite. Identity if not maker.
export const takerTwin = n => String(n).replace(/^([a-z]+)mk(-|$)/, "$1$2");
export function nameOf(n){
  const {brain, token, frame} = partsOf(n);
  const c = brain.charAt(0).toUpperCase()+brain.slice(1);
  if(!token && !frame) return c;             // bare brain (legacy history rows)
  const bits = [c];
  if(token) bits.push(token.toUpperCase());
  // underlying-only legacy keys (favorite_lead-eth) carry no frame but are 15m by default
  bits.push(frame || "15m");
  return bits.join(" · ");
}

// the entry slot in minutes-LEFT, from a preset's window fractions + the runner frame.
// enter_lo = LATE bound (less remaining = later), enter_hi = EARLY bound. zlead 15m:
// [0.27,0.45] -> "4–6.75 min left". null if no preset (e.g. archived/legacy strat).
export function entrySlot(preset, frame){
  if(!preset) return null;
  const w = frame === "5m" ? 5 : 15;
  const f = x => (Math.round(x*100)/100).toLocaleString("en-GB");
  return `${f(preset.enter_lo*w)}–${f(preset.enter_hi*w)} min left`;
}
// compact gate descriptor (band + lead floor + execution), for the strategy/pilot header
export function gateSummary(preset){
  if(!preset) return null;
  const bits = [`favorite ${preset.min_fav}–${preset.max_fav}`];
  if(preset.min_lead_z) bits.push(`z≥${preset.min_lead_z}`);
  else if(preset.min_lead_bps) bits.push(`lead≥${preset.min_lead_bps} bps`);
  if(preset.vol_cap) bits.push("vol filter");
  if(preset.btc_align) bits.push("BTC-align veto");
  if(preset.ls_flow_cap) bits.push(preset.ls_flow_tilt ? "longshot-flow tilt" : "longshot-flow veto");
  if(preset.maker_entry) bits.push("maker entry");
  if(preset.conviction_size) bits.push("conviction sizing");
  return bits.join(" · ");
}

// effective entry slot honoring a per-coin enter_lo override (COIN_SLOTS from /config),
// so bnb (slot 0.33) reads "~5 min" while btc/eth read "~4 min" off the SAME base preset.
export function effectiveSlot(preset, frame, coin, coinSlots){
  if(!preset) return null;
  const lo = (coinSlots && coin && coin in coinSlots) ? coinSlots[coin] : preset.enter_lo;
  return entrySlot({enter_lo: lo, enter_hi: preset.enter_hi}, frame);
}

// "zlead · type n" from a preset's base + composed types (the modular identity).
// Bare base (no types) -> just the base name. null if no preset.
export function presetName(preset){
  if(!preset) return null;
  const t = preset.types || [];
  return t.length ? `${preset.base} · type ${t.join("+")}` : (preset.base || preset.key);
}

// money / numbers
export const money  = x => (x<0?"-$":"$") + Math.abs(+x||0).toFixed(2);
export const moneyS = x => ((+x||0)>=0?"+$":"-$") + Math.abs(+x||0).toFixed(2);
export const pct    = (x,d=0) => (x*100).toFixed(d)+" %";
export const signCls = x => x>0 ? "pos" : x<0 ? "neg" : "mut";
export const fmt0 = x => Math.round(x).toLocaleString("en-GB");

export function ageStr(sec){
  if(sec==null) return "—";
  if(sec<60) return Math.round(sec)+" s";
  if(sec<3600) return Math.round(sec/60)+" min";
  return (sec/3600).toFixed(1)+" h";
}
export const timeOf = ts => ts ? new Date(ts*1000).toLocaleTimeString("en-GB") : "—";
export const dateOf = ts => ts ? new Date(ts*1000).toLocaleString("en-GB",
  {day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}) : "—";

export const dirLabel = d => d==="Up" ? "Up" : d==="Down" ? "Down" : (d||"");
// Journal kinds: BUY[tag] (taker cross), REST_BUY[tag] (maker resting bid),
// FILL_BUY[tag] (the resting bid got filled), CANCEL/EXPIRE[tag] (it didn't, then
// a taker fallback BUY follows), SETTLE…WIN/LOSS. Plain English so the maker flow
// (post → filled OR pulled → taker cross) reads without jargon.
export function kindLabel(k){ k=k||"";
  if(k.startsWith("REST_BUY")) return "Maker order posted";
  if(k.startsWith("FILL_BUY"))  return "Maker filled";
  // scalp exits (liquidity-provision close): rest a sell at fill+target, fill on the
  // revert (maker, no fee), or bail across the book (taker) at end-of-window / no-revert.
  if(k.startsWith("REST_SELL")) return "Maker exit posted";
  if(k.startsWith("FILL_SELL")) return "Maker exit (revert)";
  if(k.startsWith("SELL"))      return "Sell back (taker)";
  if(k.startsWith("CANCEL"))    return "Order pulled";
  if(k.startsWith("EXPIRE"))    return "Order expired";
  if(k.startsWith("BUY+"))      return "Top-up (taker)";   // top-up toward target stake
  if(k.startsWith("BUY"))       return "Bet placed (taker)";
  if(k.endsWith("WIN")) return "Won";
  if(k.endsWith("LOSS")) return "Lost";
  return k;
}

// Journal-kind classification — mirrors pmlab/journal.py (THE single backend
// definition) so the pages can't drift on what counts (cf. always-modularize). is_settle:
// an outcome resolution (SETTLE… or the legacy WIN/LOSS alias). is_entry: a taker buy or a
// FILLED maker bid — a bare REST_BUY moved no cash and does NOT count. is_close: a settlement
// OR a scalp round-trip exit (SELL/FILL_SELL). isWin: a settled WIN, or — for a scalp, which
// never settles — a positive realized P&L.
export const isSettleKind = k => { k = k || ""; return k.startsWith("SETTLE") || k === "WIN" || k === "LOSS"; };
export const isEntryKind  = k => { k = k || ""; return k.startsWith("BUY") || k.startsWith("FILL_BUY"); };
export const isCloseKind  = k => { k = k || ""; return isSettleKind(k) || k.startsWith("SELL") || k.startsWith("FILL_SELL"); };
export const isWin = (k, pnl = 0) => isSettleKind(k) ? String(k).endsWith("WIN") : (+pnl > 0);

// runner health from its last tick (telemetry freshness)
export function health(tick){
  if(!tick || tick.ts==null) return {c:"var(--ink-3)", t:"unknown", age:null};
  const age = Date.now()/1000 - tick.ts;
  if(age<75)  return {c:"var(--verdigris)", t:"live", age};
  if(age<900) return {c:"var(--amber)",     t:"slow",  age};
  return {c:"var(--vermilion)", t:"silent", age};
}

// THE tripwire verdict — does realized win-rate beat the price paid, on n>=150?
export function verdict(m){
  const n = m ? (m.n_settled||0) : 0;
  if(!n) return {key:"wait", txt:"Waiting", help:"no bet placed yet"};
  if(n<150) return {key:"trial", txt:`On trial ${n}/150`, help:"too early — ~150 bets needed"};
  return m.win_rate>m.avg_price
    ? {key:"proven", txt:"Proven edge", help:"wins more often than the price paid"}
    : {key:"none",   txt:"No edge", help:"wins as often as or less than the price paid"};
}
// CSS class for the seal component
export const sealCls = {wait:"wait", trial:"trial", proven:"proven", none:"none"};

// real-money GO lamp — the tripwire with a Wilson lower bound
export function goLamp(m){
  if(!m || !m.n_settled) return {c:"var(--ink-3)", t:"real-money light — waiting for bets"};
  if(m.win_rate<=m.avg_price)
    return {c:"var(--vermilion)", t:"RED LIGHT — does not beat the price paid (no edge)"};
  if(m.n_settled<150 || m.win_lo<=m.avg_price)
    return {c:"var(--amber)", t:`AMBER LIGHT — promising, not proven `
      + `(${m.n_settled}/150; lower bound ${pct(m.win_lo)} vs price ${pct(m.avg_price)})`};
  return {c:"var(--verdigris)", t:"GREEN LIGHT — proven edge: the win-rate CI excludes the price over 150+ bets"};
}

// per-(coin,frame) book-depth bet ceiling (measured in-band 0.85-0.95 capacity, mirrors
// coins.bet_max_for). depth = CONFIG.COIN_DEPTH ({coin:{frame:$}}). Falls back to the
// conservative min across frames when frame is unknown, then to `fallback`.
export const betMax = (coin, frame, depth, fallback=25) => {
  const d = (depth && coin) ? depth[coin] : null;
  if(!d) return fallback;
  if(frame && (frame in d)) return d[frame];
  const vals = Object.values(d);
  return vals.length ? Math.min(...vals) : fallback;
};
// effective per-bet size under the weighted model = staking.weighted_clip, mirrored in JS:
// frac of capital, floored at min_clip, capped at the (coin,frame) book depth and the capital.
export function effStake(coin, frame, capital, depth, frac=0.10, fallback=25){
  const cap = betMax(coin, frame, depth, fallback);
  const min_clip = Math.min(5, cap);
  return Math.round(Math.min(Math.max(min_clip, frac*capital), cap, capital) * 100) / 100;
}

// per-$ backtest pnl expressed in dollars at the reference stake
export const simPnL = (bk, stake) => bk ? bk.ev*stake*bk.n : null;
