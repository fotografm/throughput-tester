#!/usr/bin/env python3
"""
Throughput tester — unified CLI.

Usage:
  runner.py server [--transport tcp,ygg,i2p,rns]   # start server listeners
  runner.py test   --to <node> [--transport ...]   # run tests from this node
  runner.py results [--last N]                     # show stored results

Transport choices: tcp  ygg  i2p  rns  all
Node choices:      ct107  ct152  ct166
"""

import argparse
import sys
import time
import threading

from common import load_config, save_result, load_results, format_table, TestResult

ALL_TRANSPORTS = ["tcp", "ygg", "i2p", "rns"]


def _import_testers():
    import tcp_tester
    import ygg_tester
    import i2p_tester
    import rns_tester
    return {
        "tcp": tcp_tester,
        "ygg": ygg_tester,
        "i2p": i2p_tester,
        "rns": rns_tester,
    }


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def cmd_server(transports: list[str]):
    mods = _import_testers()
    # RNS.Reticulum must be initialized in the main thread (it sets signal handlers)
    if "rns" in transports:
        mods["rns"].init_rns()
    threads = []
    for t in transports:
        th = threading.Thread(target=mods[t].server, name=f"server-{t}", daemon=True)
        th.start()
        threads.append(th)
    print(f"Servers running: {', '.join(transports)}  (Ctrl-C to stop)")
    try:
        for th in threads:
            th.join()
    except KeyboardInterrupt:
        print("\nStopped.")


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def cmd_test(to_node: str, transports: list[str]):
    cfg = load_config()
    this = cfg["this_node"]
    from_label = cfg["nodes"][this]["label"]
    to_label   = cfg["nodes"][to_node]["label"]

    mods = _import_testers()
    # Pre-init RNS in main thread before any tests run
    if "rns" in transports:
        mods["rns"].init_rns()
    print(f"\nTesting  {from_label}  →  {to_label}")
    print(f"Transports: {', '.join(transports)}\n")

    results = []
    for t in transports:
        print(f"--- {t.upper()} ---")
        try:
            r = mods[t].client(to_node)
            result = TestResult(
                transport        = t,
                from_node        = from_label,
                to_node          = to_label,
                timestamp        = time.time(),
                latency_avg_ms   = r["latency_avg_ms"],
                latency_min_ms   = r["latency_min_ms"],
                latency_max_ms   = r["latency_max_ms"],
                latency_jitter_ms= r["latency_jitter_ms"],
                upload_mbps      = r["upload_mbps"],
                download_mbps    = r["download_mbps"],
            )
            save_result(result)
            results.append(result)
            print(f"  Latency:  avg {r['latency_avg_ms']:.1f} ms  "
                  f"min {r['latency_min_ms']:.1f}  max {r['latency_max_ms']:.1f}  "
                  f"jitter {r['latency_jitter_ms']:.1f} ms")
            print(f"  Upload:   {r['upload_mbps']:.2f} Mbps")
            print(f"  Download: {r['download_mbps']:.2f} Mbps")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    if len(results) > 1:
        print("=== Summary ===")
        for r in results:
            print(f"  {r.transport:<12} lat {r.latency_avg_ms:6.1f} ms  "
                  f"up {r.upload_mbps:6.2f} Mbps  dn {r.download_mbps:6.2f} Mbps")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

def cmd_results(last: int):
    print(format_table(load_results(last)))


# ---------------------------------------------------------------------------
# Node / transport selection helpers
# ---------------------------------------------------------------------------

def _resolve_transports(raw: str) -> list[str]:
    if raw == "all":
        return ALL_TRANSPORTS
    chosen = [x.strip().lower() for x in raw.split(",")]
    bad = [x for x in chosen if x not in ALL_TRANSPORTS]
    if bad:
        print(f"Unknown transports: {bad}. Choose from {ALL_TRANSPORTS} or 'all'")
        sys.exit(1)
    return chosen


def _pick_peer(cfg: dict, this: str) -> str:
    others = [k for k in cfg["nodes"] if k != this]
    print("Available peers:")
    for i, n in enumerate(others, 1):
        print(f"  {i}. {cfg['nodes'][n]['label']}")
    while True:
        raw = input("Select peer [1]: ").strip() or "1"
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(others):
                return others[idx]
        except ValueError:
            pass
        print("Invalid choice.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Throughput tester")
    sub = parser.add_subparsers(dest="cmd")

    p_srv = sub.add_parser("server", help="Start server listeners")
    p_srv.add_argument("--transport", default="all",
                       help="tcp,ygg,i2p,rns or 'all' (default: all)")

    p_test = sub.add_parser("test", help="Run throughput tests")
    p_test.add_argument("--to", default=None,
                        help="Target node (ct107/ct152/ct166); prompted if omitted")
    p_test.add_argument("--transport", default="all",
                        help="tcp,ygg,i2p,rns or 'all' (default: all)")

    p_res = sub.add_parser("results", help="Show stored results")
    p_res.add_argument("--last", type=int, default=20)

    args = parser.parse_args()

    if args.cmd is None:
        parser.print_help()
        sys.exit(0)

    cfg = load_config()

    if args.cmd == "server":
        cmd_server(_resolve_transports(args.transport))

    elif args.cmd == "test":
        to_node = args.to
        if to_node is None:
            to_node = _pick_peer(cfg, cfg["this_node"])
        if to_node not in cfg["nodes"]:
            print(f"Unknown node '{to_node}'. Known: {list(cfg['nodes'].keys())}")
            sys.exit(1)
        if to_node == cfg["this_node"]:
            print("Cannot test against yourself.")
            sys.exit(1)
        cmd_test(to_node, _resolve_transports(args.transport))

    elif args.cmd == "results":
        cmd_results(args.last)


if __name__ == "__main__":
    main()
