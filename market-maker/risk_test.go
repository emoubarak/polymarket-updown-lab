package main

import (
	"math"
	"strings"
	"testing"
)

func healthyGate() GateInput {
	return GateInput{
		ChainlinkAgeSec: 1, BinanceAgeSec: 0.1, HeartbeatFails: 0,
		NetShares: 0, BidOutstanding: 5, AskOutstanding: 5,
		PositionUSDC: 0, OpenBuyUSDC: 0,
		VolWarm: true, StrikeKnown: true, Live: true,
	}
}

func TestEvaluateGate(t *testing.T) {
	r := NewRisk(RiskLimits{
		MaxPositionShares: 50, MaxOpenNotionalUSDC: 25,
		MaxChainlinkAgeSec: 8, MaxBinanceAgeSec: 5, MaxHeartbeatFails: 3,
	})

	if g := r.EvaluateGate(healthyGate()); !g.AllowBid || !g.AllowAsk {
		t.Fatalf("healthy gate blocked: %+v", g)
	}

	in := healthyGate()
	in.ChainlinkAgeSec = 9
	if g := r.EvaluateGate(in); g.AllowBid || g.AllowAsk || !strings.Contains(g.Reason, "chainlink") {
		t.Fatalf("stale chainlink must block: %+v", g)
	}

	in = healthyGate()
	in.BinanceAgeSec = 6
	if g := r.EvaluateGate(in); g.AllowBid || g.AllowAsk {
		t.Fatalf("stale binance must block: %+v", g)
	}

	in = healthyGate()
	in.HeartbeatFails = 3
	if g := r.EvaluateGate(in); g.AllowBid || g.AllowAsk {
		t.Fatalf("heartbeat failures must block when live: %+v", g)
	}
	in.Live = false
	if g := r.EvaluateGate(in); !g.AllowBid {
		t.Fatalf("heartbeat must not block dry-run: %+v", g)
	}

	in = healthyGate()
	in.VolWarm = false
	if g := r.EvaluateGate(in); g.AllowBid {
		t.Fatal("cold vol must block")
	}
	in = healthyGate()
	in.StrikeKnown = false
	if g := r.EvaluateGate(in); g.AllowBid {
		t.Fatal("unknown strike must block")
	}

	// Predictive position caps are one-sided: a side is blocked as soon as
	// its worst-case fill would breach the cap, not after the breach.
	in = healthyGate()
	in.NetShares = 46 // 46 + 5 outstanding > 50
	if g := r.EvaluateGate(in); g.AllowBid || !g.AllowAsk {
		t.Fatalf("long cap predictive: %+v", g)
	}
	in.NetShares = 45 // 45 + 5 == 50: exactly at cap, allowed
	if g := r.EvaluateGate(in); !g.AllowBid || !g.AllowAsk {
		t.Fatalf("at-cap fill must be allowed: %+v", g)
	}
	in.NetShares = -46
	if g := r.EvaluateGate(in); !g.AllowBid || g.AllowAsk {
		t.Fatalf("short cap predictive: %+v", g)
	}
	// Larger resting remainder tightens the gate further.
	in = healthyGate()
	in.NetShares = 40
	in.BidOutstanding = 15
	if g := r.EvaluateGate(in); g.AllowBid {
		t.Fatalf("outstanding must count toward the cap: %+v", g)
	}

	// Notional cap blocks both.
	in = healthyGate()
	in.PositionUSDC, in.OpenBuyUSDC = 20, 6
	if g := r.EvaluateGate(in); g.AllowBid || g.AllowAsk {
		t.Fatalf("notional cap: %+v", g)
	}

	// Kill switch: permanent, blocks everything.
	r.Kill("test reason")
	r.Kill("second reason must not overwrite")
	if g := r.EvaluateGate(healthyGate()); g.AllowBid || g.AllowAsk {
		t.Fatal("killed risk must block")
	}
	if r.Reason() != "test reason" {
		t.Fatalf("kill reason overwritten: %q", r.Reason())
	}
}

func TestTakerFee(t *testing.T) {
	// Crypto rate 0.07, peak at p=0.5: 0.07*0.25 = 0.0175/share.
	if got := TakerFee(0.07, 0.5, 100); math.Abs(got-1.75) > 1e-12 {
		t.Fatalf("fee at 0.5 = %v, want 1.75", got)
	}
	// Symmetric, smaller in the wings.
	if math.Abs(TakerFee(0.07, 0.9, 100)-TakerFee(0.07, 0.1, 100)) > 1e-12 {
		t.Fatal("fee must be symmetric")
	}
	if TakerFee(0.07, 0.9, 100) >= TakerFee(0.07, 0.5, 100) {
		t.Fatal("fee must peak at 0.5")
	}
	if TakerFee(0.07, 0, 100) != 0 || TakerFee(0.07, 1, 100) != 0 {
		t.Fatal("degenerate prices fee 0")
	}
}

func TestPlanLiquidation(t *testing.T) {
	const tick, feeRate, maxSlip, trigger = 0.01, 0.07, 0.03, 50.0

	// Inside limits: nothing.
	if p := PlanLiquidation(30, 0.5, tick, feeRate, maxSlip, trigger, 0.10); p.Do {
		t.Fatalf("no breach must not liquidate: %+v", p)
	}

	// Long breach: sell Up, only the excess.
	p := PlanLiquidation(70, 0.5, tick, feeRate, maxSlip, trigger, 0.10)
	if !p.Do || !p.SellUp {
		t.Fatalf("long breach: %+v", p)
	}
	if math.Abs(p.Shares-20) > 1e-9 {
		t.Fatalf("must flatten only the excess: %+v", p)
	}
	if math.Abs(p.LimitPx-0.47) > 1e-9 {
		t.Fatalf("limit = fair - slip: %+v", p)
	}
	if p.FeeUSDC <= 0 {
		t.Fatal("fee must be accounted")
	}

	// Short breach: sell Down at (1-fair)-slip.
	p2 := PlanLiquidation(-70, 0.8, tick, feeRate, maxSlip, trigger, 0.10)
	if !p2.Do || p2.SellUp {
		t.Fatalf("short breach: %+v", p2)
	}
	if math.Abs(p2.LimitPx-(0.2-maxSlip)) > 1e-9 {
		t.Fatalf("down limit: %+v", p2)
	}

	// Near-worthless exit: don't pay fees to recover pennies.
	p3 := PlanLiquidation(70, 0.02, tick, feeRate, maxSlip, trigger, 0.10)
	if p3.Do {
		t.Fatalf("worthless exit must hold to resolution: %+v", p3)
	}

	// Uneconomic: cost >= benefit.
	p4 := PlanLiquidation(70, 0.5, tick, feeRate, maxSlip, trigger, 0.01)
	if p4.Do {
		t.Fatalf("uneconomic liquidation must be refused: %+v", p4)
	}
}
