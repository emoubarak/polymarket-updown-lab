// Unit tests for the pure formatters/domain helpers. format.js imports nothing
// (no Preact, no DOM), so Node runs it straight: `node app/format.test.mjs`.
// These are exactly the functions that break SILENTLY when a new coin/frame or a
// maker twin is added — lock them down. Wired into the compile/smoke step.
import assert from 'node:assert/strict';
import {
  partsOf, nameOf, takerTwin, isMakerName, bkey, glyphOf,
  verdict, goLamp, health, money, moneyS, pct, ageStr, simPnL, dirLabel,
  entrySlot, gateSummary, presetName, effectiveSlot, betMax, effStake,
} from './format.js';

let n = 0;
const t = (name, fn) => { fn(); n++; };

// --- name parsing: token/frame matched by MEMBERSHIP, not position ---
t('partsOf brain-token-frame', () =>
  assert.deepEqual(partsOf('zlead-sol-15m'), {brain:'zlead', token:'sol', frame:'15m'}));
t('partsOf breadth coins (doge/bnb)', () => {
  assert.deepEqual(partsOf('zlead-doge-5m'), {brain:'zlead', token:'doge', frame:'5m'});
  assert.deepEqual(partsOf('zlead-bnb-15m'), {brain:'zlead', token:'bnb', frame:'15m'});
  assert.deepEqual(partsOf('zlead-xrp-15m'), {brain:'zlead', token:'xrp', frame:'15m'});
});
t('partsOf legacy frame-only', () =>
  assert.deepEqual(partsOf('rubedo-15m'), {brain:'rubedo', token:null, frame:'15m'}));
t('partsOf underlying-only legacy', () =>
  assert.deepEqual(partsOf('fixatio-eth'), {brain:'fixatio', token:'eth', frame:null}));
t('partsOf bare brain (history rows)', () =>
  assert.deepEqual(partsOf('gurdjieff'), {brain:'gurdjieff', token:null, frame:null}));

// --- display names ---
t('nameOf full', () => assert.equal(nameOf('zlead-sol-15m'), 'Zlead · SOL · 15m'));
t('nameOf underlying-only defaults to 15m', () => assert.equal(nameOf('fixatio-eth'), 'Fixatio · ETH · 15m'));
t('nameOf bare brain', () => assert.equal(nameOf('gurdjieff'), 'Gurdjieff'));

// --- maker twin: drop trailing "mk" from the BRAIN only ---
t('takerTwin maker → taker', () => {
  assert.equal(takerTwin('zleadmk-eth-15m'), 'zlead-eth-15m');
  assert.equal(takerTwin('rubedomk'), 'rubedo');
});
t('takerTwin identity when not maker', () => assert.equal(takerTwin('zlead-eth-15m'), 'zlead-eth-15m'));
t('isMakerName', () => {
  assert.equal(isMakerName('zleadmk-eth-15m'), true);
  assert.equal(isMakerName('zlead-eth-15m'), false);
});
t('bkey = base brain', () => assert.equal(bkey('zleadmk-eth-15m'), 'zleadmk'));
t('glyphOf known/unknown', () => {
  assert.equal(glyphOf('zlead-sol-15m'), '☿');
  assert.equal(glyphOf('mystery-x-15m'), '●');
});

// --- THE tripwire verdict ---
t('verdict wait/trial/proven/none', () => {
  assert.equal(verdict({n_settled:0}).key, 'wait');
  assert.equal(verdict({n_settled:50}).key, 'trial');
  assert.equal(verdict({n_settled:200, win_rate:0.95, avg_price:0.90}).key, 'proven');
  assert.equal(verdict({n_settled:200, win_rate:0.88, avg_price:0.90}).key, 'none');
  assert.equal(verdict(null).key, 'wait');
});

// --- real-money GO lamp (Wilson lower bound) ---
t('goLamp colours', () => {
  const red = 'var(--vermilion)', amber = 'var(--amber)', green = 'var(--verdigris)';
  assert.equal(goLamp(null).c, 'var(--ink-3)');
  assert.equal(goLamp({n_settled:200, win_rate:0.80, avg_price:0.90}).c, red);     // doesn't beat price
  assert.equal(goLamp({n_settled:50,  win_rate:0.95, avg_price:0.90, win_lo:0.80}).c, amber); // n<150
  assert.equal(goLamp({n_settled:200, win_rate:0.95, avg_price:0.90, win_lo:0.92}).c, green);  // IC excludes price
});

// --- runner health from last-tick freshness (uses wall clock) ---
t('health freshness', () => {
  const now = Math.floor(Date.now()/1000);
  assert.equal(health(null).t, 'unknown');
  assert.equal(health({ts:now}).t, 'live');
  assert.equal(health({ts:now-300}).t, 'slow');
  assert.equal(health({ts:now-2000}).t, 'silent');
});

