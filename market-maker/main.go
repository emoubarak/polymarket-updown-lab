package main

// main.go — structural loop:
//
//	window roll → K fetch → per-tick fair value → requote gate →
//	(throttled) cancel-replace → fill reconciliation → risk checks
//
// Single event-loop goroutine owns all strategy state (no locks on the
// decision path). Feeds, heartbeat, user WS and order I/O run on their own
// goroutines and communicate via channels/atomics. Dry-run is the default;
// live order flow requires --live AND REBATE_LIVE=yes.

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync/atomic"
	"syscall"
	"time"
)

const (
	sideBid = 0 // BUY Up token
	sideAsk = 1 // BUY Down token at 1-ask (mirror of selling Up)
)

type restingQuote struct {
	orderID    string
	priceMicro int64 // in Up-probability space for both sides
	active     bool
	sellMode   bool // true: order is a SELL of held inventory (capital-free)
}

// Order-op outcomes. The op goroutines do HTTP ONLY and report one of
// these; every registry/quote mutation happens on the loop goroutine.
// "unknown" (transport failure) is a first-class state: the loop must
// reconcile with the venue before quoting that side again.
type opOutcome uint8

const (
	opPlaced       opOutcome = iota // order live, id confirmed
	opRejected                      // definitive 4xx: order does not exist
	opUnknown                       // transport error: order MAY exist
	opCancelFailed                  // replace aborted: old order may still rest
)

type orderResult struct {
	side       int
	windowSlug string // window the op was dispatched for
	outcome    opOutcome
	orderID    string // new order id (opPlaced)
	canceledID string // old order id whose cancel RESOLVED (registry removal)
	priceMicro int64
	sellMode   bool
	tokenID    string
	orderSide  uint8
	sizeMicro  int64
	err        error
}

type strikeResult struct {
	slug   string
	strike float64
	err    error
}

type deferredFill struct {
	f        Fill
	at       time.Time
	retries  int
	lookedUp bool // authoritative REST order lookup dispatched
}

// resyncSnapshot carries a REST open-orders snapshot plus the time the
// request was DISPATCHED: orders we tracked after that instant may be newer
// than the snapshot and must not be wiped by it.
type resyncSnapshot struct {
	orders []OpenOrder
	reqAt  time.Time
}

// fillMark tracks one fill for markout measurement. sign = +1 when the fill
// made us longer Up-equivalent (bid fill), −1 when shorter (ask fill).
type fillMark struct {
	at      time.Time
	upPrice float64 // fill price expressed in Up-probability space
	sign    float64
	size    float64 // shares
	m10Done bool
	m10     float64
}

type windowResult struct {
	w   *WindowInfo
	err error
}

// Bot owns the strategy state. All fields below are touched only by the
// main loop goroutine unless noted.
type Bot struct {
	cfg  *Config
	clob *ClobClient
	bn   *BinanceFeed
	bs   *BitstampFeed // fast leading feed (Bitstamp order_book, Frankfurt ~10ms)
	cl   *ChainlinkFeed
	uws  *UserWS
	book *BookFeed
	vol      *VolEngine
	inv      *Inventory
	reg      *OrderRegistry
	risk     *Risk
	tb       *TokenBucket
	splitter *SafeSplitter // Phase B: nil unless SPLIT_PAIRS > 0 && live
	paper    *PaperBroker  // paper mode: nil unless --paper

	window *WindowInfo
	next   *WindowInfo

	basis     float64 // EWMA of chainlink/binance ratio
	basisSet  bool
	lastObsMs int64
	lastBnNs  int64 // last Binance tick fed to the vol engine

	quotes      [2]restingQuote
	lastRequote [2]time.Time
	rejectUntil [2]time.Time // cooldown after an order rejection
	inflight    [2]atomic.Bool
	pullPending [2]bool // guard wanted a pull while an op was in flight
	rebateEq    float64 // Σ feeRate·p(1−p)·size over maker fills (rebate share proxy)
	markoutEWMA float64 // EWMA of matured per-share markout; adaptive AS gate

	liqInflight atomic.Bool // one liquidation FAK at a time
	liqUntil    time.Time   // cooldown until inventory reflects the last FAK

	mergeInflight atomic.Bool // one auto-merge tx at a time
	mergeUntil    time.Time   // cooldown until inventory reflects the last merge
	momUntil    time.Time   // paper momentum re-buy cooldown

	// Fills whose order the registry doesn't know yet (WS beat the HTTP
	// response). Retried once after the results channel catches up;
	// still-unknown fills trip the kill switch (never guess a side).
	deferred []deferredFill

	resyncCh chan resyncSnapshot // open-order snapshots applied on the loop

	lastGateReason string       // logged on change (silent gates are undebuggable)
	sellableAt     [2]time.Time // [0]=Up, [1]=Down: when bought shares settle enough to resell
	fillCoolUntil  [2]time.Time // per-side post-fill re-place brake

	// Short-horizon momentum samples (Binance) for the drift brake.
	driftPx  [5]float64
	driftTs  [5]int64 // unix nanos
	driftIdx int
	lastBrake string

	// Markout telemetry: measures adverse selection per fill (fair drift
	// after the fill, signed by our side). Negative = we got picked off.
	marks []fillMark

	results  chan orderResult
	strikeCh chan strikeResult
	windowCh chan windowResult
	fetching bool      // a window fetch is in flight
	striking bool      // a strike fetch is in flight
	strikeAt time.Time // last strike attempt (retry pacing)

	// telemetry (atomics: written by loop, read by metrics goroutine)
	mFair     atomic.Uint64 // float bits
	mSigma    atomic.Uint64
	mRequotes atomic.Int64
	mFills    atomic.Int64
	mSkips    atomic.Int64
	mRejects  atomic.Int64
	mGuards   atomic.Int64  // anti-pick-off pulls
	mRebateEq atomic.Uint64 // float bits of rebateEq
	mStepNs   atomic.Int64  // max step latency since last report
}

