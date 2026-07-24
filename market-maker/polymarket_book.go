package main

// polymarket_book.go — CLOB market channel: live order book for both tokens
// of the active window.
//
// Purpose: competitive, post-only-safe order placement. The model gives the
// LIMIT (the worst price we're willing to trade at); the book gives the
// PLACEMENT (top-of-book without crossing). This kills the "post-only order
// crosses book" spam observed live and maximizes maker fill volume — which
// is what earns crypto-market maker rebates (20% of taker fees, pro-rata by
// fee-equivalent filled maker volume).
//
// Books at tick 0.01 have ≤99 levels; we store fixed arrays indexed by
// price/0.001 (1001 slots) so tick_size_change to 0.001 stays representable.
// encoding/json is acceptable here: tens of msgs/sec worst case, µs each —
// the zero-alloc constraint is for the price-anchor feeds, not this channel.

import (
	"context"
	"encoding/json"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const bookSlots = 1001 // price index = priceMicro / 1000 (0.001 steps)

// tokenBook is one token's aggregated book. Guarded by BookFeed.mu; bests
// are mirrored into atomics for the lock-free pricing loop.
type tokenBook struct {
	bids [bookSlots]int64 // micro-shares resting at each price level
	asks [bookSlots]int64

	bestBid atomic.Int64 // micro price, 0 = none
	bestAsk atomic.Int64 // micro price, 0 = none
	ts      atomic.Int64 // unix nanos of last update
}

func (tb *tokenBook) reset() {
	clear(tb.bids[:])
	clear(tb.asks[:])
	tb.bestBid.Store(0)
	tb.bestAsk.Store(0)
}

func (tb *tokenBook) recomputeBests(nowNanos int64) {
	best := int64(0)
	for i := bookSlots - 1; i >= 0; i-- {
		if tb.bids[i] > 0 {
			best = int64(i) * 1000
			break
		}
	}
	tb.bestBid.Store(best)
	best = 0
	for i := 0; i < bookSlots; i++ {
		if tb.asks[i] > 0 {
			best = int64(i) * 1000
			break
		}
	}
	tb.bestAsk.Store(best)
	tb.ts.Store(nowNanos)
}

// Best returns (bestBidMicro, bestAskMicro); 0 means that side is empty.
func (tb *tokenBook) Best() (int64, int64) {
	return tb.bestBid.Load(), tb.bestAsk.Load()
}

// Ready reports whether this session has received a book snapshot. Quoting
// against an unseen book means placing at the raw model limit — which is
// how post-only orders cross at window open.
func (tb *tokenBook) Ready() bool { return tb.ts.Load() != 0 }

// AgeSec returns seconds since the last book update (+Inf if never).
func (tb *tokenBook) AgeSec(nowNanos int64) float64 {
	ts := tb.ts.Load()
	if ts == 0 {
		return 1e18
	}
	return float64(nowNanos-ts) / 1e9
}

// Trade is one executed print observed on the market channel (last trade).
// TakerSide is the aggressor's side: "BUY" hit the asks, "SELL" hit the bids.
type Trade struct {
	IsUp      bool
	PriceM    int64 // micro
	SizeM     int64 // micro
	TakerSide string
}

// BookFeed maintains books for the current window's Up and Down tokens.
type BookFeed struct {
	URL  string
	Up   tokenBook
	Down tokenBook

	wake chan<- struct{}
	// Trades receives executed prints (paper fill simulation). Buffered;
	// nil unless a consumer sets it via WatchTrades. Never blocks the feed.
	Trades chan Trade

	mu      sync.Mutex
	upID    string
	downID  string
	conn    *websocket.Conn
	tickHit atomic.Int64 // count of tick_size_change events (log/telemetry)
}

func NewBookFeed(url string, wake chan<- struct{}) *BookFeed {
	return &BookFeed{URL: url, wake: wake}
}

// WatchTrades enables executed-print delivery (paper mode). Call before Run.
func (f *BookFeed) WatchTrades() {
	f.Trades = make(chan Trade, 512)
}

// QueueAheadM returns the resting liquidity a new order at priceMicro would
// sit BEHIND: the size already at our level plus everything at strictly
// better prices (a crossing aggressor consumes all of it before us). This
// is the paper fill simulator's queue-position input — quoting top-of-book
// (strictly improving) yields 0, joining a crowded level yields the crowd.
func (f *BookFeed) QueueAheadM(isUp bool, priceMicro int64, isBid bool) int64 {
	f.mu.Lock()
	defer f.mu.Unlock()
	tb := &f.Down
	if isUp {
		tb = &f.Up
	}
	idx := int(priceMicro / 1000)
	if idx < 0 || idx >= bookSlots {
		return 0
	}
	var ahead int64
	if isBid {
		// Buyers ahead = same level + higher-priced bids (filled first).
		for i := idx; i < bookSlots; i++ {
			ahead += tb.bids[i]
		}
	} else {
		// Sellers ahead = same level + lower-priced asks (filled first).
		for i := idx; i >= 0; i-- {
			ahead += tb.asks[i]
		}
	}
	return ahead
}

func (f *BookFeed) emitTrade(t Trade) {
	if f.Trades == nil {
		return
	}
	select {
	case f.Trades <- t:
	default:
	}
}

// SetWindow swaps the tracked tokens and forces a reconnect.
func (f *BookFeed) SetWindow(upID, downID string) {
	f.mu.Lock()
	f.upID, f.downID = upID, downID
	f.Up.reset()
	f.Down.reset()
	c := f.conn
	f.mu.Unlock()
	if c != nil {
		c.Close()
	}
}

// Run maintains the market-channel subscription until ctx is cancelled.
func (f *BookFeed) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		start := time.Now()
		err := f.session(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Printf("bookws: session ended after %s: %v (reconnect in %s)",
			time.Since(start).Round(time.Millisecond), err, backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if time.Since(start) > time.Minute {
			backoff = 250 * time.Millisecond
		} else if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

func (f *BookFeed) session(ctx context.Context) error {
	f.mu.Lock()
	up, down := f.upID, f.downID
	f.mu.Unlock()
	if up == "" {
		select { // no window yet
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(200 * time.Millisecond):
			return nil
		}
	}

	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := dialer.DialContext(ctx, f.URL, nil)
	if err != nil {
		return err
	}
	f.mu.Lock()
	f.conn = conn
	f.mu.Unlock()
	done := make(chan struct{})
	defer func() {
		close(done)
		f.mu.Lock()
		f.conn = nil
		f.mu.Unlock()
		conn.Close()
	}()
	go func() {
		select {
		case <-ctx.Done():
			conn.Close()
		case <-done:
		}
	}()

	sub := struct {
		AssetsIDs []string `json:"assets_ids"`
		Type      string   `json:"type"`
	}{[]string{up, down}, "market"}
	if err := conn.WriteJSON(sub); err != nil {
		return err
	}
	go func() { // socket keepalive
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				if err := conn.WriteMessage(websocket.TextMessage, []byte("PING")); err != nil {
					return
				}
			}
		}
	}()

	for {
		if err := conn.SetReadDeadline(time.Now().Add(30 * time.Second)); err != nil {
			return err
		}
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		f.handle(msg, time.Now().UnixNano())
	}
}

// Wire shapes: frames may be a single event or an array of events.
type bookLevel struct {
	Price string `json:"price"`
	Size  string `json:"size"`
}

type bookEvent struct {
	EventType string `json:"event_type"`
	AssetID   string `json:"asset_id"`
	// book snapshot (both naming generations)
	Bids  []bookLevel `json:"bids"`
	Asks  []bookLevel `json:"asks"`
	Buys  []bookLevel `json:"buys"`
	Sells []bookLevel `json:"sells"`
	// price_change (single and batched forms)
	Price   string `json:"price"`
	Side    string `json:"side"`
	Size    string `json:"size"`
	Changes []struct {
		AssetID string `json:"asset_id"`
		Price   string `json:"price"`
		Side    string `json:"side"`
		Size    string `json:"size"`
	} `json:"changes"`
	PriceChanges []struct {
		AssetID string `json:"asset_id"`
		Price   string `json:"price"`
		Side    string `json:"side"`
		Size    string `json:"size"`
	} `json:"price_changes"`
	NewTick string `json:"new_tick_size"`
	// last_trade_price fields
	FeeRateBps string `json:"fee_rate_bps"`
	// (price/side/size reused from above for last_trade_price)
}

func (f *BookFeed) handle(msg []byte, nowNanos int64) {
	i := 0
	for i < len(msg) && (msg[i] == ' ' || msg[i] == '\n' || msg[i] == '\r' || msg[i] == '\t') {
		i++
	}
	if i >= len(msg) || (msg[i] != '[' && msg[i] != '{') {
		return // PONG etc.
	}
	var events []bookEvent
	if msg[i] == '[' {
		if err := json.Unmarshal(msg[i:], &events); err != nil {
			return
		}
	} else {
		var ev bookEvent
		if err := json.Unmarshal(msg[i:], &ev); err != nil {
			return
		}
		events = append(events, ev)
	}
	// Level arrays are also touched by SetWindow (loop goroutine) — take
	// the mutex, and read the CURRENT token ids under it so a frame from a
	// closing session can never repopulate freshly-reset books with the
	// previous window's levels.
	f.mu.Lock()
	upID, downID := f.upID, f.downID
	updated := false
	for k := range events {
		if f.apply(&events[k], upID, downID, nowNanos) {
			updated = true
		}
	}
	f.mu.Unlock()
	if updated {
		select {
		case f.wake <- struct{}{}:
		default:
		}
	}
}

func (f *BookFeed) bookFor(assetID, upID, downID string) *tokenBook {
	switch assetID {
	case upID:
		return &f.Up
	case downID:
		return &f.Down
	}
	return nil
}

func (f *BookFeed) apply(ev *bookEvent, upID, downID string, nowNanos int64) bool {
	switch ev.EventType {
	case "book":
		tb := f.bookFor(ev.AssetID, upID, downID)
		if tb == nil {
			return false
		}
		bids, asks := ev.Bids, ev.Asks
		if len(bids) == 0 && len(ev.Buys) > 0 {
			bids = ev.Buys
		}
		if len(asks) == 0 && len(ev.Sells) > 0 {
			asks = ev.Sells
		}
		clear(tb.bids[:])
		clear(tb.asks[:])
		for _, l := range bids {
			if idx, sz, ok := levelIdx(l.Price, l.Size); ok {
				tb.bids[idx] = sz
			}
		}
		for _, l := range asks {
			if idx, sz, ok := levelIdx(l.Price, l.Size); ok {
				tb.asks[idx] = sz
			}
		}
		tb.recomputeBests(nowNanos)
		return true

	case "price_change":
		any := false
		applyOne := func(assetID, price, side, size string) {
			tb := f.bookFor(assetID, upID, downID)
			if tb == nil {
				return
			}
			idx, sz, ok := levelIdx(price, size)
			if !ok {
				return
			}
			if side == "BUY" {
				tb.bids[idx] = sz
			} else {
				tb.asks[idx] = sz
			}
			tb.recomputeBests(nowNanos)
			any = true
		}
		for _, c := range ev.Changes {
			aid := c.AssetID
			if aid == "" {
				aid = ev.AssetID
			}
			applyOne(aid, c.Price, c.Side, c.Size)
		}
		for _, c := range ev.PriceChanges {
			aid := c.AssetID
			if aid == "" {
				aid = ev.AssetID
			}
			applyOne(aid, c.Price, c.Side, c.Size)
		}
		if len(ev.Changes) == 0 && len(ev.PriceChanges) == 0 && ev.Price != "" {
			applyOne(ev.AssetID, ev.Price, ev.Side, ev.Size)
		}
		return any

	case "last_trade_price", "trade":
		tb := f.bookFor(ev.AssetID, upID, downID)
		if tb == nil {
			return false
		}
		pm, sm := parseMicro(ev.Price), parseMicro(ev.Size)
		if pm > 0 && sm > 0 {
			f.emitTrade(Trade{
				IsUp:      ev.AssetID == upID,
				PriceM:    pm,
				SizeM:     sm,
				TakerSide: ev.Side,
			})
		}
		return false

	case "tick_size_change":
		f.tickHit.Add(1)
		log.Printf("bookws: tick_size_change asset=%.10s new=%s", ev.AssetID, ev.NewTick)
		return false
	}
	return false
}

// levelIdx converts (price, size) strings to (slot index, micro size).
func levelIdx(price, size string) (int, int64, bool) {
	pm := parseMicro(price)
	if pm <= 0 || pm >= Micro || pm%1000 != 0 {
		return 0, 0, false
	}
	return int(pm / 1000), parseMicro(size), true
}
