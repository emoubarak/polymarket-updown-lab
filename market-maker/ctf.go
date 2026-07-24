package main

// ctf.go — Conditional Tokens Framework calldata builders (Phase B).
//
// The competitive model (see std0 analysis): mint complete Up+Down sets
// BEFORE the window opens (splitPosition), quote asks on both outcomes from
// that riskless inventory, carry pairs through settlement, redeemPositions
// ~1 min after expiry, recycle. These builders produce the raw calldata;
// execution goes either through the Polymarket relayer or a direct Safe
// execTransaction (path decided by ongoing research — both consume this
// same calldata).
//
// CTF on Polygon: 0x4D97DCd97eC945f40cF65F87097ACe5EA0476045.

import (
	"encoding/hex"
	"fmt"
	"strings"
)

// CTFAddr is the ConditionalTokens contract on Polygon.
const CTFAddr = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

var (
	selSplitPosition  = methodSelector("splitPosition(address,bytes32,bytes32,uint256[],uint256)")
	selMergePositions = methodSelector("mergePositions(address,bytes32,bytes32,uint256[],uint256)")
	selRedeemPosition = methodSelector("redeemPositions(address,bytes32,bytes32,uint256[])")
)

func methodSelector(sig string) [4]byte {
	h := keccak256([]byte(sig))
	var out [4]byte
	copy(out[:], h[:4])
	return out
}

func parseBytes32(s string) ([32]byte, error) {
	var out [32]byte
	s = strings.TrimPrefix(strings.TrimPrefix(s, "0x"), "0X")
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != 32 {
		return out, fmt.Errorf("bad bytes32 %q", s)
	}
	copy(out[:], b)
	return out, nil
}

// abiWords assembles 32-byte words into calldata after a 4-byte selector.
func abiWords(sel [4]byte, words ...[32]byte) []byte {
	out := make([]byte, 4+32*len(words))
	copy(out, sel[:])
	for i, w := range words {
		copy(out[4+i*32:], w[:])
	}
	return out
}

// EncodeSplitPosition builds splitPosition(collateral, 0x0, conditionId,
// [1,2], amountMicro) — mints amount/1e6 complete Up+Down sets against the
// market's collateral token.
func EncodeSplitPosition(collateralAddr, conditionID string, amountMicro int64) ([]byte, error) {
	return encodeSplitMerge(selSplitPosition, collateralAddr, conditionID, amountMicro)
}

// EncodeMergePositions builds mergePositions(...) — burns amount/1e6
// complete sets back into collateral (mid-window capital recycling).
func EncodeMergePositions(collateralAddr, conditionID string, amountMicro int64) ([]byte, error) {
	return encodeSplitMerge(selMergePositions, collateralAddr, conditionID, amountMicro)
}

func encodeSplitMerge(sel [4]byte, collateralAddr, conditionID string, amountMicro int64) ([]byte, error) {
	if amountMicro <= 0 {
		return nil, fmt.Errorf("bad amount %d", amountMicro)
	}
	coll, err := parseAddress(collateralAddr)
	if err != nil {
		return nil, err
	}
	cond, err := parseBytes32(conditionID)
	if err != nil {
		return nil, err
	}
	// (address, bytes32 parent=0, bytes32 condition, uint256[] offset=0xa0,
	//  uint256 amount) + tail: [len=2, 1, 2]
	return abiWords(sel,
		addrWord(coll),
		[32]byte{}, // parentCollectionId = 0 (root)
		cond,
		wordU64(0xa0),
		wordU64(uint64(amountMicro)),
		wordU64(2), // partition length
		wordU64(1), // index set 0b01 (outcome 1)
		wordU64(2), // index set 0b10 (outcome 2)
	), nil
}

// EncodeRedeemPositions builds redeemPositions(collateral, 0x0, conditionId,
// [1,2]) — collects winnings for both index sets after resolution.
func EncodeRedeemPositions(collateralAddr, conditionID string) ([]byte, error) {
	coll, err := parseAddress(collateralAddr)
	if err != nil {
		return nil, err
	}
	cond, err := parseBytes32(conditionID)
	if err != nil {
		return nil, err
	}
	// (address, bytes32 parent=0, bytes32 condition, uint256[] offset=0x80)
	// + tail: [len=2, 1, 2]
	return abiWords(selRedeemPosition,
		addrWord(coll),
		[32]byte{},
		cond,
		wordU64(0x80),
		wordU64(2),
		wordU64(1),
		wordU64(2),
	), nil
}