func main() {
	liveFlag := flag.Bool("live", false, "enable live order flow (also needs REBATE_LIVE=yes)")
	paperFlag := flag.Bool("paper", false, "paper mode: simulate fills against the live book, place no orders")
	orderTest := flag.Bool("order-test", false, "place+cancel one tiny live order, then exit (needs REBATE_LIVE=yes)")
	balance := flag.Bool("balance", false, "print the venue's collateral balance for this account, then exit")
	splitTest := flag.Bool("split-test", false, "on-chain split+merge round trip of 5 pUSD, then exit (needs REBATE_LIVE=yes)")
	flag.Parse()
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)

	if *balance {
		runBalanceCheck()
		return
	}
	if *orderTest {
		runOrderTest()
		return
	}
	if *splitTest {
		runSplitTest()
		return
	}

	var cfg *Config
	var err error
	if *paperFlag {
		cfg, err = LoadConfigPaper()
	} else {
		cfg, err = LoadConfig(*liveFlag)
	}
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	mode := "DRY-RUN"
	if cfg.Live {
		mode = "LIVE"
	} else if cfg.Paper {
		mode = "PAPER"
	}
	SetSeries(cfg.Series, cfg.WindowSec)
	log.Printf("rebate starting in %s mode, series=%s strategy=%s (sellOnly=%v momentum=%v)",
		mode, cfg.Series, cfg.Strategy, cfg.SellOnly, cfg.MomentumMode)

	wallet, err := NewWallet(cfg.PrivateKey, cfg.Funder, cfg.SigType)
	if err != nil {
		log.Fatalf("wallet: %v", err)
	}
	log.Printf("signer=%s funder=%s sigType=%d", wallet.AddressHex(), wallet.FunderHex(), wallet.SigType)

	clob, err := NewClobClient(cfg.ClobURL, wallet)
	if err != nil {
		log.Fatalf("clob: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Clock sanity: L2 auth and window timing depend on it.
	if st, err := clob.ServerTime(ctx); err == nil {
		drift := time.Now().Unix() - st
		log.Printf("clock drift vs venue: %+ds", drift)
		if drift > 5 || drift < -5 {
			log.Fatalf("clock drift %+ds too large; fix NTP first", drift)
		}
	} else {
		log.Printf("WARNING: server time check failed: %v", err)
	}

	// L2 credentials (needed for user WS even in dry-run; dry-run without
	// them just means no fill stream, which is fine for pricing tests).
	creds, err := clob.DeriveAPICreds(ctx)
	if err != nil {
		if cfg.Live {
			log.Fatalf("api creds: %v", err)
		}
		log.Printf("WARNING: no API creds (%v) — dry-run continues without user channel", err)
	} else {
		clob.SetCreds(creds)
		log.Printf("api key: %s...", creds.Key[:8])
	}

	wake := make(chan struct{}, 1)
	b := &Bot{
		cfg:      cfg,
		clob:     clob,
		bn:       NewBinanceFeed(cfg.BinanceURL, wake),
		bs:       newBitstampIfEnabled(cfg, wake),
		cl:       NewChainlinkFeed(cfg.RTDSURL, wake),
		vol:      NewVolEngine(cfg.Vol),
		inv:      NewInventory(),
		reg:      NewOrderRegistry(),
		risk:     NewRisk(cfg.Risk),
		tb:       NewTokenBucket(cfg.BucketBurst, cfg.BucketPerSec),
		results:  make(chan orderResult, 16),
		strikeCh: make(chan strikeResult, 4),
		windowCh: make(chan windowResult, 4),
		resyncCh: make(chan resyncSnapshot, 4),
	}

	b.book = NewBookFeed(cfg.MarketWSURL, wake)
	if cfg.Paper {
		b.paper = NewPaperBroker(b.inv)
		b.book.WatchTrades()
		log.Printf("PAPER mode: fills simulated vs live book prints; no orders, no capital")
	}
	if cfg.Live && (cfg.SplitPairs > 0 || cfg.MergeThreshold > 0) {
		if b.splitter, err = NewSafeSplitter(cfg.PolygonRPC, wallet); err != nil {
			log.Fatalf("splitter: %v", err)
		}
		if cfg.SplitPairs > 0 {
			log.Printf("phase B: splitting %d pairs per window", cfg.SplitPairs)
		}
		if cfg.MergeThreshold > 0 {
			log.Printf("delta-neutral farm: auto-merge paired shares >= %d", cfg.MergeThreshold)
		}
	}
	go b.bn.Run(ctx)
	if b.bs != nil {
		go b.bs.Run(ctx)
	}
	go b.cl.Run(ctx)
	go b.book.Run(ctx)
	if creds.Key != "" && !cfg.Paper {
		// Paper fills come from book prints, not the user channel; skip it
		// (also avoids duplicate user-WS connections when A/B-ing regimes).
		b.uws = NewUserWS(cfg.UserWSURL, creds)
		go b.uws.Run(ctx)
	}
	if cfg.Live {
		// Clean slate: a crashed predecessor may have left resting orders.
		if err := clob.CancelAll(ctx); err != nil {
			log.Fatalf("startup cancel-all failed: %v", err)
		}
		log.Printf("startup cancel-all ok")
		go clob.RunHeartbeat(ctx, 5*time.Second)
	}
	go b.metricsLoop(ctx)

	b.run(ctx, wake)

	// Graceful shutdown: never leave resting orders behind.
	if cfg.Live {
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := clob.CancelAll(sctx); err != nil {
			log.Printf("shutdown cancel-all FAILED: %v — orders will die with the heartbeat (~15s)", err)
		} else {
			log.Printf("shutdown: all orders cancelled")
		}
	}
	s := b.inv.Snapshot()
	log.Printf("final inventory: window=%s net=%.2f up=%.2f dn=%.2f usdcOut=%.4f fills=%d",
		s.WindowID, float64(s.Net)/1e6, float64(s.UpShares)/1e6, float64(s.DnShares)/1e6,
		float64(s.USDCOut)/1e6, s.Fills)
}

func (b *Bot) run(ctx context.Context, wake <-chan struct{}) {
	tick := time.NewTicker(100 * time.Millisecond) // time-decay/endgame cadence
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-wake:
		case <-tick.C:
		case ev := <-b.uwsEvents():
			b.onUserEvent(ctx, ev)
			continue
		case <-b.cl.Hist:
			// RTDS snapshot history: drained but NOT fed to the vol engine —
			// Chainlink and Binance trade ~10bps apart and mixing levels
			// would inject a fake jump. Binance warms the engine in seconds.
			continue
		case r := <-b.results:
			b.onOrderResult(r)
			continue
		case snap := <-b.resyncCh:
			b.applyResync(snap)
			continue
		case t := <-b.paperTrades():
			if b.window != nil {
				for _, f := range b.paper.OnTrade(t, b.window.Slug, b.window.UpTokenID, b.window.DownTokenID) {
					b.recordFill(f)
				}
			}
			continue
		case wr := <-b.windowCh:
			b.fetching = false
			if wr.err != nil {
				log.Printf("window fetch: %v", wr.err)
			} else {
				b.adoptWindow(ctx, wr.w)
			}
			continue
		case sr := <-b.strikeCh:
			b.striking = false
			b.onStrike(sr)
			continue
		}
		t0 := time.Now()
		b.step(ctx, t0)
		if d := time.Since(t0).Nanoseconds(); d > b.mStepNs.Load() {
			b.mStepNs.Store(d)
		}
	}
}

// driftZ samples the Binance mid every ~5s into a small ring and returns
// the ~20-25s return expressed in sigmas. ok=false until the ring has
// enough history.
func (b *Bot) driftZ(px float64, nowN int64, sigma float64) (float64, bool) {
	cur := b.driftIdx
	if b.driftTs[cur] == 0 || nowN-b.driftTs[cur] >= 5*1e9 {
		b.driftIdx = (cur + 1) % len(b.driftPx)
		b.driftPx[b.driftIdx] = px
		b.driftTs[b.driftIdx] = nowN
	}
	oldest := (b.driftIdx + 1) % len(b.driftPx)
	if b.driftTs[oldest] == 0 || b.driftPx[oldest] <= 0 {
		return 0, false
	}
	dt := float64(nowN-b.driftTs[oldest]) / 1e9
	if dt < 5 || sigma <= 0 {
		return 0, false
	}
	ret := math.Log(px / b.driftPx[oldest])
	return ret / (sigma * math.Sqrt(dt)), true
}

// uwsEvents returns the user event channel or a nil chan (never ready).
func (b *Bot) uwsEvents() <-chan UserEvent {
	if b.uws == nil {
		return nil
	}
	return b.uws.Events
}

// paperTrades returns the book trade channel in paper mode, else nil.
func (b *Bot) paperTrades() <-chan Trade {
	if b.paper == nil || b.book == nil {
		return nil
	}
	return b.book.Trades
}

// ---------------------------------------------------------------------------
// Window lifecycle
// ---------------------------------------------------------------------------

