package main

import (
	"encoding/hex"
	"testing"
)

func TestMethodSelectors(t *testing.T) {
	// Canonical 4-byte selectors for the Gnosis CTF (verifiable against
	// any ABI tool: cast sig / 4byte.directory).
	cases := []struct {
		got  [4]byte
		want string
	}{
		{selSplitPosition, "72ce4275"},
		{selMergePositions, "9e7212ad"},
		{selRedeemPosition, "01b7037c"},
	}
	for _, c := range cases {
		if hex.EncodeToString(c.got[:]) != c.want {
			t.Fatalf("selector = %x, want %s", c.got, c.want)
		}
	}
}

func TestEncodeSplitPosition(t *testing.T) {
	const cond = "0xeb7aa3d6d7f9818d1e8fcc6240695cdcd770479f8be1c2b99165805c272a2d98"
	data, err := EncodeSplitPosition("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", cond, 100*Micro)
	if err != nil {
		t.Fatal(err)
	}
	// 4 selector + 8 words (5 head + 3 tail).
	if len(data) != 4+8*32 {
		t.Fatalf("calldata length %d", len(data))
	}
	h := hex.EncodeToString(data)
	if h[:8] != "72ce4275" {
		t.Fatalf("selector prefix %s", h[:8])
	}
	// Word 1: collateral right-aligned.
	if h[8:8+64] != "000000000000000000000000c011a7e12a19f7b1f670d46f03b03f3342e82dfb" {
		t.Fatalf("collateral word: %s", h[8:8+64])
	}
	// Word 2: parentCollectionId zero.
	if h[8+64:8+128] != "0000000000000000000000000000000000000000000000000000000000000000" {
		t.Fatal("parent collection must be zero")
	}
	// Word 3: conditionId.
	if h[8+128:8+192] != cond[2:] {
		t.Fatalf("conditionId word: %s", h[8+128:8+192])
	}
	// Word 4: dynamic offset 0xa0.
	if h[8+192:8+256] != "00000000000000000000000000000000000000000000000000000000000000a0" {
		t.Fatalf("offset word: %s", h[8+192:8+256])
	}
	// Word 5: amount 100e6.
	if h[8+256:8+320] != "0000000000000000000000000000000000000000000000000000000005f5e100" {
		t.Fatalf("amount word: %s", h[8+256:8+320])
	}
	// Tail: [2, 1, 2].
	tail := h[8+320:]
	if tail != "000000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000010000000000000000000000000000000000000000000000000000000000000002" {
		t.Fatalf("partition tail: %s", tail)
	}

	if _, err := EncodeSplitPosition("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", cond, 0); err == nil {
		t.Fatal("zero amount must be rejected")
	}
	if _, err := EncodeSplitPosition("nothex", cond, Micro); err == nil {
		t.Fatal("bad collateral must be rejected")
	}
	if _, err := EncodeSplitPosition("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", "0x1234", Micro); err == nil {
		t.Fatal("bad conditionId must be rejected")
	}
}

func TestEncodeRedeemPositions(t *testing.T) {
	const cond = "0xeb7aa3d6d7f9818d1e8fcc6240695cdcd770479f8be1c2b99165805c272a2d98"
	data, err := EncodeRedeemPositions("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", cond)
	if err != nil {
		t.Fatal(err)
	}
	if len(data) != 4+7*32 {
		t.Fatalf("calldata length %d", len(data))
	}
	h := hex.EncodeToString(data)
	if h[:8] != "01b7037c" {
		t.Fatalf("selector prefix %s", h[:8])
	}
	// Offset word (4th param head) = 0x80.
	if h[8+192:8+256] != "0000000000000000000000000000000000000000000000000000000000000080" {
		t.Fatalf("offset word: %s", h[8+192:8+256])
	}
}

func TestEncodeMergePositions(t *testing.T) {
	const cond = "0xeb7aa3d6d7f9818d1e8fcc6240695cdcd770479f8be1c2b99165805c272a2d98"
	data, err := EncodeMergePositions("0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB", cond, 50*Micro)
	if err != nil {
		t.Fatal(err)
	}
	if hex.EncodeToString(data[:4]) != "9e7212ad" {
		t.Fatalf("selector %x", data[:4])
	}
	if len(data) != 4+8*32 {
		t.Fatalf("calldata length %d", len(data))
	}
}
