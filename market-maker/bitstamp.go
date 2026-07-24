package main

// bitstamp.go — Bitstamp BTC/USD live order book ingestion.
//
// Bitstamp is the FASTEST leading signal available to us: co-located in AWS
// eu-central-1 (Frankfurt), its `order_book_btcusd` channel delivers at ~4-10ms
// from a Frankfurt VPS (vs ~50ms Coinbase, ~118ms Binance Tokyo). It is also
// historically a Chainlink BTC/USD source — i.e. close to the Polymarket
// settlement anchor. Like Binance it is a leading signal, never the settlement
// oracle itself.
//
// Unlike Binance (stream in the URL), Bitstamp requires a JSON subscribe after
// connect. The full book is large, so the read buffer is 64KiB. We only need
// the top-of-book bid/ask, extracted hand-rolled with zero allocation.

import (
	"context"
	"log"
	"time"

	"github.com/gorilla/websocket"
)

var (
	keyBitstampBid = []byte(`"bids":[["`)
	keyBitstampAsk = []byte(`"asks":[["`)
	bitstampSub    = []byte(`{"event":"bts:subscribe","data":{"channel":"order_book_btcusd"}}`)
)

// BitstampFeed maintains the latest Bitstamp mid in an atomic cell.
type BitstampFeed struct {
	Feed PriceFeed
	URL  string // wss://ws.bitstamp.net

	wake chan<- struct{}
	buf  [65536]byte
}

func NewBitstampFeed(url string, wake chan<- struct{}) *BitstampFeed {
	if url == "" {
		url = "wss://ws.bitstamp.net"
	}
	return &BitstampFeed{URL: url, wake: wake}
}

// newBitstampIfEnabled returns a Bitstamp feed when USE_BITSTAMP is on, else nil
// (the pricing loop falls back to Binance when b.bs is nil).
func newBitstampIfEnabled(cfg *Config, wake chan<- struct{}) *BitstampFeed {
	if !cfg.UseBitstamp {
		return nil
	}
	return NewBitstampFeed(cfg.BitstampURL, wake)
}

// Run maintains the connection until ctx is cancelled, reconnecting with capped
// exponential backoff. Never returns before cancellation.
func (b *BitstampFeed) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		start := time.Now()
		err := b.session(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Printf("bitstamp: session ended after %s: %v (reconnect in %s)",
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

func (b *BitstampFeed) session(ctx context.Context) error {
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

	if err := conn.WriteMessage(websocket.TextMessage, bitstampSub); err != nil {
		return err
	}

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

// handle parses one order_book frame, extracting only top-of-book:
//
//	{"event":"data","channel":"order_book_btcusd","data":{...,
//	 "bids":[["60754.01","2.1"],...],"asks":[["60754.02","0.5"],...]}}
//
// Non-data frames (subscription_succeeded, reconnect requests) have no
// bids/asks arrays, so extractFloat returns !ok and the frame is skipped.
func (b *BitstampFeed) handle(msg []byte, nowNanos int64) {
	bid, ok1 := extractFloat(msg, keyBitstampBid)
	ask, ok2 := extractFloat(msg, keyBitstampAsk)
	if !ok1 || !ok2 || bid <= 0 || ask <= 0 || ask < bid {
		return
	}
	b.Feed.Store((bid+ask)/2, nowNanos)
	select {
	case b.wake <- struct{}{}:
	default:
	}
}