func (b *Bot) step(ctx context.Context, now time.Time) {
	// 1. Window management.
	if b.window == nil || !now.Before(b.window.End) {
		b.rollWindow(ctx, now)
		if b.window == nil {
			return
		}
	}
	// Prefetch next window's IDs 20s early (Gamma creates them ~1d ahead).
	if b.next == nil && !b.fetching && time.Until(b.window.End) < 20*time.Second {
		b.fetchWindowAsync(ctx, b.window.End)
	}
	if b.window.Strike == 0 {
		// The venue stamps the open price within seconds of the boundary;
		// poll at 2Hz, not per tick.
		if !b.striking && now.Sub(b.strikeAt) > 500*time.Millisecond {
			b.strikeAt = now
			b.fetchStrikeAsync(ctx, b.window)
		}
		return // cannot price without K
	}

	// 2. Reference prices.
	sCl, _ := b.cl.Feed.Load()
	sBn, tsBn := b.bn.Feed.Load()
	nowN := now.UnixNano()
	clAge := b.cl.Feed.AgeSec(nowN)
	bnAge := b.bn.Feed.AgeSec(nowN)

	// Fast leading feed: prefer Bitstamp (Frankfurt order_book, ~10ms) over
	// Binance (~118ms Tokyo) whenever it is fresher, falling back to Binance if
	// Bitstamp is stale or disabled. From here sBn/tsBn/bnAge carry the chosen
	// fast feed — every downstream use (vol, basis, oracle prediction) benefits
	// from the lower latency. Measured: Bitstamp gives ~108ms more anticipation
	// on each BTC move vs Binance from a Frankfurt VPS (see docs/LATENCY_FRANKFURT).
	if b.bs != nil {
		if sBs, tsBs := b.bs.Feed.Load(); sBs > 0 {
			if bsAge := b.bs.Feed.AgeSec(nowN); bsAge < bnAge || bnAge >= 2 {
				sBn, tsBn, bnAge = sBs, tsBs, bsAge
			}
		}
	}

	// Vol feeds exclusively on fresh Binance ticks (dense, single source —
	// mixing feeds or replaying a stale price would distort the estimator).
	if sBn > 0 && tsBn != b.lastBnNs {
		b.lastBnNs = tsBn
		b.vol.Update(sBn, float64(tsBn)/1e9)
	}

	// Basis: refresh on each new oracle observation; predict the oracle
	// between its ~1Hz updates with the (dense) Binance move.
	if obs := b.cl.ObsMs.Load(); obs != b.lastObsMs && sCl > 0 && sBn > 0 {
		b.lastObsMs = obs
		ratio := sCl / sBn
		if !b.basisSet {
			b.basis, b.basisSet = ratio, true
		} else {
			b.basis += 0.05 * (ratio - b.basis)
		}
	}
	sEff := sCl
	if b.basisSet && sBn > 0 && bnAge < 2 {
		pred := sBn * b.basis
		// Diverging proxy = data problem; trust the settlement anchor.
		if sCl > 0 && math.Abs(pred/sCl-1) < 0.003 {
			sEff = pred
		}
	}
	if sEff <= 0 {
		return
	}

	// 3. Fair value.
	T := b.window.End.Sub(now).Seconds()
	sigma := b.vol.Sigma()
	fair, d2 := FairValue(sEff, b.window.Strike, sigma, T)
	b.mFair.Store(math.Float64bits(fair))
	b.mSigma.Store(math.Float64bits(sigma))

	// 4. Quotes + risk gate. Paired inventory (min(up,down)) redeems at $1
	// regardless of outcome, so capital at RISK is cost basis minus that
	// guaranteed payout — this is what makes the strategy scale with
	// capital: turnover is unbounded, worst-case loss is what's capped.
	q := ComputeQuotes(fair, d2, T, b.inv.NetShares(), b.cfg.Quote)
	snap := b.inv.Snapshot()
	paired := min(snap.UpShares, snap.DnShares)
	riskUSDC := math.Max(0, float64(snap.USDCOut-paired)/1e6)
	// Worst-case shares each side could still move net by (resting
	// remainder or the next order) — feeds the predictive position cap.
	sideOutstanding := func(side int) float64 {
		out := b.cfg.QuoteSize
		if qq := b.quotes[side]; qq.active && qq.orderID != "dry" {
			if st, ok := b.reg.Get(qq.orderID); ok {
				if rem := float64(st.SizeMicro-st.MatchedMicro) / 1e6; rem > out {
					out = rem
				}
			}
		}
		return out
	}
	gate := b.risk.EvaluateGate(GateInput{
		ChainlinkAgeSec: clAge,
		BinanceAgeSec:   bnAge,
		HeartbeatFails:  b.clob.HBFailures.Load(),
		NetShares:       b.inv.NetShares(),
		BidOutstanding:  sideOutstanding(sideBid),
		AskOutstanding:  sideOutstanding(sideAsk),
		PositionUSDC:    riskUSDC,
		OpenBuyUSDC:     float64(b.reg.OpenBuyUSDCMicro()) / 1e6,
		VolWarm:         b.vol.Warm(),
		StrikeKnown:     b.window.Strike > 0,
		Live:            b.cfg.Live,
	})
	if gate.Reason != b.lastGateReason {
		log.Printf("gate: %s (bid=%v ask=%v)", gate.Reason, gate.AllowBid, gate.AllowAsk)
		b.lastGateReason = gate.Reason
	}
	if !gate.AllowBid {
		q.BidOK = false
	}
	if !gate.AllowAsk {
		q.AskOK = false
	}

	// Drift brake: never lean against strong short-horizon momentum. The
	// −$7 window came from reloading top-of-book bids down a persistent
	// move — fills looked fine at t+10s (markout +0.56) and bled to expiry.
	// MomentumMode (std0 replication) HOLDS through moves instead: churn
	// forfeits queue position, which is what caps our fill volume.
	if !b.cfg.MomentumMode && !b.cfg.SellOnly && b.cfg.DriftZMax > 0 && sBn > 0 {
		if z, ok := b.driftZ(sBn, nowN, sigma); ok {
			brake := ""
			if z <= -b.cfg.DriftZMax {
				q.BidOK = false
				brake = fmt.Sprintf("drift brake: bid off (z=%.2f)", z)
			} else if z >= b.cfg.DriftZMax {
				q.AskOK = false
				brake = fmt.Sprintf("drift brake: ask off (z=%.2f)", z)
			}
			if brake != b.lastBrake {
				if brake != "" {
					log.Printf("%s", brake)
				} else {
					log.Printf("drift brake: released")
				}
				b.lastBrake = brake
			}
		}
	}

	// Momentum re-buy (paper): std0 actively BUYS the trending side as a
	// taker (~half its volume). When short-horizon momentum is strong and
	// we're not already over the cap on that side, cross the book to buy it.
	if b.paper != nil && b.cfg.MomentumMode && sBn > 0 && !math.IsNaN(fair) {
		if z, ok := b.driftZ(sBn, nowN, sigma); ok && now.After(b.momUntil) {
			netSh := b.inv.NetShares()
			sizeMicro := int64(b.cfg.QuoteSize * float64(Micro))
			// Realistic taker cost: fee 0.07·p·(1-p)/share PLUS slippage
			// (we cross AFTER the move — model as PaperSlipTicks of the
			// touch price). Both raise the effective purchase price; the
			// pure-touch fill was the source of the +12 vs −14 mirage.
			takeAt := func(ask int64) int64 {
				p := float64(ask) / 1e6
				feePerShare := b.cfg.TakerFeeRate * p * (1 - p)
				slip := b.cfg.PaperSlipTicks * b.cfg.Quote.Tick
				eff := ask + int64((feePerShare+slip)*float64(Micro)+0.5)
				if eff >= Micro {
					eff = Micro - 1
				}
				return eff
			}
			if z >= b.cfg.DriftZMax && netSh < b.cfg.MaxPosition {
				if _, ask := b.book.Up.Best(); ask > 0 {
					if f, okf := b.paper.TakerBuy(true, takeAt(ask), sizeMicro, b.window.Slug, b.window.UpTokenID, b.window.DownTokenID); okf {
						b.momUntil = now.Add(2 * time.Second)
						b.recordFill(f)
					}
				}
			} else if z <= -b.cfg.DriftZMax && netSh > -b.cfg.MaxPosition {
				if _, ask := b.book.Down.Best(); ask > 0 {
					if f, okf := b.paper.TakerBuy(false, takeAt(ask), sizeMicro, b.window.Slug, b.window.UpTokenID, b.window.DownTokenID); okf {
						b.momUntil = now.Add(2 * time.Second)
						b.recordFill(f)
					}
				}
			}
		}
	}

	// 5. Endgame inventory management — residual inventory riding into
	// settlement is the dominant loss channel (fills mark out positive,
	// windows settle negative). Approaching the close:
	//   T < FlattenT1: no NEW exposure — only the reducing side quotes
	//                  (both modes of a side move net the same direction).
	//   T < FlattenT2: force-flatten anything >= venue minimum, maker if
	//                  the book allows, taker FAK otherwise (fee-aware).
	net := b.inv.NetShares()
	// Opening noise: the reference price needs ~30s to settle after the
	// boundary; the profitable competitor does zero trades in [0,30)s.
	if T > float64(b.cfg.WindowSec)-b.cfg.OpenQuietS {
		q.BidOK, q.AskOK = false, false
	}
	// Adaptive adverse-selection gate: if recent fills marked out negative
	// (EWMA below −MarkoutGate), we're being picked off — stop quoting until
	// the market stops running against us. This is the one signal that
	// separated winning from losing net=0 windows in the live data.
	if b.cfg.MarkoutGate > 0 && b.markoutEWMA < -b.cfg.MarkoutGate {
		q.BidOK, q.AskOK = false, false
	}
	if T < b.cfg.FlattenT1 {
		if net > 1 {
			q.BidOK = false
		} else if net < -1 {
			q.AskOK = false
		}
	}
	liqTrigger := b.cfg.MaxPosition
	liqRiskPerShare := 0.10
	if b.cfg.FlattenT2 > 0 && T < b.cfg.FlattenT2 {
		liqTrigger = 0 // flatten everything sellable
		// Near expiry the residual can swing the full $1; riding it is
		// worth much more than the taker fee.
		liqRiskPerShare = 0.5
	}
	// One FAK at a time, with a cooldown so the position can reflect the
	// fill before the next decision (inventory truth lags via the WS).
	if plan := PlanLiquidation(net, fair, b.cfg.Quote.Tick,
		b.cfg.TakerFeeRate, 0.03, liqTrigger, liqRiskPerShare); plan.Do &&
		!b.liqInflight.Load() && now.After(b.liqUntil) {
		b.liqUntil = now.Add(3 * time.Second)
		b.liquidate(ctx, plan)
	}

	// Delta-neutral rebate farming: recycle frozen collateral. Once both legs
	// hold >= MergeThreshold shares, merge the paired amount back to pUSD so
	// the cash keeps turning (volume/rebates are not capped by locked capital).
	// Net-neutral: both legs shrink by the same amount, so net exposure is
	// untouched. On-chain tx, so one at a time with a cooldown.
	// Only merge shares that have settled on-chain: sellableAt[tok] is pushed
	// out 12s on every BUY fill, so a merge attempted before then would try to
	// burn tokens the wallet doesn't hold yet and revert (GS013). Split-minted
	// shares keep sellableAt at zero, so sell-only merges fire immediately.
	if b.splitter != nil && b.cfg.MergeThreshold > 0 && !b.mergeInflight.Load() &&
		now.After(b.mergeUntil) && now.After(b.sellableAt[0]) && now.After(b.sellableAt[1]) {
		ms := b.inv.Snapshot()
		paired := ms.UpShares
		if ms.DnShares < paired {
			paired = ms.DnShares
		}
		if paired >= b.cfg.MergeThreshold*Micro {
			b.mergeUntil = now.Add(5 * time.Second)
			b.mergePaired(ctx, paired)
		}
	}

	// 6. Anti-pick-off guard: any resting quote whose edge vs the CURRENT
	// reservation price has collapsed is pulled immediately — no interval,
	// no token bucket. Compared against the SKEWED center (same reference
	// the quote was built from), not raw fair: near the position cap the
	// de-risking quote deliberately sits close to fair, and pulling it
	// would disable the AS mean-reversion exactly when it matters.
	// MomentumMode skips the guard: it deliberately holds the ITM side.
	// Sell-only DOES use it now: a resting ask left in place as the binary's
	// fair converges (theta) gets filled BELOW the current fair — the biggest
	// source of the "spread pure" bleed. Pulling the stale ask forfeits queue
	// position but avoids selling ~0.1-0.2 under fair on convergence, which
	// dwarfs the queue value. In up-space both slots are sells: sideBid (SELL
	// Down) is stale when its up-price is too HIGH; sideAsk (SELL Up) when too
	// LOW — the existing checks below cover both.
	if !b.cfg.MomentumMode && !math.IsNaN(fair) {
		skew := b.cfg.Quote.Lambda * b.inv.NetShares()
		if skew > b.cfg.Quote.MaxSkewShift {
			skew = b.cfg.Quote.MaxSkewShift
		} else if skew < -b.cfg.Quote.MaxSkewShift {
			skew = -b.cfg.Quote.MaxSkewShift
		}
		center := fair - skew
		if qb := b.quotes[sideBid]; qb.active && float64(qb.priceMicro)/1e6 > center-b.cfg.GuardEdge {
			b.mGuards.Add(1)
			b.pullSide(ctx, sideBid)
		}
		if qa := b.quotes[sideAsk]; qa.active && float64(qa.priceMicro)/1e6 < center+b.cfg.GuardEdge {
			b.mGuards.Add(1)
			b.pullSide(ctx, sideAsk)
		}
	}

	// 7. Markout maturation (adverse-selection measurement) + deferred
	// fills whose orders the registry hadn't seen yet.
	if !math.IsNaN(fair) {
		for i := range b.marks {
			m := &b.marks[i]
			if !m.m10Done && now.Sub(m.at) >= 10*time.Second {
				m.m10Done = true
				m.m10 = m.sign * (fair - m.upPrice)
				// Adaptive adverse-selection gate: track an EWMA of realized
				// per-share markout. When it goes negative we are being picked
				// off — suspend quoting (see gate in the quote step) until it
				// recovers. Analysis showed losing windows avg markout −0.44,
				// winning +0.21: this is the discriminant, used live.
				b.markoutEWMA = 0.7*b.markoutEWMA + 0.3*m.m10
				log.Printf("markout10s: %+.4f/share (fill up-px %.3f, size %.0f) ewma=%+.4f", m.m10, m.upPrice, m.size, b.markoutEWMA)
			}
		}
	}
	b.processDeferredFills(now)

	// Inventory integrity backstop: a dropped fill event cannot be rebuilt
	// from any REST endpoint — trading blind is not an option.
	if b.uws != nil && b.uws.DroppedFill.Load() {
		b.risk.Kill("user-channel fill event dropped: inventory unverifiable")
	}

	// 8. Requote gate + dispatch.
	b.syncSide(ctx, sideBid, q.Bid, q.BidOK, now)
	b.syncSide(ctx, sideAsk, q.Ask, q.AskOK, now)
}

