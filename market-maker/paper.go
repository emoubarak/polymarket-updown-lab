package main

// paper.go — paper-trading fill simulator with queue-aware, multi-level
// (ladder) quoting. Reproduces std0's fill volume by resting orders at
// SEVERAL price levels per side simultaneously (a single top-of-book order
// per side captured only ~58% of std0's ~48 fills/window; std0 ladders).
//
// Fill model (maker, per level):
//   - our BUY @ p fills when a SELL-aggressor print trades at <= p
//   - our SELL @ p fills when a BUY-aggressor print trades at >= p
// Queue position: a print first consumes QueueM (liquidity resting ahead of
// us at placement, from the live book); only the remainder reaches us.
// Any change to a level's (price,size) resets its queue (back of book).

import "sync"

// PaperQuote is one virtual resting order at one ladder level.
type PaperQuote struct {
	IsUp       bool
	Sell       bool
	PriceMicro int64
	SizeMicro  int64
	FilledM    int64
	QueueM     int64
}

// LadderLevel is one requested rung (price/size/queue-ahead).
type LadderLevel struct {
	PriceMicro int64
	SizeMicro  int64
	QueueM     int64
}

// PaperBroker simulates fills for laddered virtual quotes. Owned by the loop.
type PaperBroker struct {
	mu      sync.Mutex
	ladders [2][]PaperQuote // per economic side (sideBid / sideAsk)
	inv     *Inventory
	fills   int
}

func NewPaperBroker(inv *Inventory) *PaperBroker {
	return &PaperBroker{inv: inv}
}

// PlaceLadder replaces a side's ladder. isUp/sell describe the token/side of
// every rung (a side quotes one token, one direction). Queue progress is
// preserved for rungs whose (price,size) is unchanged; new rungs go to the
// back of the queue with the supplied QueueM.
func (p *PaperBroker) PlaceLadder(side int, isUp, sell bool, levels []LadderLevel) {
	p.mu.Lock()
	defer p.mu.Unlock()
	prev := p.ladders[side]
	next := make([]PaperQuote, 0, len(levels))
	for _, lv := range levels {
		if lv.SizeMicro <= 0 || lv.PriceMicro <= 0 || lv.PriceMicro >= Micro {
			continue
		}
		q := PaperQuote{IsUp: isUp, Sell: sell, PriceMicro: lv.PriceMicro, SizeMicro: lv.SizeMicro, QueueM: lv.QueueM}
		// Carry queue/fill progress if an identical rung already rested.
		for i := range prev {
			if prev[i].IsUp == isUp && prev[i].Sell == sell &&
				prev[i].PriceMicro == lv.PriceMicro && prev[i].SizeMicro == lv.SizeMicro {
				q.QueueM, q.FilledM = prev[i].QueueM, prev[i].FilledM
				break
			}
		}
		next = append(next, q)
	}
	p.ladders[side] = next
}

// Pull clears a side's ladder.
func (p *PaperBroker) Pull(side int) {
	p.mu.Lock()
	p.ladders[side] = nil
	p.mu.Unlock()
}

// OnTrade applies one observed print to every crossable rung, booking fills
// into paper inventory. Returns the fills applied (loop runs recordFill).
func (p *PaperBroker) OnTrade(t Trade, windowID, upTok, dnTok string) []Fill {
	p.mu.Lock()
	defer p.mu.Unlock()
	var out []Fill
	seq := int64(0)
	for side := range p.ladders {
		for i := range p.ladders[side] {
			q := &p.ladders[side][i]
			if q.IsUp != t.IsUp {
				continue
			}
			remaining := q.SizeMicro - q.FilledM
			if remaining <= 0 {
				continue
			}
			var cross bool
			if q.Sell {
				cross = t.TakerSide == "BUY" && t.PriceM >= q.PriceMicro
			} else {
				cross = t.TakerSide == "SELL" && t.PriceM <= q.PriceMicro
			}
			if !cross {
				continue
			}
			printLeft := t.SizeM
			if q.QueueM > 0 {
				c := q.QueueM
				if c > printLeft {
					c = printLeft
				}
				q.QueueM -= c
				printLeft -= c
			}
			if printLeft <= 0 {
				continue
			}
			fillM := printLeft
			if fillM > remaining {
				fillM = remaining
			}
			q.FilledM += fillM
			fSide := SideBuy
			if q.Sell {
				fSide = SideSell
			}
			asset := dnTok
			if q.IsUp {
				asset = upTok
			}
			seq++
			f := Fill{
				TradeID: paperTradeID(windowID, side*100+int(seq), q.FilledM),
				OrderID: "paper", AssetID: asset, IsUp: q.IsUp,
				Side: fSide, Size: fillM, Price: q.PriceMicro,
			}
			if p.inv.ApplyFillForWindow(windowID, f) {
				p.fills++
				out = append(out, f)
			}
		}
	}
	return out
}

// TakerBuy models std0's active momentum re-buy: an order that CROSSES the
// book to buy the trending side, filling immediately at the touch price.
// This is the ~half of std0's volume that pure passive maker quoting misses
// (its buys average 0.58, 74% on the eventual winner). Books one Fill.
func (p *PaperBroker) TakerBuy(isUp bool, priceMicro, sizeMicro int64, windowID, upTok, dnTok string) (Fill, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if priceMicro <= 0 || priceMicro >= Micro || sizeMicro <= 0 {
		return Fill{}, false
	}
	asset := dnTok
	if isUp {
		asset = upTok
	}
	p.fills++
	f := Fill{
		TradeID: paperTradeID(windowID, 900, int64(p.fills)),
		OrderID: "paper-taker", AssetID: asset, IsUp: isUp,
		Side: SideBuy, Taker: true, Size: sizeMicro, Price: priceMicro,
	}
	if p.inv.ApplyFillForWindow(windowID, f) {
		return f, true
	}
	return Fill{}, false
}

// Reset clears all ladders at a window roll.
func (p *PaperBroker) Reset() {
	p.mu.Lock()
	p.ladders[sideBid] = nil
	p.ladders[sideAsk] = nil
	p.mu.Unlock()
}

func paperTradeID(windowID string, side int, seq int64) string {
	return windowID + ":paper:" + itoa(side) + ":" + itoa64(seq)
}

func itoa(i int) string { return itoa64(int64(i)) }
func itoa64(i int64) string {
	if i == 0 {
		return "0"
	}
	neg := i < 0
	if neg {
		i = -i
	}
	var b [20]byte
	n := len(b)
	for i > 0 {
		n--
		b[n] = byte('0' + i%10)
		i /= 10
	}
	if neg {
		n--
		b[n] = '-'
	}
	return string(b[n:])
}
