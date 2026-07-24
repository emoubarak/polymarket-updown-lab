package main

// polymarket_clob.go — authenticated order gateway for the Polymarket CLOB.
//
//   - L1 (EIP-712) auth to create/derive the L2 API credentials
//   - L2 (HMAC) headers on every trading request
//   - CTF Exchange V2 order build + sign (see eip712.go)
//   - requote token bucket (venue limits are generous — 500/s burst on
//     POST /order as of 2026-06 — but the bucket also protects us from
//     our own bugs)
//   - REST heartbeat: POST /v1/heartbeats every ~5s, chaining heartbeat_id;
//     without it the venue cancels all resting orders after ~15s of silence.
//     Runs on its own goroutine so nothing can starve it.
//
// There is NO atomic cancel-replace: cancel and post are independent
// requests and fills can land in between. Inventory truth comes only from
// the user WebSocket channel (polymarket_userws.go).

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/big"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// APICreds are the L2 credentials derived from the wallet key.
type APICreds struct {
	Key        string `json:"apiKey"`
	Secret     string `json:"secret"`
	Passphrase string `json:"passphrase"`
}

// ClobClient is the authenticated REST gateway. Safe for concurrent use.
type ClobClient struct {
	base    string
	hc      *http.Client
	w       *Wallet
	creds   APICreds
	exch    [20]byte
	negExch [20]byte

	hbID       atomic.Value // string: last heartbeat_id
	HBFailures atomic.Int64 // consecutive heartbeat failures (risk input)
}

func NewClobClient(base string, w *Wallet) (*ClobClient, error) {
	exch, err := parseAddress(ExchangeV2Addr)
	if err != nil {
		return nil, err
	}
	neg, err := parseAddress(NegRiskExchangeV2Addr)
	if err != nil {
		return nil, err
	}
	c := &ClobClient{
		base: strings.TrimRight(base, "/"),
		hc: &http.Client{
			Timeout: 10 * time.Second,
			Transport: &http.Transport{
				MaxIdleConnsPerHost: 8,
				IdleConnTimeout:     90 * time.Second,
				ForceAttemptHTTP2:   true,
			},
		},
		w: w, exch: exch, negExch: neg,
	}
	c.hbID.Store("")
	return c, nil
}

func (c *ClobClient) SetCreds(cr APICreds) { c.creds = cr }
func (c *ClobClient) Creds() APICreds      { return c.creds }

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

func (c *ClobClient) l1Headers(req *http.Request) {
	ts := time.Now().Unix()
	req.Header.Set("POLY_ADDRESS", c.w.AddressHex())
	req.Header.Set("POLY_SIGNATURE", c.w.SignClobAuth(ts, 0))
	req.Header.Set("POLY_TIMESTAMP", fmt.Sprintf("%d", ts))
	req.Header.Set("POLY_NONCE", "0")
}

// buildHmacSig implements the canonical CLOB HMAC: message is
// timestamp+METHOD+path[+body], key is the base64url-decoded secret, output
// is base64url WITH padding kept.
func buildHmacSig(secret string, ts int64, method, path string, body []byte) (string, error) {
	norm := strings.NewReplacer("-", "+", "_", "/").Replace(secret)
	if m := len(norm) % 4; m != 0 {
		norm += strings.Repeat("=", 4-m)
	}
	key, err := base64.StdEncoding.DecodeString(norm)
	if err != nil {
		return "", fmt.Errorf("bad api secret: %w", err)
	}
	mac := hmac.New(sha256.New, key)
	fmt.Fprintf(mac, "%d%s%s", ts, method, path)
	mac.Write(body)
	return base64.URLEncoding.EncodeToString(mac.Sum(nil)), nil
}

func (c *ClobClient) l2Headers(req *http.Request, method, path string, body []byte) error {
	ts := time.Now().Unix()
	sig, err := buildHmacSig(c.creds.Secret, ts, method, path, body)
	if err != nil {
		return err
	}
	req.Header.Set("POLY_ADDRESS", c.w.AddressHex())
	req.Header.Set("POLY_SIGNATURE", sig)
	req.Header.Set("POLY_TIMESTAMP", fmt.Sprintf("%d", ts))
	req.Header.Set("POLY_API_KEY", c.creds.Key)
	req.Header.Set("POLY_PASSPHRASE", c.creds.Passphrase)
	return nil
}

