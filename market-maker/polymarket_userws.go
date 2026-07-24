package main

// polymarket_userws.go — CLOB user channel: authoritative order/fill events.
//
// Inventory is reconciled ONLY from confirmed fills seen here (plus REST
// resync after gaps) — never from assuming a cancel/replace worked. Fills
// are applied at MATCHED and reversed on FAILED (trade lifecycle:
// MATCHED → MINED → CONFIRMED, or MATCHED → RETRYING → FAILED; replays of
// the same trade id are deduped inside Inventory).
//
// Fill volume is tiny next to the market-data path, so encoding/json here
// is fine — the zero-alloc requirement applies to price ingestion.

import (
	"context"
	"encoding/json"
	"log"
	"strconv"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

// UserEventKind tags events delivered to the main loop.
type UserEventKind uint8

const (
	// UEFill: a maker/taker fill touching our orders (apply to inventory).
	UEFill UserEventKind = iota
	// UEFillFailed: a previously-reported trade failed on-chain (reverse).
	UEFillFailed
	// UEOrderUpdate: order placement/update/cancel bookkeeping.
	UEOrderUpdate
	// UEResync: the socket (re)connected; REST-resync open orders now.
	UEResync
)

// UserEvent is the channel message from the WS goroutine to the main loop.
type UserEvent struct {
	Kind    UserEventKind
	Fill    Fill   // UEFill
	TradeID string // UEFillFailed
	// UEOrderUpdate fields:
	OrderID      string
	OrderType    string // PLACEMENT | UPDATE | CANCELLATION
	AssetID      string
	Side         string
	SizeMatched  int64 // micro-shares
	OrderStatus  string
}

// ---------------------------------------------------------------------------
// Order registry — our view of resting orders (venue-confirmed).
// ---------------------------------------------------------------------------

// OrderState tracks one order. Done entries are TOMBSTONES: the order no
// longer rests (canceled or fully matched) but its identity stays resolvable
// for a grace period — a fill can race a successful cancel by milliseconds,
// and its side/token must still be recoverable when the event arrives.
type OrderState struct {
	ID           string
	TokenID      string
	IsUp         bool
	Side         uint8 // 0 buy, 1 sell
	PriceMicro   int64
	SizeMicro    int64
	MatchedMicro int64
	PlacedAt     time.Time // when WE tracked it (resync-staleness fencing)
	Done         bool
	DoneAt       time.Time
}

// OrderRegistry is a mutex-protected map of live orders. Not on the pricing
// hot path (touched on order ops and fills only).
type OrderRegistry struct {
	mu     sync.Mutex
	orders map[string]*OrderState
}

func NewOrderRegistry() *OrderRegistry {
	return &OrderRegistry{orders: make(map[string]*OrderState)}
}

func (r *OrderRegistry) Track(o OrderState) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if o.PlacedAt.IsZero() {
		o.PlacedAt = time.Now()
	}
	cp := o
	r.orders[o.ID] = &cp
}

// StaleMissing returns live order ids tracked BEFORE cutoff that are not in
// the given set — candidates for tombstoning after a REST snapshot. Orders
// tracked after the cutoff may simply be newer than the snapshot.
func (r *OrderRegistry) StaleMissing(present map[string]bool, cutoff time.Time) []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	var out []string
	for id, o := range r.orders {
		if !o.Done && !present[id] && o.PlacedAt.Before(cutoff) {
			out = append(out, id)
		}
	}
	return out
}

// Remove tombstones an order (it keeps resolving fills for a grace period).
func (r *OrderRegistry) Remove(id string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if o, ok := r.orders[id]; ok {
		o.Done = true
		o.DoneAt = time.Now()
	}
}

// Purge drops tombstones older than the cutoff.
func (r *OrderRegistry) Purge(olderThan time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for id, o := range r.orders {
		if o.Done && o.DoneAt.Before(olderThan) {
			delete(r.orders, id)
		}
	}
}

