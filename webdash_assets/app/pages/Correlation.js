// Correlations — how many INDEPENDENT bets the race really is (the "strategy ETF"
// question), through the lens that matters for an asymmetric-payoff book:
// SIMULTANEOUS LOSSES. A favorite pays ~+0.11 to win but −1.00 to lose, so what ruins
// the book is a cluster of losses in the same window. Primary metric = φ on the per-
// window loss indicators (do two runners lose together?); full-P&L ρ is a secondary
// toggle. Redundant pairs (|φ|≥0.7) = lose together → don't double-arm.
import { html, useState } from '../preact.js';
import { usePoll } from '../api.js';
import { glyphOf, nameOf } from '../format.js';
import { Kpi, Panel, Empty, Note } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';

const rho2 = x => (x >= 0 ? "" : "-") + Math.abs(x).toFixed(2).replace(/^0/, "");

// diverging heat over the dark theme: positive (lose/co-move together) → vermilion
// (redundant, bad), negative (one wins when the other loses = a hedge) → verdigris
// (good), ~0 → faint (independent, the goal).
function cellBg(v){
  if(v == null) return "transparent";
  const a = Math.min(0.82, Math.abs(v)).toFixed(3);
  return v >= 0 ? `rgba(224,87,58,${a})` : `rgba(92,186,141,${a})`;
}

