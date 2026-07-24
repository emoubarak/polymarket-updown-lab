// Corrélations — how many INDEPENDENT bets the race really is (the "ETF de
// stratégies" question), through the lens that matters for an asymmetric-payoff book:
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
  if(!data) return html`<div class="empty">Calcul des corrélations…</div>`;
  const { names, matrix, losses, redundant, n_strats, n_groups, avg_abs_loss,
          n_pairs_loss, avg_abs_rho, n_pairs, struct_groups, avg_abs_struct, n_pairs_struct,
          min_overlap, min_losses, redundant_rho } = data;

  if(!n_strats) return html`<${Empty}>Aucune stratégie dans la course.</${Empty}>`;
  const lossView = view === "loss", structView = view === "struct";
  const val = c => structView ? c.struct : lossView ? c.loss : c.rho;

  const idx = names.map((_, i) => i + 1);   // 1..N column heads (compact) ↔ numbered rows
  const redCols = [
    {key:"a", label:"Stratégie A", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.a)}</span><span class="lead">${nameOf(r.a)}</span></span>`},
    {key:"b", label:"Stratégie B", cell:r=>html`<span class="cell-name"><span class="g">${glyphOf(r.b)}</span><span class="lead">${nameOf(r.b)}</span></span>`},
    {key:"loss", label:"φ pertes", sortable:true, get:r=>Math.abs(r.loss),
      cell:r=>html`<span class="num neg">${rho2(r.loss)}</span>`},
    {key:"both", label:"Pertes simultanées", sortable:true,
      cell:r=>html`<span class="num">${r.both}</span><span class="mut"> / ${r.la}·${r.lb}</span>`},
    {key:"n", label:"Fenêtres", sortable:true, cell:r=>html`<span class="num mut">${r.n}</span>`},
  ];

  const toggle = html`<span class="filters" style="display:inline-flex;gap:6px">
    <button class=${"btn sm" + (lossView ? " primary" : "")} onClick=${()=>setView("loss")}>Pertes simultanées</button>
    <button class=${"btn sm" + (structView ? " primary" : "")} onClick=${()=>setView("struct")}>Structurel (spot)</button>
    <button class=${"btn sm" + (view==="pnl" ? " primary" : "")} onClick=${()=>setView("pnl")}>P&L complet</button></span>`;

  return html`
    <div class="page-head">
      <div><h1><span class="glyph">▦</span>Corrélations</h1>
        <p class="sub">Un panier de stratégies peut <i>ressembler</i> à plusieurs paris tout en chargeant
          un seul facteur de risque. Et ce qui ruine un livre au payoff asymétrique (favori : +0,11 gagné /
          −1,00 perdu), ce sont les <b>pertes qui se groupent</b> : un cluster de flips dans la même fenêtre.
          On mesure donc, en direct, à quel point les runners <b>perdent ensemble</b> — le préalable à un
          « ETF » qui armerait le top X sans empiler des paris qui cratèrent en même temps. Le φ des pertes
          réalisées se remplit lentement (favoris ~90 %) ; l'onglet <b>Structurel</b> donne la réponse
          <i>tout de suite</i> via le co-mouvement spot des sous-jacents.</p></div>
    </div>

    <div class="kpis">
      <${Kpi} k="Stratégies" v=${n_strats} sub="dans la course" />
      <${Kpi} accent="gold" k="Facteurs de risque (spot)" v=${struct_groups==null ? "—" : struct_groups}
        sub=${struct_groups==null ? "corrélation spot indisponible"
          : `paris réellement indépendants — même coin ou spot corrélé (≥${redundant_rho}) fusionnés · dispo tout de suite`} />
      <${Kpi} k="Groupes indépendants (pertes)" v=${n_groups}
        sub=${`fusion des paires qui perdent ensemble (|φ|≥${redundant_rho}) — se remplit lentement`} />
      <${Kpi} accent="merc" k="Corrélation des pertes" v=${avg_abs_loss==null ? "—" : rho2(avg_abs_loss)}
        sub=${avg_abs_loss==null ? "pas encore assez de pertes communes" : `|φ| moyen · ${n_pairs_loss} paires mesurées`} />
      <${Kpi} k="Doublons de risque" v=${redundant.length}
        sub=${`paires qui perdent ensemble (|φ|≥${redundant_rho})`} tone=${redundant.length ? "neg" : "mut"} />
    </div>

    <${Panel} title=${structView ? "Matrice — co-mouvement spot des sous-jacents (structurel)"
        : lossView ? "Matrice — corrélation des pertes simultanées (φ)" : "Matrice — corrélation du P&L complet (ρ)"}
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
                  ? `${nameOf(n)} — ${c.n} fenêtres réglées, ${losses[n]} perdues`
                  : structView
                    ? (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — co-mouvement spot indisponible (un des coins n'a pas de données)`
                        : v >= 0.999
                          ? `${nameOf(n)} × ${nameOf(names[j])} — MÊME sous-jacent : un seul pari structurellement (flips simultanés garantis)`
                          : `${nameOf(n)} × ${nameOf(names[j])} — corrélation spot=${v.toFixed(2)} (co-mouvement des sous-jacents = driver des flips simultanés)`)
                    : lossView
                    ? (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — trop peu de pertes communes pour un φ fiable `
                          + `(perdent ${c.la}× et ${c.lb}× sur ${c.n} fenêtres, ${c.both} simultanées ; il faut ≥${min_losses} chacune)`
                        : `${nameOf(n)} × ${nameOf(names[j])} — φ pertes=${v.toFixed(2)} · ${c.both} pertes simultanées `
                          + `(sur ${c.la}/${c.lb}, ${c.n} fenêtres${c.lift!=null ? `, ×${c.lift.toFixed(1)} vs hasard` : ""})`)
                    : (v == null
                        ? `${nameOf(n)} × ${nameOf(names[j])} — seulement ${c.n} fenêtres communes (< ${min_overlap})`
                        : `${nameOf(n)} × ${nameOf(names[j])} — ρ=${v.toFixed(2)} sur ${c.n} fenêtres`);
                return html`<td style=${"background:"+bg} title=${ttl}
                  class=${(!diag && v!=null && Math.abs(v)>=redundant_rho) ? "red" : ""}>${txt}</td>`;
              })}
            </tr>`)}
          </tbody>
        </table>
      </div>
      <${Note}>${structView
        ? html`Chaque case = corrélation des <b>rendements spot</b> des deux <i>sous-jacents</i> (grille 15m récente,
            ≈66 fenêtres, source Binance). Pourquoi : un favori flippe quand le sous-jacent se retourne dans la fenêtre,
            donc <b>des coins corrélés flippent — et perdent — ensemble</b>. C'est le <b>driver</b> des pertes groupées,
            mesurable <b>tout de suite</b> alors que le φ de pertes réalisées met des mois à se remplir (favoris ~90 %).
            Même coin = <b>1,00</b> (un seul pari par construction — le défaut prudent côté risque). C'est un <i>majorant
            honnête</i> du φ de pertes (co-mouvement spot, pas la perte elle-même), pas un substitut une fois le φ réel
            rempli. La lecture qui compte pour la <b>pondération</b> : combien de paris <i>vraiment</i> indépendants (KPI
            « Facteurs de risque ») — le crypto-bêta fait souvent fondre 48 runners en une poignée.`
        : lossView
        ? html`Chaque case = corrélation φ des <b>pertes</b> des deux runners, fenêtre par fenêtre (perte = P&L
            net négatif sur la fenêtre ; alignées par horodatage d'ouverture, donc même <i>frame</i> = même
            grille → BTC·15m et ETH·15m se comparent). Au survol : pertes de chacun, pertes <b>simultanées</b>,
            et le facteur ×N vs le hasard. Les favoris gagnent ~90 % → les pertes sont rares : sous ${min_overlap}
            fenêtres communes ou ${min_losses} pertes de chaque côté, la case reste vide (un φ sur 1-2 pertes
            n'est que du bruit). C'est la lentille la plus exigeante en données — elle se remplira lentement
            ; en attendant, l'onglet <b>Structurel</b> donne le co-mouvement spot, disponible immédiatement.`
        : html`Chaque case = corrélation de Pearson du <b>P&L complet</b> (hausses ET baisses) des deux runners,
            fenêtre par fenêtre. Utile pour voir le co-mouvement global, mais pour l'ETF c'est la corrélation des
            <i>pertes</i> qui décide (le payoff est asymétrique). Sous ${min_overlap} fenêtres communes, vide.`}
      </${Note}>
    </${Panel}>

    <${Panel} title=${`Doublons de risque — perdent ensemble, n'en garder qu'un (|φ|≥${redundant_rho})`} flush=${true}>
      ${redundant.length
        ? html`<${DataTable} cols=${redCols} rows=${redundant} sort=${{key:"loss",dir:-1}} empty="—" />`
        : html`<div class="panel-body"><${Note} tone="ok">Aucune paire qui perde systématiquement ensemble pour
            l'instant : soit les pertes ne se groupent pas, soit il manque encore des fenêtres perdantes communes
            pour le mesurer (favoris ~90 % de réussite → les pertes s'accumulent lentement).</${Note}></div>`}
    </${Panel}>`;
}
