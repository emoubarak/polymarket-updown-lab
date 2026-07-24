package main

// eip712.go — minimal EIP-712 signing for the Polymarket CLOB, ported from
// the official clob-client-v2 TypeScript sources (verified 2026-07-02):
//
//   - CTF Exchange V2 order signing: domain {"Polymarket CTF Exchange","2",
//     137, exchange}, 11-field Order struct. V1 orders (go-order-utils) are
//     no longer accepted since the 2026-04-28 exchange upgrade.
//   - ClobAuth L1 signature for API-key derivation: domain {"ClobAuthDomain",
//     "1", 137} (NO verifyingContract — 3-field domain typehash).
//
// Uses decred secp256k1 (pure Go, RFC6979 deterministic, low-s canonical)
// and x/crypto sha3 — deliberately not the full go-ethereum dependency.

import (
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"math/big"
	"strings"

	"github.com/decred/dcrd/dcrec/secp256k1/v4"
	"github.com/decred/dcrd/dcrec/secp256k1/v4/ecdsa"
	"golang.org/x/crypto/sha3"
)

// Exact strings from clob-client-v2 (do not reformat — they are hashed).
const (
	orderTypeString = "Order(uint256 salt,address maker,address signer,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,uint256 timestamp,bytes32 metadata,bytes32 builder)"
	domain4TypeStr  = "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
	domain3TypeStr  = "EIP712Domain(string name,string version,uint256 chainId)"
	clobAuthTypeStr = "ClobAuth(address address,string timestamp,uint256 nonce,string message)"
	clobAuthMessage = "This message attests that I control the given wallet"

	exchangeDomainName    = "Polymarket CTF Exchange"
	exchangeDomainVersion = "2"
	clobAuthDomainName    = "ClobAuthDomain"
	clobAuthDomainVersion = "1"
	polygonChainID        = 137

	// CTF Exchange V2 contracts on Polygon (clob-client-v2 config.ts).
	ExchangeV2Addr        = "0xE111180000d2663C0091e4f400237545B87B996B"
	NegRiskExchangeV2Addr = "0xe2222d279d744050d28e00520010520000310F59"
)

func keccak256(chunks ...[]byte) [32]byte {
	h := sha3.NewLegacyKeccak256()
	for _, c := range chunks {
		h.Write(c)
	}
	var out [32]byte
	h.Sum(out[:0])
	return out
}

// word converts a big.Int to a 32-byte big-endian ABI word.
func word(x *big.Int) [32]byte {
	var out [32]byte
	x.FillBytes(out[:])
	return out
}

func wordU64(x uint64) [32]byte {
	var out [32]byte
	binary.BigEndian.PutUint64(out[24:], x)
	return out
}

// addrWord left-pads a 20-byte address into a 32-byte word.
func addrWord(addr [20]byte) [32]byte {
	var out [32]byte
	copy(out[12:], addr[:])
	return out
}

func parseAddress(s string) ([20]byte, error) {
	var a [20]byte
	s = strings.TrimPrefix(strings.TrimPrefix(s, "0x"), "0X")
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != 20 {
		return a, fmt.Errorf("bad address %q", s)
	}
	copy(a[:], b)
	return a, nil
}

// Wallet holds the EOA signing key and the funder (proxy) identity.
type Wallet struct {
	priv    *secp256k1.PrivateKey
	Address [20]byte // EOA derived from the private key (the "signer")
	Funder  [20]byte // order maker / source of funds (proxy for sig type 1/2)
	SigType int      // 0 EOA, 1 POLY_PROXY, 2 POLY_GNOSIS_SAFE
}

func NewWallet(privHex, funderHex string, sigType int) (*Wallet, error) {
	privHex = strings.TrimPrefix(strings.TrimPrefix(privHex, "0x"), "0X")
	kb, err := hex.DecodeString(privHex)
	if err != nil || len(kb) != 32 {
		return nil, fmt.Errorf("bad private key")
	}
	priv := secp256k1.PrivKeyFromBytes(kb)
	if priv.Key.IsZero() {
		return nil, fmt.Errorf("zero private key")
	}
	w := &Wallet{priv: priv, SigType: sigType}

	// EOA address: keccak256(uncompressed pubkey minus 0x04 prefix)[12:].
	pub := priv.PubKey().SerializeUncompressed()
	h := keccak256(pub[1:])
	copy(w.Address[:], h[12:])

	if funderHex == "" {
		w.Funder = w.Address
	} else if w.Funder, err = parseAddress(funderHex); err != nil {
		return nil, err
	}
	return w, nil
}

func (w *Wallet) AddressHex() string { return "0x" + hex.EncodeToString(w.Address[:]) }
func (w *Wallet) FunderHex() string  { return "0x" + hex.EncodeToString(w.Funder[:]) }

// SignDigest produces a 65-byte r||s||v Ethereum signature (v in {27,28}).
func (w *Wallet) SignDigest(digest [32]byte) []byte {
	// SignCompact returns [v, r(32), s(32)] with v already offset by 27
	// (+4 if compressed=true, which we don't use). Reorder to r||s||v.
	compact := ecdsa.SignCompact(w.priv, digest[:], false)
	sig := make([]byte, 65)
	copy(sig[:32], compact[1:33])
	copy(sig[32:64], compact[33:65])
	sig[64] = compact[0]
	return sig
}