// Get returns a copy of the order state.
func (r *OrderRegistry) Get(id string) (OrderState, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	o, ok := r.orders[id]
	if !ok {
		return OrderState{}, false
	}
	return *o, true
}

// OnMatched records incremental matched size; fully-matched orders become
// tombstones (late partial-fill events must still resolve).
func (r *OrderRegistry) OnMatched(id string, matchedMicro int64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if o, ok := r.orders[id]; ok {
		o.MatchedMicro += matchedMicro
		if o.MatchedMicro >= o.SizeMicro && !o.Done {
			o.Done = true
			o.DoneAt = time.Now()
		}
	}
}

// List returns copies of all LIVE (non-tombstone) orders.
func (r *OrderRegistry) List() []OrderState {
	r.mu.Lock()
	defer r.mu.Unlock()
	out := make([]OrderState, 0, len(r.orders))
	for _, o := range r.orders {
		if !o.Done {
			out = append(out, *o)
		}
	}
	return out
}

// Clear drops everything (window roll / resync baseline).
func (r *OrderRegistry) Clear() {
	r.mu.Lock()
	defer r.mu.Unlock()
	clear(r.orders)
}

// OpenBuyUSDCMicro sums the unfilled USDC commitment of resting buys —
// worst-case additional inventory if every resting order fills.
func (r *OrderRegistry) OpenBuyUSDCMicro() int64 {
	r.mu.Lock()
	defer r.mu.Unlock()
	var total int64
	for _, o := range r.orders {
		if o.Side == 0 && !o.Done {
			remaining := o.SizeMicro - o.MatchedMicro
			if remaining > 0 {
				total += remaining * o.PriceMicro / Micro
			}
		}
	}
	return total
}

// ---------------------------------------------------------------------------
// User WebSocket
// ---------------------------------------------------------------------------

type wsUserTrade struct {
	EventType   string `json:"event_type"`
	ID          string `json:"id"`
	AssetID     string `json:"asset_id"`
	Side        string `json:"side"`
	Size        string `json:"size"`
	Price       string `json:"price"`
	Status      string `json:"status"`
	TraderSide  string `json:"trader_side"`
	Owner       string `json:"owner"`
	MakerOrders []struct {
		OrderID       string `json:"order_id"`
		Owner         string `json:"owner"`
		MatchedAmount string `json:"matched_amount"`
		Price         string `json:"price"`
		AssetID       string `json:"asset_id"`
	} `json:"maker_orders"`
}

type wsUserOrder struct {
	EventType   string `json:"event_type"`
	ID          string `json:"id"`
	AssetID     string `json:"asset_id"`
	Side        string `json:"side"`
	SizeMatched string `json:"size_matched"`
	Type        string `json:"type"`   // PLACEMENT | UPDATE | CANCELLATION
	Status      string `json:"status"` // LIVE | MATCHED | CANCELED
}

// UserWS maintains the authenticated user-channel connection.
type UserWS struct {
	URL    string
	creds  APICreds
	Events chan UserEvent

	// DroppedFill latches when a UEFill could not be delivered (Events
	// buffer full). No REST endpoint can rebuild inventory — the loop
	// treats this as a kill condition.
	DroppedFill atomic.Bool

	upToken atomic.Value // string: current Up token id (fill classification)
	dnToken atomic.Value // string: current Down token id
	markets atomic.Value // []string: condition ids to subscribe
	conn    struct {
		sync.Mutex
		c *websocket.Conn
	}
}

func NewUserWS(url string, creds APICreds) *UserWS {
	u := &UserWS{URL: url, creds: creds, Events: make(chan UserEvent, 256)}
	u.markets.Store([]string(nil))
	u.upToken.Store("")
	u.dnToken.Store("")
	return u
}