func (b *Bot) rollWindow(ctx context.Context, now time.Time) {
	if b.window != nil {
		// Window just expired: close the book on it.
		w := b.window
		b.window = nil
		if b.cfg.Live {
			go func() {
				cctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := b.clob.CancelMarketOrders(cctx, w.ConditionID); err != nil {
					log.Printf("roll: cancel market orders: %v", err)
				}
			}()
		}
		sCl, _ := b.cl.Feed.Load()
		old := b.inv.Snapshot()
		upWins := sCl >= w.Strike // resolution rule: close >= open → Up
		settle := old.DnShares
		if upWins {
			settle = old.UpShares
		}
		// Per-fill P&L vs settlement + adverse-selection summary.
		settlePx := 0.0
		if upWins {
			settlePx = 1.0
		}
		var pnlVsSettle, adv10 float64
		for _, m := range b.marks {
			pnlVsSettle += m.sign * (settlePx - m.upPrice) * m.size
			if m.m10Done {
				adv10 += m.m10 * m.size
			}
		}
		b.marks = b.marks[:0]
		log.Printf("window %s closed: K=%.2f S=%.2f upWins=%v net=%.2f settleValue=%.4f usdcOut=%.4f estPnL=%.4f fillPnL=%.4f markout10s=%.4f rebateEq=%.4f",
			w.Slug, w.Strike, sCl, upWins, float64(old.Net)/1e6,
			float64(settle)/1e6, float64(old.USDCOut)/1e6, float64(settle-old.USDCOut)/1e6,
			pnlVsSettle, adv10, b.rebateEq)
		b.appendPnlCSV(w, sCl, upWins, old, pnlVsSettle, adv10)
		b.quotes[sideBid] = restingQuote{}
		b.quotes[sideAsk] = restingQuote{}
	}
	// Adopt the prefetched window if it matches "now"; else fetch.
	start := WindowStart(now)
	if b.next != nil && b.next.Start.Equal(start) {
		w := b.next
		b.next = nil
		b.adoptWindow(ctx, w)
		return
	}
	if !b.fetching {
		b.fetchWindowAsync(ctx, start)
	}
}

func (b *Bot) fetchWindowAsync(ctx context.Context, start time.Time) {
	b.fetching = true
	hc := &http.Client{Timeout: 5 * time.Second}
	go func() {
		w, err := FetchWindowInfo(ctx, hc, b.cfg.GammaURL, start)
		select {
		case b.windowCh <- windowResult{w: w, err: err}:
		case <-ctx.Done():
		}
	}()
}

func (b *Bot) adoptWindow(ctx context.Context, w *WindowInfo) {
	now := time.Now()
	switch {
	case w.Start.After(now):
		// Prefetched next window.
		b.next = w
		log.Printf("prefetched next window %s", w.Slug)
		return
	case !now.Before(w.End):
		log.Printf("fetched window %s already over; re-rolling", w.Slug)
		return
	}
	midWindowBoot := b.window == nil && b.next == nil && now.Sub(w.Start) > 5*time.Second
	b.window = w
	b.inv.ResetWindow(w.Slug)
	b.reg.Clear()
	b.quotes[sideBid] = restingQuote{}
	b.quotes[sideAsk] = restingQuote{}
	b.sellableAt[0], b.sellableAt[1] = time.Time{}, time.Time{}
	b.deferred = b.deferred[:0]
	if b.paper != nil {
		b.paper.Reset()
	}
	// Restart recovery: booted mid-window in live mode — we may already
	// hold tokens from before the crash. Never assume flat.
	if midWindowBoot && b.cfg.Live {
		b.seedInventory(ctx, w)
	}
	if w.TickSize > 0 {
		b.cfg.Quote.Tick = w.TickSize
		b.cfg.Quote.MinPrice = w.TickSize
		b.cfg.Quote.MaxPrice = 1 - w.TickSize
	}
	b.book.SetWindow(w.UpTokenID, w.DownTokenID)
	if b.uws != nil {
		b.uws.SetWindow(w.UpTokenID, w.DownTokenID, []string{w.ConditionID})
		b.uws.Bounce()
	}
	log.Printf("window %s adopted: cond=%s tick=%v negRisk=%v accepting=%v T=%.0fs",
		w.Slug, w.ConditionID, w.TickSize, w.NegRisk, w.Accepting, time.Until(w.End).Seconds())
	if !b.striking {
		b.fetchStrikeAsync(ctx, w)
	}
	// Phase B: mint riskless paired inventory for this window. Both quote
	// sides then run in sell-mode from the pairs (std0 model). The split
	// lands well inside the opening quiet period (~3s vs 30s).
	if b.splitter != nil {
		b.splitWindow(ctx, w)
	} else if b.paper != nil && b.cfg.SplitPairs > 0 {
		// Paper split: accounting only (no on-chain tx), pairs are riskless.
		pm := b.cfg.SplitPairs * Micro
		b.inv.ApplyFillForWindow(w.Slug, Fill{TradeID: "split:" + w.Slug + ":up", OrderID: "split", AssetID: w.UpTokenID, IsUp: true, Side: SideBuy, Size: pm, Price: 500_000})
		b.inv.ApplyFillForWindow(w.Slug, Fill{TradeID: "split:" + w.Slug + ":dn", OrderID: "split", AssetID: w.DownTokenID, IsUp: false, Side: SideBuy, Size: pm, Price: 500_000})
		b.sellableAt[0], b.sellableAt[1] = time.Time{}, time.Time{} // paper shares instantly sellable
		log.Printf("paper split %s: %d pairs (accounting)", w.Slug, b.cfg.SplitPairs)
	}
}

