package main

import (
	"bytes"
	"encoding/hex"
	"math/big"
	"strings"
	"testing"
	"time"
)

func timeAt(ms int64) time.Time { return time.Unix(0, ms*int64(time.Millisecond)) }

func TestKeccakVector(t *testing.T) {
	// Canonical Keccak-256 empty-input vector.
	got := keccak256(nil)
	want := "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
	if hex.EncodeToString(got[:]) != want {
		t.Fatalf("keccak256(\"\") = %x", got)
	}
}

func TestWalletAddressDerivation(t *testing.T) {
	// Well-known vector: private key 0x...01 -> this address.
	w, err := NewWallet("0x0000000000000000000000000000000000000000000000000000000000000001", "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if got := strings.ToLower(w.AddressHex()); got != "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf" {
		t.Fatalf("address = %s", got)
	}
	// No funder given: funder == signer.
	if w.FunderHex() != w.AddressHex() {
		t.Fatal("funder must default to signer")
	}
}

func TestWalletFunderSeparation(t *testing.T) {
	w, err := NewWallet(
		"0x0000000000000000000000000000000000000000000000000000000000000002",
		"0x1111111111111111111111111111111111111111", 2)
	if err != nil {
		t.Fatal(err)
	}
	if strings.ToLower(w.FunderHex()) != "0x1111111111111111111111111111111111111111" {
		t.Fatalf("funder = %s", w.FunderHex())
	}
	if w.FunderHex() == w.AddressHex() {
		t.Fatal("funder must differ from signer here")
	}
	if w.SigType != 2 {
		t.Fatal("sig type not preserved")
	}
}

func TestSignRecoverRoundTrip(t *testing.T) {
	w, _ := NewWallet("0x4c0883a69102937d6231471b5dbb6204fe51296170827936ea5cce4b76994b0f", "", 0)
	digest := keccak256([]byte("test digest"))
	sig := w.SignDigest(digest)
	if len(sig) != 65 {
		t.Fatalf("sig length %d", len(sig))
	}
	if v := sig[64]; v != 27 && v != 28 {
		t.Fatalf("v = %d, want 27/28", v)
	}
	rec, err := RecoverAddress(digest, sig)
	if err != nil {
		t.Fatal(err)
	}
	if rec != w.Address {
		t.Fatalf("recovered %x, want %x", rec, w.Address)
	}
	// RFC6979: deterministic.
	if !bytes.Equal(sig, w.SignDigest(digest)) {
		t.Fatal("signature must be deterministic")
	}
}

func TestOrderSigningRecovers(t *testing.T) {
	w, _ := NewWallet(
		"0x4c0883a69102937d6231471b5dbb6204fe51296170827936ea5cce4b76994b0f",
		"0x1111111111111111111111111111111111111111", 2)
	tok, _ := new(big.Int).SetString("8134617877604296374149687316325127960225090334073034804311675574004693344298", 10)
	core := &OrderCore{
		Salt:        123456789,
		Maker:       w.Funder,
		Signer:      w.Address,
		TokenID:     tok,
		MakerAmount: 2_400_000, // 2.40 USDC
		TakerAmount: 5_000_000, // 5 shares @ 0.48
		Side:        0,
		SigType:     2,
		TimestampMs: 1782943800000,
	}
	exch, _ := parseAddress(ExchangeV2Addr)
	sig := w.SignOrder(core, exch)

	ds := domainSeparator4(exchangeDomainName, exchangeDomainVersion, polygonChainID, exch)
	digest := eip712Digest(ds, orderStructHash(core))
	rec, err := RecoverAddress(digest, sig)
	if err != nil {
		t.Fatal(err)
	}
	if rec != w.Address {
		t.Fatal("order signature does not recover to signer EOA")
	}

	// Different exchange (neg-risk) must produce a different signature.
	neg, _ := parseAddress(NegRiskExchangeV2Addr)
	if bytes.Equal(sig, w.SignOrder(core, neg)) {
		t.Fatal("domain separation failed between exchanges")
	}

	// Any field change must change the struct hash.
	h1 := orderStructHash(core)
	core.TimestampMs++
	if orderStructHash(core) == h1 {
		t.Fatal("timestamp not part of struct hash")
	}
}