// SetWindow updates the subscription scope; Bounce() applies it by forcing
// a reconnect (the venue supports dynamic updates, but a clean reconnect is
// simpler to reason about at a 5-minute cadence). Fills for tokens outside
// the current window (late trade-status replays from a rolled window) are
// dropped — they must never touch the new window's inventory.
func (u *UserWS) SetWindow(upTokenID, downTokenID string, conditionIDs []string) {
	u.upToken.Store(upTokenID)
	u.dnToken.Store(downTokenID)
	u.markets.Store(conditionIDs)
}

// Bounce forces the current session to end (Run reconnects immediately).
func (u *UserWS) Bounce() {
	u.conn.Lock()
	if u.conn.c != nil {
		u.conn.c.Close()
	}
	u.conn.Unlock()
}

// Run maintains the connection until ctx is cancelled.
func (u *UserWS) Run(ctx context.Context) {
	backoff := 250 * time.Millisecond
	for ctx.Err() == nil {
		start := time.Now()
		err := u.session(ctx)
		if ctx.Err() != nil {
			return
		}
		log.Printf("userws: session ended after %s: %v (reconnect in %s)",
			time.Since(start).Round(time.Millisecond), err, backoff)
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		if time.Since(start) > time.Minute {
			backoff = 250 * time.Millisecond
		} else if backoff < 5*time.Second {
			backoff *= 2
		}
	}
}

type wsUserSub struct {
	Auth    APICreds `json:"auth"`
	Type    string   `json:"type"`
	Markets []string `json:"markets,omitempty"`
}

func (u *UserWS) session(ctx context.Context) error {
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second}
	conn, _, err := dialer.DialContext(ctx, u.URL, nil)
	if err != nil {
		return err
	}
	u.conn.Lock()
	u.conn.c = conn
	u.conn.Unlock()

	done := make(chan struct{})
	defer func() {
		close(done)
		u.conn.Lock()
		u.conn.c = nil
		u.conn.Unlock()
		conn.Close()
	}()
	go func() {
		select {
		case <-ctx.Done():
			conn.Close()
		case <-done:
		}
	}()

	mkts, _ := u.markets.Load().([]string)
	sub := wsUserSub{Auth: u.creds, Type: "user", Markets: mkts}
	if err := conn.WriteJSON(sub); err != nil {
		return err
	}

	// Socket keepalive (distinct from the REST order heartbeat): text PING
	// every 10s or the venue closes the connection.
	go func() {
		t := time.NewTicker(10 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				if err := conn.WriteMessage(websocket.TextMessage, []byte("PING")); err != nil {
					return
				}
			}
		}
	}()

	// Tell the main loop to REST-resync open orders for this session.
	u.emit(UserEvent{Kind: UEResync})

	for {
		if err := conn.SetReadDeadline(time.Now().Add(30 * time.Second)); err != nil {
			return err
		}
		_, msg, err := conn.ReadMessage()
		if err != nil {
			return err
		}
		u.handle(msg)
	}
}

func (u *UserWS) emit(ev UserEvent) {
	select {
	case u.Events <- ev:
	default:
		// Main loop stalled badly enough to fill a 256-slot buffer. A lost
		// fill is unrecoverable (no REST inventory rebuild exists): latch
		// the flag so the loop kills the bot rather than trade blind.
		log.Printf("userws: CRITICAL event buffer full, dropping %v", ev.Kind)
		if ev.Kind == UEFill || ev.Kind == UEFillFailed {
			u.DroppedFill.Store(true)
		}
	}
}

// handle routes one frame; frames may be a single event or an array.
func (u *UserWS) handle(msg []byte) {
	trim := 0
	for trim < len(msg) && (msg[trim] == ' ' || msg[trim] == '\n' || msg[trim] == '\t' || msg[trim] == '\r') {
		trim++
	}
	if trim >= len(msg) {
		return
	}
	if msg[trim] == '[' {
		var raws []json.RawMessage
		if err := json.Unmarshal(msg[trim:], &raws); err != nil {
			return
		}
		for _, r := range raws {
			u.handleOne(r)
		}
		return
	}
	if msg[trim] == '{' {
		u.handleOne(msg[trim:])
	}
	// else: PONG etc — ignore.
}