// splitWindow mints SplitPairs complete sets on-chain and, on confirmation,
// books them as paired inventory and refreshes the venue's balance cache so
// SELL orders on both tokens are accepted immediately.
func (b *Bot) splitWindow(ctx context.Context, w *WindowInfo) {
	pairsMicro := b.cfg.SplitPairs * Micro
	slug, cond, upTok, dnTok := w.Slug, w.ConditionID, w.UpTokenID, w.DownTokenID
	go func() {
		sctx, cancel := context.WithTimeout(ctx, 45*time.Second)
		defer cancel()
		hash, err := b.splitter.Split(sctx, cond, pairsMicro)
		if err != nil {
			log.Printf("split %s: %v — window will quote USDC-backed instead", slug, err)
			return
		}
		log.Printf("split %s: %d pairs minted (tx %.16s...)", slug, b.cfg.SplitPairs, hash)
		// Book the pairs at 0.50/leg (cost $1/pair, net exposure zero).
		// Deliberately NOT via recordFill: minted pairs are not maker
		// volume (no rebate credit, no markout marks). Window-fenced: a
		// slow tx must not pollute the next window's inventory (the pairs
		// still auto-redeem at settlement either way).
		okUp := b.inv.ApplyFillForWindow(slug, Fill{
			TradeID: "split:" + slug + ":up", OrderID: "split",
			AssetID: upTok, IsUp: true, Side: SideBuy,
			Size: pairsMicro, Price: 500_000,
		})
		okDn := b.inv.ApplyFillForWindow(slug, Fill{
			TradeID: "split:" + slug + ":dn", OrderID: "split",
			AssetID: dnTok, IsUp: false, Side: SideBuy,
			Size: pairsMicro, Price: 500_000,
		})
		if !okUp || !okDn {
			log.Printf("split %s: window rolled before confirmation — pairs will auto-redeem", slug)
			return
		}
		// Tokens are on-chain already: sellable as soon as the venue's
		// balance cache refreshes.
		for _, tok := range []string{upTok, dnTok} {
			uctx, ucancel := context.WithTimeout(context.Background(), 5*time.Second)
			if err := b.clob.UpdateBalanceAllowance(uctx, tok, b.cfg.SigType); err != nil {
				log.Printf("split %s: balance refresh %.10s: %v", slug, tok, err)
			}
			ucancel()
		}
	}()
}

// mergePaired burns `paired` complete sets (Up∧Down) back into pUSD on-chain,
// recycling the collateral for delta-neutral rebate farming. Net-neutral: both
// legs are debited equally, so directional exposure is unchanged. Books the
// reduction as SELL fills at 0.50/leg — NOT via recordFill, so it earns no
// rebate/markout marks (a merge is not maker volume).
func (b *Bot) mergePaired(ctx context.Context, paired int64) {
	if !b.mergeInflight.CompareAndSwap(false, true) {
		return
	}
	w := b.window
	if w == nil {
		b.mergeInflight.Store(false)
		return
	}
	slug, cond, upTok, dnTok := w.Slug, w.ConditionID, w.UpTokenID, w.DownTokenID
	go func() {
		defer b.mergeInflight.Store(false)
		mctx, cancel := context.WithTimeout(ctx, 45*time.Second)
		defer cancel()
		hash, err := b.splitter.Merge(mctx, cond, paired)
		if err != nil {
			log.Printf("merge %s: %v — collateral stays locked this cycle", slug, err)
			return
		}
		log.Printf("merge %s: %d pairs -> pUSD (tx %.16s...)", slug, paired/Micro, hash)
		// Debit both legs equally (net unchanged). Window-fenced so a slow tx
		// cannot corrupt the next window's inventory.
		b.inv.ApplyFillForWindow(slug, Fill{
			TradeID: "merge:" + slug + ":up:" + hash, OrderID: "merge",
			AssetID: upTok, IsUp: true, Side: SideSell,
			Size: paired, Price: 500_000,
		})
		b.inv.ApplyFillForWindow(slug, Fill{
			TradeID: "merge:" + slug + ":dn:" + hash, OrderID: "merge",
			AssetID: dnTok, IsUp: false, Side: SideSell,
			Size: paired, Price: 500_000,
		})
	}()
}

// appendPnlCSV journals one closed window for long-run analysis. Cold path;
// failures only log (the journal must never take the bot down).
func (b *Bot) appendPnlCSV(w *WindowInfo, closePx float64, upWins bool, s InventorySnapshot, fillPnL, adv10 float64) {
	if b.cfg.PnlCSV == "" {
		return
	}
	f, err := os.OpenFile(b.cfg.PnlCSV, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		log.Printf("pnl.csv: %v", err)
		return
	}
	defer f.Close()
	if st, err := f.Stat(); err == nil && st.Size() == 0 {
		fmt.Fprintln(f, "closed_at,slug,strike,close,up_wins,fills,up_shares,dn_shares,net,usdc_out,settle_value,pnl,fill_pnl,markout10s,rebate_eq_cum")
	}
	settle := s.DnShares
	if upWins {
		settle = s.UpShares
	}
	fmt.Fprintf(f, "%s,%s,%.2f,%.2f,%v,%d,%.2f,%.2f,%.2f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f\n",
		time.Now().UTC().Format(time.RFC3339), w.Slug, w.Strike, closePx, upWins, s.Fills,
		float64(s.UpShares)/1e6, float64(s.DnShares)/1e6, float64(s.Net)/1e6,
		float64(s.USDCOut)/1e6, float64(settle)/1e6, float64(settle-s.USDCOut)/1e6,
		fillPnL, adv10, b.rebateEq)
}

// seedInventory recovers on-venue positions after a mid-window restart.
func (b *Bot) seedInventory(ctx context.Context, w *WindowInfo) {
	hc := &http.Client{Timeout: 4 * time.Second}
	up, dn, cond := w.UpTokenID, w.DownTokenID, w.ConditionID
	funder := b.cfg.Funder
	go func() {
		positions, err := FetchPositions(ctx, hc, b.cfg.DataAPIURL, funder, cond)
		if err != nil {
			log.Printf("seed: positions fetch failed: %v — treating as flat, VERIFY MANUALLY", err)
			return
		}
		for _, p := range positions {
			isUp := p.Asset == up
			if !isUp && p.Asset != dn {
				continue
			}
			f := Fill{
				TradeID: "seed:" + p.Asset,
				IsUp:    isUp,
				Side:    SideBuy,
				Size:    int64(p.Size*1e6 + 0.5),
				Price:   int64(p.AvgPrice*1e6 + 0.5),
			}
			if f.Size > 0 && b.inv.ApplyFill(f) {
				log.Printf("seed: recovered %.2f %s @ %.3f from venue", p.Size,
					map[bool]string{true: "UP", false: "DOWN"}[isUp], p.AvgPrice)
			}
		}
	}()
}

func (b *Bot) fetchStrikeAsync(ctx context.Context, w *WindowInfo) {
	b.striking = true
	hc := &http.Client{Timeout: 5 * time.Second}
	slug := w.Slug
	start, end := w.Start, w.End
	go func() {
		strike, ok, err := FetchStrike(ctx, hc, b.cfg.SiteURL, start, end)
		if err == nil && !ok {
			err = fmt.Errorf("strike not stamped yet")
		}
		select {
		case b.strikeCh <- strikeResult{slug: slug, strike: strike, err: err}:
		case <-ctx.Done():
		}
	}()
}

func (b *Bot) onStrike(sr strikeResult) {
	if b.window == nil || b.window.Slug != sr.slug {
		return // stale result from a rolled window
	}
	if sr.err != nil {
		// Venue stamps the open price within seconds of the boundary;
		// retry on the next tick (striking=false re-arms it).
		return
	}
	b.window.Strike = sr.strike
	// Cross-check the strike against our own view of the oracle at open.
	if sCl, _ := b.cl.Feed.Load(); sCl > 0 {
		if dev := math.Abs(sr.strike/sCl - 1); dev > 0.01 {
			b.risk.Kill(fmt.Sprintf("strike %.2f deviates %.2f%% from oracle %.2f", sr.strike, dev*100, sCl))
			return
		}
	}
	log.Printf("window %s strike (price to beat) = %.2f", b.window.Slug, sr.strike)
}

// ---------------------------------------------------------------------------
// Quoting
// ---------------------------------------------------------------------------