export function Correlation(){
  const data = usePoll("/correlation-data").data;
  const [view, setView] = useState("loss");   // "loss" (primary) | "pnl" | "struct"
  if(!data) return html`<div class="empty">Computing correlations…</div>`;
  const { names, matrix, losses, redundant, n_strats, n_groups, avg_abs_loss,
          n_pairs_loss, avg_abs_rho, n_pairs, struct_groups, avg_abs_struct, n_pairs_struct,
          min_overlap, min_losses, redundant_rho } = data;

  if(!n_strats) return html`<${Empty}>No strategy in the race.</${Empty}>`;
  const lossView = view === "loss", structView = view === "struct";
  const val = c => structView ? c.struct : lossView ? c.loss : c.rho;

  const idx = names.map((_, i) => i + 1);   // 1..N column heads (compact) ↔ numbered rows
  const redCols = [
    {key:"a", label:"Strategy A", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.a)}</span><span class="lead">${nameOf(r.a)}</span></span>`},
    {key:"b", label:"Strategy B", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.b)}</span><span class="lead">${nameOf(r.b)}</span></span>`},
    {key:"loss", label:"loss φ", sortable:true, get:r=>Math.abs(r.loss),
      cell:r=>html`<span class="num neg">${rho2(r.loss)}</span>`},
    {key:"both", label:"Simultaneous losses", sortable:true,
      cell:r=>html`<span class="num">${r.both}</span><span class="mut"> / ${r.la}·${r.lb}</span>`},
    {key:"n", label:"Windows", sortable:true, cell:r=>html`<span class="num mut">${r.n}</span>`},
  ];

  const toggle = html`<span class="filters" style="display:inline-flex;gap:6px">
    <button class=${"btn sm" + (lossView ? " primary" : "")} onClick=${()=>setView("loss")}>Simultaneous losses</button>
    <button class=${"btn sm" + (structView ? " primary" : "")} onClick=${()=>setView("struct")}>Structural (spot)</button>
    <button class=${"btn sm" + (view==="pnl" ? " primary" : "")} onClick=${()=>setView("pnl")}>Full P&L</button></span>`;

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">▦</span>Correlations</h1>
        <p class="sub">A basket of strategies can <i>look</i> like several bets while loading
          a single risk factor. And what ruins an asymmetric-payoff book (favorite: +0.11 won /
          −1.00 lost) is <b>losses that cluster</b>: a burst of flips in the same window.
          So we measure, live, how much the runners <b>lose together</b> — the prerequisite for an
          "ETF" that would arm the top X without stacking bets that crater at the same time. The φ of realized
          losses fills in slowly (favorites ~90%); the <b>Structural</b> tab gives the answer
          <i>right away</i> via the spot co-movement of the underlyings.</p></div>
    </div>

    <div class="kpis">
      <${Kpi} k="Strategies" v=${n_strats} sub="in the race" />
      <${Kpi} accent="gold" k="Risk factors (spot)" v=${struct_groups==null ? "—" : struct_groups}
        sub=${struct_groups==null ? "spot correlation unavailable"
          : `truly independent bets — same coin or correlated spot (≥${redundant_rho}) merged · available right away`} />
      <${Kpi} k="Independent groups (losses)" v=${n_groups}
        sub=${`merging pairs that lose together (|φ|≥${redundant_rho}) — fills in slowly`} />
      <${Kpi} accent="merc" k="Loss correlation" v=${avg_abs_loss==null ? "—" : rho2(avg_abs_loss)}
        sub=${avg_abs_loss==null ? "not enough common losses yet" : `avg |φ| · ${n_pairs_loss} pairs measured`} />
      <${Kpi} k="Risk duplicates" v=${redundant.length}
        sub=${`pairs that lose together (|φ|≥${redundant_rho})`} tone=${redundant.length ? "neg" : "mut"} />
    </div>

    <${Panel} title=${structView ? "Matrix — spot co-movement of the underlyings (structural)"
        : lossView ? "Matrix — simultaneous-loss correlation (φ)" : "Matrix — full-P&L correlation (ρ)"}
      aside=${toggle}>
      <div class="heat-wrap">
        <table class="heat">
          <thead><tr><th class="rh"></th>${idx.map(i => html`<th title=${nameOf(names[i-1])}>${i}</th>`)}</tr></thead>
          <tbody>
            ${names.map((n, i) => html`<tr>
              <th class="rh" title=${nameOf(n)}><span class="g">${glyphOf(n)}</span>${i+1} · ${nameOf(n)}</th>
              ${matrix[i].map((c, j) => {
                const diag = i === j;
                const v = val(c);
                const bg = diag ? "var(--surface-3)" : cellBg(v);
                const txt = diag ? html`<span class="g">${glyphOf(n)}</span>`
                  : v == null ? html`<span class="faint">·</span>` : rho2(v);
                const ttl = diag
                  ? `${nameOf(n)} — ${c.n} settled windows, ${losses[n]} lost`
                  : structView
                    ? (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — spot co-movement unavailable (one of the coins has no data)`
                        : v >= 0.999
                          ? `${nameOf(n)} × ${nameOf(names[j])} — SAME underlying: structurally a single bet (simultaneous flips guaranteed)`
                          : `${nameOf(n)} × ${nameOf(names[j])} — spot correlation=${v.toFixed(2)} (co-movement of the underlyings = driver of simultaneous flips)`)
                    : lossView
                    ? (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — too few common losses for a reliable φ `
                          + `(they lose ${c.la}× and ${c.lb}× over ${c.n} windows, ${c.both} simultaneous; need ≥${min_losses} each)`
                        : `${nameOf(n)} × ${nameOf(names[j])} — loss φ=${v.toFixed(2)} · ${c.both} simultaneous losses `
                          + `(out of ${c.la}/${c.lb}, ${c.n} windows${c.lift!=null ? `, ×${c.lift.toFixed(1)} vs chance` : ""})`)
                    : (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — only ${c.n} common windows (< ${min_overlap})`
                        : `${nameOf(n)} × ${nameOf(names[j])} — ρ=${v.toFixed(2)} over ${c.n} windows`);
                return html`<td style=${"background:"+bg} title=${ttl}
                  class=${(!diag && v!=null && Math.abs(v)>=redundant_rho) ? "red" : ""}>${txt}</td>`;
              })}
            </tr>`)}
          </tbody>
        </table>
      </div>
      <${Note}>${structView
        ? html`Each cell = correlation of the <b>spot returns</b> of the two <i>underlyings</i> (recent 15m grid,
            ≈66 windows, Binance source). Why: a favorite flips when the underlying reverses within the window,
            so <b>correlated coins flip — and lose — together</b>. It's the <b>driver</b> of clustered losses,
            measurable <b>right away</b> whereas the realized-loss φ takes months to fill in (favorites ~90%).
            Same coin = <b>1.00</b> (a single bet by construction — the prudent default on the risk side). It's an <i>honest
            upper bound</i> on the loss φ (spot co-movement, not the loss itself), not a substitute once the real φ has
            filled in. The reading that matters for <b>weighting</b>: how many <i>truly</i> independent bets (the
            "Risk factors" KPI) — crypto-beta often melts 48 runners into a handful.`
        : lossView
        ? html`Each cell = φ correlation of the two runners' <b>losses</b>, window by window (loss = negative net
            P&L on the window; aligned by opening timestamp, so same <i>frame</i> = same
            grid → BTC·15m and ETH·15m are comparable). On hover: each one's losses, <b>simultaneous</b> losses,
            and the ×N factor vs chance. Favorites win ~90% → losses are rare: below ${min_overlap}
            common windows or ${min_losses} losses on each side, the cell stays empty (a φ over 1-2 losses
            is just noise). It's the most data-hungry lens — it will fill in slowly;
            meanwhile, the <b>Structural</b> tab gives the spot co-movement, available immediately.`
        : html`Each cell = Pearson correlation of the two runners' <b>full P&L</b> (ups AND downs),
            window by window. Useful to see the overall co-movement, but for the ETF it's the correlation of
            <i>losses</i> that decides (the payoff is asymmetric). Below ${min_overlap} common windows, empty.`}
      </${Note}>
    </${Panel}>

    <${Panel} title=${`Risk duplicates — lose together, keep only one (|φ|≥${redundant_rho})`} flush=${true}>
      ${redundant.length
        ? html`<${DataTable} cols=${redCols} rows=${redundant} sort=${{key:"loss",dir:-1}} empty="—" />`
        : html`<div class="panel-body"><${Note} tone="ok">No pair that systematically loses together
            so far: either the losses don't cluster, or there aren't yet enough common losing windows
            to measure it (favorites ~90% win rate → losses accumulate slowly).</${Note}></div>`}
    </${Panel}>`;
}
