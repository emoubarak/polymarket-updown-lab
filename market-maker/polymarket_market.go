package main

// polymarket_market.go — Polymarket market data:
//
//  1. ChainlinkFeed: RTDS WebSocket channel `crypto_prices_chainlink`, the
//     feed that mirrors the Chainlink BTC/USD stream these markets RESOLVE
//     against. This is the settlement-anchor S_t for pricing. Deliberately
//     decoupled from binance.go — different trust levels of "the same" price.
//  2. Window discovery: deterministic slug btc-updown-5m-{unix%300==0} via
//     the Gamma API (unauthenticated, discovery only — order ops live in
//     polymarket_clob.go), plus the strike ("price to beat") from the
//     crypto-price endpoint.
//
// Verified against the live venue 2026-07-01: RTDS pushes ~1 msg/s with the
// Chainlink observation; the subscribe snapshot carries ~60s of 1s history
// (used to pre-warm the vol engine); the strike is NOT in the RTDS feed and
// comes from polymarket.com/api/crypto/crypto-price (openPrice), which
// equals the previous window's closePrice.

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// ---------------------------------------------------------------------------
// RTDS Chainlink feed
// ---------------------------------------------------------------------------

// Exact payload verified live: filters is a JSON-encoded *string*.
const rtdsSubscribeMsg = `{"action":"subscribe","subscriptions":[{"topic":"crypto_prices_chainlink","type":"*","filters":"{\"symbol\":\"btc/usd\"}"}]}`

var (
	keyRTDSPayload  = []byte(`"payload":`)
	keyRTDSValue    = []byte(`"value":`)
	keyRTDSTs       = []byte(`"timestamp":`)
	keyRTDSUpdate   = []byte(`"type":"update"`)
	keyRTDSSnapshot = []byte(`"data":[`)
	keyRTDSPoint    = []byte(`{"timestamp":`)
)

// PricePoint is one historical observation from the subscribe snapshot.
type PricePoint struct {
	TS    float64 // seconds (unix, fractional)
	Price float64
}

// ChainlinkFeed maintains the latest Chainlink-mirror price in an atomic
// cell, plus the oracle observation timestamp (ms) for staleness checks
// against the oracle itself (heartbeat/deviation updates, not every tick).
type ChainlinkFeed struct {
	Feed  PriceFeed
	URL   string       // wss://ws-live-data.polymarket.com
	ObsMs atomic.Int64 // Chainlink observation timestamp of latest update

	wake chan<- struct{}
	// Hist receives snapshot history points on every (re)connect; the main
	// loop drains it to warm the vol engine. Buffered, never blocks the feed.
	Hist chan PricePoint

	buf [16384]byte // snapshot frames carry ~60 points
}

func NewChainlinkFeed(url string, wake chan<- struct{}) *ChainlinkFeed {
	return &ChainlinkFeed{URL: url, wake: wake, Hist: make(chan PricePoint, 256)}
}

// Run maintains the RTDS connection until ctx is cancelled.
func (f *ChainlinkFeed) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		start := time.Now()
		err := f.session(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Printf("rtds: session ended after %s: %v (reconnect in %s)",
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

func (f *ChainlinkFeed) session(ctx context.Context) error {
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := dialer.DialContext(ctx, f.URL, nil)
	if err != nil {
		return err
	}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
		case <-done:
		}
		conn.Close()
	}()

	if err := conn.WriteMessage(websocket.TextMessage, []byte(rtdsSubscribeMsg)); err != nil {
		return err
	}
	// RTDS keepalive: literal PING every 5s. Sole writer after subscribe.
	go func() {
		t := time.NewTicker(5 * time.Second)
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
		// Updates arrive ~1/s; 15s of silence means the feed is dead.
		if err := conn.SetReadDeadline(time.Now().Add(15 * time.Second)); err != nil {
			return err
		}
		_, r, err := conn.NextReader()
		if err != nil {
			return err
		}
		n, err := readFrame(r, f.buf[:])
		if err != nil {
			return err
		}
		f.handle(f.buf[:n], time.Now().UnixNano())
	}
}

