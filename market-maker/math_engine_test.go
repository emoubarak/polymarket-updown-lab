package main

import (
	"math"
	"testing"
)

// phiRef is the reference normal CDF built on the stdlib's high-accuracy erfc.
func phiRef(x float64) float64 {
	return 0.5 * math.Erfc(-x/math.Sqrt2)
}

func TestPhiAccuracy(t *testing.T) {
	worst := 0.0
	for x := -8.0; x <= 8.0; x += 0.0005 {
		got := Phi(x)
		want := phiRef(x)
		if d := math.Abs(got - want); d > worst {
			worst = d
		}
	}
	if worst > 1e-7 {
		t.Fatalf("Phi max abs error %.3e exceeds 1e-7 vs erfc reference", worst)
	}
}

func TestPhiProperties(t *testing.T) {
	if got := Phi(0); math.Abs(got-0.5) > 1e-9 {
		t.Fatalf("Phi(0) = %v, want 0.5", got)
	}
	if Phi(9) != 1 || Phi(-9) != 0 {
		t.Fatal("Phi tails must pin to exactly 0 and 1")
	}
	// Symmetry and monotonicity.
	prev := -1.0
	for x := -8.0; x <= 8.0; x += 0.01 {
		p := Phi(x)
		if p < prev {
			t.Fatalf("Phi not monotonic at x=%v", x)
		}
		prev = p
		if d := math.Abs(Phi(-x) - (1 - p)); d > 2e-7 {
			t.Fatalf("Phi symmetry violated at x=%v: %v", x, d)
		}
	}
	if !math.IsNaN(Phi(math.NaN())) {
		t.Fatal("Phi(NaN) must be NaN")
	}
}

func TestFairValue(t *testing.T) {
	const sigma = 1e-4
	// At the money: exactly 0.5 regardless of sigma/T.
	if p, d2 := FairValue(100000, 100000, sigma, 150); math.Abs(p-0.5) > 1e-9 || d2 != 0 {
		t.Fatalf("ATM: p=%v d2=%v, want 0.5, 0", p, d2)
	}
	// Deep ITM / OTM.
	if p, _ := FairValue(101000, 100000, sigma, 10); p < 0.999999 {
		t.Fatalf("deep ITM p=%v, want ~1", p)
	}
	if p, _ := FairValue(99000, 100000, sigma, 10); p > 0.000001 {
		t.Fatalf("deep OTM p=%v, want ~0", p)
	}
	// Expiry step function, >= convention.
	if p, _ := FairValue(100000, 100000, sigma, 0); p != 1 {
		t.Fatalf("at expiry S==K must resolve Up (>=), got %v", p)
	}
	if p, _ := FairValue(99999.99, 100000, sigma, -1); p != 0 {
		t.Fatalf("at expiry S<K must resolve Down, got %v", p)
	}
	// Known value: ln(S/K)=sigma*sqrt(T) exactly one sd -> Phi(1).
	S := 100000 * math.Exp(sigma*math.Sqrt(300))
	if p, d2 := FairValue(S, 100000, sigma, 300); math.Abs(d2-1) > 1e-9 || math.Abs(p-phiRef(1)) > 1e-7 {
		t.Fatalf("one-sd case: p=%v d2=%v", p, d2)
	}
	// Invalid inputs.
	if p, _ := FairValue(-1, 100000, sigma, 100); !math.IsNaN(p) {
		t.Fatal("negative S must yield NaN")
	}
	if p, _ := FairValue(100000, 100000, 0, 100); !math.IsNaN(p) {
		t.Fatal("zero sigma must yield NaN")
	}
	// More time near the money => probability closer to 0.5.
	pShort, _ := FairValue(100050, 100000, sigma, 30)
	pLong, _ := FairValue(100050, 100000, sigma, 300)
	if !(pLong < pShort && pShort > 0.5 && pLong > 0.5) {
		t.Fatalf("time monotonicity: pShort=%v pLong=%v", pShort, pLong)
	}
}

// synthetic tick stream with constant per-interval log return magnitude r,
// alternating sign, sampled every dt seconds.
func feedConstantVol(v *VolEngine, r, dt float64, n int) {
	price := 100000.0
	ts := 0.0
	v.Update(price, ts)
	sign := 1.0
	for i := 0; i < n; i++ {
		ts += dt
		price *= math.Exp(sign * r)
		sign = -sign
		v.Update(price, ts)
	}
}