// syncSide reconciles one side's resting order with the desired quote.
// The model quote (Up-probability space) is the LIMIT; the live book gives
// the competitive placement: top-of-book, post-only-safe, never beyond the
// limit.
//
// Execution mode per side: BUY the side's token with USDC, or — when we
// already hold enough of the OPPOSITE exposure — SELL held tokens instead.
// A sell uses shares as collateral (frees USDC for turnover) and unwinding
// a paired position leg-by-leg banks bid+ask > $1 vs the $1 redemption:
// that is what makes the strategy scale with capital.
//
//	bid: BUY Up @ p            or SELL Down @ (1-p) if we hold Down
//	ask: BUY Down @ (1-p)      or SELL Up @ p       if we hold Up
func (b *Bot) syncSide(ctx context.Context, side int, price float64, want bool, now time.Time) {
	cur := &b.quotes[side]
	sizeMicro := int64(b.cfg.QuoteSize * float64(Micro))
	snap := b.inv.Snapshot()

	var (
		tokenID         string
		orderSide       uint8 // 0 BUY, 1 SELL
		sellMode        bool
		orderPriceMicro int64
	)
	if want {
		tick := b.cfg.Quote.Tick
		var p float64
		var book *tokenBook
		// Sell-mode needs shares that are actually spendable: settled
		// on-chain and past the venue balance-cache refresh.
		dnSellable := snap.DnShares >= sizeMicro && now.After(b.sellableAt[1])
		upSellable := snap.UpShares >= sizeMicro && now.After(b.sellableAt[0])
		if side == sideBid {
			if dnSellable { // SELL Down instead of BUY Up
				sellMode, orderSide, tokenID = true, 1, b.window.DownTokenID
				book = &b.book.Down
				bb, ba := book.Best()
				p = CompetitiveAsk(1-price, float64(bb)/1e6, float64(ba)/1e6, tick)
			} else if b.cfg.SellOnly {
				want = false // std0's early method: NEVER bid (bids are what
				// get adverse-selected; only distribute split pairs as asks)
			} else {
				tokenID = b.window.UpTokenID
				book = &b.book.Up
				bb, ba := book.Best()
				p = CompetitiveBid(price, float64(bb)/1e6, float64(ba)/1e6, tick)
			}
		} else {
			if upSellable { // SELL Up instead of BUY Down
				sellMode, orderSide, tokenID = true, 1, b.window.UpTokenID
				book = &b.book.Up
				bb, ba := book.Best()
				p = CompetitiveAsk(price, float64(bb)/1e6, float64(ba)/1e6, tick)
			} else if b.cfg.SellOnly {
				want = false // sell-only: no bid on this side either
			} else {
				tokenID = b.window.DownTokenID
				book = &b.book.Down
				bb, ba := book.Best()
				p = CompetitiveBid(1-price, float64(bb)/1e6, float64(ba)/1e6, tick)
			}
		}
		// Never place against an unseen book (session snapshot not yet in):
		// that is quoting blind at the raw model limit — the exact recipe
		// for a post-only cross at window open. (book is nil when sell-only
		// suppressed this side — want is already false there.)
		if !want || book == nil || !book.Ready() || p <= 0 {
			want = false
		} else {
			orderPriceMicro = int64(p*float64(Micro) + 0.5)
		}
	}
	if !want {
		if cur.active {
			b.pullSide(ctx, side)
		}
		return
	}
	// Up-space price for state tracking/compare on both sides. The Up token
	// trades in up-space directly; Down-token orders mirror at 1-p.
	priceMicro := orderPriceMicro
	if tokenID == b.window.DownTokenID {
		priceMicro = Micro - orderPriceMicro
	}
	if cur.active && cur.sellMode == sellMode {
		if diff := math.Abs(float64(priceMicro-cur.priceMicro)) / float64(Micro); diff < b.cfg.RequoteMinMove {
			return // desired placement hasn't moved a tick — hold the quote
		}
	}
	if now.Before(b.rejectUntil[side]) {
		return // cooling down after a rejection
	}
	if !b.cfg.MomentumMode && !b.cfg.SellOnly && !cur.active && now.Before(b.fillCoolUntil[side]) {
		return // just got filled on this side: don't reload into the flow
	}
	if now.Sub(b.lastRequote[side]) < b.cfg.RequoteInterval {
		b.mSkips.Add(1)
		return
	}
	if b.inflight[side].Load() {
		return
	}
	if !b.tb.Allow(now) {
		b.mSkips.Add(1)
		return
	}
	b.lastRequote[side] = now
	b.mRequotes.Add(1)
	if sellMode {
		// Sell only what we actually hold.
		held := snap.UpShares
		if tokenID == b.window.DownTokenID {
			held = snap.DnShares
		}
		if held < sizeMicro {
			return // raced with a fill; next step re-evaluates
		}
	}
	cancelID := ""
	if cur.active {
		cancelID = cur.orderID
	}

	if b.paper != nil {
		// Ladder: rest LadderLevels rungs one tick apart from the base
		// competitive price, each with its own live-book queue position.
		// SELL rungs step UP (fill as price rises), BUY rungs step DOWN.
		isUpTok := tokenID == b.window.UpTokenID
		tick := int64(b.cfg.Quote.Tick*float64(Micro) + 0.5)
		n := b.cfg.LadderLevels
		if n < 1 {
			n = 1
		}
		levels := make([]LadderLevel, 0, n)
		for k := 0; k < n; k++ {
			lp := orderPriceMicro
			if sellMode {
				lp += int64(k) * tick
			} else {
				lp -= int64(k) * tick
			}
			if lp <= 0 || lp >= Micro {
				continue
			}
			levels = append(levels, LadderLevel{
				PriceMicro: lp, SizeMicro: sizeMicro,
				QueueM: b.book.QueueAheadM(isUpTok, lp, !sellMode),
			})
		}
		b.paper.PlaceLadder(side, isUpTok, sellMode, levels)
		cur.active, cur.priceMicro, cur.orderID, cur.sellMode = true, priceMicro, "paper", sellMode
		return
	}
	if !b.cfg.Live {
		cur.active, cur.priceMicro, cur.orderID, cur.sellMode = true, priceMicro, "dry", sellMode
		log.Printf("DRY %s: %s up-px %.3f (order px %.3f sell=%v) size %.0f",
			sideName(side), tokenID[:10], float64(priceMicro)/1e6, float64(orderPriceMicro)/1e6, sellMode, b.cfg.QuoteSize)
		return
	}

	b.inflight[side].Store(true)
	negRisk := b.window.NegRisk
	slug := b.window.Slug
	go func() {
		defer b.inflight[side].Store(false)
		res := orderResult{
			side: side, windowSlug: slug, priceMicro: priceMicro,
			sellMode: sellMode, tokenID: tokenID, orderSide: orderSide,
			sizeMicro: sizeMicro,
		}
		octx, cancel := context.WithTimeout(ctx, 4*time.Second)
		defer cancel()

		// Phase 1: cancel the order being replaced. A transport failure
		// means the old order MAY still rest — abort the replace entirely
		// (posting would double-quote the side). An HTTP 200 resolves the
		// cancel either way: the order is canceled, or it was already
		// matched/gone — in both cases it can no longer double-quote.
		if cancelID != "" && cancelID != "dry" {
			if _, err := b.clob.CancelOrders(octx, []string{cancelID}); err != nil {
				res.outcome, res.err = opCancelFailed, err
				b.sendResult(res)
				return
			}
			res.canceledID = cancelID
		}

		o, err := b.clob.BuildOrder(tokenID, orderSide, orderPriceMicro, sizeMicro, negRisk)
		if err != nil {
			res.outcome, res.err = opRejected, err
			b.sendResult(res)
			return
		}
		resp, err := b.clob.PostOrder(octx, o, "GTC", true)
		switch {
		case err == nil && resp.Success && resp.OrderID != "":
			res.outcome, res.orderID = opPlaced, resp.OrderID
		case err == nil:
			res.outcome = opRejected
			res.err = fmt.Errorf("rejected: %s (%s)", resp.ErrorMsg, resp.Status)
		default:
			var he *ClobHTTPError
			if asClobErr(err, &he) && he.Status >= 400 && he.Status < 500 {
				res.outcome, res.err = opRejected, err // definitive: no order
			} else {
				res.outcome, res.err = opUnknown, err // order MAY exist
			}
		}
		b.sendResult(res)
	}()
}

// sendResult delivers an op outcome to the loop; it must never be dropped
// (state reconciliation depends on it), so block until accepted.
func (b *Bot) sendResult(r orderResult) {
	b.results <- r
}

func (b *Bot) pullSide(ctx context.Context, side int) {
	cur := &b.quotes[side]
	id := cur.orderID
	*cur = restingQuote{}
	if b.paper != nil {
		b.paper.Pull(side)
		return
	}
	if !b.cfg.Live || id == "" || id == "dry" {
		return
	}
	// Hold the side's inflight lock through the cancel so a requote can't
	// post a replacement while the old order might still be live.
	if !b.inflight[side].CompareAndSwap(false, true) {
		// A replace op is running; its result must not reinstate a quote
		// the guard already condemned — onOrderResult checks this flag.
		b.pullPending[side] = true
		return
	}
	go func() {
		defer b.inflight[side].Store(false)
		octx, cancel := context.WithTimeout(ctx, 4*time.Second)
		defer cancel()
		if _, err := b.clob.CancelOrders(octx, []string{id}); err != nil {
			log.Printf("pull %s: %v (order may be orphaned until window roll)", sideName(side), err)
			return // keep the registry entry: the order may still rest
		}
		b.reg.Remove(id)
	}()
}