func TestClobAuthSignatureShape(t *testing.T) {
	w, _ := NewWallet("0x4c0883a69102937d6231471b5dbb6204fe51296170827936ea5cce4b76994b0f", "", 0)
	sig := w.SignClobAuth(1782943800, 0)
	if !strings.HasPrefix(sig, "0x") || len(sig) != 132 {
		t.Fatalf("clob auth sig shape: len=%d", len(sig))
	}
	if sig != w.SignClobAuth(1782943800, 0) {
		t.Fatal("clob auth signature must be deterministic")
	}
	if sig == w.SignClobAuth(1782943801, 0) {
		t.Fatal("timestamp must affect signature")
	}
}

func TestHmacSignature(t *testing.T) {
	// Secret with url-safe alphabet chars must decode; output must be
	// url-safe base64 WITH padding preserved (venue requirement).
	secret := "c2VjcmV0LWtleS_tZXN0" // contains '_' and '-'
	sig, err := buildHmacSig(secret, 1719430000, "POST", "/order", []byte(`{"a":1}`))
	if err != nil {
		t.Fatal(err)
	}
	if strings.ContainsAny(sig, "+/") {
		t.Fatalf("HMAC sig must be url-safe: %q", sig)
	}
	if !strings.HasSuffix(sig, "=") {
		t.Fatalf("HMAC sig must keep base64 padding: %q", sig)
	}
	sig2, _ := buildHmacSig(secret, 1719430000, "POST", "/order", []byte(`{"a":1}`))
	if sig != sig2 {
		t.Fatal("HMAC must be deterministic")
	}
	sigNoBody, _ := buildHmacSig(secret, 1719430000, "POST", "/order", nil)
	if sig == sigNoBody {
		t.Fatal("body must affect HMAC")
	}
	if _, err := buildHmacSig("!!!notbase64!!!", 1, "GET", "/x", nil); err == nil {
		t.Fatal("invalid secret must error")
	}
}

func TestNewSaltRange(t *testing.T) {
	for i := 0; i < 1000; i++ {
		s := NewSalt()
		if s < 0 || s >= 1<<52 {
			t.Fatalf("salt %d outside float64-safe range", s)
		}
	}
}

func TestBuildOrderAmounts(t *testing.T) {
	w, _ := NewWallet("0x0000000000000000000000000000000000000000000000000000000000000001", "", 0)
	c, err := NewClobClient("https://clob.example", w)
	if err != nil {
		t.Fatal(err)
	}
	c.SetCreds(APICreds{Key: "k", Secret: "c2VjcmV0", Passphrase: "p"})

	// BUY 5 shares @ 0.48: maker (USDC in) = 2.40, taker (shares) = 5.
	o, err := c.BuildOrder("123456", 0, 480_000, 5_000_000, false)
	if err != nil {
		t.Fatal(err)
	}
	if o.MakerAmount != "2400000" || o.TakerAmount != "5000000" || o.Side != "BUY" {
		t.Fatalf("buy amounts: %+v", o)
	}
	if o.Expiration != "0" || o.Metadata != bytes32ZeroHex || o.Builder != bytes32ZeroHex {
		t.Fatalf("defaults wrong: %+v", o)
	}
	if len(o.Signature) != 132 {
		t.Fatalf("sig length %d", len(o.Signature))
	}

	// SELL 10 shares @ 0.52: maker (shares in) = 10, taker (USDC out) = 5.20.
	o2, err := c.BuildOrder("123456", 1, 520_000, 10_000_000, false)
	if err != nil {
		t.Fatal(err)
	}
	if o2.MakerAmount != "10000000" || o2.TakerAmount != "5200000" || o2.Side != "SELL" {
		t.Fatalf("sell amounts: %+v", o2)
	}

	// Degenerate params refused.
	if _, err := c.BuildOrder("123456", 0, 0, Micro, false); err == nil {
		t.Fatal("price 0 must be rejected")
	}
	if _, err := c.BuildOrder("123456", 0, Micro, Micro, false); err == nil {
		t.Fatal("price 1.0 must be rejected")
	}
	if _, err := c.BuildOrder("nothex", 0, 500_000, Micro, false); err == nil {
		t.Fatal("bad token id must be rejected")
	}
}

func TestTokenBucket(t *testing.T) {
	tb := NewTokenBucket(2, 10)
	now := timeAt(0)
	if !tb.Allow(now) || !tb.Allow(now) {
		t.Fatal("burst capacity must allow 2")
	}
	if tb.Allow(now) {
		t.Fatal("bucket must be empty")
	}
	// 10/s refill: after 100ms one token is back.
	if !tb.Allow(timeAt(101)) {
		t.Fatal("refill failed")
	}
	if tb.Allow(timeAt(102)) {
		t.Fatal("over-refill")
	}
}