func TestVolEngineConvergence(t *testing.T) {
	cfg := DefaultVolConfig()
	v := NewVolEngine(cfg)
	// r per 1s interval => true per-sqrt-second sigma == r.
	const r = 1e-4
	feedConstantVol(v, r, 1.0, 2000)
	got := v.Sigma()
	if math.Abs(got-r)/r > 0.02 {
		t.Fatalf("sigma=%v, want ~%v (within 2%%)", got, r)
	}
}

func TestVolEngineSamplingInvariance(t *testing.T) {
	// The same true diffusion sampled at 1s and at 0.5s must give similar
	// sigma: per-interval return scales with sqrt(dt).
	cfg := DefaultVolConfig()
	const sigmaTrue = 2e-4
	v1 := NewVolEngine(cfg)
	feedConstantVol(v1, sigmaTrue*math.Sqrt(1.0), 1.0, 2000)
	v2 := NewVolEngine(cfg)
	feedConstantVol(v2, sigmaTrue*math.Sqrt(0.5), 0.5, 4000)
	s1, s2 := v1.Sigma(), v2.Sigma()
	if math.Abs(s1-s2)/sigmaTrue > 0.05 {
		t.Fatalf("sampling variance: s1=%v s2=%v", s1, s2)
	}
}

func TestVolEngineFloorCapWarmup(t *testing.T) {
	cfg := DefaultVolConfig()
	v := NewVolEngine(cfg)
	if v.Sigma() != cfg.SigmaStartup || v.Warm() {
		t.Fatal("cold engine must report SigmaStartup and !Warm")
	}
	// Nearly-still prices -> floor (long feed: the EWMA is seeded at
	// SigmaStartup^2 and needs several tau to decay to the floor).
	feedConstantVol(v, 1e-9, 1.0, 3000)
	if !v.Warm() {
		t.Fatal("engine should be warm after 3000 samples")
	}
	if got := v.Sigma(); got != cfg.SigmaFloor {
		t.Fatalf("sigma=%v, want floor %v", got, cfg.SigmaFloor)
	}
	// Seeding: right after the first samples, sigma must sit near the
	// startup prior rather than collapsing toward zero.
	v3 := NewVolEngine(cfg)
	feedConstantVol(v3, 1e-9, 1.0, cfg.WarmupSamples+1)
	if got := v3.Sigma(); got < cfg.SigmaStartup/2 {
		t.Fatalf("post-warmup sigma=%v collapsed below seeded prior %v", got, cfg.SigmaStartup)
	}
	// Wild prices -> cap.
	v2 := NewVolEngine(cfg)
	feedConstantVol(v2, 0.05, 1.0, 200)
	if got := v2.Sigma(); got != cfg.SigmaCap {
		t.Fatalf("sigma=%v, want cap %v", got, cfg.SigmaCap)
	}
}

func TestVolEngineMinSampleDt(t *testing.T) {
	cfg := DefaultVolConfig()
	v := NewVolEngine(cfg)
	v.Update(100000, 0)
	// A flood of sub-MinSampleDt ticks must not update the estimate.
	for i := 1; i <= 100; i++ {
		v.Update(100000+float64(i), float64(i)*0.001)
	}
	if v.samples != 0 {
		t.Fatalf("sub-interval ticks were sampled: %d", v.samples)
	}
	// The anchor must not have moved: a real sample afterwards uses t=0 price.
	v.Update(100100, 1.0)
	if v.samples != 1 {
		t.Fatal("expected exactly one sample")
	}
}

