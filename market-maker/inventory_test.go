package main

import (
	"fmt"
	"sync"
	"testing"
)

func TestInventoryBasicAccounting(t *testing.T) {
	inv := NewInventory()
	inv.ResetWindow("w1")

	// Buy 10 Up @ 0.48, buy 4 Down @ 0.50.
	if !inv.ApplyFill(Fill{TradeID: "t1", IsUp: true, Side: SideBuy, Size: 10 * Micro, Price: 480_000}) {
		t.Fatal("t1 rejected")
	}
	if !inv.ApplyFill(Fill{TradeID: "t2", IsUp: false, Side: SideBuy, Size: 4 * Micro, Price: 500_000}) {
		t.Fatal("t2 rejected")
	}
	if got := inv.NetMicro(); got != 6*Micro {
		t.Fatalf("net = %d, want %d", got, 6*Micro)
	}
	if got := inv.NetShares(); got != 6.0 {
		t.Fatalf("net shares = %v, want 6", got)
	}
	s := inv.Snapshot()
	// 10*0.48 + 4*0.50 = 6.80 USDC out.
	if s.USDCOut != 6_800_000 {
		t.Fatalf("usdcOut = %d, want 6800000", s.USDCOut)
	}

	// Sell 3 Up @ 0.60 (emergency liquidation path).
	inv.ApplyFill(Fill{TradeID: "t3", IsUp: true, Side: SideSell, Size: 3 * Micro, Price: 600_000})
	if got := inv.NetMicro(); got != 3*Micro {
		t.Fatalf("net after sell = %d, want %d", got, 3*Micro)
	}
	if s := inv.Snapshot(); s.USDCOut != 6_800_000-1_800_000 {
		t.Fatalf("usdcOut after sell = %d", s.USDCOut)
	}
}

func TestInventoryDedupeAndReverse(t *testing.T) {
	inv := NewInventory()
	inv.ResetWindow("w1")
	f := Fill{TradeID: "dup", IsUp: true, Side: SideBuy, Size: 5 * Micro, Price: 500_000}
	if !inv.ApplyFill(f) {
		t.Fatal("first apply rejected")
	}
	// Status replays (MATCHED -> MINED -> CONFIRMED) must be no-ops.
	if inv.ApplyFill(f) || inv.ApplyFill(f) {
		t.Fatal("duplicate TradeID applied twice")
	}
	if got := inv.NetMicro(); got != 5*Micro {
		t.Fatalf("net = %d, want %d", got, 5*Micro)
	}
	// FAILED after MATCHED: reverse restores state exactly.
	if !inv.ReverseFill("dup") {
		t.Fatal("reverse of applied fill failed")
	}
	if inv.ReverseFill("dup") {
		t.Fatal("double reverse must fail")
	}
	if got := inv.NetMicro(); got != 0 {
		t.Fatalf("net after reverse = %d, want 0", got)
	}
	if s := inv.Snapshot(); s.USDCOut != 0 {
		t.Fatalf("usdcOut after reverse = %d, want 0", s.USDCOut)
	}
	// Rejected garbage.
	if inv.ApplyFill(Fill{TradeID: "", Size: Micro}) {
		t.Fatal("empty TradeID accepted")
	}
	if inv.ApplyFill(Fill{TradeID: "x", Size: 0}) {
		t.Fatal("zero size accepted")
	}
}

func TestInventoryWindowReset(t *testing.T) {
	inv := NewInventory()
	inv.ResetWindow("w1")
	inv.ApplyFill(Fill{TradeID: "a", IsUp: true, Side: SideBuy, Size: 7 * Micro, Price: 500_000})
	old := inv.ResetWindow("w2")
	if old.WindowID != "w1" || old.Net != 7*Micro || old.Fills != 1 {
		t.Fatalf("closing snapshot wrong: %+v", old)
	}
	if inv.NetMicro() != 0 {
		t.Fatal("net not zeroed on reset")
	}
	// Same TradeID is valid again in a new window only after map clear —
	// venue trade ids are globally unique, but the clear must not leak.
	if !inv.ApplyFill(Fill{TradeID: "a", IsUp: false, Side: SideBuy, Size: Micro, Price: 500_000}) {
		t.Fatal("apply after reset rejected")
	}
	if s := inv.Snapshot(); s.WindowID != "w2" || s.Net != -1*Micro {
		t.Fatalf("post-reset snapshot: %+v", s)
	}
}

// Race-detector test: hammer writes from many goroutines while readers spin
// on the lock-free path. Run with -race.
func TestInventoryConcurrency(t *testing.T) {
	inv := NewInventory()
	inv.ResetWindow("w1")

	const writers = 8
	const fillsPerWriter = 500

	var wg sync.WaitGroup
	stop := make(chan struct{})

	// Readers on the hot path.
	for r := 0; r < 4; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
					_ = inv.NetMicro()
					_ = inv.NetShares()
				}
			}
		}()
	}
	// Snapshot reader (lock path).
	wg.Add(1)
	go func() {
		defer wg.Done()
		for {
			select {
			case <-stop:
				return
			default:
				_ = inv.Snapshot()
			}
		}
	}()

	// Writers: unique trade ids, alternating tokens/sides, plus deliberate
	// duplicate deliveries of every fill.
	applied := make([]int, writers)
	var wwg sync.WaitGroup
	for w := 0; w < writers; w++ {
		wwg.Add(1)
		go func(w int) {
			defer wwg.Done()
			for i := 0; i < fillsPerWriter; i++ {
				f := Fill{
					TradeID: fmt.Sprintf("w%d-%d", w, i),
					IsUp:    i%2 == 0,
					Side:    SideBuy,
					Size:    Micro,
					Price:   500_000,
				}
				if inv.ApplyFill(f) {
					applied[w]++
				}
				inv.ApplyFill(f) // duplicate delivery must be rejected
			}
		}(w)
	}
	wwg.Wait()
	close(stop)
	wg.Wait()

	total := 0
	for _, n := range applied {
		total += n
	}
	if total != writers*fillsPerWriter {
		t.Fatalf("applied %d fills, want %d", total, writers*fillsPerWriter)
	}
	// Each writer applied fillsPerWriter fills alternating Up/Down equally.
	s := inv.Snapshot()
	if s.Fills != writers*fillsPerWriter {
		t.Fatalf("fill count %d, want %d", s.Fills, writers*fillsPerWriter)
	}
	if s.Net != 0 {
		t.Fatalf("net = %d, want 0 (balanced up/down)", s.Net)
	}
	if s.UpShares != int64(writers*fillsPerWriter/2)*Micro {
		t.Fatalf("upShares = %d", s.UpShares)
	}
}