// --- money / numbers / dates ---
t('money & moneyS signs', () => {
  assert.equal(money(-5), '-$5.00');
  assert.equal(money(5), '$5.00');
  assert.equal(moneyS(5), '+$5.00');
  assert.equal(moneyS(-5), '-$5.00');
});
t('pct', () => { assert.equal(pct(0.95), '95 %'); assert.equal(pct(0.9012, 1), '90.1 %'); });
t('ageStr buckets', () => {
  assert.equal(ageStr(null), '—');
  assert.equal(ageStr(30), '30 s');
  assert.equal(ageStr(120), '2 min');
  assert.equal(ageStr(7200), '2.0 h');
});
t('simPnL = ev × stake × n', () => assert.equal(simPnL({ev:0.01, n:100}, 25), 25));
t('dirLabel', () => { assert.equal(dirLabel('Up'), 'Up'); assert.equal(dirLabel('Down'), 'Down'); });

// --- preset gate display (served from /config; locks the widened-slot rendering) ---
t('entrySlot = window fractions × frame, minutes left', () => {
  const z = {enter_lo:0.27, enter_hi:0.45};            // zlead flagship (widened)
  assert.match(entrySlot(z, '15m'), /^4[.,]05.*6[.,]75 min left$/);  // 0.27/0.45 × 15
  assert.match(entrySlot(z, '5m'),  /^1[.,]35.*2[.,]25 min left$/);  // × 5
  assert.equal(entrySlot(null, '15m'), null);
});
t('gateSummary = band + lead floor + execution', () => {
  assert.equal(gateSummary({min_fav:0.85, max_fav:0.95, min_lead_z:1}), 'favorite 0.85–0.95 · z≥1');
  assert.equal(gateSummary({min_fav:0.85, max_fav:0.9, min_lead_z:1}), 'favorite 0.85–0.9 · z≥1');
  assert.equal(gateSummary({min_fav:0.85, max_fav:0.95, min_lead_z:1, maker_entry:true}),
    'favorite 0.85–0.95 · z≥1 · maker entry');
  assert.equal(gateSummary(null), null);
});
t('presetName = base · type (the modular identity)', () => {
  assert.equal(presetName({base:'zlead', types:[]}), 'zlead');
  assert.equal(presetName({base:'zlead', types:['n']}), 'zlead · type n');
  assert.equal(presetName({base:'zlead', types:['n','mk']}), 'zlead · type n+mk');
  assert.equal(presetName(null), null);
});
t('effectiveSlot applies per-coin COIN_SLOTS override', () => {
  const z = {enter_lo:0.27, enter_hi:0.45};
  assert.match(effectiveSlot(z, '15m', 'btc', {bnb:0.33}), /^4[.,]05/);   // btc = base 0.27×15
  assert.match(effectiveSlot(z, '15m', 'bnb', {bnb:0.33}), /^4[.,]95/);   // bnb = 0.33×15
  assert.equal(effectiveSlot(null, '15m', 'btc', {}), null);
});

// --- per-(coin,frame) book-depth bet ceiling + weighted sizing (mirrors staking.weighted_clip) ---
t('betMax = per-(coin,frame) cap, falls back to min-across-frames then fallback', () => {
  const depth = {btc:{'5m':4773,'15m':2210}, bnb:{'5m':11,'15m':15}};
  assert.equal(betMax('btc', '5m', depth), 4773);
  assert.equal(betMax('btc', '15m', depth), 2210);
  assert.equal(betMax('btc', null, depth), 2210);     // frame unknown → conservative min across frames
  assert.equal(betMax('bnb', '15m', depth), 15);
  assert.equal(betMax('sol', '5m', depth, 25), 25);   // coin absent → fallback
  assert.equal(betMax(null, '5m', depth, 25), 25);
});
t('effStake = frac of capital, floored, capped at the (coin,frame) book depth', () => {
  const depth = {btc:{'5m':4773,'15m':2210}, bnb:{'5m':11,'15m':15}};
  assert.equal(effStake('btc', '5m', 100, depth, 0.10), 10);      // 10 % of $100, under cap
  assert.equal(effStake('btc', '15m', 50000, depth, 0.10), 2210); // capped at 15m book depth
  assert.equal(effStake('bnb', '15m', 100, depth, 0.10), 10);     // 10 % of $100, under the $15 cap
  assert.equal(effStake('bnb', '15m', 1000, depth, 0.10), 15);    // bnb 15m capped at $15
  assert.equal(effStake('btc', '5m', 30, depth, 0.10), 5);        // floored at min_clip 5
});

console.log(`format.js — ${n} tests OK`);
