// The method — what this is, in plain English. Lifted out of the overview so the
// home screen stays a dashboard, not a manual. Closes with "the bestiary": one
// complete description per DEPLOYED brain, read straight from CONFIG (labels,
// taglines, gates, technical paragraphs) so it never drifts from the code.
import { html } from '../preact.js';
import { CONFIG } from '../api.js';
import { partsOf, gateSummary, fmt0 } from '../format.js';

// Display order of coins in the capacity table (then any extra from /config).
// Derived from the central registry served by /config (CONFIG.COINS); literal fallback if the
// config isn't loaded yet. Accessor (not a module const) because CONFIG is
// reassigned by loadConfig() before render.
const coinOrder = () => (CONFIG.COINS && CONFIG.COINS.length)
  ? CONFIG.COINS : ["btc", "eth", "sol", "xrp", "doge", "bnb"];

// Distinct deployed brains, in lineup order, each with the coins/frames it runs on.
// A "strategy" = a brain (zlead, zleadmk, …); coin/frame are its parameterisations,
// so we describe the brain once and list where it's deployed.
function deployedBrains(){
  const order = [], meta = {};
  for(const n of CONFIG.STRATS){
    const {brain, token, frame} = partsOf(n);
    if(!meta[brain]){ meta[brain] = {coins:[], frames:[]}; order.push(brain); }
    if(token && !meta[brain].coins.includes(token)) meta[brain].coins.push(token);
    if(frame && !meta[brain].frames.includes(frame)) meta[brain].frames.push(frame);
  }
  return order.map(b => ({brain:b, ...meta[b]}));
}

function StratCard({brain, coins, frames}){
  const p = (CONFIG.PRESETS || {})[brain] || {};
  const tagline = p.tagline || "";
  const desc = CONFIG.DESCS[brain] || "";
  const gate = gateSummary(p);
  const where = [
    coins.map(c => c.toUpperCase()).join(" · "),
    frames.slice().sort((a, b) => (a === "5m" ? 0 : 1) - (b === "5m" ? 0 : 1)).join(" / "),
  ].filter(Boolean).join(" — ");
  return html`
    <article class="strat-card">
      <header class="strat-card-head">
        <span class="g" aria-hidden="true">${p.glyph || "●"}</span>
        <span class="strat-name">${p.label || brain}</span>
        ${where ? html`<span class="strat-where">${where}</span>` : null}
      </header>
      ${tagline ? html`<p class="strat-tag">${tagline}</p>` : null}
      ${gate ? html`<p class="strat-meta"><b>Settings</b> · ${gate}</p>` : null}
      ${desc ? html`<p class="strat-desc">${desc}</p>` : null}
    </article>`;
}

// Book capacity: the max absorbable stake per coin AND per frame (depth on the
// favorite's ask between 0.85 and 0.95), which caps EVERY bet (paper AND real pilot).
// Read from /config (COIN_DEPTH = {coin:{frame:$}}). 5m and 15m = separate books.
function CapacityTable(){
  const depth = CONFIG.COIN_DEPTH || {};
  const frames = (CONFIG.FRAMES && CONFIG.FRAMES.length) ? CONFIG.FRAMES : ["5m", "15m"];
  const pct = Math.round((CONFIG.WEIGHT_PCT || 0.10) * 100);
  const order = coinOrder();
  const coins = [...order.filter(c => c in depth),
                 ...Object.keys(depth).filter(c => !order.includes(c))];
  if(!coins.length) return null;
  return html`
    <section class="panel">
      <div class="panel-head"><h2>Book capacity — the max stake per coin <i>and per frame</i></h2>
        <span class="aside">$${fmt0(CONFIG.START || 100)} start · ${pct}% of capital per bet</span></div>
      <div class="panel-body">
        <p class="mut" style="margin:0 0 14px;max-width:74ch;line-height:1.6">Each bet is
          <b>${pct}% of capital</b>, <b>capped at the book depth</b> of the favorite — the $ that <b>a
          SINGLE actor</b> can fill on the ask <b>between 0.85 and 0.95</b> <i>while everyone else is
          buying too</i>. Measured <b>per market</b> (5m and 15m = separate books) as the median of the
          <b>largest single wallet</b> per window — not the aggregate flow, which is shared among 9 to
          185 simultaneous buyers (we capture only ~15–35% of it on liquid coins). Source:
          trade history, <code>tools/measure_capacity.py</code>. Paper AND the real pilot
          use this cap.</p>
        <table class="tbl">
          <thead><tr><th>Coin</th>${frames.map(f => html`<th>Max stake ${f}</th>`)}</tr></thead>
          <tbody>
            ${coins.map(c => html`<tr>
              <td><b>${c.toUpperCase()}</b></td>
              ${frames.map(f => { const cap = (depth[c] || {})[f]; const dead = cap != null && cap <= 12;
                return html`<td class=${"num" + (dead ? " mut" : "")}>${cap != null ? html`$${fmt0(cap)}` : "—"}${dead ? html` <span class="mut">— nearly dead</span>` : ""}</td>`; })}
            </tr>`)}
          </tbody>
        </table>
        <p class="mut" style="margin:14px 0 0;max-width:74ch;line-height:1.6">As long as ${pct}% of capital
          stays under these caps, the stake compounds; beyond that, it hits the book. BTC carries
          most of the capacity — and its <b>5m is deeper than its 15m</b> (the opposite of the alts).</p>
      </div>
    </section>`;
}

