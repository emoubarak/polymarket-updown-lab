// Data layer: the static config (fetched once) + a polling hook that every page
// uses to stay live on the 10 s cadence. Live bindings: CONFIG is reassigned by
// loadConfig() before the app renders, so importers see the populated value.
import { useState, useEffect } from './preact.js';
import { partsOf, isMakerName, takerTwin, setRegistry } from './format.js';

// Default shape mirrors the /config payload (site_config) so a page that renders before the
// fetch lands reads sane empties instead of `undefined`. LABELS/TAGS were retired (the dead
// duplicated dicts) — derive names/taglines from PRESETS + format helpers instead.
export let CONFIG = {STRATS:[], EVENTS:[], COPY:[], DESCS:{}, BACKTEST:{}, PRESETS:{}, STAKE:25,
  FAMILY:[], COIN_BET_MAX:{}, COIN_DEPTH:{}, COIN_SLOTS:{}, BTCURVE:{}, COINS:[], FRAMES:[], WEIGHT_PCT:0.10, START:100};

export async function loadConfig(){
  CONFIG = await (await fetch("/config")).json();
  // Push the config-derived registries into the dependency-free format module: coin/frame
  // lists for runner-key parsing, and living-brain glyphs from the presets (so a new brain's
  // glyph flows from /config, not a hand-kept dict). format.js can't import CONFIG (api↔format
  // cycle + browser-only Preact chain would break its node test), hence this one-way push.
  setRegistry({coins: CONFIG.COINS, frames: CONFIG.FRAMES,
    glyphs: Object.fromEntries(Object.entries(CONFIG.PRESETS || {})
      .filter(([, p]) => p && p.glyph).map(([k, p]) => [k, p.glyph]))});
  return CONFIG;
}

export const getJSON = u => fetch(u).then(r => {
  if(!r.ok) throw new Error("HTTP "+r.status+" "+u);
  return r.json();
});

// fetch `url`, then re-fetch every `ms`. Re-subscribes when url/ms change (e.g.
// switching strategy tab → /data?strat=X). Returns {data, error, loading, updatedAt}.
//
// Hardened for a live-money dashboard left open for hours:
//  • PAUSE when the tab is hidden (no point hammering /all in a background tab) —
//    resume + immediate fetch on visibilitychange / reconnect.
//  • EXPONENTIAL BACKOFF on error (a dead server isn't re-tapped every 10 s).
//  • RESET data to null on url change so the page shows its own loading state
//    instead of flashing the previous strategy's numbers under the new name.
//  • EXPOSE error + updatedAt so the UI can show "reconnexion…" / staleness.
export function usePoll(url, ms = 10000){
  const [state, setState] = useState({data:null, error:null, loading:true, updatedAt:null});
  useEffect(() => {
    let live = true, timer = null, fails = 0;
    setState({data:null, error:null, loading:true, updatedAt:null});
    const schedule = d => { if(!live) return; clearTimeout(timer); timer = setTimeout(run, d); };
    async function run(){
      if(!live || document.hidden) return;   // hidden: paused; visibilitychange resumes
      try {
        const d = await getJSON(url);
        if(!live) return;
        fails = 0;
        setState({data:d, error:null, loading:false, updatedAt:Date.now()});
        schedule(ms);
      } catch(e){
        if(!live) return;
        fails++;
        setState(s => ({...s, error:String(e), loading:false}));
        schedule(Math.min(ms * 2 ** fails, 60000));   // back off, cap at 60 s
      }
    }
    const wake = () => { if(!document.hidden && live) schedule(0); };   // resume at once
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("online", wake);
    run();
    return () => { live = false; clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
      window.removeEventListener("online", wake); };
  }, [url, ms]);
  return state;
}

// re-render every `ms` so relative "il y a Xs" labels stay live between polls
export function useNow(ms = 2000){
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id); }, [ms]);
  return now;
}

export async function postPilot(body){
  const r = await fetch("/pilot", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"}, body});
  return r.json();
}

// rewards-MM control plane (separate registry/endpoint from the zlead pilots).
export async function postMM(body){
  const r = await fetch("/mm", {method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"}, body});
  return r.json();
}

// Best backtest match for a deployed runner name, most- to least-specific:
// full -> brain-token -> brain-frame -> brain (mirrors webdash.match_keyed, so the
// Overview/validation panel agree with the Strategy page; e.g. favorite_leadz-sol-15m ->
// favorite_leadz-sol, favorite_lead-btc-5m -> favorite_lead-5m). null = no backtest for this strat.
export const bkOf = name => {
  const {brain, token, frame} = partsOf(name);
  const cands = [name];
  if(token) cands.push(`${brain}-${token}`);
  if(frame) cands.push(`${brain}-${frame}`);
  cands.push(brain);
  for(const k of cands) if(CONFIG.BACKTEST[k]) return CONFIG.BACKTEST[k];
  return null;
};

// Effective backtest for a runner: its own, or — for a maker runner with no maker
// backtest of its own — the taker twin's (the maker only changes the EXECUTION of
// the same entry gate). Returns {bk, gate}; gate=true means it was borrowed from the
// twin. Single source for the Overview "Avantage bt/$" + "Win bt" columns (was
// inlined as `bk||tbk` / `!bk&&tbk` in three places).
export const gatedBk = name => {
  const own = bkOf(name);
  if(own) return {bk:own, gate:false};
  if(isMakerName(name)){ const t = bkOf(takerTwin(name)); if(t) return {bk:t, gate:true}; }
  return {bk:null, gate:false};
};