// DeriveAPICreds obtains L2 credentials: derive-first (deterministic for an
// existing key), falling back to creation.
func (c *ClobClient) DeriveAPICreds(ctx context.Context) (APICreds, error) {
	var creds APICreds
	err := c.doL1(ctx, http.MethodGet, "/auth/derive-api-key", &creds)
	if err == nil && creds.Key != "" {
		return creds, nil
	}
	deriveErr := err
	if err := c.doL1(ctx, http.MethodPost, "/auth/api-key", &creds); err != nil {
		return creds, fmt.Errorf("derive failed (%v); create failed: %w", deriveErr, err)
	}
	if creds.Key == "" {
		return creds, fmt.Errorf("api-key create returned empty key")
	}
	return creds, nil
}

func (c *ClobClient) doL1(ctx context.Context, method, path string, out any) error {
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, nil)
	if err != nil {
		return err
	}
	c.l1Headers(req)
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("%s %s: status %d: %.300s", method, path, resp.StatusCode, body)
	}
	return json.Unmarshal(body, out)
}

// doL2 sends an L2-authenticated request. The exact body bytes are signed.
func (c *ClobClient) doL2(ctx context.Context, method, path string, reqBody any, out any) error {
	var body []byte
	var err error
	if reqBody != nil {
		if body, err = json.Marshal(reqBody); err != nil {
			return err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, c.base+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	// The HMAC covers the path WITHOUT the query string (matches the
	// official clients; signing the query yields 401).
	signPath := path
	if i := strings.IndexByte(path, '?'); i >= 0 {
		signPath = path[:i]
	}
	if err := c.l2Headers(req, method, signPath, body); err != nil {
		return err
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return &ClobHTTPError{Status: resp.StatusCode, Body: string(respBody), Path: path}
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(respBody, out)
}

// ClobHTTPError preserves status and body for callers that branch on them
// (e.g. heartbeat 400 carries the corrected heartbeat_id).
type ClobHTTPError struct {
	Status int
	Body   string
	Path   string
}

func (e *ClobHTTPError) Error() string {
	return fmt.Sprintf("clob %s: status %d: %.300s", e.Path, e.Status, e.Body)
}

// ---------------------------------------------------------------------------
// Orders
// ---------------------------------------------------------------------------

// SignedOrderJSON is the exact POST /order wire format (orderToJsonV2).
type SignedOrderJSON struct {
	Salt          int64  `json:"salt"`
	Maker         string `json:"maker"`
	Signer        string `json:"signer"`
	TokenID       string `json:"tokenId"`
	MakerAmount   string `json:"makerAmount"`
	TakerAmount   string `json:"takerAmount"`
	Side          string `json:"side"`
	SignatureType int    `json:"signatureType"`
	Timestamp     string `json:"timestamp"`
	Expiration    string `json:"expiration"`
	Metadata      string `json:"metadata"`
	Builder       string `json:"builder"`
	Signature     string `json:"signature"`
}

type postOrderBody struct {
	Order     SignedOrderJSON `json:"order"`
	Owner     string          `json:"owner"`
	OrderType string          `json:"orderType"`
	PostOnly  bool            `json:"postOnly"`
	DeferExec bool            `json:"deferExec"`
}

// PostOrderResp is the venue's POST /order response.
type PostOrderResp struct {
	Success  bool   `json:"success"`
	ErrorMsg string `json:"errorMsg"`
	OrderID  string `json:"orderID"`
	Status   string `json:"status"` // live | matched | delayed | unmatched
}

const bytes32ZeroHex = "0x0000000000000000000000000000000000000000000000000000000000000000"

// BuildOrder creates and signs a limit order. priceMicro is the probability
// price ×1e6 (tick-aligned by the caller), sizeMicro is shares ×1e6.
// BUY: makerAmount = USDC in, takerAmount = shares out.
// SELL: makerAmount = shares in, takerAmount = USDC out.
func (c *ClobClient) BuildOrder(tokenID string, side uint8, priceMicro, sizeMicro int64, negRisk bool) (*SignedOrderJSON, error) {
	if priceMicro <= 0 || priceMicro >= Micro || sizeMicro <= 0 {
		return nil, fmt.Errorf("bad order params price=%d size=%d", priceMicro, sizeMicro)
	}
	// Venue precision rules (verified live): share sizes max 2 decimals;
	// the derived USDC amount max 4 decimals. With price on a 0.01 tick and
	// size at 2 decimals the product is always ≤4 decimals.
	if sizeMicro%10_000 != 0 {
		return nil, fmt.Errorf("size %d not 2-decimal aligned", sizeMicro)
	}
	if priceMicro%10_000 != 0 {
		return nil, fmt.Errorf("price %d not tick aligned", priceMicro)
	}
	tok, ok := new(big.Int).SetString(tokenID, 10)
	if !ok {
		return nil, fmt.Errorf("bad tokenId %q", tokenID)
	}
	usdc := priceMicro * sizeMicro / Micro
	var makerAmt, takerAmt int64
	var sideStr string
	if side == 0 {
		makerAmt, takerAmt, sideStr = usdc, sizeMicro, "BUY"
	} else {
		makerAmt, takerAmt, sideStr = sizeMicro, usdc, "SELL"
	}
	core := &OrderCore{
		Salt:        NewSalt(),
		Maker:       c.w.Funder,
		Signer:      c.w.Address,
		TokenID:     tok,
		MakerAmount: makerAmt,
		TakerAmount: takerAmt,
		Side:        side,
		SigType:     uint8(c.w.SigType),
		TimestampMs: time.Now().UnixMilli(),
	}
	exch := c.exch
	if negRisk {
		exch = c.negExch
	}
	sig := c.w.SignOrder(core, exch)
	return &SignedOrderJSON{
		Salt:          core.Salt,
		Maker:         c.w.FunderHex(),
		Signer:        c.w.AddressHex(),
		TokenID:       tokenID,
		MakerAmount:   fmt.Sprintf("%d", makerAmt),
		TakerAmount:   fmt.Sprintf("%d", takerAmt),
		Side:          sideStr,
		SignatureType: c.w.SigType,
		Timestamp:     fmt.Sprintf("%d", core.TimestampMs),
		Expiration:    "0",
		Metadata:      bytes32ZeroHex,
		Builder:       bytes32ZeroHex,
		Signature:     "0x" + hexEncode(sig),
	}, nil
}

func hexEncode(b []byte) string {
	const digits = "0123456789abcdef"
	out := make([]byte, len(b)*2)
	for i, v := range b {
		out[i*2] = digits[v>>4]
		out[i*2+1] = digits[v&0xf]
	}
	return string(out)
}

// PostOrder submits a signed order. postOnly=true makes the venue reject
// (not match) an order that would cross — required for pure maker quoting.
func (c *ClobClient) PostOrder(ctx context.Context, o *SignedOrderJSON, orderType string, postOnly bool) (PostOrderResp, error) {
	var out PostOrderResp
	body := postOrderBody{Order: *o, Owner: c.creds.Key, OrderType: orderType, PostOnly: postOnly}
	err := c.doL2(ctx, http.MethodPost, "/order", &body, &out)
	return out, err
}

type cancelResp struct {
	Canceled    []string          `json:"canceled"`
	NotCanceled map[string]string `json:"not_canceled"`
}

// CancelOrders cancels a batch of order IDs (venue cap 1000/batch).
func (c *ClobClient) CancelOrders(ctx context.Context, ids []string) (canceled []string, err error) {
	if len(ids) == 0 {
		return nil, nil
	}
	var out cancelResp
	if err := c.doL2(ctx, http.MethodDelete, "/orders", ids, &out); err != nil {
		return nil, err
	}
	return out.Canceled, nil
}

// CancelMarketOrders cancels all our orders in one market (window roll).
func (c *ClobClient) CancelMarketOrders(ctx context.Context, conditionID string) error {
	body := map[string]string{"market": conditionID}
	return c.doL2(ctx, http.MethodDelete, "/cancel-market-orders", body, nil)
}

// CancelAll cancels everything (kill switch).
func (c *ClobClient) CancelAll(ctx context.Context) error {
	return c.doL2(ctx, http.MethodDelete, "/cancel-all", nil, nil)
}

// OpenOrder is one row of GET /data/orders (resync after WS gaps).
type OpenOrder struct {
	ID           string `json:"id"`
	Market       string `json:"market"`
	AssetID      string `json:"asset_id"`
	Side         string `json:"side"`
	Price        string `json:"price"`
	OriginalSize string `json:"original_size"`
	SizeMatched  string `json:"size_matched"`
}

// GetOrder fetches one order by id — authoritative resolution for fills
// whose order the local registry doesn't know (fill beat the HTTP post
// response under load).
func (c *ClobClient) GetOrder(ctx context.Context, orderID string) (*OpenOrder, error) {
	var out OpenOrder
	if err := c.doL2(ctx, http.MethodGet, "/data/order/"+orderID, nil, &out); err != nil {
		return nil, err
	}
	if out.ID == "" {
		return nil, fmt.Errorf("order %s not found", orderID)
	}
	return &out, nil
}

// GetOpenOrders lists our live orders, optionally filtered by condition id.
// The endpoint is cursor-paginated: {data, next_cursor}, "MA=="→start,
// "LTE="→end (matches the official client).
func (c *ClobClient) GetOpenOrders(ctx context.Context, conditionID string) ([]OpenOrder, error) {
	var all []OpenOrder
	cursor := "MA=="
	for cursor != "LTE=" {
		path := "/data/orders?next_cursor=" + cursor
		if conditionID != "" {
			path += "&market=" + conditionID
		}
		var page struct {
			Data       []OpenOrder `json:"data"`
			NextCursor string      `json:"next_cursor"`
		}
		if err := c.doL2(ctx, http.MethodGet, path, nil, &page); err != nil {
			return nil, err
		}
		all = append(all, page.Data...)
		if page.NextCursor == "" || page.NextCursor == cursor {
			break
		}
		cursor = page.NextCursor
	}
	return all, nil
}

// UpdateBalanceAllowance asks the venue to refresh its cached balance for a
// conditional token — required after buy fills before the shares can back a
// SELL order (verified live: sells reject with "balance: 0" otherwise).
func (c *ClobClient) UpdateBalanceAllowance(ctx context.Context, tokenID string, sigType int) error {
	path := fmt.Sprintf("/balance-allowance/update?asset_type=CONDITIONAL&token_id=%s&signature_type=%d", tokenID, sigType)
	return c.doL2(ctx, http.MethodGet, path, nil, nil)
}

// ServerTime returns the venue clock (drift check at startup).
func (c *ClobClient) ServerTime(ctx context.Context) (int64, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.base+"/time", nil)
	if err != nil {
		return 0, err
	}
	resp, err := c.hc.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1024))
	var ts int64
	if _, err := fmt.Sscanf(string(bytes.TrimSpace(body)), "%d", &ts); err != nil {
		return 0, fmt.Errorf("bad /time response %q", body)
	}
	return ts, nil
}

