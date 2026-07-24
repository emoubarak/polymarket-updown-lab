package main

// evm.go — minimal EIP-1559 transaction layer for Phase B on-chain ops
// (Safe execTransaction → CtfCollateralAdapter split/merge). Pure Go on top
// of our existing keccak/secp256k1; no go-ethereum.

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"net/http"
	"time"
)

// ---------------------------------------------------------------------------
// RLP (subset: byte strings and lists — all a typed tx needs)
// ---------------------------------------------------------------------------

type rlpItem interface{ encode(buf *bytes.Buffer) }

type rlpBytes []byte

type rlpList []rlpItem

func (b rlpBytes) encode(buf *bytes.Buffer) {
	switch {
	case len(b) == 1 && b[0] < 0x80:
		buf.WriteByte(b[0])
	case len(b) <= 55:
		buf.WriteByte(0x80 + byte(len(b)))
		buf.Write(b)
	default:
		l := intToMinimal(uint64(len(b)))
		buf.WriteByte(0xb7 + byte(len(l)))
		buf.Write(l)
		buf.Write(b)
	}
}

func (l rlpList) encode(buf *bytes.Buffer) {
	var inner bytes.Buffer
	for _, it := range l {
		it.encode(&inner)
	}
	n := inner.Len()
	if n <= 55 {
		buf.WriteByte(0xc0 + byte(n))
	} else {
		ln := intToMinimal(uint64(n))
		buf.WriteByte(0xf7 + byte(len(ln)))
		buf.Write(ln)
	}
	buf.Write(inner.Bytes())
}

// intToMinimal returns the minimal big-endian encoding (empty for zero).
func intToMinimal(v uint64) []byte {
	if v == 0 {
		return nil
	}
	var tmp [8]byte
	n := 0
	for i := 7; i >= 0; i-- {
		tmp[7-i] = byte(v >> (uint(i) * 8))
	}
	for n < 8 && tmp[n] == 0 {
		n++
	}
	return tmp[n:]
}

func rlpUint(v uint64) rlpBytes      { return rlpBytes(intToMinimal(v)) }
func rlpBig(v *big.Int) rlpBytes     { if v == nil || v.Sign() == 0 { return rlpBytes{} }; return rlpBytes(v.Bytes()) }
func rlpAddr(a [20]byte) rlpBytes    { return rlpBytes(a[:]) }

// ---------------------------------------------------------------------------
// EIP-1559 transaction build + sign
// ---------------------------------------------------------------------------

// Eip1559Tx is a type-2 Polygon transaction.
type Eip1559Tx struct {
	ChainID   uint64
	Nonce     uint64
	TipWei    *big.Int // maxPriorityFeePerGas
	MaxFeeWei *big.Int // maxFeePerGas
	GasLimit  uint64
	To        [20]byte
	ValueWei  *big.Int
	Data      []byte
}

func (tx *Eip1559Tx) fields(sig []rlpItem) []byte {
	items := rlpList{
		rlpUint(tx.ChainID), rlpUint(tx.Nonce), rlpBig(tx.TipWei),
		rlpBig(tx.MaxFeeWei), rlpUint(tx.GasLimit), rlpAddr(tx.To),
		rlpBig(tx.ValueWei), rlpBytes(tx.Data), rlpList{}, // empty access list
	}
	items = append(items, sig...)
	var buf bytes.Buffer
	buf.WriteByte(0x02)
	items.encode(&buf)
	return buf.Bytes()
}

// SigningHash is keccak256(0x02 || rlp(unsigned fields)).
func (tx *Eip1559Tx) SigningHash() [32]byte {
	return keccak256(tx.fields(nil))
}

// SignedRaw signs with the wallet's EOA key and returns the raw tx bytes.
func (tx *Eip1559Tx) SignedRaw(w *Wallet) []byte {
	sig := w.SignDigest(tx.SigningHash()) // r||s||v(27/28)
	yParity := uint64(sig[64] - 27)
	r := new(big.Int).SetBytes(sig[:32])
	s := new(big.Int).SetBytes(sig[32:64])
	return tx.fields([]rlpItem{rlpUint(yParity), rlpBig(r), rlpBig(s)})
}

