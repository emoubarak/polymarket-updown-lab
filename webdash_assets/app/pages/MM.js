// Rewards-MM — the std0 liquidity-rewards harvester cockpit. A SEPARATE control plane from
// "Le pilote" (different engine run_rewardmm.py, different telemetry: two-sided inventory
// Up/Down, maker fills, the rebate, marked P&L vs cash) so it can never touch the armed zlead
// pilots. Same shared real wallet ⇒ REAL arming is real money. The REAL on-chain path is still
// QUOTA-BOUND until the Gnosis Safe lands (research/std0/NEXT-STEPS.md) — DRY runs cleanly now.
import { html, useState } from '../preact.js';
import { CONFIG, usePoll, postMM } from '../api.js';
import { money, moneyS, signCls, timeOf, fmt0 } from '../format.js';
import { Kpi, Panel, Tag, Empty, Money, Note } from '../components/ui.js';
import { DataTable } from '../components/DataTable.js';
import { LineChart } from '../components/LineChart.js';

// Defaults track run_rewardmm.py; spot-anchor on + btc-5m = the research-recommended target
// (research/std0/NEXT-STEPS.md). Advanced knobs hidden behind a toggle to keep the form sane.
const DEFAULTS = {underlying:"btc", interval:"5m", mint_usd:15, clip:5, quote_dist:0.01,
  spot_anchor:true, beta:0.5, back_off:0, max_inv:10, min_quote:0.30, max_quote:0.70,
  recenter_eps:0.01, flatten_buf:45, kill_loss:8, poll:3};

const frameOpts = () => (CONFIG.FRAMES && CONFIG.FRAMES.length) ? CONFIG.FRAMES : ["5m","15m"];
const coinOpts  = () => ((CONFIG.COINS && CONFIG.COINS.length) ? CONFIG.COINS
  : ["btc","eth","sol","xrp","doge","bnb"]).map(k => [k, k.toUpperCase()]);

const cfgLabel = c => `${String(c.underlying||"").toUpperCase()} ${c.interval}`;
const modeOf = r => !r.alive ? "arrêté" : (r.status.mode==="live" ? "🔴 RÉEL" : "DRY-RUN");
const isLive = r => r.alive && r.status.mode==="live";
// neutrality reads as the std0 invariant: held Up vs Down, Δ should hover ≈ 0.
const neutTxt = r => `${fmt0(r.inv_up)}/${fmt0(r.inv_dn)}${r.neutrality?` (Δ${r.neutrality>0?"+":""}${r.neutrality})`:" (Δ0)"}`;

