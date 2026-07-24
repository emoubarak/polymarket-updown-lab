// The method — what this is, in plain French. Lifted out of the overview so the
// home screen stays a dashboard, not a manual. Closes with "le bestiaire" : one
// complete description per DEPLOYED brain, read straight from CONFIG (labels,
// taglines, gates, technical paragraphs) so it never drifts from the code.
import { html } from '../preact.js';
import { CONFIG } from '../api.js';
import { partsOf, gateSummary, fmt0 } from '../format.js';

// Ordre d'affichage des coins dans le tableau de capacité (puis tout extra du /config).
// Dérivé du registre central servi par /config (CONFIG.COINS) ; littéral en secours si la
// config n'est pas encore chargée. Accesseur (pas une const de module) car CONFIG est
// réassigné par loadConfig() avant le rendu.
const coinOrder = () => (CONFIG.COINS && CONFIG.COINS.length)
  ? CONFIG.COINS : ["btc", "eth", "sol", "xrp", "doge", "bnb"];

// Distinct deployed brains, in lineup order, each with the coins/frames it runs on.
// A "stratégie" = a brain (zlead, zleadmk, …); coin/frame are its parameterisations,
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
      ${gate ? html`<p class="strat-meta"><b>Réglage</b> · ${gate}</p>` : null}
      ${desc ? html`<p class="strat-desc">${desc}</p>` : null}
    </article>`;
}

// La capacité des carnets : la mise max absorbable par coin ET par frame (profondeur sur
// l'ask du favori entre 0,85 et 0,95), qui plafonne CHAQUE pari (paper ET pilote réel).
// Lue depuis /config (COIN_DEPTH = {coin:{frame:$}}). 5m et 15m = carnets distincts.
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
      <div class="panel-head"><h2>Capacité des carnets — la mise max par coin <i>et par frame</i></h2>
        <span class="aside">départ $${fmt0(CONFIG.START || 100)} · ${pct} % du capital par pari</span></div>
      <div class="panel-body">
        <p class="mut" style="margin:0 0 14px;max-width:74ch;line-height:1.6">Chaque pari fait
          <b>${pct} % du capital</b>, <b>plafonné à la profondeur du carnet</b> du favori — le $ qu'<b>UN
          seul acteur</b> peut filler sur l'ask <b>entre 0,85 et 0,95</b> <i>pendant que les autres
          achètent aussi</i>. Mesuré <b>par marché</b> (5m et 15m = carnets distincts) comme la médiane
          du <b>plus gros wallet seul</b> par fenêtre — pas le flux agrégé, qui est partagé entre 9 à
          185 acheteurs simultanés (on n'en capte que ~15–35 % sur les coins liquides). Source :
          historique des trades, <code>tools/measure_capacity.py</code>. Le paper ET le pilote réel
          utilisent ce plafond.</p>
        <table class="tbl">
          <thead><tr><th>Coin</th>${frames.map(f => html`<th>Mise max ${f}</th>`)}</tr></thead>
          <tbody>
            ${coins.map(c => html`<tr>
              <td><b>${c.toUpperCase()}</b></td>
              ${frames.map(f => { const cap = (depth[c] || {})[f]; const dead = cap != null && cap <= 12;
                return html`<td class=${"num" + (dead ? " mut" : "")}>${cap != null ? html`$${fmt0(cap)}` : "—"}${dead ? html` <span class="mut">— quasi mort</span>` : ""}</td>`; })}
            </tr>`)}
          </tbody>
        </table>
        <p class="mut" style="margin:14px 0 0;max-width:74ch;line-height:1.6">Tant que ${pct} % du capital
          reste sous ces plafonds, la mise compose ; au-delà, elle bute sur le carnet. BTC porte
          l'essentiel de la capacité — et son <b>5m est plus profond que son 15m</b> (l'inverse des alts).</p>
      </div>
    </section>`;
}

export function About(){
  const brains = deployedBrains();
  return html`
    <div class="page-head">
      <div><h1><span class="glyph">☉</span>La méthode</h1>
        <p class="sub">Pourquoi ce robot parie, le seul juge qui décide si l'avantage est réel,
          et ce que fait chaque stratégie en course.</p></div>
    </div>
    <section class="panel"><div class="panel-body" style="max-width:74ch;line-height:1.7">
      <p><b>Le marché.</b> Toutes les 5 ou 15 min, Polymarket paie selon que Bitcoin clôt au-dessus ou en
        dessous de son ouverture. Vers la fin d'une fenêtre, un côté est déjà donné quasi gagnant — le
        <b class="gold">favori</b> (coté 0,85–0,95).</p>
      <p><b>L'edge.</b> La foule <b>surpaie</b> le côté presque mort (le <i>longshot</i>), donc le favori
        est légèrement sous-coté. On l'achète et on porte jusqu'au règlement. Aucune sortie : c'est un pari
        sur la <b>calibration des prix</b>, pas sur la direction de BTC. Le <i>magnum opus</i> : transmuter
        ce minuscule biais en or, réglage après réglage.</p>
      <p><b>Le payoff est brutalement asymétrique</b> — gagner rapporte ~+0,11, perdre coûte −1,00. Un
        groupe de retournements fait mal. D'où les variantes (filtre de volatilité, plancher de lead) :
        chacune purge un mode d'échec observé dans les journaux.</p>
      <p><b>Le juge unique — le <span class="verd">tripwire</span>.</b> Sur ~150 paris, la réussite réelle
        bat-elle le prix moyen payé ? Au-dessus → avantage réel. Égal ou en dessous → pas d'avantage, on
        retire (c'est ainsi que les six cerveaux ésotériques d'origine sont morts). En dessous de 150 paris,
        tout chiffre est du bruit (~4 paris/h).</p>
      <p><b>Deux vérités, deux couleurs.</b> <span class="gold">L'or</span> = on a gagné de l'argent.
        <span class="verd">Le vert-de-gris</span> = l'avantage est statistiquement prouvé. Une stratégie peut
        être dans le vert sans être prouvée (chance), ou prouvée tout en étant momentanément dans le rouge.</p>
      <p class="mut">Tout est en lecture seule sur les vraies APIs. Le paper trading n'envoie aucun ordre.
        Seul <b>Le pilote</b> peut toucher de l'argent réel, et seulement une fois explicitement armé.</p>
    </div></section>

    <${CapacityTable} />

    <section class="panel">
      <div class="panel-head"><h2>Le bestiaire — chaque stratégie en course</h2>
        <span class="aside">${brains.length} cerveau${brains.length > 1 ? "x" : ""} · un moteur paramétré</span></div>
      <div class="panel-body">
        <p class="mut" style="margin:0 0 16px;max-width:74ch;line-height:1.6">Une <b>stratégie</b> = un cerveau ;
          le coin (BTC/ETH/SOL/XRP/DOGE/BNB) et la durée (5m/15m) ne sont que ses paramétrages. Toutes
          parient le <b>même favori</b> — c'est UN edge décliné, pas N indépendants.</p>
        <div class="strat-list">
          ${brains.map(b => html`<${StratCard} ...${b} />`)}
        </div>
      </div>
    </section>`;
}