// handle parses one RTDS frame. Update frames (hot path, zero-alloc):
//
//	{"connection_id":"..","payload":{"full_accuracy_value":"..","symbol":
//	"btc/usd","timestamp":1782943986000,"value":60754.03},"timestamp":...,
//	"topic":"crypto_prices_chainlink","type":"update"}
//
// Subscribe-snapshot frames carry payload.data:[{timestamp,value},...] and
// are forwarded to Hist (cold path).
func (f *ChainlinkFeed) handle(msg []byte, nowNanos int64) {
	if extractObject(msg, keyRTDSPayload) == nil {
		return // PONG or other control frame
	}
	if idx := bytes.Index(msg, keyRTDSUpdate); idx >= 0 {
		payload := extractObject(msg, keyRTDSPayload)
		if payload == nil {
			return
		}
		price, ok := extractFloat(payload, keyRTDSValue)
		if !ok || price <= 0 {
			return
		}
		if obsMs, ok := extractFloat(payload, keyRTDSTs); ok {
			f.ObsMs.Store(int64(obsMs))
		}
		f.Feed.Store(price, nowNanos)
		select {
		case f.wake <- struct{}{}:
		default:
		}
		return
	}
	// Snapshot: history points for vol warmup. Cold path.
	f.handleSnapshot(msg)
}

func (f *ChainlinkFeed) handleSnapshot(msg []byte) {
	i := bytes.Index(msg, keyRTDSSnapshot)
	if i < 0 {
		return
	}
	rest := msg[i:]
	for {
		j := bytes.Index(rest, keyRTDSPoint)
		if j < 0 {
			return
		}
		rest = rest[j:]
		ts, ok1 := extractFloat(rest, keyRTDSTs)
		price, ok2 := extractFloat(rest, keyRTDSValue)
		if ok1 && ok2 && price > 0 {
			select {
			case f.Hist <- PricePoint{TS: ts / 1000, Price: price}:
			default: // full: vol warmup is best-effort
			}
		}
		rest = rest[len(keyRTDSPoint):]
	}
}

// ---------------------------------------------------------------------------
// Window discovery (Gamma + strike endpoint) — cold path, encoding/json OK.
// ---------------------------------------------------------------------------

// WindowInfo describes one 5-minute market window.
type WindowInfo struct {
	Slug        string
	ConditionID string
	UpTokenID   string
	DownTokenID string
	TickSize    float64
	MinSize     float64
	NegRisk     bool
	Accepting   bool
	Start       time.Time
	End         time.Time
	Strike      float64 // 0 until fetched; the Chainlink open price
}

// Series parameters — set once at startup from config (5m or 15m). The
// window duration and slug prefix are the only things that differ between
// the btc-updown series; pricing (T from window.End), vol and sell-only
// logic all adapt automatically.
var (
	windowSec  int64 = 300
	seriesSlug       = "btc-updown-5m"
)

// SetSeries configures the traded series (e.g. "btc-updown-15m", 900).
func SetSeries(slug string, sec int64) {
	seriesSlug, windowSec = slug, sec
}

// WindowStart returns the start of the window containing t.
func WindowStart(t time.Time) time.Time {
	u := t.Unix()
	return time.Unix(u-u%windowSec, 0).UTC()
}

// WindowSlug builds the deterministic market slug for a window start:
// {series}-{unix}, unix % windowSec == 0.
func WindowSlug(start time.Time) string {
	return fmt.Sprintf("%s-%d", seriesSlug, start.Unix())
}

type gammaMarket struct {
	ConditionID     string  `json:"conditionId"`
	ClobTokenIDs    string  `json:"clobTokenIds"` // JSON-encoded string array
	Outcomes        string  `json:"outcomes"`     // JSON-encoded string array
	TickSize        float64 `json:"orderPriceMinTickSize"`
	MinSize         float64 `json:"orderMinSize"`
	NegRisk         bool    `json:"negRisk"`
	AcceptingOrders bool    `json:"acceptingOrders"`
	EventStartTime  string  `json:"eventStartTime"`
	EndDate         string  `json:"endDate"`
}

type gammaEvent struct {
	Slug    string        `json:"slug"`
	NegRisk bool          `json:"negRisk"`
	Markets []gammaMarket `json:"markets"`
}

