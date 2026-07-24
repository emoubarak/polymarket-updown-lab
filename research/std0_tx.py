#!/usr/bin/env python3
"""Decode std0 SPLIT/MERGE/REDEEM transactions on Polygon to answer Q3 (batching)
and Q4 (gas + submission path: direct EOA vs relayer).

For each sampled op tx we fetch eth_getTransactionByHash (from/to/input/gasPrice)
and eth_getTransactionReceipt (gasUsed, effectiveGasPrice, status, #logs), then
decode the proxy(ProxyCall[]) / splitPosition / mergePositions / redeemPositions
calldata to count how many proxy sub-calls are batched per tx.
"""
import json, os, time, urllib.request, urllib.error

DATA = os.path.join(os.path.dirname(__file__), "data", "std0_activity.json")
RPCS = ["https://polygon-bor-rpc.publicnode.com", "https://1rpc.io/matic",
        "https://polygon-rpc.com"]
SIGNER = "0x3ec3577a6a22f9b4716c5aefe0963a052bf703a6"
PROXY_WALLET = "0xdf7930e89a2c47560165331863c31deca0733dcd"
PROXY_FACTORY = "0xab45c5a4b0c941a2f231c04c3f49182e1a254052"
ADAPTER = "0xada100db00ca00073811820692005400218fce1f"

SELECTORS = {
    "0x34ee9791": "proxy(ProxyCall[])",
    "0x72ce4275": "splitPosition",
    # common CTF selectors
    "0xa9059cbb": "transfer",
    "0x095ea7b3": "approve",
}
# mergePositions / redeemPositions selectors (computed below for reference)


def rpc(method, params, _rpc=[0]):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                          "params": params}).encode()
    last = None
    for off in range(len(RPCS)):
        url = RPCS[(_rpc[0] + off) % len(RPCS)]
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0 research"})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if "result" in r and r["result"] is not None:
                _rpc[0] = (_rpc[0] + off) % len(RPCS)
                return r["result"]
            last = r.get("error")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as e:
            last = str(e)
            time.sleep(0.4)
    raise RuntimeError(f"rpc {method} failed: {last}")


def decode_proxy_calls(inp):
    """Decode proxy(ProxyCall[] calls) -> list of (typeCode, to, dataSelector, datalen).
    ProxyCall struct = (uint8 typeCode, address to, uint256 value, bytes data).
    Returns None if selector isn't proxy()."""
    if not inp or len(inp) < 10:
        return None
    sel = inp[:10]
    if sel != "0x34ee9791":
        return ("NON_PROXY", sel)
    body = inp[10:]
    words = [body[i:i + 64] for i in range(0, len(body), 64)]
    if not words:
        return []
    def w(i):
        return int(words[i], 16)
    # head: offset to array (in bytes from start of args)
    arr_off = w(0) // 32
    n = w(arr_off)
    calls = []
    # element offsets are relative to start of array data (word after length)
    base = arr_off + 1
    for k in range(n):
        el_off = base + w(base + k) // 32
        typecode = w(el_off + 0)
        to = "0x" + words[el_off + 1][24:]
        value = w(el_off + 2)
        data_off = el_off + w(el_off + 3) // 32
        data_len = w(data_off)
        data_hex = "".join(words[data_off + 1: data_off + 1 + (data_len + 31) // 32])
        dsel = "0x" + data_hex[:8] if data_len >= 4 else "0x"
        calls.append({"typecode": typecode, "to": to, "value": value,
                      "data_len": data_len, "data_sel": dsel})
    return calls


def main():
    rows = json.load(open(DATA))
    # sample several of each op type, spread across the window
    samples = {}
    for t in ("SPLIT", "REDEEM", "MERGE"):
        hs = [r for r in rows if r["type"] == t]
        pick = hs[:: max(1, len(hs) // 8)][:8] if hs else []
        samples[t] = pick

    gas_records = []
    print("=== Q3/Q4 TX DECODE ===")
    for t, picks in samples.items():
        if not picks:
            print(f"\n--- {t}: none in window ---")
            continue
        print(f"\n--- {t} ({len(picks)} sampled) ---")
        for r in picks:
            h = r["transactionHash"]
            try:
                tx = rpc("eth_getTransactionByHash", [h])
                rc = rpc("eth_getTransactionReceipt", [h])
            except RuntimeError as e:
                print(f"  {h[:12]} RPC fail: {e}")
                continue
            frm = tx["from"].lower()
            to = tx["to"].lower()
            gas_used = int(rc["gasUsed"], 16)
            egp = int(rc.get("effectiveGasPrice", tx.get("gasPrice", "0x0")), 16)
            gas_gwei = egp / 1e9
            gas_matic = gas_used * egp / 1e18
            status = int(rc["status"], 16)
            nlogs = len(rc.get("logs", []))
            calls = decode_proxy_calls(tx["input"])
            via = ("EOA-direct" if frm == SIGNER else
                   "PROXY-direct" if frm == PROXY_WALLET else
                   f"RELAYER({frm[:10]})")
            ncalls = len(calls) if isinstance(calls, list) else "n/a"
            to_lbl = ("FACTORY" if to == PROXY_FACTORY else
                      "ADAPTER" if to == ADAPTER else
                      "PROXY" if to == PROXY_WALLET else to[:12])
            print(f"  {h[:12]} ${r['usdcSize']:.0f} from={via} to={to_lbl} "
                  f"gas={gas_used} @{gas_gwei:.1f}gwei = {gas_matic:.5f} MATIC "
                  f"st={status} logs={nlogs} proxyCalls={ncalls}")
            if isinstance(calls, list) and calls:
                for c in calls:
                    print(f"       call typecode={c['typecode']} to={c['to'][:12]} "
                          f"sel={c['data_sel']} datalen={c['data_len']}")
            elif isinstance(calls, tuple):
                print(f"       NON-proxy selector={calls[1]}")
            gas_records.append({"type": t, "from": via, "gas_used": gas_used,
                                "gas_gwei": gas_gwei, "gas_matic": gas_matic,
                                "ncalls": ncalls})
            time.sleep(0.15)

    # gas summary
    if gas_records:
        import statistics as st
        print("\n=== Q4 GAS SUMMARY ===")
        gu = [g["gas_used"] for g in gas_records]
        gp = [g["gas_gwei"] for g in gas_records]
        gm = [g["gas_matic"] for g in gas_records]
        froms = set(g["from"].split("(")[0] for g in gas_records)
        print(f"submission path(s): {froms}")
        print(f"gasUsed: median={int(st.median(gu))} min={min(gu)} max={max(gu)}")
        print(f"gasPrice gwei: median={st.median(gp):.1f} min={min(gp):.1f} max={max(gp):.1f}")
        print(f"MATIC/op: median={st.median(gm):.5f} mean={st.mean(gm):.5f}")
        per_type = {}
        for g in gas_records:
            per_type.setdefault(g["type"], []).append(g["gas_used"])
        for t, xs in per_type.items():
            print(f"  {t}: median gasUsed={int(st.median(xs))}")
    return gas_records


if __name__ == "__main__":
    main()