// domainSeparator4 hashes the 4-field EIP712Domain (with verifyingContract).
func domainSeparator4(name, version string, chainID uint64, contract [20]byte) [32]byte {
	th := keccak256([]byte(domain4TypeStr))
	nh := keccak256([]byte(name))
	vh := keccak256([]byte(version))
	cid := wordU64(chainID)
	ca := addrWord(contract)
	return keccak256(th[:], nh[:], vh[:], cid[:], ca[:])
}

// domainSeparator3 hashes the 3-field EIP712Domain (no verifyingContract) —
// used by ClobAuthDomain.
func domainSeparator3(name, version string, chainID uint64) [32]byte {
	th := keccak256([]byte(domain3TypeStr))
	nh := keccak256([]byte(name))
	vh := keccak256([]byte(version))
	cid := wordU64(chainID)
	return keccak256(th[:], nh[:], vh[:], cid[:])
}

func eip712Digest(domainSep, structHash [32]byte) [32]byte {
	return keccak256([]byte{0x19, 0x01}, domainSep[:], structHash[:])
}

// ---------------------------------------------------------------------------
// Order signing (CTF Exchange V2)
// ---------------------------------------------------------------------------

// OrderCore carries the numeric fields that get signed. Amounts are
// 6-decimal fixed point; TimestampMs is order creation time in milliseconds
// (V2 replaced the nonce with this).
type OrderCore struct {
	Salt        int64
	Maker       [20]byte
	Signer      [20]byte
	TokenID     *big.Int
	MakerAmount int64
	TakerAmount int64
	Side        uint8 // 0 BUY, 1 SELL
	SigType     uint8
	TimestampMs int64
}

// orderStructHash implements the 11-field V2 Order struct hash.
func orderStructHash(o *OrderCore) [32]byte {
	th := keccak256([]byte(orderTypeString))
	salt := wordU64(uint64(o.Salt))
	maker := addrWord(o.Maker)
	signer := addrWord(o.Signer)
	token := word(o.TokenID)
	mkAmt := wordU64(uint64(o.MakerAmount))
	tkAmt := wordU64(uint64(o.TakerAmount))
	side := wordU64(uint64(o.Side))
	st := wordU64(uint64(o.SigType))
	ts := wordU64(uint64(o.TimestampMs))
	var zero32 [32]byte // metadata and builder: bytes32(0)
	return keccak256(th[:], salt[:], maker[:], signer[:], token[:], mkAmt[:],
		tkAmt[:], side[:], st[:], ts[:], zero32[:], zero32[:])
}

// SignOrder returns the 65-byte signature for an order against the given
// exchange contract (regular vs neg-risk V2).
func (w *Wallet) SignOrder(o *OrderCore, exchangeAddr [20]byte) []byte {
	ds := domainSeparator4(exchangeDomainName, exchangeDomainVersion, polygonChainID, exchangeAddr)
	sh := orderStructHash(o)
	return w.SignDigest(eip712Digest(ds, sh))
}

// ---------------------------------------------------------------------------
// ClobAuth L1 signature (API key create/derive)
// ---------------------------------------------------------------------------

// SignClobAuth builds the L1 auth signature for POLY_SIGNATURE. timestamp is
// unix seconds (stringified into the struct), nonce normally 0.
func (w *Wallet) SignClobAuth(timestamp int64, nonce uint64) string {
	th := keccak256([]byte(clobAuthTypeStr))
	addr := addrWord(w.Address)
	tsh := keccak256(fmt.Appendf(nil, "%d", timestamp))
	n := wordU64(nonce)
	msgh := keccak256([]byte(clobAuthMessage))
	sh := keccak256(th[:], addr[:], tsh[:], n[:], msgh[:])
	ds := domainSeparator3(clobAuthDomainName, clobAuthDomainVersion, polygonChainID)
	sig := w.SignDigest(eip712Digest(ds, sh))
	return "0x" + hex.EncodeToString(sig)
}

// NewSalt returns a random order salt that stays comfortably inside the
// float64-exact integer range (the venue parses it with parseInt).
func NewSalt() int64 {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err) // crypto/rand failure is not survivable for a trading bot
	}
	return int64(binary.LittleEndian.Uint64(b[:]) & ((1 << 52) - 1))
}

// RecoverAddress recovers the signing address from a 65-byte r||s||v
// signature over digest. Test/verification helper.
func RecoverAddress(digest [32]byte, sig []byte) ([20]byte, error) {
	var out [20]byte
	if len(sig) != 65 {
		return out, fmt.Errorf("bad sig length %d", len(sig))
	}
	compact := make([]byte, 65)
	compact[0] = sig[64]
	copy(compact[1:33], sig[:32])
	copy(compact[33:], sig[32:64])
	pub, _, err := ecdsa.RecoverCompact(compact, digest[:])
	if err != nil {
		return out, err
	}
	raw := pub.SerializeUncompressed()
	h := keccak256(raw[1:])
	copy(out[:], h[12:])
	return out, nil
}