// ---------------------------------------------------------------------------
// JSON-RPC client
// ---------------------------------------------------------------------------

// RPCClient is a minimal Ethereum JSON-RPC client.
type RPCClient struct {
	URL string
	hc  *http.Client
}

func NewRPCClient(url string) *RPCClient {
	return &RPCClient{URL: url, hc: &http.Client{Timeout: 12 * time.Second}}
}

func (c *RPCClient) call(ctx context.Context, method string, params []any, out any) error {
	body, err := json.Marshal(map[string]any{
		"jsonrpc": "2.0", "id": 1, "method": method, "params": params,
	})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.URL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Error  *struct {
			Code    int    `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil {
		return fmt.Errorf("%s: %w", method, err)
	}
	if envelope.Error != nil {
		return fmt.Errorf("%s: rpc %d: %s", method, envelope.Error.Code, envelope.Error.Message)
	}
	if out != nil {
		return json.Unmarshal(envelope.Result, out)
	}
	return nil
}

func hexToBig(s string) *big.Int {
	v, _ := new(big.Int).SetString(trim0x(s), 16)
	if v == nil {
		v = big.NewInt(0)
	}
	return v
}

func trim0x(s string) string {
	if len(s) >= 2 && (s[:2] == "0x" || s[:2] == "0X") {
		return s[2:]
	}
	return s
}

// Nonce returns the EOA's pending nonce.
func (c *RPCClient) Nonce(ctx context.Context, addr string) (uint64, error) {
	var out string
	if err := c.call(ctx, "eth_getTransactionCount", []any{addr, "pending"}, &out); err != nil {
		return 0, err
	}
	return hexToBig(out).Uint64(), nil
}

// GasPrice returns a max-fee suggestion (base fee headroom + tip).
func (c *RPCClient) GasPrice(ctx context.Context) (tip, maxFee *big.Int, err error) {
	var gp string
	if err := c.call(ctx, "eth_gasPrice", []any{}, &gp); err != nil {
		return nil, nil, err
	}
	price := hexToBig(gp)
	// Polygon: healthy inclusion at gasPrice + 50% headroom, tip = 30 gwei min.
	tip = big.NewInt(30_000_000_000)
	if price.Cmp(tip) > 0 {
		tip = new(big.Int).Set(price)
	}
	maxFee = new(big.Int).Mul(tip, big.NewInt(2))
	return tip, maxFee, nil
}

// EstimateGas estimates gas for a call from `from` to `to` with data.
func (c *RPCClient) EstimateGas(ctx context.Context, from, to string, data []byte) (uint64, error) {
	var out string
	err := c.call(ctx, "eth_estimateGas", []any{map[string]string{
		"from": from, "to": to, "data": "0x" + hex.EncodeToString(data),
	}}, &out)
	if err != nil {
		return 0, err
	}
	return hexToBig(out).Uint64(), nil
}

// SendRaw broadcasts a signed transaction, returning the tx hash.
func (c *RPCClient) SendRaw(ctx context.Context, raw []byte) (string, error) {
	var out string
	err := c.call(ctx, "eth_sendRawTransaction", []any{"0x" + hex.EncodeToString(raw)}, &out)
	return out, err
}

// WaitReceipt polls for a receipt until ctx expires. Returns (status, err).
func (c *RPCClient) WaitReceipt(ctx context.Context, txHash string) (bool, error) {
	for {
		var out struct {
			Status string `json:"status"`
		}
		err := c.call(ctx, "eth_getTransactionReceipt", []any{txHash}, &out)
		if err == nil && out.Status != "" {
			return out.Status == "0x1", nil
		}
		select {
		case <-ctx.Done():
			return false, fmt.Errorf("receipt timeout for %s", txHash)
		case <-time.After(2 * time.Second):
		}
	}
}