func (u *UserWS) handleOne(msg []byte) {
	var head struct {
		EventType string `json:"event_type"`
	}
	if err := json.Unmarshal(msg, &head); err != nil {
		return
	}
	switch head.EventType {
	case "trade":
		var t wsUserTrade
		if err := json.Unmarshal(msg, &t); err != nil {
			log.Printf("userws: bad trade frame: %v", err)
			return
		}
		u.onTrade(&t)
	case "order":
		var o wsUserOrder
		if err := json.Unmarshal(msg, &o); err != nil {
			log.Printf("userws: bad order frame: %v", err)
			return
		}
		u.emit(UserEvent{
			Kind:        UEOrderUpdate,
			OrderID:     o.ID,
			OrderType:   o.Type,
			AssetID:     o.AssetID,
			Side:        o.Side,
			SizeMatched: parseMicro(o.SizeMatched),
			OrderStatus: o.Status,
		})
	}
}

// onTrade converts a trade event into inventory fills.
func (u *UserWS) onTrade(t *wsUserTrade) {
	upTok, _ := u.upToken.Load().(string)
	dnTok, _ := u.dnToken.Load().(string)
	inWindow := func(assetID string) bool {
		return assetID == upTok || assetID == dnTok
	}
	switch t.Status {
	case "MATCHED":
		// Apply-once happens in Inventory via trade-id dedupe.
	case "MINED", "CONFIRMED", "RETRYING":
		// Replays of a known trade — Inventory dedupes; still forward so a
		// missed MATCHED (WS gap) gets applied late rather than never.
	case "FAILED":
		u.emit(UserEvent{Kind: UEFillFailed, TradeID: t.ID})
		// Sub-fills carry per-order ids (see below).
		for _, mo := range t.MakerOrders {
			if mo.Owner == u.creds.Key {
				u.emit(UserEvent{Kind: UEFillFailed, TradeID: t.ID + ":" + mo.OrderID})
			}
		}
		return
	default:
		return
	}

	if t.TraderSide == "TAKER" && t.Owner == u.creds.Key {
		// Our taker order (emergency liquidation path).
		if !inWindow(t.AssetID) {
			log.Printf("userws: dropping out-of-window taker fill asset=%.10s", t.AssetID)
			return
		}
		u.emit(UserEvent{Kind: UEFill, Fill: Fill{
			TradeID: t.ID,
			OrderID: t.ID,
			AssetID: t.AssetID,
			IsUp:    t.AssetID == upTok,
			Side:    sideFromString(t.Side),
			Taker:   true,
			Size:    parseMicro(t.Size),
			Price:   parseMicro(t.Price),
		}})
		return
	}

	// Maker side: our involvement is the subset of maker_orders we own.
	// Trade-level asset/side describe the TAKER; per-maker-order asset_id
	// can be the complement (mint/merge matching), so classify per entry.
	// Side of our order comes from the registry via OrderID in main.go.
	for _, mo := range t.MakerOrders {
		if mo.Owner != u.creds.Key {
			continue
		}
		if !inWindow(mo.AssetID) {
			log.Printf("userws: dropping out-of-window maker fill asset=%.10s", mo.AssetID)
			continue
		}
		u.emit(UserEvent{Kind: UEFill, Fill: Fill{
			TradeID: t.ID + ":" + mo.OrderID,
			OrderID: mo.OrderID,
			AssetID: mo.AssetID,
			IsUp:    mo.AssetID == upTok,
			Side:    255, // unknown here — main loop resolves from registry
			Size:    parseMicro(mo.MatchedAmount),
			Price:   parseMicro(mo.Price),
		}})
	}
}

func sideFromString(s string) Side {
	if s == "SELL" {
		return SideSell
	}
	return SideBuy
}

// parseMicro converts a decimal string ("12.34") into micro fixed point.
func parseMicro(s string) int64 {
	if s == "" {
		return 0
	}
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return 0
	}
	return int64(f*float64(Micro) + 0.5)
}
