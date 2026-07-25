// Per-strategy deep dive (the autopsy bench). Reached from the leaderboard or
// the sidebar list. /data?strat=<name>, polled.
import { html } from '../preact.js';
import { CONFIG, usePoll, bkOf } from '../api.js';
import { glyphOf, nameOf, bkey, partsOf, isMakerName, takerTwin, verdict, goLamp, simPnL, money, moneyS, pct, signCls,
         timeOf, dirLabel, kindLabel, effectiveSlot, gateSummary, presetName, betMax, effStake, fmt0,
         isEntryKind, isSettleKind, isWin } from '../format.js';
import { Kpi, Panel, Seal, Opus, Lamp, Stat, Note, Empty, Money } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';
import { Projection } from '../components/Projection.js';

export function Strategy({name}){
  const d = usePoll("/data?strat=" + encodeURIComponent(name)).data;
  if(!d) return html`<div class="empty">Loading ${nameOf(name)}…</div>`;
  const s = d.state || {}, m = d.metrics || {}, bk = bkOf(name);
  const isMaker = isMakerName(name);
  const preset = (CONFIG.PRESETS || {})[bkey(name)], frame = partsOf(name).frame;
  const coin = partsOf(name).token;
  const slotStr = preset ? effectiveSlot(preset, frame, coin, CONFIG.COIN_SLOTS) : null;
  const perCoinSlot = !!(preset && CONFIG.COIN_SLOTS && coin && (coin in CONFIG.COIN_SLOTS));
  const slotTag = perCoinSlot ? ` — ${(coin || "").toUpperCase()}-specific setting`
    : (preset && preset.enter_lo <= 0.27 ? " — widened slot 2026-06-25" : "");
  const twinName = isMaker ? takerTwin(name) : null;     // favorite_leadzmk-btc-15m -> favorite_leadz-btc-15m
  const twinBk = isMaker ? bkOf(twinName) : null;        // the GATE's backtest (reference, not the maker's own)
  // Per-coin weighted sizing: cap = book depth ceiling; stake = the runner's CURRENT
  // effective bet (10 % of current capital, capped). $ figures use it so live and backtest
  // dollars share one scale (flat $25 would now lie). cap drives the setting note.
  const cap = betMax(coin, frame, CONFIG.COIN_DEPTH, CONFIG.STAKE);
  const capital = (d.equity && d.equity.length) ? d.equity[d.equity.length-1][1]
                                                : (s.initial||CONFIG.START)+(s.realized_pnl||0);
  const stake = effStake(coin, frame, capital, CONFIG.COIN_DEPTH, CONFIG.WEIGHT_PCT, CONFIG.STAKE);
  const wpct = Math.round((CONFIG.WEIGHT_PCT||0.10)*100);
  const live = s.realized_pnl || 0, v = verdict(m), lamp = goLamp(m);
  const btc = (CONFIG.BTCURVE || {})[name];   // backtest equity curve ($100-compounded)
  // backtest $ = the compounded curve final when we have it, else flat per-coin approx
  const sim = btc ? btc.final : simPnL(bk, stake);
  const aboveRec = m.n_recent && m.win_recent > m.px_recent;

  // sim/real panels
  const real = m.n_settled ? html`
    <${Stat} k="Win rate [95 % CI]" v=${`${pct(m.win_rate)} [${pct(m.win_lo)}–${pct(m.win_hi)}]`} />
    <${Stat} k="Average price paid" v=${pct(m.avg_price)} />
    <${Stat} k="Recent win rate (30)" tone=${m.n_recent?(aboveRec?"pos":"neg"):""}
      v=${m.n_recent ? `${pct(m.win_recent)} vs price ${pct(m.px_recent)}` : "—"} />
    <${Stat} k="Avg win / avg loss" v=${`${moneyS(m.avg_win)} / ${money(m.avg_loss)}`} />
    <${Stat} k="Worst drawdown · fees" v=${`${money(-m.max_dd)} · ${pct(m.fee_share)} of the edge`} />
    <${Note} tone=${m.win_rate>m.avg_price?"ok":"bad"}>${m.win_rate>m.avg_price
      ? "So far, wins more often than the price paid." : "So far, does not beat the price paid."}
      <br/><span class="mut">≈ ${moneyS(m.ev_live*stake)} per bet at the current stake ($${fmt0(stake)})</span></${Note}>`
    : html`<${Empty}>No clear-enough favorite yet. As soon as one side crosses the threshold, the 1st bet fires.</${Empty}>`;

  const simBody = bk ? html`
    <${Stat} k="Win rate" v=${pct(bk.win)} />
    <${Stat} k="Average price paid" v=${pct(bk.px)} />
    <${Stat} k="Winning days" v=${pct(bk.daysp)} />
    <${Note} tone=${bk.win>bk.px?"ok":"bad"}>${bk.win>bk.px
      ? "Beats the price paid → edge confirmed on past data." : "Does not beat the price paid."}
      <br/><span class="mut">≈ ${moneyS(bk.ev*stake)} per bet at the current stake ($${fmt0(stake)})</span></${Note}>`
    : isMaker ? html`
    <${Note}><b>No dedicated maker backtest.</b> The maker <b>execution</b> gain can't be
      validated on past data (the tape lacks the book's future depth) → it's a live
      <b>forward-test</b>.${twinBk ? html`<br/><br/>The <b>entry gate</b> is the one from
      <b>${nameOf(twinName)}</b> (same selection), backtested: win rate ${pct(twinBk.win)} vs price
      ${pct(twinBk.px)}, ≈ ${moneyS(twinBk.ev*stake)}/bet at the current stake (n=${twinBk.n}). The maker
      only changes the EXECUTION of that gate.` : ""}</${Note}>`
    : html`<${Empty}>No simulation for this setting.</${Empty}>`;

  // recent trades
  const trades = (d.trades || []).slice(-25).reverse();
  // DISPLAY predicate = the shared entry kinds (BUY/FILL_BUY) PLUS REST_BUY, so the "Staked"
  // column also shows the size POSTED on a resting maker bid (before the 0.7 fill). The
  // journal entry semantics (isEntryKind) exclude REST_BUY (no cash moved) — that extra is
  // kept EXPLICIT here on purpose, not folded into the shared helper.
  const isEntry = k => isEntryKind(k) || !!(k && k.startsWith("REST_BUY"));
  const tradeCols = [
    {key:"ts", label:"Time", cell:t=>html`<span class="num">${timeOf(t.ts)}</span>`},
    {key:"kind", label:"Kind", cell:t=>kindLabel(t.kind)},
    {key:"dir", label:"Side", cell:t=>dirLabel(t.direction)},
    {key:"price", label:"Price paid", cell:t=>html`<span class="num">${t.price?(+t.price).toFixed(2):"—"}</span>`},
    // how much was actually staked on this bet = shares × price (REST_BUY shows the
    // size POSTED, before the 0.7 fill). Reveals the stake and whether it's flat or
    // weighted (constant column = flat stake, varying = weighted).
    {key:"stake", label:"Staked", cell:t=>{ const v = isEntry(t.kind) ? (+t.shares)*(+t.price) : NaN;
      return isFinite(v) && v>0 ? html`<span class="num">${money(v)}</span>` : html`<span class="mut">—</span>`; }},
    {key:"fee", label:"Fee", cell:t=>html`<span class="num">${(t.fee!==undefined&&t.fee!=="")?money(+t.fee):"—"}</span>`},
    {key:"pnl", label:"Result", cell:t=>(+t.pnl)?html`<${Money} v=${+t.pnl} signed=${true} />`:html`<span class="mut">—</span>`},
  ];

  // losses by lead bucket (favorite_lead thesis)
  const lead = leadBuckets(d.decisions, d.trades);

  // positions + orders
  const poss = s.positions || (s.position?[s.position]:[]), ords = s.orders || [];
  const posRows = [
    ...poss.map(p=>({type:"Position", slug:p.slug, dir:p.direction, sh:+p.shares, px:p.avg_price})),
    ...ords.map(o=>({type:"Order "+o.side, slug:o.slug, dir:o.direction, sh:+o.shares, px:o.price})),
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">${glyphOf(name)}</span>${nameOf(name)}</h1>
        <p class="sub">${(CONFIG.PRESETS || {})[bkey(name)]?.tagline || ""}</p></div>
      <div class="head-aside"><${Seal} v=${v} lg=${true} /></div>
    </div>

    <${Opus} m=${m} />
    <div style="height:14px"></div>
    <${Lamp} lamp=${lamp} />
    <div style="height:18px"></div>

    <${Note}>Three regimes, not to be confused: <b style="color:#a3bac9">📊 Backtest</b> = replay
      on <b>past data</b> (history, what would have happened) · <b class="gold">🟡 Live
      (paper)</b> = the bot trades <b>right now</b> on the real markets, with <b>paper money</b>
      (the "real" test currently running) · <b style="color:var(--vermilion)">🔴 Pilot</b> = <b>real
      money</b> (Pilot tab). This page = backtest + live paper.</${Note}>
    <div style="height:14px"></div>

    ${preset ? html`<${Note}><b>Setting</b> (<code>${presetName(preset)}</code>):
      ${gateSummary(preset)} · <b>entry window ${slotStr}</b>${slotTag}.
      <br/><b>Stake</b>: ${wpct} % of capital, ${(coin||"").toUpperCase()} book cap $${fmt0(cap)} (max stake @+2 ¢).
      <span class="mut">Goal: maximise the day's cumulative PnL (not EV per bet).</span></${Note}>
    <div style="height:14px"></div>` : null}

    <div class="kpis">
      <${Kpi} real=${false} accent=${live>=0?"gold":""} k="🟡 Live (paper)"
        v=${html`<span class=${signCls(live)}>${moneyS(live)}</span>`}
        sub=${m.n_settled ? `${m.n_settled} bets · ${m.run_days.toFixed(1)} d · real markets, paper money` : "waiting"} />
      <${Kpi} accent="merc" k="📊 Backtest (past data)"
        v=${sim!=null?html`<span class=${signCls(sim)}>${moneyS(sim)}</span>`:"—"}
        sub=${bk ? `${bk.n} bets simulated on history` : isMaker ? "maker = forward-test (no dedicated backtest)" : "no backtest"} />
      <${Kpi} k="Edge / $" v=${m.n_settled?html`<span class=${signCls(m.ev_live)}>${(m.ev_live>=0?"+":"")+m.ev_live.toFixed(4)}</span>`:"—"}
        sub=${`backtest ${bk?(bk.ev>=0?"+":"")+bk.ev.toFixed(4):(isMaker&&twinBk?`${(twinBk.ev>=0?"+":"")+twinBk.ev.toFixed(4)} (gate)`:"—")} · per $ staked`} />
      <${Kpi} k="$ / hour" v=${html`<span class=${signCls(m.per_hour||0)}>${moneyS(m.per_hour||0)}</span>`}
        sub=${`day ${moneyS(m.pnl_day||0)} · last h ${moneyS(m.pnl_hour||0)}`} />
    </div>

    <div class="grid-2">
      <${Panel} title="🟡 Live — real markets, paper money">${real}</${Panel}>
      <${Panel} title="📊 Backtest — past data (history)">${simBody}</${Panel}>
    </div>

    <${Panel} title="🟡 Live capital (paper) — paper money">
      <${LineChart} series=${[{pts:d.equity, color:"#e8c264", name:nameOf(name)}]} base=${s.initial||CONFIG.START} single=${true} />
    </${Panel}>

    ${btc ? html`<${Panel} title="Backtest — cumulative P&L on past data (start $${fmt0(btc.start||CONFIG.START)}, ${wpct} % of capital)">
      <${LineChart} height=${240} xfmt=${n => "n°"+Math.round(n)} base=${0}
        series=${[
          {pts:btc.is, color:"#a3bac9", name:"in-sample"},
          {pts:btc.oos, color:"#5cba8d", name:"out-of-sample (OOS)"},
        ]} />
      <div style="padding:0 18px 16px"><${Note} tone=${btc.real?"ok":"bad"}>
        ${btc.n} bets simulated, final P&L ${moneyS(btc.final)} (start $${fmt0(btc.start||CONFIG.START)}, ${wpct} %/bet, cap $${fmt0(btc.bet_max||cap)}).
        ${btc.n_oos!=null ? html` Out-of-sample (green): ${btc.n_oos} bets, win rate ${pct(btc.win_oos)}
          vs price paid ${pct(btc.px_oos)} → ${btc.real ? "beats the price (OOS edge)." : "does not beat the price."}` : ""}
        <br/><span class="mut">Faithful replay of the deployed gate on the held-out tape; IS/OOS split at 06-15.
        Out-of-sample (green) is the only judge.</span></${Note}></div>
    </${Panel}>` : null}

    <${Panel} title="Recent bets" flush=${true}>
      <${DataTable} cols=${tradeCols} rows=${trades} empty="No bets yet." />
      ${isMaker ? html`<div style="padding:0 18px 16px"><${Note}><b>Maker</b> entry: instead of crossing
        the book (taker), we <b>post</b> a buy order at the favorite's price
        ("Maker order posted"), fee-free. If it gets hit → "Maker filled". Otherwise, at
        end of window, the order is "pulled" and we cross as taker ("Bet placed (taker)")
        so a favorite running straight away is never missed. On <b>paper</b>, a passive fill takes
        only 0.7 of the size (adverse selection); on the <b>real pilot</b>, maker entry is now
        <b>implemented</b> (<code>post_only</code> order + taker fallback). The gain — capturing the
        spread — is still <b>to be proven in forward-test</b>.</${Note}></div>` : null}
    </${Panel}>

    ${lead ? html`<${Panel} title="Losses by lead level — live validation of favorite_lead" flush=${true}>
      <${DataTable} empty="No decisions recorded." rows=${lead.rows} cols=${[
        {key:"lab", label:"Favorite at entry"},
        {key:"n", label:"Bets", cell:r=>html`<span class="num">${r.n}</span>`},
        {key:"win", label:"Win rate", cell:r=>html`<span class="num">${(100*r.w/r.n).toFixed(0)} %</span>`},
        {key:"slip", label:"Avg slippage", cell:r=>html`<span class="num">${(r.slip/r.n).toFixed(2)} ¢</span>`},
      ]} />
      <div style="padding:0 18px 16px"><${Note}>If "soft" wins clearly less than "firm", the favorite_lead
        thesis holds live. Slippage = price paid − mid at decision (~2 ¢ assumed in the backtest).</${Note}></div>
    </${Panel}>` : null}

    <details class="collapse"><summary>Open positions & orders</summary><div class="body">
      ${posRows.length ? html`<${DataTable} rows=${posRows} cols=${[
        {key:"type", label:"Type"},
        {key:"slug", label:"Market", cell:r=>html`<span class="num">${String(r.slug).replace("btc-updown-","")}</span>`},
        {key:"dir", label:"Side", cell:r=>dirLabel(r.dir)},
        {key:"sh", label:"Shares", cell:r=>html`<span class="num">${r.sh.toFixed(1)}</span>`},
        {key:"px", label:"Price", cell:r=>html`<span class="num">${(+r.px).toFixed(3)}</span>`},
      ]} />` : html`<${Empty}>None — the bot is watching, waiting for a clear favorite.</${Empty}>`}
    </div></details>

    <details class="collapse"><summary>Technical details</summary>
      <div class="body" style="padding-top:14px">${CONFIG.DESCS[bkey(name)] || "—"}</div></details>

    <details class="collapse"><summary>Profit projection (advanced)</summary><div class="body">
      <${Projection} m=${m} stake=${stake} /></div></details>

    <details class="collapse"><summary>Engine log</summary>
      <div class="body"><pre class="log">${(d.log||[]).join("\n")}</pre></div></details>`;
}

// soft (lead < 6 bps) vs firm favourites: win-rate + slippage, the favorite_lead test
function leadBuckets(decisions, trades){
  if(!decisions || !decisions.length) return null;
  const won = {};
  // settlement → outcome, via the shared journal-aligned classifier (isSettleKind also
  // catches the legacy WIN/LOSS aliases the inline startsWith("SETTLE") missed).
  (trades||[]).forEach(t => { if(isSettleKind(t.kind)) won[t.slug] = isWin(t.kind); });
  const b = {soft:{lab:"Soft (lead < 6 bps)", w:0, n:0, slip:0}, firm:{lab:"Firm (lead ≥ 6 bps)", w:0, n:0, slip:0}};
  decisions.forEach(dd => { const o = won[dd.slug]; if(o===undefined) return;
    const k = Math.abs(+dd.lead_bps) < 6 ? "soft" : "firm";
    b[k].n++; b[k].w += o?1:0; b[k].slip += (+dd.slip_c||0); });
  if(!b.soft.n && !b.firm.n) return null;
  return {rows:[b.soft, b.firm].filter(x=>x.n)};
}