// FetchWindowInfo resolves a window's slug into condition/token IDs via the
// Gamma API. Gamma is discovery-only; all order operations use the CLOB API.
func FetchWindowInfo(ctx context.Context, hc *http.Client, gammaBase string, start time.Time) (*WindowInfo, error) {
	slug := WindowSlug(start)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		gammaBase+"/events?slug="+url.QueryEscape(slug), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	resp, err := hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("gamma %s: status %d: %.200s", slug, resp.StatusCode, body)
	}
	var events []gammaEvent
	if err := json.Unmarshal(body, &events); err != nil {
		return nil, fmt.Errorf("gamma %s: %w", slug, err)
	}
	if len(events) == 0 || len(events[0].Markets) == 0 {
		return nil, fmt.Errorf("gamma %s: no event/market", slug)
	}
	m := events[0].Markets[0]

	// clobTokenIds and outcomes are JSON strings inside JSON.
	var tokenIDs, outcomes []string
	if err := json.Unmarshal([]byte(m.ClobTokenIDs), &tokenIDs); err != nil {
		return nil, fmt.Errorf("gamma %s: clobTokenIds: %w", slug, err)
	}
	if err := json.Unmarshal([]byte(m.Outcomes), &outcomes); err != nil {
		return nil, fmt.Errorf("gamma %s: outcomes: %w", slug, err)
	}
	if len(tokenIDs) != 2 || len(outcomes) != 2 {
		return nil, fmt.Errorf("gamma %s: want 2 outcomes, got %d/%d", slug, len(outcomes), len(tokenIDs))
	}
	w := &WindowInfo{
		Slug:        slug,
		ConditionID: m.ConditionID,
		TickSize:    m.TickSize,
		MinSize:     m.MinSize,
		NegRisk:     m.NegRisk || events[0].NegRisk,
		Accepting:   m.AcceptingOrders,
		Start:       start,
		End:         start.Add(time.Duration(windowSec) * time.Second),
	}
	// Map outcome labels to token ids — never assume ordering.
	for i, o := range outcomes {
		switch o {
		case "Up":
			w.UpTokenID = tokenIDs[i]
		case "Down":
			w.DownTokenID = tokenIDs[i]
		}
	}
	if w.UpTokenID == "" || w.DownTokenID == "" {
		return nil, fmt.Errorf("gamma %s: unexpected outcomes %v", slug, outcomes)
	}
	if ets := m.EventStartTime; ets != "" {
		if t, err := time.Parse(time.RFC3339, ets); err == nil && !t.Equal(start) {
			return nil, fmt.Errorf("gamma %s: eventStartTime %s != window start %s", slug, t, start)
		}
	}
	return w, nil
}

// VenuePosition is one row of the public data-api positions endpoint.
type VenuePosition struct {
	Asset    string  `json:"asset"` // token id
	Size     float64 `json:"size"`
	AvgPrice float64 `json:"avgPrice"`
}

// FetchPositions reads current on-venue positions for one market (restart
// recovery: a rebooted bot must not assume it is flat mid-window).
func FetchPositions(ctx context.Context, hc *http.Client, dataAPIBase, user, conditionID string) ([]VenuePosition, error) {
	q := url.Values{}
	q.Set("user", user)
	q.Set("market", conditionID)
	q.Set("sizeThreshold", "0.01")
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, dataAPIBase+"/positions?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	resp, err := hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("positions: status %d: %.200s", resp.StatusCode, body)
	}
	var out []VenuePosition
	if err := json.Unmarshal(body, &out); err != nil {
		return nil, fmt.Errorf("positions: %w", err)
	}
	return out, nil
}

type cryptoPriceResp struct {
	OpenPrice  *float64 `json:"openPrice"`
	ClosePrice *float64 `json:"closePrice"`
	Completed  bool     `json:"completed"`
}

// FetchStrike reads the window's "price to beat" (Chainlink open price) from
// the polymarket.com crypto-price endpoint. Undocumented but public; needs a
// browser-ish User-Agent. Returns (strike, ok). ok=false while the venue has
// not yet stamped the open price.
func FetchStrike(ctx context.Context, hc *http.Client, siteBase string, start, end time.Time) (float64, bool, error) {
	q := url.Values{}
	q.Set("symbol", "BTC")
	q.Set("eventStartTime", start.UTC().Format(time.RFC3339))
	q.Set("variant", "fiveminute")
	q.Set("endDate", end.UTC().Format(time.RFC3339))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		siteBase+"/api/crypto/crypto-price?"+q.Encode(), nil)
	if err != nil {
		return 0, false, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
	resp, err := hc.Do(req)
	if err != nil {
		return 0, false, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1<<16))
	if err != nil {
		return 0, false, err
	}
	if resp.StatusCode != http.StatusOK {
		return 0, false, fmt.Errorf("crypto-price: status %d: %.200s", resp.StatusCode, body)
	}
	var cp cryptoPriceResp
	if err := json.Unmarshal(body, &cp); err != nil {
		return 0, false, fmt.Errorf("crypto-price: %w: %.200s", err, body)
	}
	if cp.OpenPrice == nil || *cp.OpenPrice <= 0 {
		return 0, false, nil
	}
	return *cp.OpenPrice, true, nil
}