func (b *Bot) onOrderResult(r orderResult) {
	// Resolved cancels leave the registry regardless of what follows.
	if r.canceledID != "" {
		b.reg.Remove(r.canceledID)
	}

	// Result from a window that has since rolled: the market is expired or
	// expiring; any order that did get placed must not linger.
	if b.window == nil || b.window.Slug != r.windowSlug {
		if r.outcome == opPlaced {
			log.Printf("%s: late order %.10s from rolled window %s — cancelling", sideName(r.side), r.orderID, r.windowSlug)
			b.cancelOrderAsync(r.orderID)
		}
		return
	}

	switch r.outcome {
	case opPlaced:
		if b.pullPending[r.side] {
			// The guard demanded a pull while this op was in flight — the
			// price is already stale; kill the fresh order immediately.
			b.pullPending[r.side] = false
			b.quotes[r.side] = restingQuote{}
			b.cancelOrderAsync(r.orderID)
			return
		}
		tokPrice := r.priceMicro // Up token trades in up-space directly
		if r.tokenID == b.window.DownTokenID {
			tokPrice = Micro - r.priceMicro
		}
		b.reg.Track(OrderState{
			ID:         r.orderID,
			TokenID:    r.tokenID,
			IsUp:       r.tokenID == b.window.UpTokenID,
			Side:       r.orderSide,
			PriceMicro: tokPrice,
			SizeMicro:  r.sizeMicro,
		})
		b.quotes[r.side] = restingQuote{orderID: r.orderID, priceMicro: r.priceMicro, active: true, sellMode: r.sellMode}

	case opRejected:
		b.mRejects.Add(1)
		log.Printf("%s order rejected: %v", sideName(r.side), r.err)
		b.quotes[r.side] = restingQuote{}
		b.pullPending[r.side] = false
		// Balance/allowance problems don't fix themselves in a second.
		cool := 800 * time.Millisecond
		if es := r.err.Error(); strings.Contains(es, "balance") || strings.Contains(es, "allowance") {
			cool = 3 * time.Second
		}
		b.rejectUntil[r.side] = time.Now().Add(cool)

	case opCancelFailed:
		// Old order may still rest; quote state still tracks it and the
		// guard still watches it. Retry the replace after a beat.
		log.Printf("%s replace aborted (cancel failed): %v", sideName(r.side), r.err)
		b.rejectUntil[r.side] = time.Now().Add(time.Second)

	case opUnknown:
		// Transport failure on post: an order MAY be resting untracked.
		// Nuke the market's orders and requote from a clean slate — the
		// only state we can prove.
		b.mRejects.Add(1)
		log.Printf("%s order outcome UNKNOWN (%v) — cancelling market and resyncing", sideName(r.side), r.err)
		b.quotes[sideBid] = restingQuote{}
		b.quotes[sideAsk] = restingQuote{}
		b.pullPending[r.side] = false
		b.rejectUntil[sideBid] = time.Now().Add(2 * time.Second)
		b.rejectUntil[sideAsk] = time.Now().Add(2 * time.Second)
		cond := b.window.ConditionID
		go func() {
			cctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			if err := b.clob.CancelMarketOrders(cctx, cond); err != nil {
				log.Printf("unknown-outcome cleanup failed: %v", err)
			}
		}()
		// No registry Clear here: tombstones must survive for late-fill
		// resolution. The follow-up snapshot merge prunes stale entries.
		b.resyncOrders(context.Background())
	}
}

// cancelOrderAsync fires a best-effort cancel for one order id.
func (b *Bot) cancelOrderAsync(id string) {
	go func() {
		cctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if _, err := b.clob.CancelOrders(cctx, []string{id}); err != nil {
			log.Printf("async cancel %.10s: %v", id, err)
		}
		b.reg.Remove(id)
	}()
}

func (b *Bot) liquidate(ctx context.Context, plan LiquidationPlan) {
	if !b.cfg.Live {
		log.Printf("DRY liquidation: %s", plan.Rationale)
		return
	}
	if !b.tb.Allow(time.Now()) {
		return
	}
	if !b.liqInflight.CompareAndSwap(false, true) {
		return
	}
	w := b.window
	tokenID := w.UpTokenID
	held := b.inv.Snapshot().UpShares
	if !plan.SellUp {
		tokenID = w.DownTokenID
		held = b.inv.Snapshot().DnShares
	}
	// Tick-align the limit (floor: safe direction for a sell) and never
	// sell more than we hold.
	tick := b.cfg.Quote.Tick
	limitMicro := int64(floorToTick(plan.LimitPx, tick)*float64(Micro) + 0.5)
	sizeMicro := int64(plan.Shares*float64(Micro) + 0.5)
	if held < sizeMicro {
		sizeMicro = held
	}
	sizeMicro -= sizeMicro % 10_000 // venue: share sizes max 2 decimals
	if limitMicro < int64(tick*float64(Micro)) || sizeMicro < 5*Micro {
		b.liqInflight.Store(false)
		return // nothing sellable at venue minimums
	}
	log.Printf("LIQUIDATION: %s (aligned px %.3f size %.2f)", plan.Rationale,
		float64(limitMicro)/1e6, float64(sizeMicro)/1e6)
	negRisk := w.NegRisk
	go func() {
		defer b.liqInflight.Store(false)
		octx, cancel := context.WithTimeout(ctx, 4*time.Second)
		defer cancel()
		o, err := b.clob.BuildOrder(tokenID, 1, limitMicro, sizeMicro, negRisk)
		if err != nil {
			log.Printf("liquidation build: %v", err)
			return
		}
		resp, err := b.clob.PostOrder(octx, o, "FAK", false)
		if err != nil || !resp.Success {
			log.Printf("liquidation post: %v %+v", err, resp)
		}
	}()
}

// deferFill parks a fill whose order the registry hasn't seen yet.
func (b *Bot) deferFill(f Fill) {
	b.deferred = append(b.deferred, deferredFill{f: f, at: time.Now()})
}

// processDeferredFills retries parked fills once the results channel has
// had time to Track the order. If the registry still doesn't know it (fill
// beat the HTTP post response under load), the venue is asked directly —
// GET /data/order/{id} is authoritative. Only a fill the VENUE cannot
// resolve trips the kill switch.
func (b *Bot) processDeferredFills(now time.Time) {
	if len(b.deferred) == 0 {
		return
	}
	keep := b.deferred[:0]
	for _, d := range b.deferred {
		if b.inv.Seen(d.f.TradeID) {
			continue // a replay slipped in while parked
		}
		if now.Sub(d.at) < 400*time.Millisecond {
			keep = append(keep, d)
			continue
		}
		if st, ok := b.reg.Get(d.f.OrderID); ok {
			f := d.f
			f.Side = Side(st.Side)
			if b.inv.ApplyFill(f) {
				b.recordFill(f)
			}
			continue
		}
		d.retries++
		d.at = now
		if d.retries == 4 && !d.lookedUp {
			d.lookedUp = true
			b.lookupOrderAsync(d.f.OrderID)
		}
		if d.retries < 12 { // ~5s total: lookup result lands via resyncCh-style Track
			keep = append(keep, d)
			continue
		}
		b.risk.Kill(fmt.Sprintf("fill %.16s for order %.10s unknown even to the venue", d.f.TradeID, d.f.OrderID))
	}
	b.deferred = keep
}

// lookupOrderAsync resolves an order authoritatively from the venue and
// Tracks it (registry is thread-safe; Track is upsert). The parked fill
// then resolves on the next deferred pass.
func (b *Bot) lookupOrderAsync(orderID string) {
	upTok := ""
	if b.window != nil {
		upTok = b.window.UpTokenID
	}
	go func() {
		octx, cancel := context.WithTimeout(context.Background(), 4*time.Second)
		defer cancel()
		o, err := b.clob.GetOrder(octx, orderID)
		if err != nil {
			log.Printf("order lookup %.10s: %v", orderID, err)
			return
		}
		side := uint8(0)
		if o.Side == "SELL" {
			side = 1
		}
		b.reg.Track(OrderState{
			ID:           o.ID,
			TokenID:      o.AssetID,
			IsUp:         o.AssetID == upTok,
			Side:         side,
			PriceMicro:   parseMicro(o.Price),
			SizeMicro:    parseMicro(o.OriginalSize),
			MatchedMicro: parseMicro(o.SizeMatched),
		})
		log.Printf("order lookup %.10s resolved: %s %s", orderID, o.Side, o.AssetID[:10])
	}()
}

// ---------------------------------------------------------------------------
// Fill reconciliation
// ---------------------------------------------------------------------------

