package main

// inventory.go — signed inventory state for the active 5-minute window.
//
// Position truth comes ONLY from confirmed fills on the CLOB user WebSocket
// channel (never from assuming a cancel/replace succeeded — there is no
// atomic cancel-replace over REST). Writes take a mutex (fills are rare);
// the pricing loop reads the signed net position from an atomic mirror with
// zero lock contention.
//
// Convention: we quote the Up token's book. A bought Down share is
// economically a short Up share, so signed inventory I = up - down.

import (
	"sync"
	"sync/atomic"
)

// Micro is the fixed-point scale used for shares, prices and USDC amounts
// (matches the 6-decimal on-chain units used by USDC/CTF).
const Micro int64 = 1_000_000

// Side is our side of a fill on the given token's book.
type Side uint8

const (
	SideBuy Side = iota
	SideSell
)

// Fill is one confirmed execution from the user channel.
type Fill struct {
	TradeID string // venue trade id, dedupe key across status replays
	OrderID string
	AssetID string // token id, window-integrity check in the loop
	IsUp    bool   // token identity: true = Up token, false = Down token
	Side    Side   // our side on that token
	Taker   bool   // true when we were the taker (liquidation fills)
	Size    int64  // micro-shares
	Price   int64  // micro-probability, 0..1_000_000
}

// InventorySnapshot is a consistent copy of the state for logging/risk.
type InventorySnapshot struct {
	WindowID string
	UpShares int64 // micro-shares
	DnShares int64 // micro-shares
	Net      int64 // up - down, micro-shares
	USDCOut  int64 // net micro-USDC paid out (negative = net received)
	Fills    int
}

// Inventory tracks per-window position. Safe for concurrent use.
type Inventory struct {
	mu       sync.Mutex
	windowID string
	upShares int64
	dnShares int64
	usdcOut  int64
	applied  map[string]Fill // tradeID -> fill, kept to allow reversal

	net atomic.Int64 // mirror of upShares-dnShares for lock-free reads
}

func NewInventory() *Inventory {
	return &Inventory{applied: make(map[string]Fill)}
}

// ApplyFill records a fill exactly once; repeated deliveries of the same
// TradeID (the user channel replays trades across MATCHED/MINED/CONFIRMED
// status transitions) are ignored. Returns true if the fill was applied.
func (inv *Inventory) ApplyFill(f Fill) bool {
	if f.TradeID == "" || f.Size <= 0 {
		return false
	}
	inv.mu.Lock()
	defer inv.mu.Unlock()
	if _, dup := inv.applied[f.TradeID]; dup {
		return false
	}
	inv.applied[f.TradeID] = f
	inv.add(f, +1)
	return true
}

// ReverseFill undoes a previously applied fill (trade later reported FAILED).
// Returns true if there was a matching applied fill to reverse.
func (inv *Inventory) ReverseFill(tradeID string) bool {
	inv.mu.Lock()
	defer inv.mu.Unlock()
	f, ok := inv.applied[tradeID]
	if !ok {
		return false
	}
	delete(inv.applied, tradeID)
	inv.add(f, -1)
	return true
}

// add applies dir*fill to the position. Caller holds inv.mu.
func (inv *Inventory) add(f Fill, dir int64) {
	delta := dir * f.Size
	if f.Side == SideSell {
		delta = -delta
	}
	if f.IsUp {
		inv.upShares += delta
	} else {
		inv.dnShares += delta
	}
	// Cash: buying costs price*size, selling receives it.
	inv.usdcOut += delta * f.Price / Micro
	inv.net.Store(inv.upShares - inv.dnShares)
}

// ApplyFillForWindow applies the fill only if the given window is still the
// active one — for async producers (on-chain split confirmations) that may
// land after a roll. Returns false if the window has moved on.
func (inv *Inventory) ApplyFillForWindow(windowID string, f Fill) bool {
	inv.mu.Lock()
	if inv.windowID != windowID {
		inv.mu.Unlock()
		return false
	}
	inv.mu.Unlock()
	return inv.ApplyFill(f)
}

// Seen reports whether a trade id was already applied — status replays
// (MINED/CONFIRMED after MATCHED) must be dropped before any registry
// lookup, because the registry forgets fully-matched orders.
func (inv *Inventory) Seen(tradeID string) bool {
	inv.mu.Lock()
	defer inv.mu.Unlock()
	_, ok := inv.applied[tradeID]
	return ok
}

// NetMicro returns signed inventory (up - down) in micro-shares. Lock-free;
// this is the pricing-loop read path.
func (inv *Inventory) NetMicro() int64 { return inv.net.Load() }

// NetShares returns signed inventory in whole shares as a float for the
// skew term. Lock-free.
func (inv *Inventory) NetShares() float64 {
	return float64(inv.net.Load()) / float64(Micro)
}

// Snapshot returns a consistent copy of the current state.
func (inv *Inventory) Snapshot() InventorySnapshot {
	inv.mu.Lock()
	defer inv.mu.Unlock()
	return InventorySnapshot{
		WindowID: inv.windowID,
		UpShares: inv.upShares,
		DnShares: inv.dnShares,
		Net:      inv.upShares - inv.dnShares,
		USDCOut:  inv.usdcOut,
		Fills:    len(inv.applied),
	}
}

// ResetWindow closes out the old window's book-keeping and starts a new one,
// returning the final snapshot of the window being closed. Shares held at
// reset settle on-chain (Up pays $1 or $0); settlement P&L is accounted
// downstream from this snapshot.
func (inv *Inventory) ResetWindow(windowID string) InventorySnapshot {
	inv.mu.Lock()
	defer inv.mu.Unlock()
	out := InventorySnapshot{
		WindowID: inv.windowID,
		UpShares: inv.upShares,
		DnShares: inv.dnShares,
		Net:      inv.upShares - inv.dnShares,
		USDCOut:  inv.usdcOut,
		Fills:    len(inv.applied),
	}
	inv.windowID = windowID
	inv.upShares, inv.dnShares, inv.usdcOut = 0, 0, 0
	clear(inv.applied)
	inv.net.Store(0)
	return out
}
