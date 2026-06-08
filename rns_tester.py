"""
Reticulum (RNS) throughput tester.

Each node has a persistent identity stored at rns_identity/identity.
The server announces a destination; clients request a Link and run the
same latency + throughput protocol as the other testers.

Bootstrap:
  python rns_tester.py identity   -> prints this node's RNS hash, paste to config.json
  python rns_tester.py server     -> start announcing and accepting links
  python rns_tester.py client ct152  -> run tests against ct152
"""

import sys
import time
import statistics
import os
import threading
import json
from pathlib import Path

import RNS
import RNS.vendor.umsgpack as msgpack

from common import load_config

TRANSPORT   = "reticulum"
APP_NAME    = "throughput_tester"
ASPECT      = "tester"
ID_DIR      = Path(__file__).parent / "rns_identity"
RNS_CONF    = Path(__file__).parent / "rns_config"
CHUNK       = 1024        # RNS packet payload limit


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def _load_or_create_identity() -> RNS.Identity:
    ID_DIR.mkdir(exist_ok=True)
    id_file = ID_DIR / "identity"
    if id_file.exists():
        return RNS.Identity.from_file(str(id_file))
    identity = RNS.Identity()
    identity.to_file(str(id_file))
    return identity


def print_identity():
    RNS.Reticulum(configdir=str(RNS_CONF), loglevel=RNS.LOG_WARNING)
    identity = _load_or_create_identity()
    dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                           APP_NAME, ASPECT)
    print(f"RNS destination hash: {RNS.prettyhexrep(dest.hash)}")
    print(f"Paste into config.json nodes.<this_node>.rns_hash  (no spaces)")
    print(dest.hash.hex())


# ---------------------------------------------------------------------------
# Test protocol over RNS Link using Channel
# ---------------------------------------------------------------------------

class _ServerLink:
    def __init__(self, link: RNS.Link, cfg: dict):
        self.link = link
        self.cfg = cfg
        self.channel = None
        link.set_link_established_callback(self._on_established)
        link.set_link_closed_callback(self._on_closed)

    def _on_established(self, link):
        self.channel = link.get_channel()
        self.channel.register_message_type(_TesterMsg)
        self.channel.add_message_handler(self._on_msg)
        print(f"[rns] link established from {RNS.prettyhexrep(link.hash)}")

    def _on_closed(self, link):
        print(f"[rns] link closed")

    def _on_msg(self, msg):
        if not isinstance(msg, _TesterMsg):
            return
        cmd = msg.data.get("cmd")
        if cmd == "PING":
            self.channel.send(_TesterMsg({"cmd": "PONG"}))
        elif cmd == "UPLOAD":
            pass  # server receives; client measures
        elif cmd == "UPLOAD_DONE":
            elapsed = msg.data.get("elapsed_ms", 0)
            n = msg.data.get("n", 0)
            self.channel.send(_TesterMsg({"cmd": "UPLOAD_ACK", "n": n, "elapsed_ms": elapsed}))
        elif cmd == "DOWNLOAD_REQ":
            n = msg.data.get("n", 0)
            # send in chunks
            t0 = time.monotonic()
            sent = 0
            while sent < n:
                size = min(800, n - sent)  # keep well under MDU
                self.channel.send(_TesterMsg({"cmd": "DOWNLOAD_DATA",
                                              "payload": os.urandom(size).hex(),
                                              "sent": sent + size,
                                              "total": n}))
                sent += size
            elapsed_ms = (time.monotonic() - t0) * 1000
            self.channel.send(_TesterMsg({"cmd": "DOWNLOAD_DONE",
                                          "n": n, "elapsed_ms": elapsed_ms}))
        elif cmd == "BYE":
            self.link.teardown()


