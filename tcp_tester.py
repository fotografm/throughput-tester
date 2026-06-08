"""TCP throughput tester — raw LAN baseline."""

import sys
from common import load_config
import socket_tester as st

TRANSPORT = "tcp"


def server():
    cfg = load_config()
    bind = cfg["nodes"][cfg["this_node"]]["tcp_addr"]
    port = cfg["ports"]["tcp_server"]
    st.run_server(bind, port, TRANSPORT)


def client(peer_node: str) -> dict:
    cfg = load_config()
    peer = cfg["nodes"][peer_node]["tcp_addr"]
    port = cfg["ports"]["tcp_server"]
    t = cfg["test"]
    return st.run_client(peer, port, TRANSPORT,
                         t["latency_pings"], t["throughput_runs"], t["throughput_mb"])


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "server":
        server()
    else:
        import json
        print(json.dumps(client(sys.argv[1]), indent=2))