// ---------------------------------------------------------------------------
// Heartbeat
// ---------------------------------------------------------------------------

type heartbeatBody struct {
	HeartbeatID string `json:"heartbeat_id"`
}

type heartbeatResp struct {
	Status      string `json:"status"`
	HeartbeatID string `json:"heartbeat_id"`
}

// Heartbeat sends one liveness ping, chaining the previous heartbeat_id
// (empty string starts a session). A 400 response carries the corrected id;
// we adopt it and count the beat as failed (the next one recovers).
func (c *ClobClient) Heartbeat(ctx context.Context) error {
	prev, _ := c.hbID.Load().(string)
	var out heartbeatResp
	err := c.doL2(ctx, http.MethodPost, "/v1/heartbeats", heartbeatBody{HeartbeatID: prev}, &out)
	if err != nil {
		var he *ClobHTTPError
		if ok := asClobErr(err, &he); ok && he.Status == http.StatusBadRequest {
			var fix heartbeatResp
			if jerr := json.Unmarshal([]byte(he.Body), &fix); jerr == nil && fix.HeartbeatID != "" {
				c.hbID.Store(fix.HeartbeatID)
			}
		}
		return err
	}
	if out.HeartbeatID != "" {
		c.hbID.Store(out.HeartbeatID)
	}
	return nil
}