class _TesterMsg(RNS.Channel.MessageBase):
    MSGTYPE = 0xA001

    def __init__(self, data: dict | None = None):
        super().__init__()
        self.data = data or {}

    def pack(self) -> bytes:
        return msgpack.packb(self.data)

    def unpack(self, raw: bytes):
        self.data = msgpack.unpackb(raw)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def server():
    cfg = load_config()
    RNS.Reticulum(configdir=str(RNS_CONF), loglevel=RNS.LOG_WARNING)
    identity = _load_or_create_identity()
    dest = RNS.Destination(identity, RNS.Destination.IN, RNS.Destination.SINGLE,
                           APP_NAME, ASPECT)
    dest.set_link_established_callback(
        lambda link: _ServerLink(link, cfg)
    )
    dest.announce()
    print(f"[rns] server announced: {RNS.prettyhexrep(dest.hash)}")
    try:
        while True:
            time.sleep(30)
            dest.announce()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def client(peer_node: str) -> dict:
    cfg = load_config()
    peer_hash_hex = cfg["nodes"][peer_node]["rns_hash"]
    if not peer_hash_hex:
        raise ValueError(f"rns_hash not set for {peer_node} in config.json")
    t = cfg["test"]
    pings = t["latency_pings"]
    runs = t["throughput_runs"]
    n_bytes = t["throughput_mb"] * 1024 * 1024

    RNS.Reticulum(configdir=str(RNS_CONF), loglevel=RNS.LOG_WARNING)
    peer_hash = bytes.fromhex(peer_hash_hex)

    if not RNS.Transport.has_path(peer_hash):
        print(f"[rns] requesting path to {peer_hash_hex[:16]}...")
        RNS.Transport.request_path(peer_hash)
        deadline = time.time() + 30
        while not RNS.Transport.has_path(peer_hash):
            if time.time() > deadline:
                raise TimeoutError("RNS path not found within 30s")
            time.sleep(0.5)

    peer_identity = RNS.Identity.recall(peer_hash)
    dest = RNS.Destination(peer_identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                           APP_NAME, ASPECT)

    # establish link
    link = RNS.Link(dest)
    link_ready = threading.Event()
    link.set_link_established_callback(lambda l: link_ready.set())
    if not link_ready.wait(timeout=30):
        raise TimeoutError("RNS link not established within 30s")

    channel = link.get_channel()
    channel.register_message_type(_TesterMsg)

    recv_event = threading.Event()
    recv_buf = []

    def on_msg(msg):
        if isinstance(msg, _TesterMsg):
            recv_buf.append(msg.data)
            recv_event.set()

    channel.add_message_handler(on_msg)

    def _wait(timeout=60) -> dict:
        recv_event.clear()
        if not recv_event.wait(timeout=timeout):
            raise TimeoutError("RNS response timeout")
        return recv_buf.pop(0)

    # --- latency ---
    rtts = []
    for _ in range(pings):
        t0 = time.monotonic()
        channel.send(_TesterMsg({"cmd": "PING"}))
        r = _wait(10)
        if r.get("cmd") != "PONG":
            raise ValueError(f"expected PONG got {r}")
        rtts.append((time.monotonic() - t0) * 1000)

    lat_avg = statistics.mean(rtts)
    lat_min = min(rtts)
    lat_max = max(rtts)
    lat_jitter = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    # --- upload (client sends, times itself, sends elapsed to server) ---
    up_speeds = []
    for _ in range(runs):
        t0 = time.monotonic()
        sent = 0
        while sent < n_bytes:
            size = min(800, n_bytes - sent)
            channel.send(_TesterMsg({"cmd": "UPLOAD",
                                     "payload": os.urandom(size).hex()}))
            sent += size
        elapsed_ms = (time.monotonic() - t0) * 1000
        channel.send(_TesterMsg({"cmd": "UPLOAD_DONE",
                                 "n": n_bytes, "elapsed_ms": elapsed_ms}))
        _wait(30)  # UPLOAD_ACK
        up_speeds.append(n_bytes * 8 / elapsed_ms / 1000)

    # --- download ---
    dn_speeds = []
    for _ in range(runs):
        channel.send(_TesterMsg({"cmd": "DOWNLOAD_REQ", "n": n_bytes}))
        received = 0
        t0 = time.monotonic()
        while True:
            r = _wait(60)
            cmd = r.get("cmd")
            if cmd == "DOWNLOAD_DATA":
                received += len(bytes.fromhex(r["payload"]))
            elif cmd == "DOWNLOAD_DONE":
                elapsed_ms = (time.monotonic() - t0) * 1000
                dn_speeds.append(n_bytes * 8 / elapsed_ms / 1000)
                break

    channel.send(_TesterMsg({"cmd": "BYE"}))
    link.teardown()

    return {
        "latency_avg_ms":    lat_avg,
        "latency_min_ms":    lat_min,
        "latency_max_ms":    lat_max,
        "latency_jitter_ms": lat_jitter,
        "upload_mbps":       statistics.mean(up_speeds),
        "download_mbps":     statistics.mean(dn_speeds),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] == "server":
        server()
    elif sys.argv[1] == "identity":
        print_identity()
    else:
        print(json.dumps(client(sys.argv[1]), indent=2))
