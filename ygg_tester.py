"""Yggdrasil throughput tester — TCP over Yggdrasil IPv6 addresses."""

import sys
from common import load_config
import socket_tester as st

TRANSPORT = "yggdrasil"


def server():
    cfg = load_config()
    bind = cfg["nodes"][cfg["this_node"]]["ygg_addr"]
    if not bind:
        raise ValueError("ygg_addr not set for this node in config.json")
    port = cfg["ports"]["ygg_server"]
    st.run_server(bind, port, TRANSPORT)


def client(peer_node: str) -> dict:
    cfg = load_config()
    peer = cfg["nodes"][peer_node]["ygg_addr"]
    if not peer:
        raise ValueError(f"ygg_addr not set for {peer_node} in config.json")
    port = cfg["ports"]["ygg_server"]
    t = cfg["test"]
    return st.run_client(peer, port, TRANSPORT,
                         t["latency_pings"], t["throughput_runs"], t["throughput_mb"])


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "server":
        server()
    else:
        import json
        print(json.dumps(client(sys.argv[1]), indent=2))
