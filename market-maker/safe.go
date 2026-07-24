package main

// safe.go — Gnosis Safe v1.3.0 execTransaction encoding for the Polymarket
// proxy (our funder). Because the transaction is SENT BY the Safe's single
// owner EOA, we use the pre-validated signature form (v=1, r=owner, s=0):
// the Safe checks msg.sender == r and requires no ECDSA over the SafeTx
// hash. Verified live layout: our Safe is v1.3.0, threshold 1, owner = our
// EOA, with all pUSD/CTF approvals already in place.

import (
	"context"
	"fmt"
	"math/big"
)

var selExecTransaction = methodSelector("execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)")

// EncodeExecTransaction wraps an inner call (to, data) into a Safe
// execTransaction calldata with operation=CALL, zero gas params and the
// owner's pre-validated signature.
func EncodeExecTransaction(innerTo [20]byte, innerData []byte, owner [20]byte) []byte {
	// Head: 10 slots.
	//  0 to
	//  1 value (0)
	//  2 offset(data)        = 0x140 (10*32)
	//  3 operation (0 CALL)
	//  4 safeTxGas (0)
	//  5 baseGas (0)
	//  6 gasPrice (0)
	//  7 gasToken (0)
	//  8 refundReceiver (0)
	//  9 offset(signatures)  = 0x140 + 32 + pad32(len(data))
	dataPadded := pad32(innerData)
	sigOffset := 10*32 + 32 + len(dataPadded)

	// Pre-validated signature: r = owner (left-padded), s = 0, v = 1.
	sig := make([]byte, 65)
	copy(sig[12:32], owner[:])
	sig[64] = 1
	sigPadded := pad32(sig)

	words := [][32]byte{
		addrWord(innerTo),
		{}, // value 0
		wordU64(0x140),
		{}, // operation CALL
		{}, {}, {}, // safeTxGas, baseGas, gasPrice
		{}, {}, // gasToken, refundReceiver
		wordU64(uint64(sigOffset)),
	}
	out := make([]byte, 0, 4+len(words)*32+32+len(dataPadded)+32+len(sigPadded))
	out = append(out, selExecTransaction[:]...)
	for _, w := range words {
		out = append(out, w[:]...)
	}
	lenWord := wordU64(uint64(len(innerData)))
	out = append(out, lenWord[:]...)
	out = append(out, dataPadded...)
	lenWord = wordU64(uint64(len(sig)))
	out = append(out, lenWord[:]...)
	out = append(out, sigPadded...)
	return out
}

// pad32 right-pads b to a multiple of 32 bytes.
func pad32(b []byte) []byte {
	if len(b)%32 == 0 {
		return b
	}
	out := make([]byte, ((len(b)/32)+1)*32)
	copy(out, b)
	return out
}

// SafeSplitter executes split/merge through the Safe from the owner EOA.
type SafeSplitter struct {
	rpc     *RPCClient
	w       *Wallet
	adapter [20]byte // CtfCollateralAdapter
	pusd    string
	chainID uint64
}

// CtfCollateralAdapterAddr wraps pUSD<->USDC.e around the canonical CTF for
// the up/down markets (verified from live split transactions).
const CtfCollateralAdapterAddr = "0xAdA100Db00Ca00073811820692005400218FcE1f"

// PUSDAddr is Polymarket USD, the V2 collateral.
const PUSDAddr = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"

func NewSafeSplitter(rpcURL string, w *Wallet) (*SafeSplitter, error) {
	adapter, err := parseAddress(CtfCollateralAdapterAddr)
	if err != nil {
		return nil, err
	}
	return &SafeSplitter{
		rpc: NewRPCClient(rpcURL), w: w, adapter: adapter,
		pusd: PUSDAddr, chainID: polygonChainID,
	}, nil
}

// execute sends one Safe-wrapped inner call and waits for the receipt.
func (s *SafeSplitter) execute(ctx context.Context, innerData []byte, label string) (string, error) {
	calldata := EncodeExecTransaction(s.adapter, innerData, s.w.Address)
	nonce, err := s.rpc.Nonce(ctx, s.w.AddressHex())
	if err != nil {
		return "", fmt.Errorf("%s: nonce: %w", label, err)
	}
	tip, maxFee, err := s.rpc.GasPrice(ctx)
	if err != nil {
		return "", fmt.Errorf("%s: gas price: %w", label, err)
	}
	gas, err := s.rpc.EstimateGas(ctx, s.w.AddressHex(), s.w.FunderHex(), calldata)
	if err != nil {
		return "", fmt.Errorf("%s: estimate: %w", label, err)
	}
	tx := &Eip1559Tx{
		ChainID: s.chainID, Nonce: nonce, TipWei: tip, MaxFeeWei: maxFee,
		GasLimit: gas + gas/5, To: s.w.Funder, ValueWei: big.NewInt(0),
		Data: calldata,
	}
	hash, err := s.rpc.SendRaw(ctx, tx.SignedRaw(s.w))
	if err != nil {
		return "", fmt.Errorf("%s: send: %w", label, err)
	}
	ok, err := s.rpc.WaitReceipt(ctx, hash)
	if err != nil {
		return hash, fmt.Errorf("%s: %w", label, err)
	}
	if !ok {
		return hash, fmt.Errorf("%s: tx %s REVERTED", label, hash)
	}
	return hash, nil
}

// Split mints amountMicro/1e6 complete Up+Down sets for the condition.
func (s *SafeSplitter) Split(ctx context.Context, conditionID string, amountMicro int64) (string, error) {
	data, err := EncodeSplitPosition(s.pusd, conditionID, amountMicro)
	if err != nil {
		return "", err
	}
	return s.execute(ctx, data, "split")
}

// Merge burns amountMicro/1e6 complete sets back into pUSD.
func (s *SafeSplitter) Merge(ctx context.Context, conditionID string, amountMicro int64) (string, error) {
	data, err := EncodeMergePositions(s.pusd, conditionID, amountMicro)
	if err != nil {
		return "", err
	}
	return s.execute(ctx, data, "merge")
}
