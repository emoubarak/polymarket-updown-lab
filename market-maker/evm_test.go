package main

import (
	"bytes"
	"encoding/hex"
	"encoding/json"
	"math/big"
	"os"
	"testing"
)

func rlpHex(it rlpItem) string {
	var buf bytes.Buffer
	it.encode(&buf)
	return hex.EncodeToString(buf.Bytes())
}

func TestRLPCanonicalVectors(t *testing.T) {
	// Canonical vectors from the Ethereum wiki.
	cases := []struct {
		item rlpItem
		want string
	}{
		{rlpBytes{}, "80"},
		{rlpBytes("dog"), "83646f67"},
		{rlpList{rlpBytes("cat"), rlpBytes("dog")}, "c88363617483646f67"},
		{rlpBytes{0x00}, "00"},
		{rlpUint(0), "80"},
		{rlpUint(15), "0f"},
		{rlpUint(1024), "820400"},
		{rlpList{}, "c0"},
		{rlpBytes(bytes.Repeat([]byte{0x61}, 56)), "b838" + hex.EncodeToString(bytes.Repeat([]byte{0x61}, 56))},
	}
	for i, c := range cases {
		if got := rlpHex(c.item); got != c.want {
			t.Errorf("case %d: got %s want %s", i, got, c.want)
		}
	}
}

func TestExecTransactionEncoding(t *testing.T) {
	// Known selector for Safe v1.3.0 execTransaction.
	if hex.EncodeToString(selExecTransaction[:]) != "6a761202" {
		t.Fatalf("selector %x", selExecTransaction)
	}
	inner := []byte{0xde, 0xad, 0xbe, 0xef, 0x01} // 5 bytes -> pads to 32
	to, _ := parseAddress(CtfCollateralAdapterAddr)
	owner, _ := parseAddress("0x2222222222222222222222222222222222222222")
	data := EncodeExecTransaction(to, inner, owner)
	h := hex.EncodeToString(data)
	// data offset = 0x140 in head slot 2.
	if h[8+2*64:8+3*64] != "0000000000000000000000000000000000000000000000000000000000000140" {
		t.Fatalf("data offset word: %s", h[8+2*64:8+3*64])
	}
	// signatures offset = 0x140 + 0x20 + 0x20 = 0x180 in head slot 9.
	if h[8+9*64:8+10*64] != "0000000000000000000000000000000000000000000000000000000000000180" {
		t.Fatalf("sig offset word: %s", h[8+9*64:8+10*64])
	}
	// data length word then padded payload.
	if h[8+10*64:8+11*64] != "0000000000000000000000000000000000000000000000000000000000000005" {
		t.Fatal("data length word wrong")
	}
	// signatures: length 65, r contains the owner, v==1 at the end.
	sigLen := h[8+12*64 : 8+13*64]
	if sigLen != "0000000000000000000000000000000000000000000000000000000000000041" {
		t.Fatalf("sig length word: %s", sigLen)
	}
	sigBlock := h[8+13*64:]
	if sigBlock[:64] != "0000000000000000000000002222222222222222222222222222222222222222" {
		t.Fatalf("sig r != owner: %s", sigBlock[:64])
	}
	if sigBlock[128:130] != "01" {
		t.Fatalf("sig v: %s", sigBlock[128:130])
	}
	// Total: 4 + head(10) + lenData(1) + data(1) + lenSig(1) + sig(3 words).
	if len(data) != 4+16*32 {
		t.Fatalf("calldata length %d", len(data))
	}
}

// Prints an EIP-1559 signing vector for cross-validation with eth_account.
// REBATE_PRINT_VECTORS=1 go test -run TestPrintTxVector -v
func TestPrintTxVector(t *testing.T) {
	if os.Getenv("REBATE_PRINT_VECTORS") != "1" {
		t.Skip("set REBATE_PRINT_VECTORS=1 to print")
	}
	const testKey = "0x4c0883a69102937d6231471b5dbb6204fe51296170827936ea5cce4b76994b0f"
	w, err := NewWallet(testKey, "", 0)
	if err != nil {
		t.Fatal(err)
	}
	to, _ := parseAddress(CtfCollateralAdapterAddr)
	tx := &Eip1559Tx{
		ChainID:   137,
		Nonce:     42,
		TipWei:    big.NewInt(30_000_000_000),
		MaxFeeWei: big.NewInt(60_000_000_000),
		GasLimit:  400_000,
		To:        to,
		ValueWei:  big.NewInt(0),
		Data:      []byte{0x72, 0xce, 0x42, 0x75, 0x01, 0x02},
	}
	out := map[string]any{
		"privateKey": testKey,
		"chainId":    tx.ChainID,
		"nonce":      tx.Nonce,
		"tip":        tx.TipWei.String(),
		"maxFee":     tx.MaxFeeWei.String(),
		"gas":        tx.GasLimit,
		"to":         CtfCollateralAdapterAddr,
		"data":       "0x" + hex.EncodeToString(tx.Data),
		"raw":        "0x" + hex.EncodeToString(tx.SignedRaw(w)),
	}
	enc, _ := json.Marshal(out)
	t.Logf("TXVECTOR %s", enc)
}
