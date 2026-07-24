"""Per-window A/B of a MAKER pilot vs its TAKER twin — is maker entry effective, did
it MISS windows, and any per-window advantage? Reads two live_state_*/journal.csv on
the engine host. Run: python3 research/maker_ab_detail.py <maker_dir> <taker_dir>"""
import csv
import collections
import sys
import time


def load(d):
    by = collections.defaultdict(list)
    try:
        for r in csv.DictReader(open(d + "/journal.csv")):
            if r.get("slug"):
                by[r["slug"]].append(r)
    except FileNotFoundError:
        pass
    return by


def summ(rs):
    kinds = [r["kind"] for r in rs]
    rest = sum(1 for k in kinds if k.startswith("REST_BUY"))
    canc = sum(1 for k in kinds if k == "CANCEL")
    fill = [r for r in rs if r["kind"].startswith("FILL_BUY")]
    tak = [r for r in rs if r["kind"].startswith("BUY")]
    settle = [r for r in rs if r["kind"] in ("WIN", "LOSS")]
    entry = fill[0] if fill else (tak[0] if tak else None)
    etype = "MAKER" if fill else ("taker" if tak else "none")
    return dict(rest=rest, canc=canc, etype=etype,
                px=(entry["price"] if entry else ""),
                pnl=(settle[-1]["pnl"] if settle else ""),
                res=(settle[-1]["kind"] if settle else "open"))


def main():
    mk, tk = load(sys.argv[1]), load(sys.argv[2])
    lo = min(int(s.rsplit("-", 1)[1]) for s in mk) if mk else 0
    slugs = sorted(set(mk) | {s for s in tk if int(s.rsplit("-", 1)[1]) >= lo},
                   key=lambda s: int(s.rsplit("-", 1)[1]))
    print("window           | MAKER  rest/canc  entry@px  -> res          | TAKER  entry@px -> res")
    nf = nt = miss = 0
    for s in slugs:
        t = int(s.rsplit("-", 1)[1])
        hm = time.strftime("%m-%d %H:%M", time.gmtime(t))
        m = summ(mk[s]) if s in mk else None
        k = summ(tk[s]) if s in tk else None
        ms = (f"r{m['rest']}/c{m['canc']}  {m['etype']:>5}@{m['px']:<5} -> {m['res']:<5}" if m else "(not acted)")
        ks = (f"{k['etype']:>5}@{k['px']:<5} -> {k['res']}" if k else "(not acted)")
        print(f"{hm:>14}   | {ms:<42} | {ks}")
        if m and m["etype"] == "MAKER":
            nf += 1
        if m and m["etype"] == "taker":
            nt += 1
        if (not m or m["etype"] == "none") and k and k["etype"] != "none":
            miss += 1
    print(f"\nMAKER-filled={nf}  taker-fallback={nt}  | windows the taker took but the maker MISSED={miss}")
    if nf + nt:
        print(f"maker-entry effectiveness: {nf}/{nf+nt} = {100*nf/(nf+nt):.0f}% actually rested+filled as maker "
              f"(the rest crossed the book -> taker fallback)")


if __name__ == "__main__":
    main()
