package main

// risk.go — hard limits, kill switch, and the fee-aware emergency
// liquidation decision. The kill switch is one-way per process: once
// tripped, the bot cancels everything and refuses to quote until restarted.

import (
	"fmt"
	"log"
	"math"
	"sync/atomic"
)

// RiskLimits are the hard operating bounds.
type RiskLimits struct {
	MaxPositionShares   float64 // |net| beyond this: stop quoting the widening side, consider liquidation
	MaxOpenNotionalUSDC float64 // position cost + resting buy commitment cap
	MaxChainlinkAgeSec  float64 // settlement-anchor feed staleness bound
	MaxBinanceAgeSec    float64 // leading-signal staleness bound
	MaxHeartbeatFails   int64   // consecutive REST heartbeat failures
}

// Risk is the kill switch + gate evaluator.
type Risk struct {
	Limits RiskLimits

	killed atomic.Bool
	reason atomic.Value // string
}

func NewRisk(l RiskLimits) *Risk {
	r := &Risk{Limits: l}
	r.reason.Store("")
	return r
}

// Kill trips the switch permanently (for this process). LOUD by design:
// a silent kill looks exactly like "no opportunities" in the logs.
func (r *Risk) Kill(reason string) {
	if r.killed.CompareAndSwap(false, true) {
		r.reason.Store(reason)
		log.Printf("KILL SWITCH TRIPPED: %s — quoting disabled until restart", reason)
	}
}

func (r *Risk) Killed() bool   { return r.killed.Load() }
func (r *Risk) Reason() string { s, _ := r.reason.Load().(string); return s }

// GateInput is everything the quote gate looks at, gathered by the caller
// so this stays a pure decision function (testable, no I/O).
type GateInput struct {
	ChainlinkAgeSec float64
	BinanceAgeSec   float64
	HeartbeatFails  int64
	NetShares       float64 // signed inventory
	BidOutstanding  float64 // shares the bid side could still add to net (resting remainder or next order size)
	AskOutstanding  float64 // shares the ask side could still subtract from net
	PositionUSDC    float64 // cost basis committed to current position
	OpenBuyUSDC     float64 // resting buy commitment
	VolWarm         bool
	StrikeKnown     bool
	Live            bool // heartbeat checks only matter when live
}

// GateResult says which sides may quote. Reasons are for the log.
type GateResult struct {
	AllowBid bool
	AllowAsk bool
	Reason   string
}

// EvaluateGate applies hard risk rules on top of the pricing regime:
//   - any critical failure → no quotes at all
//   - position beyond +cap → no more buying of Up-exposure (bid off);
//     beyond −cap → ask off
//   - notional cap → no new buys either side
func (r *Risk) EvaluateGate(in GateInput) GateResult {
	if r.Killed() {
		return GateResult{Reason: "killed: " + r.Reason()}
	}
	if !in.StrikeKnown {
		return GateResult{Reason: "strike unknown"}
	}
	if in.ChainlinkAgeSec > r.Limits.MaxChainlinkAgeSec {
		return GateResult{Reason: fmt.Sprintf("chainlink stale %.1fs", in.ChainlinkAgeSec)}
	}
	if in.BinanceAgeSec > r.Limits.MaxBinanceAgeSec {
		return GateResult{Reason: fmt.Sprintf("binance stale %.1fs", in.BinanceAgeSec)}
	}
	if in.Live && in.HeartbeatFails >= r.Limits.MaxHeartbeatFails {
		return GateResult{Reason: fmt.Sprintf("heartbeat failing x%d", in.HeartbeatFails)}
	}
	if !in.VolWarm {
		return GateResult{Reason: "vol warming up"}
	}

	g := GateResult{AllowBid: true, AllowAsk: true, Reason: "ok"}
	// PREDICTIVE position cap: a side may quote only if its worst-case
	// fill keeps |net| within the cap. The reactive form (block once
	// breached) let resting + in-flight orders stack residuals to ~2x the
	// cap — measured repeatedly at settlement, the dominant loss channel.
	if in.NetShares+in.BidOutstanding > r.Limits.MaxPositionShares {
		g.AllowBid = false
		g.Reason = "long cap (predictive)"
	}
	if in.NetShares-in.AskOutstanding < -r.Limits.MaxPositionShares {
		g.AllowAsk = false
		g.Reason = "short cap (predictive)"
	}
	if in.PositionUSDC+in.OpenBuyUSDC >= r.Limits.MaxOpenNotionalUSDC {
		g.AllowBid, g.AllowAsk = false, false
		g.Reason = "notional cap"
	}
	return g
}

// TakerFee returns the venue taker fee in USDC for crossing: crypto markets
// charge rate*p*(1-p) per share (maker side is free). Peak cost sits exactly
// where these markets trade most (p≈0.5) — never cross without checking.
func TakerFee(rate, price, shares float64) float64 {
	if price <= 0 || price >= 1 {
		return 0
	}
	return rate * price * (1 - price) * shares
}

// LiquidationPlan is a decision to reduce |inventory| with a taker order.
type LiquidationPlan struct {
	Do       bool
	SellUp   bool    // true: sell Up tokens; false: sell Down tokens
	Shares   float64 // amount to flatten
	LimitPx  float64 // aggressive limit (FAK) — never worse than this
	FeeUSDC  float64 // expected fee at LimitPx
	Rationale string
}

// PlanLiquidation decides whether to flatten excess inventory with a taker
// (FAK) sell. Fee-aware: it prices the venue taker fee plus spread cost into
// the decision instead of crossing unconditionally.
//
//	net      — signed inventory in shares (+ long Up, − long Down)
//	fair     — model fair value of Up
//	tick     — market tick
//	feeRate  — taker fee rate (0.07 crypto)
//	maxSlip  — max acceptable slippage below fair, in prob units
//	trigger  — |net| that triggers flattening (usually MaxPositionShares)
//
// The excess above trigger is flattened only when expected cost (fee +
// slippage) is below the risk value of carrying it: for a binary about to
// gamma-flip, carrying cost is up to $1/share on the wrong side; we use
// lambda-style linear costing riskPerShare as the caller's estimate.
func PlanLiquidation(net, fair, tick, feeRate, maxSlip, trigger, riskPerShare float64) LiquidationPlan {
	absNet := math.Abs(net)
	if absNet <= trigger {
		return LiquidationPlan{}
	}
	excess := absNet - trigger

	sellUp := net > 0
	// Selling Up receives ~fair; selling Down receives ~(1-fair).
	recv := fair
	if !sellUp {
		recv = 1 - fair
	}
	limit := recv - maxSlip
	if limit < tick {
		// Nothing meaningful to receive — position is nearly worthless;
		// crossing pays fees to recover pennies. Let it ride to resolution
		// (bounded loss, already capped by position limits).
		return LiquidationPlan{Rationale: "exit value below tick; hold to resolution"}
	}
	fee := TakerFee(feeRate, limit, excess)
	cost := fee + maxSlip*excess
	benefit := riskPerShare * excess
	if cost >= benefit {
		return LiquidationPlan{Rationale: fmt.Sprintf(
			"liquidation uneconomic: cost %.4f >= benefit %.4f", cost, benefit)}
	}
	return LiquidationPlan{
		Do:      true,
		SellUp:  sellUp,
		Shares:  excess,
		LimitPx: limit,
		FeeUSDC: fee,
		Rationale: fmt.Sprintf("flatten %.2f sh @ >=%.3f (fee %.4f)",
			excess, limit, fee),
	}
}