func TestClassifyRegime(t *testing.T) {
	cfg := DefaultQuoteConfig() // EndgameT=10, EndgameD2=2.0
	cases := []struct {
		name string
		T    float64
		d2   float64
		want Regime
	}{
		{"normal mid-window ATM", 150, 0, RegimeNormal},
		{"normal mid-window deep", 150, 5, RegimeNormal},
		{"boundary T==EndgameT stays normal", 10, 0, RegimeNormal},
		{"endgame near-the-money pulls", 9.9, 0.5, RegimePull},
		{"endgame near-the-money negative d2 pulls", 5, -1.9, RegimePull},
		{"endgame deep ITM defensive", 9.9, 4, RegimeDefensive},
		{"endgame deep OTM defensive", 3, -6, RegimeDefensive},
		{"expiring deep ITM defensive", 0.5, math.Inf(1), RegimeDefensive},
	}
	for _, c := range cases {
		if got := ClassifyRegime(c.T, c.d2, cfg); got != c.want {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
}

func TestComputeQuotes(t *testing.T) {
	cfg := DefaultQuoteConfig()

	// Flat inventory, mid-window: symmetric around mid.
	q := ComputeQuotes(0.500, 0, 150, 0, cfg)
	if !q.BidOK || !q.AskOK || q.Regime != RegimeNormal {
		t.Fatalf("flat quote not two-sided: %+v", q)
	}
	if math.Abs(q.Bid-0.480) > 1e-9 || math.Abs(q.Ask-0.520) > 1e-9 {
		t.Fatalf("flat quotes: bid=%v ask=%v, want 0.480/0.520", q.Bid, q.Ask)
	}

	// Long inventory shifts both quotes down; short shifts up.
	qLong := ComputeQuotes(0.500, 0, 150, 100, cfg) // skew = 0.04
	if !(qLong.Bid < q.Bid && qLong.Ask < q.Ask) {
		t.Fatalf("long inventory must shift quotes down: %+v", qLong)
	}
	qShort := ComputeQuotes(0.500, 0, 150, -100, cfg)
	if !(qShort.Bid > q.Bid && qShort.Ask > q.Ask) {
		t.Fatalf("short inventory must shift quotes up: %+v", qShort)
	}

	// Skew cap: absurd inventory can't shift more than MaxSkewShift.
	qCap := ComputeQuotes(0.500, 0, 150, 1e6, cfg)
	if qCap.BidOK && q.Bid-qCap.Bid > cfg.MaxSkewShift+cfg.HalfSpread {
		t.Fatalf("skew must be capped: %+v", qCap)
	}

	// Pull regime: no quotes.
	qPull := ComputeQuotes(0.500, 0, 5, 0, cfg)
	if qPull.BidOK || qPull.AskOK || qPull.Regime != RegimePull {
		t.Fatalf("near-money endgame must pull: %+v", qPull)
	}

	// Defensive regime: wider than normal.
	qDef := ComputeQuotes(0.98, 4, 5, 0, cfg)
	if qDef.Regime != RegimeDefensive {
		t.Fatalf("want defensive regime: %+v", qDef)
	}
	if qDef.AskOK && qDef.Ask-0.98 < cfg.HalfSpread*cfg.DefensiveMult-cfg.Tick {
		t.Fatalf("defensive ask not widened: %+v", qDef)
	}

	// Near the floor: bid falls below MinPrice and is suppressed, ask survives.
	qLow := ComputeQuotes(0.005, -3, 150, 0, cfg)
	if qLow.BidOK {
		t.Fatalf("bid below MinPrice must be suppressed: %+v", qLow)
	}
	if !qLow.AskOK || qLow.Ask < cfg.MinPrice {
		t.Fatalf("ask should survive near floor: %+v", qLow)
	}

	// Tick rounding: bid floored, ask ceiled, never crossed.
	qR := ComputeQuotes(0.5004, 0, 150, 0, cfg)
	if qR.Bid > 0.5004-cfg.HalfSpread || qR.Ask < 0.5004+cfg.HalfSpread {
		t.Fatalf("rounding must widen, not tighten: %+v", qR)
	}
	if qR.BidOK && qR.AskOK && qR.Ask <= qR.Bid {
		t.Fatalf("crossed quotes: %+v", qR)
	}

	// NaN mid: everything off.
	qNaN := ComputeQuotes(math.NaN(), 0, 150, 0, cfg)
	if qNaN.BidOK || qNaN.AskOK {
		t.Fatalf("NaN mid must produce no quotes: %+v", qNaN)
	}
}

func TestCompetitiveBid(t *testing.T) {
	const tick = 0.01
	cases := []struct {
		name                    string
		limit, bestBid, bestAsk float64
		want                    float64
	}{
		{"empty book: quote at limit", 0.48, 0, 0, 0.48},
		{"would cross: top-of-book beats ask clip", 0.61, 0.55, 0.58, 0.56},
		{"improve best bid by one tick only", 0.48, 0.40, 0.60, 0.41},
		{"join when improve would exceed limit", 0.405, 0.40, 0.60, 0.40},
		{"limit below book: rest deep", 0.30, 0.40, 0.60, 0.30},
		{"one-tick spread: join best bid", 0.50, 0.49, 0.50, 0.49},
		{"ask at tick: nothing to do", 0.50, 0, 0.01, 0},
		{"limit below tick: no quote", 0.005, 0.40, 0.60, 0},
		{"crossed limit vs tight book", 0.99, 0.51, 0.52, 0.51},
	}
	for _, c := range cases {
		got := CompetitiveBid(c.limit, c.bestBid, c.bestAsk, tick)
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
	// Never crosses, never exceeds limit — sweep.
	for limit := 0.01; limit < 1.0; limit += 0.03 {
		for bb := 0.0; bb < 0.9; bb += 0.07 {
			for ba := bb + tick; ba < 1.0; ba += 0.07 {
				p := CompetitiveBid(limit, bb, ba, tick)
				if p == 0 {
					continue
				}
				if p > limit+1e-9 {
					t.Fatalf("exceeds limit: p=%v limit=%v", p, limit)
				}
				if ba > 0 && p > ba-tick+1e-9 {
					t.Fatalf("crosses: p=%v bestAsk=%v", p, ba)
				}
			}
		}
	}
}

func TestCompetitiveAsk(t *testing.T) {
	const tick = 0.01
	cases := []struct {
		name                    string
		limit, bestBid, bestAsk float64
		want                    float64
	}{
		{"empty book: quote at limit", 0.52, 0, 0, 0.52},
		{"would cross bid: clip above", 0.40, 0.45, 0.50, 0.49},
		{"undercut best ask by one tick", 0.52, 0.40, 0.60, 0.59},
		{"join when undercut would break limit", 0.595, 0.40, 0.60, 0.60},
		{"limit above book: rest deep", 0.70, 0.40, 0.60, 0.70},
		{"one-tick spread: join best ask", 0.50, 0.49, 0.50, 0.50},
		{"limit above max: no quote", 0.995, 0.40, 0.60, 0},
	}
	for _, c := range cases {
		got := CompetitiveAsk(c.limit, c.bestBid, c.bestAsk, tick)
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("%s: got %v want %v", c.name, got, c.want)
		}
	}
	// Never crosses, never below limit — sweep.
	for limit := 0.01; limit < 1.0; limit += 0.03 {
		for bb := 0.0; bb < 0.9; bb += 0.07 {
			for ba := bb + tick; ba < 1.0; ba += 0.07 {
				p := CompetitiveAsk(limit, bb, ba, tick)
				if p == 0 {
					continue
				}
				if p < limit-1e-9 {
					t.Fatalf("below limit: p=%v limit=%v", p, limit)
				}
				if bb > 0 && p < bb+tick-1e-9 {
					t.Fatalf("crosses bid: p=%v bestBid=%v", p, bb)
				}
			}
		}
	}
}

// The hot path must not allocate: Phi, FairValue, VolEngine.Update, Sigma,
// ComputeQuotes.
func TestHotPathZeroAlloc(t *testing.T) {
	v := NewVolEngine(DefaultVolConfig())
	feedConstantVol(v, 1e-4, 1.0, 50)
	cfg := DefaultQuoteConfig()
	price, ts := 100000.0, 1000.0
	allocs := testing.AllocsPerRun(1000, func() {
		ts += 1.0
		price += 1.0
		v.Update(price, ts)
		sigma := v.Sigma()
		p, d2 := FairValue(price, 100000, sigma, 150)
		q := ComputeQuotes(p, d2, 150, 10, cfg)
		_ = q
	})
	if allocs != 0 {
		t.Fatalf("hot path allocates %v per run, want 0", allocs)
	}
}

func BenchmarkPricingHotPath(b *testing.B) {
	v := NewVolEngine(DefaultVolConfig())
	feedConstantVol(v, 1e-4, 1.0, 50)
	cfg := DefaultQuoteConfig()
	price, ts := 100000.0, 1000.0
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		ts += 0.25
		price += 0.5
		v.Update(price, ts)
		sigma := v.Sigma()
		p, d2 := FairValue(price, 100000, sigma, 150)
		_ = ComputeQuotes(p, d2, 150, 10, cfg)
	}
}
