package main

// Prints signing vectors as JSON for cross-validation against an independent
// implementation (eth-account in Python). Run:
//
//	REBATE_PRINT_VECTORS=1 go test -run TestPrintSigningVectors -v
//
// Uses a well-known TEST key only — never a real one.

import (
	"encoding/hex"
	"encoding/json"
	"math/big"
	"os"
	"testing"
)

func TestPrintSigningVectors(t *testing.T) {
	if os.Getenv("REBATE_PRINT_VECTORS") != "1" {
		t.Skip("set REBATE_PRINT_VECTORS=1 to print")
	}
	const testKey = "0x4c0883a69102937d6231471b5dbb6204fe51296170827936ea5cce4b76994b0f"
	w, err := NewWallet(testKey, "0x1111111111111111111111111111111111111111", 2)
	if err != nil {
		t.Fatal(err)
	}

	tok, _ := new(big.Int).SetString("8134617877604296374149687316325127960225090334073034804311675574004693344298", 10)
	core := &OrderCore{
		Salt:        424242424242,
		Maker:       w.Funder,
		Signer:      w.Address,
		TokenID:     tok,
		MakerAmount: 2_400_000,
		TakerAmount: 5_000_000,
		Side:        0,
		SigType:     2,
		TimestampMs: 1782943800123,
	}
	exch, _ := parseAddress(ExchangeV2Addr)
	ds := domainSeparator4(exchangeDomainName, exchangeDomainVersion, polygonChainID, exch)
	digest := eip712Digest(ds, orderStructHash(core))
	orderSig := w.SignOrder(core, exch)

	authTs := int64(1782943800)
	authSig := w.SignClobAuth(authTs, 0)

	hmacSig, err := buildHmacSig("dGVzdC1zZWNyZXQtZm9yLXZlY3RvcnM=", 1782943800, "POST", "/order", []byte(`{"x":1}`))
	if err != nil {
		t.Fatal(err)
	}

	out := map[string]any{
		"privateKey": testKey,
		"address":    w.AddressHex(),
		"funder":     w.FunderHex(),
		"order": map[string]any{
			"salt":        core.Salt,
			"maker":       w.FunderHex(),
			"signer":      w.AddressHex(),
			"tokenId":     tok.String(),
			"makerAmount": core.MakerAmount,
			"takerAmount": core.TakerAmount,
			"side":        core.Side,
			"sigType":     core.SigType,
			"timestampMs": core.TimestampMs,
			"exchange":    ExchangeV2Addr,
			"digest":      "0x" + hex.EncodeToString(digest[:]),
			"signature":   "0x" + hex.EncodeToString(orderSig),
		},
		"clobAuth": map[string]any{
			"timestamp": authTs,
			"nonce":     0,
			"signature": authSig,
		},
		"hmac": map[string]any{
			"secret":    "dGVzdC1zZWNyZXQtZm9yLXZlY3RvcnM=",
			"timestamp": 1782943800,
			"method":    "POST",
			"path":      "/order",
			"body":      `{"x":1}`,
			"signature": hmacSig,
		},
	}
	enc, _ := json.Marshal(out)
	// Marker line for the verifier to grep.
	t.Logf("VECTORS %s", enc)
}