export function MM(){
  const d = usePoll("/mm-data", 6000).data;
  const [cfg, setCfg] = useState(DEFAULTS);
  const [selected, setSelected] = useState(null);
  const [adv, setAdv] = useState(false);
  const set = (k,v) => setCfg(o => ({...o, [k]: v}));

  async function act(a, id){
    if(a === "start_live"){
      if(!confirm(`⚠ ARMER LE REWARDS-MM EN RÉEL\n\nDe VRAIS ordres maker deux-côtés + un MINT on-chain `
        + `seront envoyés sur Polymarket :\nrewardMM · ${String(cfg.underlying).toUpperCase()} ${cfg.interval} `
        + `· mint $${cfg.mint_usd} · clip ${cfg.clip}\n\n`
        + `⚠ Le chemin RÉEL reste EXPÉRIMENTAL et QUOTA-BOUND tant que le Gnosis Safe n'est pas déployé `
        + `(les merges relayer peuvent caler). Net-positif seulement à grosse échelle. Petite mise.\n\n`
        + `De l'argent que tu peux perdre. Confirmer l'armement ?`)) return;
    }
    if(a === "resume" && id){
      const r = (d.runners||[]).find(x => x.id === id);
      if(r && r.status.armed_mode === "live"
        && !confirm(`⚠ REPRENDRE EN RÉEL\n\n${id} repart en argent réel. Confirmer la reprise ?`)) return;
    }
    if(a === "forget" && !confirm("Supprimer ce runner de la liste ?\nSon état sur disque est conservé (le relancer le récupère).")) return;

    let body;
    if(a === "stop" || a === "resume" || a === "forget"){
      body = new URLSearchParams({action:a, id}).toString();
    } else {
      body = new URLSearchParams({action:a, underlying:cfg.underlying, interval:cfg.interval,
        mint_usd:cfg.mint_usd, clip:cfg.clip, quote_dist:cfg.quote_dist,
        spot_anchor:cfg.spot_anchor?"1":"0", beta:cfg.beta, back_off:cfg.back_off,
        max_inv:cfg.max_inv, min_quote:cfg.min_quote, max_quote:cfg.max_quote,
        recenter_eps:cfg.recenter_eps, flatten_buf:cfg.flatten_buf,
        kill_loss:cfg.kill_loss, poll:cfg.poll}).toString();
    }
    try {
      const r = await postMM(body);
      alert(r.msg || (r.ok ? "ok" : "échec"));
    } catch(e){ alert("erreur réseau : " + e); }
  }

  if(!d) return html`<div class="empty">Chargement du rewards-MM…</div>`;
  const runners = d.runners || [];
  const agg = d.agg || {};
  const sr = runners.find(r => r.id === selected) || runners[0] || null;

  const sel = (key, opts) => html`<select class="ctl" value=${cfg[key]}
    onChange=${e=>set(key, e.target.value)}>
    ${opts.map(o => { const [v,l] = Array.isArray(o)?o:[o,o]; return html`<option value=${v}>${l}</option>`; })}</select>`;
  const num = (key, step=1) => html`<input class="ctl" type="number" step=${step} value=${cfg[key]}
    onInput=${e=>set(key, +e.target.value)} style="width:96px" />`;

  const cols = [
    {key:"mode", label:"État", cell:r=>html`<${Tag} tone=${isLive(r)?"real":""}>${modeOf(r)}</${Tag}>${r.killed?html` <${Tag} tone="bad">🛑 kill</${Tag}>`:r.alive&&!r.fresh?html` <span class="mut" title="snapshot figé > 60 s">⚠</span>`:null}`},
    {key:"id", label:"Marché", cell:r=>html`<span class="cell-name">${sr&&sr.id===r.id?html`<span class="g">▸</span>`:null}
      <span class="g">⚒</span> ${cfgLabel(r.status.config||{})}</span>`},
    {key:"mark", label:"P&L marqué", sortable:true, get:r=>r.mark, cell:r=>html`<${Money} v=${r.mark} signed=${true} />`},
    {key:"rebate", label:"Rebate≈", sortable:true, get:r=>r.rebate, cell:r=>html`<span class="num">${money(r.rebate)}</span>`},
    {key:"fills", label:"Fills", sortable:true, get:r=>r.fills, cell:r=>html`<span class="num">${r.fills}</span>`},
    {key:"neut", label:"Inv Up/Dn", cell:r=>html`<span class=${"num "+(Math.abs(r.neutrality)>r.minted*0.5+1?"neg":"")}>${neutTxt(r)}</span>`},
    {key:"resting", label:"Quotes", cell:r=>html`<span class="num">${r.resting}</span>`},
    {key:"act", label:"", cell:r=>{ const stop = e=>e.stopPropagation();
      return r.alive
        ? html`<button class="btn sm" onClick=${e=>{stop(e); act("stop", r.id);}}>⏸ Pause</button>`
        : html`<span class="row-actions" style="display:inline-flex;gap:6px">
            <button class="btn sm ${r.status.armed_mode==="live"?"danger":""}" onClick=${e=>{stop(e); act("resume", r.id);}}>▶ Reprendre</button>
            <button class="btn sm danger" onClick=${e=>{stop(e); act("forget", r.id);}}>✕ Supprimer</button></span>`;
    }},
  ];

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">⚒</span>Rewards-MM — moissonneur de liquidité (std0)</h1>
        <p class="sub">Maker deux-côtés serré autour du mid, neutre, redeem au règlement. Le revenu = le
          <b>rebate</b> maker (≈0,014·Σp(1−p)·parts), pas le spread. ${runners.length} runner${runners.length>1?"s":""}
          (${agg.n_real||0} en réel).</p></div>
      <div class="head-aside">${d.armed_env ? html`<${Tag} tone="real">clé prête</${Tag}>` : html`<${Tag}>clé absente</${Tag}>`}
        ${d.stale ? html` <${Tag} tone="bad" title=${`moteur AWS injoignable depuis ${d.stale_age}s`}>flux figé ${d.stale_age}s</${Tag}>`:null}</div>
    </div>

    ${(agg.n_real > 0) ? html`<div class="kpis" style="margin-bottom:16px">
      <${Kpi} real=${true} k="P&L marqué — runners réels"
        v=${html`<span class=${signCls(agg.mark)}>${moneyS(agg.mark)}</span>`}
        sub=${`${agg.n_real} runner${agg.n_real>1?"s":""} réel · marqué (inv inclus)`} />
      <${Kpi} accent="gold" k="Rebate maker estimé"
        v=${money(agg.rebate)} sub=${`${agg.fills} fills · le vrai moteur du gain std0`} />
      <${Kpi} k="P&L marqué + rebate" v=${html`<span class=${signCls(agg.mark+agg.rebate)}>${moneyS(agg.mark+agg.rebate)}</span>`}
        sub="ce que std0 monétise à grande échelle" />
    </div>` : null}

    <div class="howto warn">⚠ <b>Argent réel.</b> « Armer le RÉEL » envoie de VRAIS ordres maker + un mint on-chain.
      Le chemin réel reste <b>expérimental / quota-bound</b> tant que le <b>Gnosis Safe</b> self-submittable n'est pas
      déployé (<code>research/std0/NEXT-STEPS.md</code>) : net-positif seulement à grosse échelle (blocs std0 ~$500–2500).
      Commence par <b>DRY-RUN</b> (zéro argent, zéro clé) pour voir la mécanique churner.</div>

    <${Panel} title=${html`Armer un runner — <span class="glyph" style="font-size:18px">⚒</span> ${cfgLabel(cfg)}`}
      aside=${html`<span class="mut">identité = mm-${cfg.underlying}-${cfg.interval} (un seul par identité)</span>`}>
      <div class="controls" style="margin-bottom:12px">
        <span class="field"><b>Actif</b> ${sel("underlying", coinOpts())}</span>
        <span class="field"><b>Frame</b> ${sel("interval", frameOpts())}</span>
        <span class="field"><b>Mint</b> ${num("mint_usd",5)} $ <span class="mut" title="taille des sets mintés = munitions ASK (récupérés au merge)">munitions</span></span>
        <span class="field"><b>Clip</b> ${num("clip",1)} parts <span class="mut" title="parts par ordre au repos — min CLOB = 5">≥5</span></span>
        <span class="field"><b>Dist. quote</b> ${num("quote_dist",0.005)} <span class="mut" title="distance bid/ask au mid — serré = plus de fills = plus de rebate">serré</span></span>
        <span class="field"><b>Ancrage spot</b> <input type="checkbox" checked=${cfg.spot_anchor} onChange=${e=>set("spot_anchor", e.target.checked)} /> <span class="mut" title="centre les quotes sur le fair-p Binance (devance le CLOB lag, coupe le pickoff)">anti-pickoff</span></span>
        <span class="field"><b>Poll</b> ${num("poll",0.5)} s <span class="mut" title="colocation Dublin ~24ms → cadence sub-seconde possible sur le 5m rapide">colo</span></span>
      </div>
      <div class="controls" style="margin-bottom:12px">
        <button class="btn sm ${adv?"primary":""}" onClick=${()=>setAdv(a=>!a)}>${adv?"▾":"▸"} Réglages avancés</button>
      </div>
      ${adv ? html`<div class="controls" style="margin-bottom:16px">
        <span class="field"><b>β anchor</b> ${num("beta",0.1)} <span class="mut" title="0=mid CLOB pur, 1=fair-p spot pur">blend</span></span>
        <span class="field"><b>Recul touche</b> ${num("back_off",0.005)} <span class="mut" title="s'assoit derrière la touche pour ne pas crosser (post-only) et rester">rest</span></span>
        <span class="field"><b>Inv max</b> ${num("max_inv",1)} parts <span class="mut" title="parts directionnelles max avant de skewer les quotes (défend la neutralité)">skew</span></span>
        <span class="field"><b>Quote min</b> ${num("min_quote",0.05)}</span>
        <span class="field"><b>Quote max</b> ${num("max_quote",0.05)} <span class="mut" title="n'opère que si le mid est dans [min,max] (près de 0,5 = deux-côtés)">bande</span></span>
        <span class="field"><b>ε recentre</b> ${num("recenter_eps",0.005)} <span class="mut" title="re-poste quand le mid dérive de plus que ça">re-peg</span></span>
        <span class="field"><b>Flatten</b> ${num("flatten_buf",5)} s <span class="mut" title="arrête de quoter ce délai avant le règlement, puis merge">avant règlt</span></span>
        <span class="field"><b>Kill</b> ${num("kill_loss",1)} $ <span class="mut" title="coupe tout si le P&L marqué descend sous −kill">stop dur</span></span>
      </div>` : null}
      <${Note}>ℹ Cible recherche = <b>BTC 5m</b> (64 % du volume/rebate de std0) + <b>ancrage spot</b> + <b>poll
        sub-seconde</b> (colocation Dublin). Le merge récupère le bloc minté avant le règlement (robuste vs redeem).</${Note}>
      <div class="controls" style="margin-top:14px">
        <button class="btn primary" onClick=${()=>act("start_dry")}>▶ Lancer DRY-RUN</button>
        <button class="btn danger" onClick=${()=>act("start_live")}>🔴 Armer le RÉEL</button>
      </div>
    </${Panel}>

    <${Panel} title="Runners rewards-MM" flush=${true}
      aside=${runners.length?html`<span class="mut">clique une ligne pour le détail · ⏸ pause · ▶ reprend · ✕ supprime</span>`:null}>
      <${DataTable} empty="Aucun runner. Configure-en un ci-dessus et lance le DRY-RUN."
        rows=${runners} cols=${cols} onRowClick=${r=>setSelected(r.id)} />
    </${Panel}>

    ${sr ? html`<${MMDetail} sr=${sr} />` : null}`;
}

function MMDetail({sr}){
  const c = sr.status.config || {};
  const started = sr.status.started;
  const total = sr.mark + sr.rebate;
  return html`
    <${Panel} title=${html`Détail — <span class="glyph" style="font-size:18px">⚒</span> ${cfgLabel(c)}`}
      aside=${html`<span class="mut">${modeOf(sr)}${sr.alive&&started?` · depuis ${timeOf(started)}`:""}${sr.cur_slug?` · ${String(sr.cur_slug).replace(c.underlying+"-updown-","")}`:""}</span>`}>
      <div class="kpis">
        <${Kpi} real=${isLive(sr)} k="P&L marqué (inv inclus)"
          v=${html`<span class=${signCls(sr.mark)}>${moneyS(sr.mark)}</span>`}
          sub=${`cash brut ${moneyS(sr.realized)} · mint $${fmt0(sr.minted)}`} />
        <${Kpi} accent="gold" k="Rebate maker estimé" v=${money(sr.rebate)}
          sub=${`${sr.fills} fills · marqué+rebate ${moneyS(total)}`} />
        <${Kpi} k="Neutralité — inv Up/Down" num=${false}
          tone=${Math.abs(sr.neutrality)>sr.minted*0.5+1?"neg":""}
          v=${neutTxt(sr)} sub=${`Δ ${sr.neutrality} part(s) · neutre = redeem net-zéro`} />
        <${Kpi} k="Quotes au repos" v=${sr.resting} sub=${`dépensé ${money(sr.spent)} · reçu ${money(sr.received)}`} />
      </div>
    </${Panel}>

    <${Panel} title="Cash-flow réalisé (cumulé)"
      aside=${html`<span class="mut">≠ P&L marqué — le cash plonge au mint, remonte au merge/redeem</span>`}>
      ${(sr.curve||[]).length>=2
        ? html`<${LineChart} series=${[{pts:sr.curve, color:"#e8c264", name:"cash réalisé"}]} base=${0} single=${true} height=${220} />`
        : html`<${Empty}>Courbe disponible dès le 2ᵉ mouvement de cash (fill/merge).</${Empty}>`}
    </${Panel}>

    <${Panel} title="Journal du runner" flush=${true}>
      <${DataTable} empty="Aucun mouvement encore. Lance le DRY-RUN pour voir les quotes/fills simulés."
        rows=${(sr.journal||[]).slice(-30).reverse()} cols=${journalCols} />
    </${Panel}>`;
}

const KIND_LABEL = {MINT:"⚒ mint", MERGE:"⚗ merge", FILL:"● fill", BID:"bid", ASK:"ask"};
const journalCols = [
  {key:"ts", label:"Heure", cell:r=>html`<span class="num">${timeOf(+r.ts)}</span>`},
  {key:"kind", label:"Type", cell:r=>html`<span>${KIND_LABEL[r.kind]||r.kind}</span>`},
  {key:"order", label:"Ordre", cell:r=>html`<span class="mut">${r.order||"—"}</span>`},
  {key:"price", label:"Prix", cell:r=>html`<span class="num">${r.price?(+r.price).toFixed(3):"—"}</span>`},
  {key:"shares", label:"Parts", cell:r=>html`<span class="num">${r.shares?fmt0(+r.shares):"—"}</span>`},
  {key:"inv", label:"Inv Up/Dn", cell:r=>html`<span class="num">${fmt0(+r.inv_up)}/${fmt0(+r.inv_dn)}</span>`},
  {key:"realized", label:"Cash", cell:r=>r.realized!==""&&r.realized!==undefined?html`<${Money} v=${+r.realized} signed=${true} />`:html`<span class="mut">—</span>`},
];
