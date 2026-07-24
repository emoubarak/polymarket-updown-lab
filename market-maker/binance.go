package main

// binance.go — Binance spot btcusdt@bookTicker ingestion.
//
// Binance is the FAST LEADING SIGNAL only. It is never the settlement
// anchor: these markets resolve on the Chainlink BTC/USD data stream (see
// polymarket_market.go). We use Binance to anticipate where the oracle
// reference is headed between its ~1Hz updates.
//
// Hot path budget: <5ms from wire to atomic store, zero allocations per
// frame (pre-allocated read buffer, hand-rolled field extraction).

import (
	"context"
	"errors"
	"io"
	"log"
	"time"

	"github.com/gorilla/websocket"
)

var (
	keyBinanceBid = []byte(`"b":"`)
	keyBinanceAsk = []byte(`"a":"`)
)

// BinanceFeed maintains the latest Binance mid in an atomic cell.
type BinanceFeed struct {
	Feed PriceFeed
	URL  string // e.g. wss://stream.binance.com:9443/ws/btcusdt@bookTicker

	wake chan<- struct{} // coalesced pricing-loop wakeup, never blocks
	buf  [4096]byte
}

func NewBinanceFeed(url string, wake chan<- struct{}) *BinanceFeed {
	return &BinanceFeed{URL: url, wake: wake}
}

// Run maintains the connection until ctx is cancelled, reconnecting with
// capped exponential backoff. Never returns before cancellation.
func (b *BinanceFeed) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		start := time.Now()
		err := b.session(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Printf("binance: session ended after %s: %v (reconnect in %s)",
			time.Since(start).Round(time.Millisecond), err, backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if time.Since(start) > time.Minute {
			backoff = 250 * time.Millisecond // healthy session: reset
		} else if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

func (b *BinanceFeed) session(ctx context.Context) error {
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := dialer.DialContext(ctx, b.URL, nil)
	if err != nil {
		return err
	}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			conn.Close()
		case <-done:
			conn.Close()
		}
	}()

	// Default gorilla ping handler answers server pings with pongs, which is
	// all Binance requires. bookTicker is high-rate: silence means trouble.
	for {
		if err := conn.SetReadDeadline(time.Now().Add(15 * time.Second)); err != nil {
			return err
		}
		_, r, err := conn.NextReader()
		if err != nil {
			return err
		}
		n, err := readFrame(r, b.buf[:])
		if err != nil {
			return err
		}
		b.handle(b.buf[:n], time.Now().UnixNano())
	}
}

// handle parses one bookTicker frame:
//
//	{"u":123,"s":"BTCUSDT","b":"60754.01","B":"2.1","a":"60754.02","A":"0.5"}
//
// Exported for tests via lowercase method; no allocation, no encoding/json.
func (b *BinanceFeed) handle(msg []byte, nowNanos int64) {
	bid, ok1 := extractFloat(msg, keyBinanceBid)
	ask, ok2 := extractFloat(msg, keyBinanceAsk)
	if !ok1 || !ok2 || bid <= 0 || ask <= 0 || ask < bid {
		return
	}
	b.Feed.Store((bid+ask)/2, nowNanos)
	select {
	case b.wake <- struct{}{}:
	default:
	}
}

var errFrameTooBig = errors.New("ws frame exceeds buffer")

// readFrame reads one full WS message into buf without allocating.
func readFrame(r io.Reader, buf []byte) (int, error) {
	total := 0
	for {
		n, err := r.Read(buf[total:])
		total += n
		if err == io.EOF {
			return total, nil
		}
		if err != nil {
			return total, err
		}
		if total == len(buf) {
			return total, errFrameTooBig
		}
	}
}
