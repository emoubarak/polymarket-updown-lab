package main

// math_engine.go — pure pricing math for the 5-minute BTC binary market maker.
//
// Everything in this file is a pure function or a single-goroutine state
// machine (VolEngine). No I/O, no locks, no allocation on any code path
// called per tick. Not goroutine-safe by design: the pricing loop is the
// sole owner.

import "math"

const invSqrt2Pi = 0.3989422804014327 // 1/sqrt(2*pi)

// Phi is a fast approximation of the standard normal CDF.
//
// Abramowitz & Stegun 26.2.17 rational approximation, |error| < 7.5e-8,
// one exp() and five multiplies — no external math libraries. Inputs beyond
// |x| >= 8 are pinned to exactly 0 or 1 (true tail mass < 1e-15, far below
// the approximation error).
func Phi(x float64) float64 {
	if math.IsNaN(x) {
		return math.NaN()
	}
	if x >= 8 {
		return 1
	}
	if x <= -8 {
		return 0
	}
	neg := x < 0
	if neg {
		x = -x
	}
	t := 1 / (1 + 0.2316419*x)
	poly := t * (0.319381530 + t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))))
	p := 1 - invSqrt2Pi*math.Exp(-0.5*x*x)*poly
	if neg {
		return 1 - p
	}
	return p
}

// FairValue prices the binary "Up" outcome: probability that S_T >= K given
// current reference price S, strike K, per-sqrt-second volatility sigma and
// T seconds to expiry.
//
//	d2 = ln(S/K) / (sigma * sqrt(T))
//
// The -sigma^2*T/2 drift term is intentionally dropped: at T <= 300s and
// realistic BTC vol it is O(1e-6), immaterial next to ln(S/K). Do not add it
// back. sigma must already be per-sqrt-second (not annualized); T in seconds.
//
// At T <= 0 the payoff is a step function; resolution uses >= per the market
// rules (confirm per-market wording before live trading — see risk config).
// Returns (fair probability, d2).
func FairValue(S, K, sigma, T float64) (float64, float64) {
	if S <= 0 || K <= 0 || sigma <= 0 {
		return math.NaN(), math.NaN()
	}
	if T <= 0 {
		if S >= K {
			return 1, math.Inf(1)
		}
		return 0, math.Inf(-1)
	}
	d2 := math.Log(S/K) / (sigma * math.Sqrt(T))
	return Phi(d2), d2
}

// ---------------------------------------------------------------------------
// Volatility: dual-window EWMA of per-second variance with a hard floor.
//
// A raw short-window realized vol is a noisy estimator (few observations,
// bid-ask bounce) and sits in the denominator of d2 — the noise is amplified
// exactly as T -> 0. Instead we blend a fast and a slow EWMA and floor the
// result so the denominator can never collapse.
// ---------------------------------------------------------------------------

// VolConfig configures the EWMA volatility engine. All times in seconds,
// sigmas in per-sqrt-second units (annualized_vol / sqrt(365*24*3600);
// e.g. 50%/yr BTC vol ~= 8.9e-5 per sqrt-second).
type VolConfig struct {
	TauFast       float64 // fast EWMA time constant, e.g. 60
	TauSlow       float64 // slow EWMA time constant, e.g. 300
	WFast         float64 // blend weight on the fast variance, [0,1]
	SigmaFloor    float64 // hard lower bound on blended sigma
	SigmaCap      float64 // hard upper bound on blended sigma
	SigmaStartup  float64 // sigma reported until WarmupSamples reached
	MinSampleDt   float64 // ignore updates arriving closer than this, e.g. 0.2
	WarmupSamples int     // samples required before trusting the EWMA
}

// DefaultVolConfig returns sane starting parameters for BTC.
func DefaultVolConfig() VolConfig {
	return VolConfig{
		TauFast:       60,
		TauSlow:       300,
		WFast:         0.5,
		SigmaFloor:    1.5e-5, // ~8%/yr: never let the d2 denominator collapse
		// (3e-5 was binding for whole windows in calm markets, pinning fair
		// to extremes faster than the book — book clipping now bounds that
		// failure mode, so the floor can sit lower.)
		SigmaCap:      1e-3,  // ~560%/yr: clamp data glitches
		SigmaStartup:  1.2e-4, // ~67%/yr conservative prior while warming up
		MinSampleDt:   0.2,
		WarmupSamples: 25,
	}
}

// VolEngine maintains two exponentially-weighted estimates of per-second
// log-return variance under irregular sampling. Single-goroutine only.
type VolEngine struct {
	cfg       VolConfig
	lastPrice float64
	lastTS    float64 // seconds, monotonic
	varFast   float64 // per-second variance
	varSlow   float64
	samples   int
}

