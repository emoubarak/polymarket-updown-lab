#!/usr/bin/env python3
"""maker_taker — decode std0's on-chain fills to count maker vs taker roles.

Polymarket CTF Exchange emits, per matched pair:
  OrderFilled(bytes32 indexed orderHash, address indexed maker,
              address indexed taker, uint256 makerAssetId,
              uint256 takerAssetId, uint256 makerAmountFilled,
              uint256 takerAmountFilled, uint256 fee)
maker/taker are indexed (topics[2], topics[3]). We fetch receipts for std0's
recent trade txs and count how often std0's proxy is the maker vs the taker.
Read-only.
"""
import json, subprocess, pathlib, collections, time
from eth_utils import keccak

STD0 = "0xdf7930e89a2c47560165331863c31deca0733dcd".lower()
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://polygon-rpc.com", "https://1rpc.io/matic"]
EXCHANGES = {
    "0xe111180000d2663c0091e4f400237545b87b996b",  # CTF Exchange V2
    "0xe2222d279d744050d28e00520010520000310f59",  # Neg-Risk Exchange V2
    "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e",  # legacy exchange
    "0xc5d563a36ae78145c45a50134d48a1215220f80a",  # legacy neg-risk
}
# Real Polymarket CTFExchange OrderFilled topic (confirmed on-chain; the
# ABI-derived hash didn't match — the deployed signature differs).
TOPIC = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
_ = keccak  # kept for reference

def rpc(method, params):
    body = json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params})
    last = None
    for u in RPCS:
        try:
            out = subprocess.run(
                ["curl","-s","-X","POST",u,"-H","Content-Type: application/json","-d",body],
                capture_output=True, text=True, timeout=20).stdout
            return json.loads(out).get("result")
        except Exception as e:
            last = e
    raise last

def topic_addr(t):
    return "0x" + t[-40:]

# collect recent std0 TRADE txHashes from recorded activity
rows, seen = [], set()
for fn in ["std0_hist.jsonl", "std0_activity.jsonl"]:
    p = pathlib.Path.home()/"rebate"/fn
    if not p.exists(): continue
    for line in p.read_text().splitlines():
        if not line.strip(): continue
        try: a = json.loads(line)
        except Exception: continue
        if a.get("type")!="TRADE": continue
        h = a.get("transactionHash")
        if h and h not in seen:
            seen.add(h); rows.append(a)

rows.sort(key=lambda x:x["timestamp"], reverse=True)
sample = rows[:40]
print(f"decoding {len(sample)} recent std0 trade txs (of {len(rows)} unique)\n")

roles = collections.Counter()
by_side = collections.defaultdict(lambda: collections.Counter())
checked = 0
for a in sample:
    try:
        r = rpc("eth_getTransactionReceipt", [a["transactionHash"]])
    except Exception:
        continue
    if not r: continue
    checked += 1
    exch_addrs = {e.lower() for e in EXCHANGES}
    for log in r.get("logs", []):
        if log["address"].lower() not in EXCHANGES: continue
        topics = log.get("topics", [])
        if not topics or topics[0].lower()!=TOPIC.lower(): continue
        if len(topics)<4: continue
        maker = topic_addr(topics[2]).lower()
        taker = topic_addr(topics[3]).lower()
        # A "taker" that is the exchange address = a mint/complement leg, not
        # a real external taker. Classify std0's role only vs real counterparties.
        if maker==STD0:
            if taker in exch_addrs:
                roles["maker-mint"]+=1          # our resting order, complement-minted
            else:
                roles["maker"]+=1               # our resting order taken by someone
                by_side[a.get("side","?")]["maker"]+=1
        if taker==STD0:
            roles["taker"]+=1                   # WE crossed to take
            by_side[a.get("side","?")]["taker"]+=1
    time.sleep(0.05)

print(f"receipts decoded: {checked}")
print(f"std0 OrderFilled roles: {dict(roles)}")
tot = roles["maker"]+roles["taker"]
if tot:
    print(f"  MAKER: {100*roles['maker']/tot:.0f}%   TAKER: {100*roles['taker']/tot:.0f}%")
print("by trade side (BUY/SELL) → role:")
for s,c in by_side.items():
    print(f"  {s}: {dict(c)}")