// recordFill books telemetry and order-state consequences of an APPLIED
// fill (inventory already updated). Loop goroutine only.
func (b *Bot) recordFill(f Fill) {
	b.mFills.Add(1)
	b.reg.OnMatched(f.OrderID, f.Size)
	p := float64(f.Price) / 1e6
	// Rebate proxy: crypto maker rebates are distributed pro-rata by the
	// fee-equivalent of filled MAKER volume. Taker fills (liquidation)
	// pay fees instead of earning rebates — exclude them.
	if !f.Taker {
		b.rebateEq += b.cfg.TakerFeeRate * p * (1 - p) * float64(f.Size) / 1e6
		b.mRebateEq.Store(math.Float64bits(b.rebateEq))
	}
	// Markout bookkeeping (adverse-selection measurement).
	upPx, sign := p, 1.0
	if !f.IsUp {
		upPx = 1 - p
		sign = -1
	}
	if f.Side == SideSell {
		sign = -sign
	}
	b.marks = append(b.marks, fillMark{
		at: time.Now(), upPrice: upPx, sign: sign, size: float64(f.Size) / 1e6,
	})
	// Post-fill brake: the side that just traded waits before re-placing.
	side := sideAsk
	if sign > 0 {
		side = sideBid
	}
	b.fillCoolUntil[side] = time.Now().Add(b.cfg.FillCooldown)
	log.Printf("fill: %s %s %.2f @ %.3f taker=%v net=%.2f rebateEq=%.4f",
		map[bool]string{true: "UP", false: "DOWN"}[f.IsUp],
		map[Side]string{SideBuy: "BUY", SideSell: "SELL"}[f.Side],
		float64(f.Size)/1e6, p, f.Taker, b.inv.NetShares(), b.rebateEq)
	// Bought shares can back a SELL only after on-chain settlement AND a
	// venue balance-cache refresh (sells reject with "balance: 0" before).
	// Paper has no such constraint (no real settlement).
	if f.Side == SideBuy && b.paper == nil {
		tokIdx := 0 // Up
		if !f.IsUp {
			tokIdx = 1 // Down
		}
		b.sellableAt[tokIdx] = time.Now().Add(12 * time.Second)
		if b.cfg.Live {
			tokenID := f.AssetID
			go func() {
				uctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
				defer cancel()
				if err := b.clob.UpdateBalanceAllowance(uctx, tokenID, b.cfg.SigType); err != nil {
					log.Printf("balance-allowance update %.10s: %v", tokenID, err)
				}
			}()
		}
	}
	// A fully-consumed resting quote frees the side for the next step.
	for s := range b.quotes {
		if b.quotes[s].active && b.quotes[s].orderID == f.OrderID {
			if st, ok := b.reg.Get(f.OrderID); !ok || st.MatchedMicro >= st.SizeMicro {
				b.quotes[s] = restingQuote{}
			}
		}
	}
}

func (b *Bot) onUserEvent(ctx context.Context, ev UserEvent) {
	switch ev.Kind {
	case UEFill:
		f := ev.Fill
		// Window integrity: a fill whose asset is not in the CURRENT window
		// (late replay buffered across a roll) must never touch inventory.
		if b.window == nil || (f.AssetID != b.window.UpTokenID && f.AssetID != b.window.DownTokenID) {
			log.Printf("dropping out-of-window fill %.16s asset=%.10s", f.TradeID, f.AssetID)
			return
		}
		f.IsUp = f.AssetID == b.window.UpTokenID
		// Status replays (MINED/CONFIRMED) of an already-applied trade must
		// short-circuit HERE: the registry forgets fully-matched orders, so
		// a replay would otherwise land in the deferred queue and, being
		// forever unresolvable, trip the kill switch.
		if b.inv.Seen(f.TradeID) {
			return
		}
		if f.Side == 255 { // maker fill: side comes from our registry
			if st, ok := b.reg.Get(f.OrderID); ok {
				f.Side = Side(st.Side)
			} else {
				// Registry may simply not have caught up (the WS fill can
				// beat the HTTP post response). NEVER guess a side — a
				// mis-signed sell fill corrupts inventory by 2× the size.
				// Defer and retry after the results channel drains.
				b.deferFill(f)
				return
			}
		}
		if b.inv.ApplyFill(f) {
			b.recordFill(f)
		}
	case UEFillFailed:
		if b.inv.ReverseFill(ev.TradeID) {
			log.Printf("fill REVERSED (on-chain failure): %s net=%.2f", ev.TradeID, b.inv.NetShares())
		}
	case UEOrderUpdate:
		if ev.OrderType == "CANCELLATION" || ev.OrderStatus == "CANCELED" {
			b.reg.Remove(ev.OrderID)
			for s := range b.quotes {
				if b.quotes[s].orderID == ev.OrderID {
					b.quotes[s] = restingQuote{}
				}
			}
		}
	case UEResync:
		b.resyncOrders(ctx)
	}
}

// resyncOrders fetches the venue's open orders after a WS (re)connect; the
// snapshot is APPLIED ON THE LOOP GOROUTINE (via resyncCh) so it cannot
// race fills or order results.
func (b *Bot) resyncOrders(ctx context.Context) {
	if !b.cfg.Live || b.window == nil {
		return
	}
	cond := b.window.ConditionID
	reqAt := time.Now()
	go func() {
		octx, cancel := context.WithTimeout(ctx, 5*time.Second)
		defer cancel()
		orders, err := b.clob.GetOpenOrders(octx, cond)
		if err != nil {
			log.Printf("resync: %v", err)
			return
		}
		select {
		case b.resyncCh <- resyncSnapshot{orders: orders, reqAt: reqAt}:
		case <-ctx.Done():
		}
	}()
}

// applyResync MERGES a REST snapshot into the registry. Loop goroutine
// only. Never clear-and-rebuild: orders tracked after the snapshot request
// may be newer than the snapshot, and wiping them turns their fills into
// "unknown order" kills (observed live). Removal is by tombstone and only
// for orders provably stale (tracked before the request, absent from the
// snapshot — venue auto-cancel, manual cancel, heartbeat lapse).
func (b *Bot) applyResync(snap resyncSnapshot) {
	if b.window == nil {
		return
	}
	upTok := b.window.UpTokenID
	live := make(map[string]bool, len(snap.orders))
	for _, o := range snap.orders {
		live[o.ID] = true
		side := uint8(0)
		if o.Side == "SELL" {
			side = 1
		}
		b.reg.Track(OrderState{
			ID:           o.ID,
			TokenID:      o.AssetID,
			IsUp:         o.AssetID == upTok,
			Side:         side,
			PriceMicro:   parseMicro(o.Price),
			SizeMicro:    parseMicro(o.OriginalSize),
			MatchedMicro: parseMicro(o.SizeMatched),
		})
	}
	stale := b.reg.StaleMissing(live, snap.reqAt)
	for _, id := range stale {
		b.reg.Remove(id) // tombstone: late fills stay resolvable
	}
	for s := range b.quotes {
		q := b.quotes[s]
		if !q.active || q.orderID == "dry" || live[q.orderID] || b.inflight[s].Load() {
			continue
		}
		for _, id := range stale {
			if id == q.orderID {
				log.Printf("resync: %s quote %.10s no longer on venue — clearing", sideName(s), q.orderID)
				b.quotes[s] = restingQuote{}
				break
			}
		}
	}
	log.Printf("resync: %d venue orders merged, %d stale tombstoned", len(snap.orders), len(stale))
}

// ---------------------------------------------------------------------------
// Telemetry (off the hot path)
// ---------------------------------------------------------------------------

func (b *Bot) metricsLoop(ctx context.Context) {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			b.reg.Purge(time.Now().Add(-60 * time.Second))
			nowN := time.Now().UnixNano()
			sCl, _ := b.cl.Feed.Load()
			sBn, _ := b.bn.Feed.Load()
			sBs, bsAge := 0.0, -1.0
			if b.bs != nil {
				sBs, _ = b.bs.Feed.Load()
				bsAge = b.bs.Feed.AgeSec(nowN)
			}
			ubb, uba := b.book.Up.Best()
			log.Printf("stats: cl=%.2f(%.1fs) bn=%.2f(%.1fs) bs=%.2f(%.1fs) sigma=%.3e fair=%.3f bookUp=%.2f/%.2f pos=%.2f requotes=%d skips=%d rejects=%d fills=%d rebEq=%.4f maxStep=%.2fms hbFail=%d",
				sCl, b.cl.Feed.AgeSec(nowN), sBn, b.bn.Feed.AgeSec(nowN), sBs, bsAge,
				math.Float64frombits(b.mSigma.Load()), math.Float64frombits(b.mFair.Load()),
				float64(ubb)/1e6, float64(uba)/1e6,
				b.inv.NetShares(), b.mRequotes.Swap(0), b.mSkips.Swap(0), b.mRejects.Swap(0),
				b.mFills.Load(), math.Float64frombits(b.mRebateEq.Load()),
				float64(b.mStepNs.Swap(0))/1e6, b.clob.HBFailures.Load())
			if g := b.mGuards.Swap(0); g > 0 {
				log.Printf("stats: %d anti-pick-off pulls in last 30s", g)
			}
		}
	}
}

func sideName(s int) string {
	if s == sideBid {
		return "bid"
	}
	return "ask"
}
