package main

// config.go — env-driven configuration. No secrets in source; the wallet
// key and funder come exclusively from the environment (POLY_PRIVATE_KEY,
// POLY_FUNDER, POLY_SIG_TYPE — matching the venue's conventional names).
//
// DRY-RUN IS THE DEFAULT. Live trading requires BOTH the --live flag and
// REBATE_LIVE=yes in the environment (belt and suspenders).

import (
	"fmt"
	"os"
	"strconv"
	"time"
)

type Config struct {
	// Identity (secrets — env only).
	PrivateKey string // POLY_PRIVATE_KEY
	Funder     string // POLY_FUNDER (proxy wallet holding funds)
	SigType    int    // POLY_SIG_TYPE: 0 EOA, 1 proxy, 2 Gnosis safe

	// Endpoints.
	ClobURL     string
	GammaURL    string
	SiteURL     string // strike ("price to beat") endpoint host
	RTDSURL     string
	UserWSURL   string
	MarketWSURL string // CLOB market channel (order books)
	DataAPIURL  string // public positions endpoint (restart recovery)
	PolygonRPC  string // JSON-RPC for Phase B on-chain split/merge
	PnlCSV      string // per-window P&L journal path ("" disables)
	BinanceURL  string
	BitstampURL string
	UseBitstamp bool // prefer Bitstamp order_book (Frankfurt ~10ms) over Binance

	// Trading.
	Live           bool    // false = dry run (default)
	Paper          bool    // paper mode: simulate fills vs live book, no orders

	// Series: which btc-updown market. 5m has more windows but tighter
	// spreads/more HFT; 15m has fewer windows but more spread and time to
	// distribute patiently (std0 earned +0.78/win on 15m vs +0.14 on 5m).
	Series    string // "btc-updown-5m" | "btc-updown-15m"
	WindowSec int64  // 300 | 900
	QuoteSize      float64 // shares per side (venue min 5)
	MaxPosition    float64 // |net shares| hard cap
	MaxOpenNotional float64 // USDC committed in resting buys + position cost

	// Quoting parameters (see math_engine.go).
	Quote QuoteConfig
	Vol   VolConfig

	// Requote gate: BOTH must pass — mid moved enough AND min interval
	// elapsed — plus the token bucket. Naive tick-by-tick requoting is a
	// rate-limit violation by design (Binance ticks tens of times/second).
	RequoteMinMove   float64       // min |theoretical - resting| in prob units
	RequoteInterval  time.Duration // min time between requotes per side
	BucketBurst      float64
	BucketPerSec     float64

	// GuardEdge: minimum edge a RESTING quote must keep vs current fair;
	// below it the quote is pulled instantly (anti-pick-off, un-throttled).
	GuardEdge float64

	// MarkoutGate: adaptive adverse-selection gate. When the EWMA of realized
	// per-share markout drops below −MarkoutGate, suspend quoting (we're being
	// picked off) until it recovers. 0 = disabled. Analysis: losing windows
	// avg markout −0.44/share, winning +0.21 — the discriminant.
	MarkoutGate float64

	// FillCooldown: after a maker fill on a side, that side may not
	// re-place for this long. Kills the reload-into-trend pattern (fills
	// look fine at t+10s but the side keeps getting hit down a move).
	FillCooldown time.Duration

	// DriftZMax: suppress the side leaning against short-horizon momentum
	// when |20s Binance return| exceeds this many sigmas. 0 disables.
	DriftZMax float64

	// Strategy is the named regime; it sets the flags below to a coherent
	// profile so the two capital regimes stay cleanly separated:
	//
	//   "sellonly"  — SMALL capital (std0's early method, verified
	//                 profitable at ~$100-150): split pairs, distribute
	//                 both sides as passive asks, NEVER bid, no taker.
	//                 Avoids the adverse selection that bids/taker incur.
	//   "momentum"  — LARGE capital (std0's scaled method): maker both
	//                 sides + hold + active taker re-buy. Higher volume &
	//                 rebate share, but pays adverse selection the rebate
	//                 pool must cover — only worth it at big capital.
	//   "neutral"   — defensive maker both sides with guard+brake.
	Strategy string

	// MomentumMode: lean into the trend + taker re-buy (set by "momentum").
	MomentumMode bool
	// SellOnly: never bid; only distribute split pairs as asks (set by
	// "sellonly"). The single change that flips small-capital MM from
	// loss-making (bids adverse-selected) to profitable.
	SellOnly bool

	// LadderLevels: paper only — rest this many rungs per side, one tick
	// apart, to capture prints at multiple prices (std0 ladders; a single
	// top-of-book order caught only ~58% of its fill volume).
	LadderLevels int

	// PaperSlipTicks: paper only — model taker execution slippage (we cross
	// AFTER the move) as this many ticks added to the touch price, on top of
	// the known taker fee. The instantaneous-touch fill produced a +12 vs
	// −14 mirage; this is the honest (if estimated) cost of chasing.
	PaperSlipTicks float64

	// Endgame flattening: residual inventory riding into settlement is the
	// dominant loss channel (markouts positive, window P&L negative).
	FlattenT1 float64 // T below which no NEW exposure may be opened (s)
	FlattenT2 float64 // T below which |net| >= venue-min is force-flattened (s); 0 disables (carry pairs, never pay the exit spread)

	// OpenQuietS: no quoting for the first N seconds of a window — the
	// reference price needs to settle after the boundary (competitor
	// analysis: the profitable MM does zero trades in the first 30s).
	OpenQuietS float64

	// SplitPairs: Phase B — complete Up+Down sets to mint on-chain at each
	// window adoption. Both quote sides then SELL from riskless paired
	// inventory (the std0 model). 0 disables (USDC-bid quoting as before).
	SplitPairs int64 // in shares

	// MergeThreshold: min paired shares before auto-merging back to pUSD
	// (delta-neutral rebate farming — recycles frozen collateral). 0 = off.
	MergeThreshold int64

	// Risk.
	Risk RiskLimits

	// Fees (crypto markets, taker-only): fee = rate * p * (1-p) * shares.
	TakerFeeRate float64
}

