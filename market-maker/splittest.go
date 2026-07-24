package main

// splittest.go — one-shot Phase B on-chain validation: split 5 pUSD into a
// complete Up+Down set on the current window's condition, verify the pUSD
// left the Safe, merge back, verify it returned. ~$0.006 gas round trip,
// zero market exposure, fully reversible.

import (
	"context"
	"encoding/hex"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"
)

// pusdBalance reads pUSD balanceOf(funder) via eth_call.
func pusdBalance(ctx context.Context, rpc *RPCClient, funder [20]byte) (int64, error) {
	data := make([]byte, 4+32)
	sel := methodSelector("balanceOf(address)")
	copy(data, sel[:])
	w := addrWord(funder)
	copy(data[4:], w[:])
	var out string
	err := rpc.call(ctx, "eth_call", []any{map[string]string{
		"to":   PUSDAddr,
		"data": "0x" + hex.EncodeToString(data),
	}, "latest"}, &out)
	if err != nil {
		return 0, err
	}
	return hexToBig(out).Int64(), nil
}

func runSplitTest() {
	if os.Getenv("REBATE_LIVE") != "yes" {
		log.Fatal("split-test sends real on-chain transactions; set REBATE_LIVE=yes to confirm")
	}
	cfg, err := LoadConfig(false)
	if err != nil {
		log.Fatalf("config: %v", err)
	}
	wallet, err := NewWallet(cfg.PrivateKey, cfg.Funder, cfg.SigType)
	if err != nil {
		log.Fatalf("wallet: %v", err)
	}
	splitter, err := NewSafeSplitter(cfg.PolygonRPC, wallet)
	if err != nil {
		log.Fatalf("splitter: %v", err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	// Real condition id from the currently-trading window.
	hc := &http.Client{Timeout: 5 * time.Second}
	w, err := FetchWindowInfo(ctx, hc, cfg.GammaURL, WindowStart(time.Now()))
	if err != nil {
		log.Fatalf("window: %v", err)
	}
	log.Printf("condition: %s (%s)", w.ConditionID, w.Slug)

	before, err := pusdBalance(ctx, splitter.rpc, wallet.Funder)
	if err != nil {
		log.Fatalf("balance: %v", err)
	}
	log.Printf("pUSD before: %.6f", float64(before)/1e6)
	const amount = 5 * 1_000_000 // 5 pUSD -> 5 Up + 5 Down

	hash, err := splitter.Split(ctx, w.ConditionID, amount)
	if err != nil {
		log.Fatalf("SPLIT FAILED: %v", err)
	}
	log.Printf("split ok: https://polygonscan.com/tx/%s", hash)

	mid, err := pusdBalance(ctx, splitter.rpc, wallet.Funder)
	if err != nil {
		log.Fatalf("balance: %v", err)
	}
	log.Printf("pUSD after split: %.6f (delta %.6f)", float64(mid)/1e6, float64(mid-before)/1e6)
	if mid != before-amount {
		log.Printf("WARNING: unexpected delta (expected -5)")
	}

	hash, err = splitter.Merge(ctx, w.ConditionID, amount)
	if err != nil {
		log.Fatalf("MERGE FAILED (5 Up + 5 Down tokens remain in the Safe, redeemable at settlement): %v", err)
	}
	log.Printf("merge ok: https://polygonscan.com/tx/%s", hash)

	after, err := pusdBalance(ctx, splitter.rpc, wallet.Funder)
	if err != nil {
		log.Fatalf("balance: %v", err)
	}
	log.Printf("pUSD after merge: %.6f (net %.6f)", float64(after)/1e6, float64(after-before)/1e6)
	if after != before {
		log.Fatalf("SPLIT-TEST INCOMPLETE: net pUSD delta %.6f", float64(after-before)/1e6)
	}
	fmt.Println("SPLIT-TEST PASSED: split + merge round trip verified on-chain, pUSD fully restored")
}