func NewVolEngine(cfg VolConfig) *VolEngine {
	return &VolEngine{cfg: cfg}
}

// Update ingests a reference-price observation at monotonic time ts (seconds).
// Observations closer together than MinSampleDt are skipped without moving
// the anchor, which suppresses microstructure noise instead of amplifying it
// through the r^2/dt division.
func (v *VolEngine) Update(price, ts float64) {
	if price <= 0 {
		return
	}
	if v.lastPrice == 0 {
		v.lastPrice, v.lastTS = price, ts
		// Seed the EWMAs at the prior variance instead of zero: with ~25
		// samples the EWMA carries only ~5% of true variance mass, which
		// understated sigma ~4-5x for minutes after every restart (and made
		// fair pin to extremes while the guard, using the same wrong fair,
		// stayed blind). Seeding turns warmup into a smooth blend from the
		// prior toward measured vol.
		v.varFast = v.cfg.SigmaStartup * v.cfg.SigmaStartup
		v.varSlow = v.varFast
		return
	}
	dt := ts - v.lastTS
	if dt < v.cfg.MinSampleDt {
		return
	}
	r := math.Log(price / v.lastPrice)
	instVar := r * r / dt // per-second variance estimate from this interval
	decayFast := math.Exp(-dt / v.cfg.TauFast)
	decaySlow := math.Exp(-dt / v.cfg.TauSlow)
	v.varFast = decayFast*v.varFast + (1-decayFast)*instVar
	v.varSlow = decaySlow*v.varSlow + (1-decaySlow)*instVar
	v.lastPrice, v.lastTS = price, ts
	v.samples++
}

// Sigma returns the blended, floored per-sqrt-second volatility.
func (v *VolEngine) Sigma() float64 {
	if v.samples < v.cfg.WarmupSamples {
		return v.cfg.SigmaStartup
	}
	blended := v.cfg.WFast*v.varFast + (1-v.cfg.WFast)*v.varSlow
	sigma := math.Sqrt(blended)
	if sigma < v.cfg.SigmaFloor {
		return v.cfg.SigmaFloor
	}
	if sigma > v.cfg.SigmaCap {
		return v.cfg.SigmaCap
	}
	return sigma
}

// Warm reports whether the engine has seen enough samples to trust Sigma.
func (v *VolEngine) Warm() bool { return v.samples >= v.cfg.WarmupSamples }

// ---------------------------------------------------------------------------
// Quoting: Avellaneda–Stoikov-style inventory skew + explicit endgame regime.
// ---------------------------------------------------------------------------

// Regime classifies the quoting posture for the current (T, d2) state.
type Regime int32

const (
	// RegimeNormal: standard spread around the skewed center.
	RegimeNormal Regime = iota
	// RegimeDefensive: T < EndgameT but comfortably ITM/OTM — Phi is pinned,
	// stay quoted but widen against an oracle flash-move.
	RegimeDefensive
	// RegimePull: T < EndgameT and near-the-money — one tick can flip Phi
	// between ~0 and ~1; pull both quotes entirely.
	RegimePull
)

func (r Regime) String() string {
	switch r {
	case RegimeNormal:
		return "normal"
	case RegimeDefensive:
		return "defensive"
	case RegimePull:
		return "pull"
	}
	return "unknown"
}

// QuoteConfig holds quoting parameters. Prices are probabilities in (0,1).
type QuoteConfig struct {
	HalfSpread    float64 // base half-spread, e.g. 0.02
	Lambda        float64 // inventory skew per share of signed inventory
	Tick          float64 // market tick size, e.g. 0.001 or 0.01
	MinPrice      float64 // lowest quotable price, typically == Tick
	MaxPrice      float64 // highest quotable price, typically 1 - Tick
	EndgameT      float64 // endgame threshold in seconds, e.g. 10
	EndgameD2     float64 // |d2| below which endgame means pull, e.g. 2.0
	DefensiveMult float64 // half-spread multiplier in RegimeDefensive, e.g. 3
	MaxSkewShift  float64 // cap on |Lambda*I| so runaway inventory can't cross the book, e.g. 0.10
}

// DefaultQuoteConfig returns sane starting parameters.
func DefaultQuoteConfig() QuoteConfig {
	return QuoteConfig{
		HalfSpread:    0.02,
		Lambda:        0.0004,
		Tick:          0.001,
		MinPrice:      0.001,
		MaxPrice:      0.999,
		EndgameT:      10,
		EndgameD2:     2.0,
		DefensiveMult: 3,
		MaxSkewShift:  0.10,
	}
}

