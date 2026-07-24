package main

import (
	"math"
	"testing"
	"time"
)

func TestParseDecimal(t *testing.T) {
	cases := []struct {
		in   string
		want float64
		ok   bool
	}{
		{"60754.032964611615", 60754.032964611615, true},
		{"0.01", 0.01, true},
		{"-3.5", -3.5, true},
		{"1782943986000", 1782943986000, true},
		{"1e3", 1000, true},
		{"2.5E-2", 0.025, true},
		{"60754.01\",\"B\"", 60754.01, true}, // stops at delimiter
		{"", 0, false},
		{"abc", 0, false},
		{".", 0, false},
		{"1.2.3", 0, false},
		{"12345678901234567890123", 0, false}, // >18 integer digits refused
	}
	for _, c := range cases {
		got, ok := parseDecimal([]byte(c.in))
		if ok != c.ok {
			t.Errorf("parseDecimal(%q) ok=%v want %v", c.in, ok, c.ok)
			continue
		}
		if ok && math.Abs(got-c.want) > math.Abs(c.want)*1e-15 {
			t.Errorf("parseDecimal(%q) = %v, want %v", c.in, got, c.want)
		}
	}
}

const binanceFrame = `{"u":400900217,"s":"BTCUSDT","b":"60753.99000000","B":"31.21000000","a":"60754.01000000","A":"40.66000000"}`

func TestBinanceHandle(t *testing.T) {
	b := NewBinanceFeed("", make(chan struct{}, 1))
	b.handle([]byte(binanceFrame), 42)
	p, ts := b.Feed.Load()
	if ts != 42 {
		t.Fatalf("ts = %d", ts)
	}
	if math.Abs(p-60754.0) > 1e-9 {
		t.Fatalf("mid = %v, want 60754.0", p)
	}
	// Crossed/garbage frames are dropped.
	before, _ := b.Feed.Load()
	b.handle([]byte(`{"b":"10","a":"9"}`), 43)
	b.handle([]byte(`{"x":1}`), 44)
	after, ts2 := b.Feed.Load()
	if after != before || ts2 != 42 {
		t.Fatal("bad frame must not update feed")
	}
}

func TestBinanceWakeCoalesces(t *testing.T) {
	wake := make(chan struct{}, 1)
	b := NewBinanceFeed("", wake)
	for i := 0; i < 10; i++ {
		b.handle([]byte(binanceFrame), int64(i+1))
	}
	select {
	case <-wake:
	default:
		t.Fatal("expected a wake signal")
	}
	select {
	case <-wake:
		t.Fatal("wake channel must coalesce, got second signal")
	default:
	}
}

// Verbatim structure captured live from wss://ws-live-data.polymarket.com.
const rtdsUpdateFrame = `{"connection_id":"gSvN7i_U2WeIKEhQvA==","payload":{"full_accuracy_value":"60754032964611615000000","symbol":"btc/usd","timestamp":1782943986000,"value":60754.032964611615},"timestamp":1782943987064,"topic":"crypto_prices_chainlink","type":"update"}`

const rtdsSnapshotFrame = `{"connection_id":"abc","payload":{"symbol":"btc/usd","data":[{"timestamp":1782943900000,"value":60750.5},{"timestamp":1782943901000,"value":60751.25},{"timestamp":1782943902000,"value":60752.0}]},"timestamp":1782943987064,"topic":"crypto_prices","type":"subscribe"}`

func TestChainlinkHandleUpdate(t *testing.T) {
	f := NewChainlinkFeed("", make(chan struct{}, 1))
	f.handle([]byte(rtdsUpdateFrame), 99)
	p, ts := f.Feed.Load()
	if ts != 99 || math.Abs(p-60754.032964611615) > 1e-6 {
		t.Fatalf("p=%v ts=%d", p, ts)
	}
	if f.ObsMs.Load() != 1782943986000 {
		t.Fatalf("obs ts = %d", f.ObsMs.Load())
	}
	// PONG / garbage does nothing.
	f.handle([]byte("PONG"), 100)
	if _, ts := f.Feed.Load(); ts != 99 {
		t.Fatal("control frame must not update feed")
	}
}

func TestChainlinkHandleSnapshot(t *testing.T) {
	f := NewChainlinkFeed("", make(chan struct{}, 1))
	f.handle([]byte(rtdsSnapshotFrame), 1)
	want := []PricePoint{
		{TS: 1782943900, Price: 60750.5},
		{TS: 1782943901, Price: 60751.25},
		{TS: 1782943902, Price: 60752.0},
	}
	for i, w := range want {
		select {
		case got := <-f.Hist:
			if math.Abs(got.TS-w.TS) > 1e-9 || got.Price != w.Price {
				t.Fatalf("point %d: %+v want %+v", i, got, w)
			}
		default:
			t.Fatalf("missing snapshot point %d", i)
		}
	}
	select {
	case p := <-f.Hist:
		t.Fatalf("unexpected extra point %+v", p)
	default:
	}
	// Snapshot must not touch the live price cell.
	if _, ts := f.Feed.Load(); ts != 0 {
		t.Fatal("snapshot must not set live price")
	}
}

func TestFeedHandleZeroAlloc(t *testing.T) {
	wakeB := make(chan struct{}, 1)
	wakeC := make(chan struct{}, 1)
	b := NewBinanceFeed("", wakeB)
	f := NewChainlinkFeed("", wakeC)
	frameB := []byte(binanceFrame)
	frameC := []byte(rtdsUpdateFrame)
	drain := func(ch <-chan struct{}) {
		select {
		case <-ch:
		default:
		}
	}
	allocs := testing.AllocsPerRun(500, func() {
		b.handle(frameB, 1)
		f.handle(frameC, 1)
		drain(wakeB)
		drain(wakeC)
	})
	if allocs != 0 {
		t.Fatalf("feed handlers allocate %v per frame, want 0", allocs)
	}
}

func TestWindowSlugAndStart(t *testing.T) {
	// 2026-07-01 22:10:00 UTC == 1782943800, verified live slug.
	ts := time.Date(2026, 7, 1, 22, 12, 34, 0, time.UTC)
	start := WindowStart(ts)
	if start.Unix() != 1782943800 {
		t.Fatalf("window start = %d, want 1782943800", start.Unix())
	}
	if got := WindowSlug(start); got != "btc-updown-5m-1782943800" {
		t.Fatalf("slug = %q", got)
	}
	// Exact boundary belongs to the window it opens.
	if WindowStart(start).Unix() != start.Unix() {
		t.Fatal("boundary must map to its own window")
	}
	if PriceFeedAge := (&PriceFeed{}).AgeSec(123); !math.IsInf(PriceFeedAge, 1) {
		t.Fatal("empty feed must report +Inf age")
	}
}