func asClobErr(err error, target **ClobHTTPError) bool {
	e, ok := err.(*ClobHTTPError)
	if ok {
		*target = e
	}
	return ok
}

// RunHeartbeat keeps resting orders alive: one beat every `every` (5s per
// venue docs; auto-cancel fires ~15s after the last valid beat). Runs until
// ctx is cancelled. Dedicated goroutine — never share it with the pricing
// or quoting loops.
func (c *ClobClient) RunHeartbeat(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			hctx, cancel := context.WithTimeout(ctx, 3*time.Second)
			err := c.Heartbeat(hctx)
			cancel()
			if err != nil {
				n := c.HBFailures.Add(1)
				log.Printf("heartbeat: failure %d: %v", n, err)
			} else {
				c.HBFailures.Store(0)
			}
		}
	}
}

// ---------------------------------------------------------------------------
// Requote throttle
// ---------------------------------------------------------------------------

// TokenBucket is a simple thread-safe rate limiter for requote actions.
type TokenBucket struct {
	mu     sync.Mutex
	tokens float64
	max    float64
	rate   float64 // tokens per second
	last   time.Time
}

func NewTokenBucket(max, perSec float64) *TokenBucket {
	return &TokenBucket{tokens: max, max: max, rate: perSec}
}

// Allow consumes one token if available.
func (tb *TokenBucket) Allow(now time.Time) bool {
	tb.mu.Lock()
	defer tb.mu.Unlock()
	if !tb.last.IsZero() {
		tb.tokens += now.Sub(tb.last).Seconds() * tb.rate
		if tb.tokens > tb.max {
			tb.tokens = tb.max
		}
	}
	tb.last = now
	if tb.tokens >= 1 {
		tb.tokens--
		return true
	}
	return false
}
