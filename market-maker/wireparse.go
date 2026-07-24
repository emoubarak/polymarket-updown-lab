package main

// wireparse.go — allocation-free extraction of numeric fields from WebSocket
// JSON frames, plus the shared atomic latest-price cell.
//
// encoding/json is deliberately absent from the message hot path: we only
// need two or three numeric fields per frame, so we scan for the key pattern
// and parse the decimal in place.

import (
	"bytes"
	"math"
	"sync/atomic"
)

// PriceFeed is an atomically-updated latest-value cell shared by all feeds.
// Writers call Store from the feed goroutine; the pricing loop reads with
// Load/AgeSec and never blocks.
type PriceFeed struct {
	bits atomic.Uint64 // math.Float64bits of the latest price
	ts   atomic.Int64  // unix nanos at receipt of the latest update
}

func (f *PriceFeed) Store(p float64, tsNanos int64) {
	f.bits.Store(math.Float64bits(p))
	f.ts.Store(tsNanos)
}

// Load returns the latest price and its receipt time (unix nanos).
// Price 0 means "never updated".
func (f *PriceFeed) Load() (float64, int64) {
	return math.Float64frombits(f.bits.Load()), f.ts.Load()
}

// AgeSec returns seconds since the last update, or +Inf if never updated.
func (f *PriceFeed) AgeSec(nowNanos int64) float64 {
	ts := f.ts.Load()
	if ts == 0 {
		return math.Inf(1)
	}
	return float64(nowNanos-ts) / 1e9
}

var pow10tab = [19]float64{1, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9,
	1e10, 1e11, 1e12, 1e13, 1e14, 1e15, 1e16, 1e17, 1e18}

// parseDecimal parses [-]digits[.digits][(e|E)[±]digits] from the front of b.
// Returns (value, ok). Accumulates up to 18 significant digits in an integer
// mantissa — more than enough for prices — and never allocates.
func parseDecimal(b []byte) (float64, bool) {
	i, n := 0, len(b)
	neg := false
	if i < n && (b[i] == '-' || b[i] == '+') {
		neg = b[i] == '-'
		i++
	}
	var mant uint64
	digits, frac := 0, 0
	seenDot, seenDigit := false, false
	for ; i < n; i++ {
		c := b[i]
		if c == '.' {
			if seenDot {
				return 0, false
			}
			seenDot = true
			continue
		}
		if c < '0' || c > '9' {
			break
		}
		seenDigit = true
		if digits < 18 {
			mant = mant*10 + uint64(c-'0')
			digits++
			if seenDot {
				frac++
			}
		} else if !seenDot {
			// Integer part overflows our mantissa budget: not a price we
			// ever expect; refuse rather than silently misparse.
			return 0, false
		}
		// Extra fractional digits beyond 18 significant: truncate silently.
	}
	if !seenDigit {
		return 0, false
	}
	v := float64(mant) / pow10tab[frac]
	// Optional exponent.
	if i < n && (b[i] == 'e' || b[i] == 'E') {
		i++
		eneg := false
		if i < n && (b[i] == '-' || b[i] == '+') {
			eneg = b[i] == '-'
			i++
		}
		exp := 0
		start := i
		for ; i < n && b[i] >= '0' && b[i] <= '9'; i++ {
			exp = exp*10 + int(b[i]-'0')
			if exp > 308 {
				return 0, false
			}
		}
		if i == start {
			return 0, false
		}
		if eneg {
			v /= math.Pow(10, float64(exp))
		} else {
			v *= math.Pow(10, float64(exp))
		}
	}
	if neg {
		v = -v
	}
	return v, true
}

// extractFloat finds key in buf and parses the decimal immediately after it.
// key must include everything up to the first byte of the number, e.g.
// `"value":` for bare numbers or `"b":"` for quoted numbers.
func extractFloat(buf, key []byte) (float64, bool) {
	i := bytes.Index(buf, key)
	if i < 0 {
		return 0, false
	}
	return parseDecimal(buf[i+len(key):])
}

// extractObject returns the byte range of the flat JSON object that follows
// key (which must end at the opening brace, e.g. `"payload":`). Only valid
// for objects with no nested braces.
func extractObject(buf, key []byte) []byte {
	i := bytes.Index(buf, key)
	if i < 0 {
		return nil
	}
	start := i + len(key)
	if start >= len(buf) || buf[start] != '{' {
		return nil
	}
	end := bytes.IndexByte(buf[start:], '}')
	if end < 0 {
		return nil
	}
	return buf[start : start+end+1]
}