export function About(){
  const brains = deployedBrains();
  return html`
    <div class="page-head">
      <div><h1><span class="glyph">☉</span>The method</h1>
        <p class="sub">Why this bot bets, the only judge that decides whether the edge is real,
          and what each strategy in the race does.</p></div>
    </div>
    <section class="panel"><div class="panel-body" style="max-width:74ch;line-height:1.7">
      <p><b>The market.</b> Every 5 or 15 minutes, Polymarket pays out on whether Bitcoin closes above or
        below its open. Toward the end of a window, one side is already all but decided — the
        <b class="gold">favorite</b> (priced 0.85–0.95).</p>
      <p><b>The edge.</b> The crowd <b>overpays</b> for the nearly-dead side (the <i>longshot</i>), so the favorite
        is slightly underpriced. We buy it and hold to settlement. No exit: it's a bet
        on <b>price calibration</b>, not on BTC's direction. The <i>magnum opus</i>: transmute
        this tiny bias into gold, one adjustment at a time.</p>
      <p><b>The payoff is brutally asymmetric</b> — winning earns ~+0.11, losing costs −1.00. A
        cluster of reversals hurts. Hence the variants (volatility filter, lead floor):
        each purges a failure mode observed in the journals.</p>
      <p><b>The single judge — the <span class="verd">tripwire</span>.</b> Over ~150 bets, does the real win
        rate beat the average price paid? Above → real edge. Equal or below → no edge, we
        retire it (that's how the six original esoteric brains died). Below 150 bets,
        every number is noise (~4 bets/h).</p>
      <p><b>Two truths, two colors.</b> <span class="gold">Gold</span> = we made money.
        <span class="verd">Verdigris</span> = the edge is statistically proven. A strategy can
        be in the green without being proven (luck), or proven while momentarily in the red.</p>
      <p class="mut">Everything is read-only on the real APIs. Paper trading sends no orders.
        Only <b>The pilot</b> can touch real money, and only once explicitly armed.</p>
    </div></section>

    <${CapacityTable} />

    <section class="panel">
      <div class="panel-head"><h2>The bestiary — every strategy in the race</h2>
        <span class="aside">${brains.length} brain${brains.length > 1 ? "s" : ""} · one parameterised engine</span></div>
      <div class="panel-body">
        <p class="mut" style="margin:0 0 16px;max-width:74ch;line-height:1.6">A <b>strategy</b> = a brain;
          the coin (BTC/ETH/SOL/XRP/DOGE/BNB) and the duration (5m/15m) are just its parameterisations. All
          of them bet the <b>same favorite</b> — it's ONE edge declined, not N independent ones.</p>
        <div class="strat-list">
          ${brains.map(b => html`<${StratCard} ...${b} />`)}
        </div>
      </div>
    </section>`;
}