// ClassifyRegime implements the explicit endgame switch. It is deliberately
// independent of the AS spread term: variance-time spread terms shrink as
// T -> 0 (built for continuous payoffs), which is the opposite of the gamma
// risk that explodes near expiry on a binary. Do not merge the two.
func ClassifyRegime(T, d2 float64, cfg QuoteConfig) Regime {
	if T >= cfg.EndgameT {
		return RegimeNormal
	}
	if math.Abs(d2) < cfg.EndgameD2 {
		return RegimePull
	}
	return RegimeDefensive
}

// Quotes is the output of ComputeQuotes. A side with OK=false must not be
// quoted (pulled or unpriceable at this state).
type Quotes struct {
	Bid    float64
	Ask    float64
	BidOK  bool
	AskOK  bool
	Regime Regime
}

func floorToTick(p, tick float64) float64 {
	return math.Floor(p/tick+1e-9) * tick
}

func ceilToTick(p, tick float64) float64 {
	return math.Ceil(p/tick-1e-9) * tick
}

// CompetitiveBid returns the price for a post-only BUY on a token's book:
// as aggressive as the model allows, but never crossing (post-only-safe)
// and never giving more than one tick of improvement over the best bid —
// top-of-book priority is all that matters for maker fill volume (and
// therefore rebate capture); paying more is wasted edge.
//
//	limit    — model's worst acceptable price (token's own probability space)
//	bestBid  — book best bid (0 = empty side)
//	bestAsk  — book best ask (0 = empty side)
//
// Returns 0 when no valid price exists.
func CompetitiveBid(limit, bestBid, bestAsk, tick float64) float64 {
	if limit < tick {
		return 0
	}
	p := limit
	if bestAsk > 0 && p > bestAsk-tick {
		p = bestAsk - tick // one tick inside the ask: cannot cross
	}
	if bestBid > 0 && p > bestBid+tick {
		p = bestBid + tick // improve best bid by exactly one tick
	}
	p = floorToTick(p, tick)
	if p < tick {
		return 0
	}
	return p
}

// CompetitiveAsk is the SELL mirror of CompetitiveBid: as low as the model
// allows but never crossing the bid (post-only-safe) and undercutting the
// best ask by exactly one tick at most.
//
//	limit — model's minimum acceptable sale price (token's own prob space)
//
// Returns 0 when no valid price exists.
func CompetitiveAsk(limit, bestBid, bestAsk, tick float64) float64 {
	if limit > 1-tick {
		return 0
	}
	p := limit
	if bestBid > 0 && p < bestBid+tick {
		p = bestBid + tick // one tick above the bid: cannot cross
	}
	if bestAsk > 0 && p < bestAsk-tick {
		p = bestAsk - tick // undercut best ask by exactly one tick
	}
	p = ceilToTick(p, tick)
	if p > 1-tick {
		return 0
	}
	return p
}

// ComputeQuotes builds the passive two-sided quote:
//
//	Center = mid - Lambda*I   (AS-style reservation shift, capped)
//	Bid    = Center - HalfSpread (floored to tick)
//	Ask    = Center + HalfSpread (ceiled to tick)
//
// inv is signed inventory in shares (long Up positive). d2 and T feed the
// endgame regime switch, which overrides everything else.
func ComputeQuotes(mid, d2, T, inv float64, cfg QuoteConfig) Quotes {
	q := Quotes{Regime: ClassifyRegime(T, d2, cfg)}
	if math.IsNaN(mid) || q.Regime == RegimePull {
		q.Regime = RegimePull
		return q
	}

	hs := cfg.HalfSpread
	if q.Regime == RegimeDefensive {
		hs *= cfg.DefensiveMult
	}

	skew := cfg.Lambda * inv
	if skew > cfg.MaxSkewShift {
		skew = cfg.MaxSkewShift
	} else if skew < -cfg.MaxSkewShift {
		skew = -cfg.MaxSkewShift
	}
	center := mid - skew

	bid := floorToTick(center-hs, cfg.Tick)
	ask := ceilToTick(center+hs, cfg.Tick)
	if ask-bid < cfg.Tick { // rounding collapsed the spread
		ask = bid + cfg.Tick
	}

	if bid >= cfg.MinPrice && bid <= cfg.MaxPrice {
		q.Bid, q.BidOK = bid, true
	}
	if ask >= cfg.MinPrice && ask <= cfg.MaxPrice {
		q.Ask, q.AskOK = ask, true
	}
	// Never emit a crossed or degenerate pair.
	if q.BidOK && q.AskOK && q.Ask <= q.Bid {
		q.BidOK, q.AskOK = false, false
	}
	return q
}