func envStr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envFloat(key string, def float64) (float64, error) {
	v := os.Getenv(key)
	if v == "" {
		return def, nil
	}
	f, err := strconv.ParseFloat(v, 64)
	if err != nil {
		return 0, fmt.Errorf("%s=%q: %w", key, v, err)
	}
	return f, nil
}

func envInt(key string, def int) (int, error) {
	v := os.Getenv(key)
	if v == "" {
		return def, nil
	}
	i, err := strconv.Atoi(v)
	if err != nil {
		return 0, fmt.Errorf("%s=%q: %w", key, v, err)
	}
	return i, nil
}

// LoadConfig reads the environment. liveFlag is the --live CLI flag.
func LoadConfig(liveFlag bool) (*Config, error) {
	return loadConfig(liveFlag, false)
}

// LoadConfigPaper is LoadConfig with paper mode requested.
func LoadConfigPaper() (*Config, error) {
	return loadConfig(false, true)
}

func loadConfig(liveFlag, paper bool) (*Config, error) {
	cfg := &Config{
		PrivateKey: os.Getenv("POLY_PRIVATE_KEY"),
		Funder:     os.Getenv("POLY_FUNDER"),

		ClobURL:    envStr("CLOB_URL", "https://clob.polymarket.com"),
		GammaURL:   envStr("GAMMA_URL", "https://gamma-api.polymarket.com"),
		SiteURL:    envStr("SITE_URL", "https://polymarket.com"),
		RTDSURL:    envStr("RTDS_URL", "wss://ws-live-data.polymarket.com"),
		UserWSURL:   envStr("USER_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/user"),
		MarketWSURL: envStr("MARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
		DataAPIURL:  envStr("DATA_API_URL", "https://data-api.polymarket.com"),
		PolygonRPC:  envStr("POLYGON_RPC", "https://polygon-bor-rpc.publicnode.com"),
		PnlCSV:      envStr("PNL_CSV", "pnl.csv"),
		BinanceURL:  envStr("BINANCE_URL", "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"),
		BitstampURL: envStr("BITSTAMP_URL", "wss://ws.bitstamp.net"),
		UseBitstamp: envStr("USE_BITSTAMP", "yes") == "yes",

		Quote: DefaultQuoteConfig(),
		Vol:   DefaultVolConfig(),
	}

	var err error
	if cfg.SigType, err = envInt("POLY_SIG_TYPE", 2); err != nil {
		return nil, err
	}
	if cfg.QuoteSize, err = envFloat("QUOTE_SIZE", 5); err != nil {
		return nil, err
	}
	if cfg.MaxPosition, err = envFloat("MAX_POSITION", 50); err != nil {
		return nil, err
	}
	if cfg.MaxOpenNotional, err = envFloat("MAX_OPEN_NOTIONAL", 25); err != nil {
		return nil, err
	}
	if cfg.Quote.HalfSpread, err = envFloat("HALF_SPREAD", cfg.Quote.HalfSpread); err != nil {
		return nil, err
	}
	if cfg.Quote.Lambda, err = envFloat("LAMBDA", cfg.Quote.Lambda); err != nil {
		return nil, err
	}
	if cfg.TakerFeeRate, err = envFloat("TAKER_FEE_RATE", 0.07); err != nil {
		return nil, err
	}
	moveDef := 0.01 // one 1c tick
	if cfg.RequoteMinMove, err = envFloat("REQUOTE_MIN_MOVE", moveDef); err != nil {
		return nil, err
	}
	ivalMs, err := envInt("REQUOTE_INTERVAL_MS", 400)
	if err != nil {
		return nil, err
	}
	cfg.RequoteInterval = time.Duration(ivalMs) * time.Millisecond
	if cfg.BucketBurst, err = envFloat("BUCKET_BURST", 8); err != nil {
		return nil, err
	}
	if cfg.BucketPerSec, err = envFloat("BUCKET_PER_SEC", 5); err != nil {
		return nil, err
	}
	if cfg.MarkoutGate, err = envFloat("MARKOUT_GATE", 0); err != nil {
		return nil, err
	}
	if cfg.GuardEdge, err = envFloat("GUARD_EDGE", 0.005); err != nil {
		return nil, err
	}
	fcMs, err := envInt("FILL_COOLDOWN_MS", 4000)
	if err != nil {
		return nil, err
	}
	cfg.FillCooldown = time.Duration(fcMs) * time.Millisecond
	if cfg.DriftZMax, err = envFloat("DRIFT_Z_MAX", 1.5); err != nil {
		return nil, err
	}
	// Strategy selects a coherent regime profile. Explicit MOMENTUM_MODE /
	// SELL_ONLY env vars still override for fine-grained A/B testing.
	cfg.Series = envStr("SERIES", "btc-updown-5m")
	switch cfg.Series {
	case "btc-updown-5m":
		cfg.WindowSec = 300
	case "btc-updown-15m":
		cfg.WindowSec = 900
	default:
		return nil, fmt.Errorf("unknown SERIES %q (btc-updown-5m|btc-updown-15m)", cfg.Series)
	}

	cfg.Strategy = envStr("STRATEGY", "neutral")
	switch cfg.Strategy {
	case "sellonly":
		cfg.SellOnly = true
	case "momentum":
		cfg.MomentumMode = true
	case "neutral":
		// defaults (guard+brake), nothing to set
	default:
		return nil, fmt.Errorf("unknown STRATEGY %q (sellonly|momentum|neutral)", cfg.Strategy)
	}
	if os.Getenv("MOMENTUM_MODE") == "yes" {
		cfg.MomentumMode = true
	}
	if os.Getenv("SELL_ONLY") == "yes" {
		cfg.SellOnly = true
	}
	if cfg.PaperSlipTicks, err = envFloat("PAPER_SLIP_TICKS", 1.0); err != nil {
		return nil, err
	}
	if ll, err := envInt("LADDER_LEVELS", 1); err != nil {
		return nil, err
	} else {
		cfg.LadderLevels = ll
	}
	if cfg.FlattenT1, err = envFloat("FLATTEN_T1_S", 60); err != nil {
		return nil, err
	}
	// Default 0: carrying near-expiry residuals beats paying empty-book
	// exit spreads (verified: flatten FAKs never filled at T<20s).
	if cfg.FlattenT2, err = envFloat("FLATTEN_T2_S", 0); err != nil {
		return nil, err
	}
	if cfg.OpenQuietS, err = envFloat("OPEN_QUIET_S", 30); err != nil {
		return nil, err
	}
	splitPairs, err := envInt("SPLIT_PAIRS", 0)
	if err != nil {
		return nil, err
	}
	cfg.SplitPairs = int64(splitPairs)

	// MergeThreshold: delta-neutral rebate farming. When we've accumulated
	// >= this many paired (Up∧Down) shares from two-sided maker fills, merge
	// them back into pUSD on-chain to recycle the cash — net-neutral (both
	// legs shrink equally), keeps the capital turning so volume/rebates are
	// not capped by frozen collateral. 0 = disabled.
	mergeThr, err := envInt("MERGE_THRESHOLD", 0)
	if err != nil {
		return nil, err
	}
	cfg.MergeThreshold = int64(mergeThr)

	cfg.Risk = RiskLimits{
		MaxPositionShares:   cfg.MaxPosition,
		MaxOpenNotionalUSDC: cfg.MaxOpenNotional,
		MaxChainlinkAgeSec:  8,  // oracle mirrors ~1/s; 8s means feed trouble
		MaxBinanceAgeSec:    5,  // bookTicker is high-rate; 5s is an outage
		MaxHeartbeatFails:   3,  // ~15s: venue is auto-cancelling us anyway
	}

	// Live gating: flag AND env must agree. Paper never touches funds.
	cfg.Paper = paper
	envLive := os.Getenv("REBATE_LIVE") == "yes"
	cfg.Live = liveFlag && envLive && !paper
	if liveFlag && !envLive {
		return nil, fmt.Errorf("--live given but REBATE_LIVE=yes not set; refusing")
	}

	if cfg.PrivateKey == "" {
		return nil, fmt.Errorf("POLY_PRIVATE_KEY not set")
	}
	if cfg.SigType != 0 && cfg.Funder == "" {
		return nil, fmt.Errorf("POLY_FUNDER required for sig type %d", cfg.SigType)
	}
	if cfg.QuoteSize < 5 {
		return nil, fmt.Errorf("QUOTE_SIZE %v below venue minimum 5", cfg.QuoteSize)
	}
	return cfg, nil
}
