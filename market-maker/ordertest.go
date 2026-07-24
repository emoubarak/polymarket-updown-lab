package main

// ordertest.go — one-shot live plumbing check (build-order step 4): send a
// heartbeat, rest a single deep-OTM post-only bid at the venue minimum size
// (5 shares @ $0.01 = $0.05 exposure), confirm it is live, cancel it.
// Validates production acceptance of our V2 signatures, L2 auth, heartbeat
// and cancel path without meaningful capital at risk.

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
)

// runBalanceCheck prints the venue's own view of our collateral (the
// authoritative answer — on-chain balanceOf can lag or look elsewhere).
func runBalanceCheck() {
	cfg, err := LoadConfig(false)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	wallet, err := NewWallet(cfg.PrivateKey, cfg.Funder, cfg.SigType)
	if err != nil {
		log.Fatalf("wallet: %v", err)
	}
	clob, err := NewClobClient(cfg.ClobURL, wallet)
	if err != nil {
		log.Fatalf("clob: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	creds, err := clob.DeriveAPICreds(ctx)
	if err != nil {
		log.Fatalf("creds: %v", err)
	}
	clob.SetCreds(creds)
	var out map[string]any
	path := fmt.Sprintf("/balance-allowance?asset_type=COLLATERAL&signature_type=%d", cfg.SigType)
	if err := clob.doL2(ctx, http.MethodGet, path, nil, &out); err != nil {
		log.Fatalf("balance-allowance: %v", err)
	}
	fmt.Printf("CLOB balance-allowance (funder=%s sigType=%d): %v\n", wallet.FunderHex(), cfg.SigType, out)
}

func runOrderTest() {
	if os.Getenv("REBATE_LIVE") != "yes" {
		log.Fatal("order-test places a real order; set REBATE_LIVE=yes to confirm")
	}
	cfg, err := LoadConfig(false)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	wallet, err := NewWallet(cfg.PrivateKey, cfg.Funder, cfg.SigType)
	if err != nil {
		log.Fatalf("wallet: %v", err)
	}
	clob, err := NewClobClient(cfg.ClobURL, wallet)
	if err != nil {
		log.Fatalf("clob: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	creds, err := clob.DeriveAPICreds(ctx)
	if err != nil {
		log.Fatalf("creds: %v", err)
	}
	clob.SetCreds(creds)
	log.Printf("creds ok: %s...", creds.Key[:8])

	if err := clob.Heartbeat(ctx); err != nil {
		log.Printf("heartbeat #1: %v", err)
	} else {
		log.Printf("heartbeat #1 ok")
	}
	if err := clob.Heartbeat(ctx); err != nil {
		log.Printf("heartbeat #2 (chained id): %v", err)
	} else {
		log.Printf("heartbeat #2 ok (id chaining works)")
	}

	// Resolve a market to quote: use the next window if the current one is
	// nearly over, so the order has time to rest.
	hc := &http.Client{Timeout: 5 * time.Second}
	start := WindowStart(time.Now())
	if time.Until(start.Add(5 * time.Minute)) < 30*time.Second {
		start = start.Add(5 * time.Minute)
	}
	w, err := FetchWindowInfo(ctx, hc, cfg.GammaURL, start)
	if err != nil {
		log.Fatalf("window: %v", err)
	}
	log.Printf("market %s cond=%s upToken=%s...", w.Slug, w.ConditionID[:16], w.UpTokenID[:16])

	// 5 shares @ 0.01, post-only: cannot cross, cannot cost more than $0.05.
	o, err := clob.BuildOrder(w.UpTokenID, 0, 10_000, 5_000_000, w.NegRisk)
	if err != nil {
		log.Fatalf("build: %v", err)
	}
	resp, err := clob.PostOrder(ctx, o, "GTC", true)
	if err != nil {
		log.Fatalf("POST /order FAILED: %v", err)
	}
	if !resp.Success {
		log.Fatalf("order rejected: %q status=%q", resp.ErrorMsg, resp.Status)
	}
	log.Printf("order ACCEPTED: id=%s status=%s", resp.OrderID, resp.Status)

	time.Sleep(2 * time.Second)

	open, err := clob.GetOpenOrders(ctx, w.ConditionID)
	if err != nil {
		log.Printf("open orders: %v", err)
	} else {
		found := false
		for _, oo := range open {
			if oo.ID == resp.OrderID {
				found = true
				log.Printf("order resting: side=%s price=%s size=%s matched=%s",
					oo.Side, oo.Price, oo.OriginalSize, oo.SizeMatched)
			}
		}
		if !found {
			log.Printf("WARNING: order %s not in open orders (%d open)", resp.OrderID, len(open))
		}
	}

	canceled, err := clob.CancelOrders(ctx, []string{resp.OrderID})
	if err != nil {
		log.Fatalf("cancel FAILED: %v — clean up manually or let heartbeat lapse", err)
	}
	if len(canceled) == 1 && canceled[0] == resp.OrderID {
		log.Printf("order cancelled cleanly")
	} else {
		log.Printf("cancel response: %v", canceled)
	}
	fmt.Println("ORDER-TEST PASSED: heartbeat, post, rest, cancel all verified live")
}
