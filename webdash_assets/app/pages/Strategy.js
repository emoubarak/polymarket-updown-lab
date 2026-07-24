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
  if(!d) return html`<div class="empty">Chargement de ${nameOf(name)}…</div>`;
  const s = d.state || {}, m = d.metrics || {}, bk = bkOf(name);
  const isMaker = isMakerName(name);
  const preset = (CONFIG.PRESETS || {})[bkey(name)], frame = partsOf(name).frame;
  const coin = partsOf(name).token;
  const slotStr = preset ? effectiveSlot(preset, frame, coin, CONFIG.COIN_SLOTS) : null;
  const perCoinSlot = !!(preset && CONFIG.COIN_SLOTS && coin && (coin in CONFIG.COIN_SLOTS));
  const slotTag = perCoinSlot ? ` — réglage propre à ${(coin || "").toUpperCase()}`
    : (preset && preset.enter_lo <= 0.27 ? " — créneau élargi 2026-06-25" : "");
  const twinName = isMaker ? takerTwin(name) : null;     // favorite_leadzmk-btc-15m -> favorite_leadz-btc-15m
  const twinBk = isMaker ? bkOf(twinName) : null;        // the GATE's backtest (reference, not the maker's own)
  // Per-coin weighted sizing: cap = book depth ceiling; stake = the runner's CURRENT
  // effective bet (10 % of current capital, capped). $ figures use it so live and backtest
  // dollars share one scale (flat $25 would now lie). cap drives the réglage note.
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
    <${Stat} k="Réussite [IC 95 %]" v=${`${pct(m.win_rate)} [${pct(m.win_lo)}–${pct(m.win_hi)}]`} />
    <${Stat} k="Prix moyen payé" v=${pct(m.avg_price)} />
    <${Stat} k="Réussite récente (30)" tone=${m.n_recent?(aboveRec?"pos":"neg"):""}
      v=${m.n_recent ? `${pct(m.win_recent)} vs prix ${pct(m.px_recent)}` : "—"} />
    <${Stat} k="Gain moy / perte moy" v=${`${moneyS(m.avg_win)} / ${money(m.avg_loss)}`} />
    <${Stat} k="Pire creux · frais" v=${`${money(-m.max_dd)} · ${pct(m.fee_share)} de l'edge`} />
    <${Note} tone=${m.win_rate>m.avg_price?"ok":"bad"}>${m.win_rate>m.avg_price
      ? "Pour l'instant, gagne plus souvent que le prix payé." : "Pour l'instant, ne bat pas le prix payé."}
      <br/><span class="mut">≈ ${moneyS(m.ev_live*stake)} par pari à la mise actuelle ($${fmt0(stake)})</span></${Note}>`
    : html`<${Empty}>Pas encore de favori assez net. Dès qu'un côté franchira le seuil, le 1ᵉʳ pari tombera.</${Empty}>`;

  const simBody = bk ? html`
    <${Stat} k="Réussite" v=${pct(bk.win)} />
    <${Stat} k="Prix moyen payé" v=${pct(bk.px)} />
    <${Stat} k="Jours gagnants" v=${pct(bk.daysp)} />
    <${Note} tone=${bk.win>bk.px?"ok":"bad"}>${bk.win>bk.px
      ? "Bat le prix payé → avantage confirmé sur le passé." : "Ne bat pas le prix payé."}
      <br/><span class="mut">≈ ${moneyS(bk.ev*stake)} par pari à la mise actuelle ($${fmt0(stake)})</span></${Note}>`
    : isMaker ? html`
    <${Note}><b>Pas de backtest maker propre.</b> Le gain d'<b>exécution</b> maker n'est pas
      validable sur données passées (la tape n'a pas la profondeur future du carnet) → c'est un
      <b>forward-test</b> en direct.${twinBk ? html`<br/><br/>Le <b>gate d'entrée</b> est celui de
      <b>${nameOf(twinName)}</b> (même sélection), backtesté : réussite ${pct(twinBk.win)} vs prix
      ${pct(twinBk.px)}, ≈ ${moneyS(twinBk.ev*stake)}/pari à la mise actuelle (n=${twinBk.n}). Le maker ne
      change que l'EXÉCUTION de ce gate.` : ""}</${Note}>`
    : html`<${Empty}>Pas de simulation pour ce réglage.</${Empty}>`;

  // recent trades
  const trades = (d.trades || []).slice(-25).reverse();
  // DISPLAY predicate = the shared entry kinds (BUY/FILL_BUY) PLUS REST_BUY, so the "Misé"
  // column also shows the size POSTED on a resting maker bid (before the 0.7 fill). The
  // journal entry semantics (isEntryKind) exclude REST_BUY (no cash moved) — that extra is
  // kept EXPLICIT here on purpose, not folded into the shared helper.
  const isEntry = k => isEntryKind(k) || !!(k && k.startsWith("REST_BUY"));
  const tradeCols = [
    {key:"ts", label:"Heure", cell:t=>html`<span class="num">${timeOf(t.ts)}</span>`},
    {key:"kind", label:"Type", cell:t=>kindLabel(t.kind)},
    {key:"dir", label:"Sens", cell:t=>dirLabel(t.direction)},
    {key:"price", label:"Prix payé", cell:t=>html`<span class="num">${t.price?(+t.price).toFixed(2):"—"}</span>`},
    // how much was actually staked on this bet = shares × price (REST_BUY shows the
    // size POSTED, before the 0.7 fill). Reveals the stake and whether it's flat or
    // weighted (constant column = mise fixe, varying = pondérée).
    {key:"stake", label:"Misé", cell:t=>{ const v = isEntry(t.kind) ? (+t.shares)*(+t.price) : NaN;
      return isFinite(v) && v>0 ? html`<span class="num">${money(v)}</span>` : html`<span class="mut">—</span>`; }},
    {key:"fee", label:"Frais", cell:t=>html`<span class="num">${(t.fee!==undefined&&t.fee!=="")?money(+t.fee):"—"}</span>`},
    {key:"pnl", label:"Résultat", cell:t=>(+t.pnl)?html`<${Money} v=${+t.pnl} signed=${true} />`:html`<span class="mut">—</span>`},
  ];

  // losses by lead bucket (favorite_lead thesis)
  const lead = leadBuckets(d.decisions, d.trades);

  // positions + orders
  const poss = s.positions || (s.position?[s.position]:[]), ords = s.orders || [];
  const posRows = [
    ...poss.map(p=>({type:"Position", slug:p.slug, dir:p.direction, sh:+p.shares, px:p.avg_price})),
    ...ords.map(o=>({type:"Ordre "+o.side, slug:o.slug, dir:o.direction, sh:+o.shares, px:o.price})),
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

    <${Note}>Trois régimes, à ne pas confondre : <b style="color:#a3bac9">📊 Backtest</b> = rejeu
      sur <b>données passées</b> (l'historique, ce qui se serait passé) · <b class="gold">🟡 En direct
      (papier)</b> = le robot trade <b>maintenant</b> sur les vrais marchés, <b>argent fictif</b>
      (le test « réel » qui tourne) · <b style="color:var(--vermilion)">🔴 Pilote</b> = <b>argent
      réel</b> (onglet Pilote). Cette page = backtest + en direct papier.</${Note}>
    <div style="height:14px"></div>

    ${preset ? html`<${Note}><b>Réglage</b> (<code>${presetName(preset)}</code>) :
      ${gateSummary(preset)} · <b>fenêtre d'entrée ${slotStr}</b>${slotTag}.
      <br/><b>Mise</b> : ${wpct} % du capital, plafond carnet ${(coin||"").toUpperCase()} $${fmt0(cap)} (mise max @+2 ¢).
      <span class="mut">Objectif : maximiser le PnL cumulé du jour (pas l'EV par pari).</span></${Note}>
    <div style="height:14px"></div>` : null}

    <div class="kpis">
      <${Kpi} real=${false} accent=${live>=0?"gold":""} k="🟡 En direct (papier)"
        v=${html`<span class=${signCls(live)}>${moneyS(live)}</span>`}
        sub=${m.n_settled ? `${m.n_settled} paris · ${m.run_days.toFixed(1)} j · marchés réels, argent fictif` : "en attente"} />
      <${Kpi} accent="merc" k="📊 Backtest (données passées)"
        v=${sim!=null?html`<span class=${signCls(sim)}>${moneyS(sim)}</span>`:"—"}
        sub=${bk ? `${bk.n} paris simulés sur l'historique` : isMaker ? "maker = forward-test (pas de backtest propre)" : "pas de backtest"} />
      <${Kpi} k="Avantage / $" v=${m.n_settled?html`<span class=${signCls(m.ev_live)}>${(m.ev_live>=0?"+":"")+m.ev_live.toFixed(4)}</span>`:"—"}
        sub=${`backtest ${bk?(bk.ev>=0?"+":"")+bk.ev.toFixed(4):(isMaker&&twinBk?`${(twinBk.ev>=0?"+":"")+twinBk.ev.toFixed(4)} (gate)`:"—")} · /$ misé`} />
      <${Kpi} k="$ / heure" v=${html`<span class=${signCls(m.per_hour||0)}>${moneyS(m.per_hour||0)}</span>`}
        sub=${`jour ${moneyS(m.pnl_day||0)} · dernière h ${moneyS(m.pnl_hour||0)}`} />
    </div>

    <div class="grid-2">
      <${Panel} title="🟡 En direct — marchés réels, argent fictif">${real}</${Panel}>
      <${Panel} title="📊 Backtest — données passées (historique)">${simBody}</${Panel}>
    </div>

    <${Panel} title="🟡 Capital en direct (papier) — argent fictif">
      <${LineChart} series=${[{pts:d.equity, color:"#e8c264", name:nameOf(name)}]} base=${s.initial||CONFIG.START} single=${true} />
    </${Panel}>

    ${btc ? html`<${Panel} title="Backtest — P&L cumulé sur le passé (départ $${fmt0(btc.start||CONFIG.START)}, ${wpct} % du capital)">
      <${LineChart} height=${240} xfmt=${n => "n°"+Math.round(n)} base=${0}
        series=${[
          {pts:btc.is, color:"#a3bac9", name:"in-sample"},
          {pts:btc.oos, color:"#5cba8d", name:"hors-échantillon (OOS)"},
        ]} />
      <div style="padding:0 18px 16px"><${Note} tone=${btc.real?"ok":"bad"}>
        ${btc.n} paris simulés, P&L final ${moneyS(btc.final)} (départ $${fmt0(btc.start||CONFIG.START)}, ${wpct} %/pari, plafond $${fmt0(btc.bet_max||cap)}).
        ${btc.n_oos!=null ? html` Hors-échantillon (vert) : ${btc.n_oos} paris, réussite ${pct(btc.win_oos)}
          vs prix payé ${pct(btc.px_oos)} → ${btc.real ? "bat le prix (avantage OOS)." : "ne bat pas le prix."}` : ""}
        <br/><span class="mut">Rejeu fidèle du gate déployé sur le tape caché ; split IS/OOS au 06-15.
        L'hors-échantillon (vert) est le seul juge.</span></${Note}></div>
    </${Panel}>` : null}

    <${Panel} title="Paris récents" flush=${true}>
      <${DataTable} cols=${tradeCols} rows=${trades} empty="Aucun pari pour l'instant." />
      ${isMaker ? html`<div style="padding:0 18px 16px"><${Note}>Entrée <b>maker</b> : au lieu de
        traverser le carnet (taker), on <b>pose</b> un ordre d'achat au prix du favori
        (« Ordre maker posé »), sans frais. S'il se fait toucher → « Maker rempli ». Sinon, en
        fin de fenêtre, l'ordre est « retiré » et on traverse en taker (« Pari placé (taker) »)
        pour ne jamais rater un favori parti tout droit. En <b>papier</b>, un fill passif ne prend
        que 0,7 de la taille (anti-sélection) ; en <b>pilote réel</b>, l'entrée maker est désormais
        <b>implémentée</b> (ordre <code>post_only</code> + repli taker). Le gain — récupérer le
        spread — reste à <b>prouver en forward-test</b>.</${Note}></div>` : null}
    </${Panel}>

    ${lead ? html`<${Panel} title="Pertes par niveau de lead — validation live de favorite_lead" flush=${true}>
      <${DataTable} empty="Pas de décisions enregistrées." rows=${lead.rows} cols=${[
        {key:"lab", label:"Favori à l'entrée"},
        {key:"n", label:"Paris", cell:r=>html`<span class="num">${r.n}</span>`},
        {key:"win", label:"Réussite", cell:r=>html`<span class="num">${(100*r.w/r.n).toFixed(0)} %</span>`},
        {key:"slip", label:"Slippage moyen", cell:r=>html`<span class="num">${(r.slip/r.n).toFixed(2)} ¢</span>`},
      ]} />
      <div style="padding:0 18px 16px"><${Note}>Si « mou » gagne nettement moins que « établi », la thèse
        de favorite_lead tient en réel. Slippage = prix payé − mid à la décision (~2 ¢ supposés au backtest).</${Note}></div>
    </${Panel}>` : null}

    <details class="collapse"><summary>Positions en cours & ordres</summary><div class="body">
      ${posRows.length ? html`<${DataTable} rows=${posRows} cols=${[
        {key:"type", label:"Type"},
        {key:"slug", label:"Marché", cell:r=>html`<span class="num">${String(r.slug).replace("btc-updown-","")}</span>`},
        {key:"dir", label:"Sens", cell:r=>dirLabel(r.dir)},
        {key:"sh", label:"Parts", cell:r=>html`<span class="num">${r.sh.toFixed(1)}</span>`},
        {key:"px", label:"Prix", cell:r=>html`<span class="num">${(+r.px).toFixed(3)}</span>`},
      ]} />` : html`<${Empty}>Aucune — le robot observe en attendant un favori net.</${Empty}>`}
    </div></details>

    <details class="collapse"><summary>Détails techniques</summary>
      <div class="body" style="padding-top:14px">${CONFIG.DESCS[bkey(name)] || "—"}</div></details>

    <details class="collapse"><summary>Projection de gains (avancé)</summary><div class="body">
      <${Projection} m=${m} stake=${stake} /></div></details>

    <details class="collapse"><summary>Journal du moteur</summary>
      <div class="body"><pre class="log">${(d.log||[]).join("\n")}</pre></div></details>`;
}

// soft (lead < 6 bps) vs firm favourites: win-rate + slippage, the favorite_lead test
function leadBuckets(decisions, trades){
  if(!decisions || !decisions.length) return null;
  const won = {};
  // settlement → outcome, via the shared journal-aligned classifier (isSettleKind also
  // catches the legacy WIN/LOSS aliases the inline startsWith("SETTLE") missed).
  (trades||[]).forEach(t => { if(isSettleKind(t.kind)) won[t.slug] = isWin(t.kind); });
  const b = {soft:{lab:"Mou (lead < 6 bps)", w:0, n:0, slip:0}, firm:{lab:"Établi (lead ≥ 6 bps)", w:0, n:0, slip:0}};
  decisions.forEach(dd => { const o = won[dd.slug]; if(o===undefined) return;
    const k = Math.abs(+dd.lead_bps) < 6 ? "soft" : "firm";
    b[k].n++; b[k].w += o?1:0; b[k].slip += (+dd.slip_c||0); });
  if(!b.soft.n && !b.firm.n) return null;
  return {rows:[b.soft, b.firm].filter(x=>x.n)};
}
