"""
I2P throughput tester via SAMv3 streaming API (i2pd SAM bridge on port 7656).

Bootstrap sequence (run once on each node before testing):
  python i2p_tester.py keygen
  -> prints this node's I2P destination (base64), paste into config.json i2p_dest

Server mode holds the SAM session open and accepts streams.
Client mode opens a new SAM session per test run and connects by destination.
"""

import socket
import time
import statistics
import os
import sys
import json
from pathlib import Path
from common import load_config

TRANSPORT = "i2p"
KEY_FILE = Path(__file__).parent / "i2p_keys" / "tester.keys"
CHUNK = 65536

# 1-hop tunnels: much higher build success rate on firewalled/container nodes.
# Anonymity not needed for benchmarking.
# Server uses more inbound tunnels to reduce the chance of a rebuild cycle leaving
# the server unreachable (with 30% success rate, 3 tunnels → 34% chance all fail;
# 8 tunnels → 5.7% chance all fail).
CLIENT_TUNNEL_OPTS = "inbound.length=1 outbound.length=1 inbound.quantity=3 outbound.quantity=3"
SERVER_TUNNEL_OPTS = "inbound.length=1 outbound.length=1 inbound.quantity=8 outbound.quantity=3"
TUNNEL_OPTS = CLIENT_TUNNEL_OPTS  # kept for backwards compat; client() uses CLIENT_TUNNEL_OPTS

# Pre-created client SAM session — populated by init_i2p(), reused by client().
_cli_ctrl: "socket.socket | None" = None
_CLI_SID = f"thru-cli-{os.getpid()}"


# ---------------------------------------------------------------------------
# Minimal synchronous SAMv3 helper
# ---------------------------------------------------------------------------

class SAMError(Exception):
    pass


def _sam_readline(s: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        c = s.recv(1)
        if not c:
            raise SAMError("SAM socket closed")
        buf += c
    return buf.decode().strip()


def _sam_parse(line: str) -> dict:
    parts = line.split()
    result = {"_cmd": " ".join(parts[:2])}
    for p in parts[2:]:
        if "=" in p:
            k, v = p.split("=", 1)
            result[k] = v
    return result


def _sam_hello(s: socket.socket):
    s.sendall(b"HELLO VERSION MIN=3.1 MAX=3.3\n")
    r = _sam_parse(_sam_readline(s))
    if r.get("RESULT") != "OK":
        raise SAMError(f"HELLO failed: {r}")


def _open_sam(sam_host: str, sam_port: int, timeout: int = 120) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((sam_host, sam_port))
    _sam_hello(s)
    return s


def _sam_session_create(s: socket.socket, style: str, session_id: str,
                        dest: str = "TRANSIENT", extra: str = "") -> str:
    msg = f"SESSION CREATE STYLE={style} ID={session_id} DESTINATION={dest} {extra}\n"
    s.sendall(msg.encode())
    r = _sam_parse(_sam_readline(s))
    if r.get("RESULT") != "OK":
        raise SAMError(f"SESSION CREATE failed: {r}")
    return r.get("DESTINATION", "")


def _sam_stream_connect(s: socket.socket, session_id: str, dest: str):
    s.sendall(f"STREAM CONNECT ID={session_id} DESTINATION={dest} SILENT=false\n".encode())
    r = _sam_parse(_sam_readline(s))
    if r.get("RESULT") != "OK":
        raise SAMError(f"STREAM CONNECT failed: {r}")


def _sam_stream_accept(s: socket.socket, session_id: str):
    """Send STREAM ACCEPT and read the STATUS reply. Socket then blocks until a
    remote connects, at which point SAM sends the remote destination line and the
    socket becomes the live stream. Caller reads the destination line next."""
    s.sendall(f"STREAM ACCEPT ID={session_id} SILENT=false\n".encode())
    r = _sam_parse(_sam_readline(s))
    if r.get("RESULT") != "OK":
        raise SAMError(f"STREAM ACCEPT failed: {r}")


def _sam_naming_lookup(s: socket.socket, name: str) -> str:
    s.sendall(f"NAMING LOOKUP NAME={name}\n".encode())
    r = _sam_parse(_sam_readline(s))
    if r.get("RESULT") != "OK":
        raise SAMError(f"NAMING LOOKUP failed: {r}")
    return r["VALUE"]


# ---------------------------------------------------------------------------
# Key generation / persistence
# ---------------------------------------------------------------------------

def generate_or_load_keys(sam_host: str, sam_port: int) -> tuple[str, str]:
    """Return (private_key_b64, destination_b64). Creates KEY_FILE if absent."""
    KEY_FILE.parent.mkdir(exist_ok=True)
    if KEY_FILE.exists():
        data = json.loads(KEY_FILE.read_text())
        return data["privkey"], data["destination"]

    s = _open_sam(sam_host, sam_port)
    s.sendall(b"DEST GENERATE SIGNATURE_TYPE=EdDSA_SHA512_Ed25519\n")
    r = _sam_parse(_sam_readline(s))
    privkey = r["PRIV"]
    pubdest = r["PUB"]
    s.close()
    KEY_FILE.write_text(json.dumps({"privkey": privkey, "destination": pubdest}))
    print(f"Generated I2P destination:\n{pubdest}")
    print(f"Paste into config.json nodes.<this_node>.i2p_dest")
    return privkey, pubdest


# ---------------------------------------------------------------------------
# Test wire protocol (same line-based protocol as socket_tester)
# ---------------------------------------------------------------------------

def _recv_line_sock(s: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        c = s.recv(1)
        if not c:
            raise ConnectionError("closed mid-line")
        buf += c
    return buf.decode().strip()


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(min(CHUNK, n - len(buf)))
        if not chunk:
            raise ConnectionError("closed mid-data")
        buf += chunk
    return buf


def _handle_session(conn: socket.socket):
    while True:
        cmd = _recv_line_sock(conn)
        if cmd == "PING":
            conn.sendall(b"PONG\n")
        elif cmd.startswith("UPLOAD "):
            n = int(cmd.split()[1])
            t0 = time.monotonic()
            _recv_exact(conn, n)
            elapsed_ms = (time.monotonic() - t0) * 1000
            conn.sendall(f"DONE {n} {elapsed_ms:.3f}\n".encode())
        elif cmd.startswith("DOWNLOAD "):
            n = int(cmd.split()[1])
            data = os.urandom(min(CHUNK, n))
            sent = 0
            while sent < n:
                block = data[:n - sent] if n - sent < len(data) else data
                conn.sendall(block)
                sent += len(block)
            _recv_line_sock(conn)  # ACK
        elif cmd == "BYE":
            break
        else:
            break


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def server():
    cfg = load_config()
    sam_host = "127.0.0.1"
    sam_port = cfg["ports"]["i2p_sam"]

    privkey, dest = generate_or_load_keys(sam_host, sam_port)
    svr_sid = f"thru-svr-{os.getpid()}"

    # Retry SESSION CREATE until i2pd has built enough tunnels.
    # On a freshly booted node this takes 60-120s; on a warmed node it's instant.
    # Creating this session also warms i2pd's tunnel pool so client() is fast.
    attempt = 0
    while True:
        attempt += 1
        try:
            ctrl = _open_sam(sam_host, sam_port, timeout=300)
            _sam_session_create(ctrl, "STREAM", svr_sid, privkey, extra=SERVER_TUNNEL_OPTS)
            print(f"[i2p] server session ready after {attempt} attempt(s)", flush=True)
            break
        except Exception as e:
            print(f"[i2p] session attempt {attempt} failed ({e}) — retrying in 30s", flush=True)
            time.sleep(30)

    # ctrl kept alive as local var for the lifetime of this function

    while True:
        # New SAM socket for each incoming stream (SAMv3: session ≠ stream socket)
        acc = _open_sam(sam_host, sam_port)
        try:
            _sam_stream_accept(acc, svr_sid)
            # SAM sends the remote destination line when a client connects
            remote = _recv_line_sock(acc)
            print(f"[i2p] accepted stream from {remote[:32]}...")
            _handle_session(acc)
        except Exception as e:
            print(f"[i2p] session error: {e}")
        finally:
            acc.close()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

def init_i2p():
    """Pre-create a TRANSIENT client SAM session with short tunnels.
    Call once from the main thread before running tests so client() only
    needs STREAM CONNECT (no per-test SESSION CREATE delay)."""
    global _cli_ctrl
    cfg = load_config()
    sam_host = "127.0.0.1"
    sam_port = cfg["ports"]["i2p_sam"]
    attempt = 0
    while True:
        attempt += 1
        try:
            ctrl = _open_sam(sam_host, sam_port, timeout=300)
            _sam_session_create(ctrl, "STREAM", _CLI_SID, "TRANSIENT",
                                extra=CLIENT_TUNNEL_OPTS)
            _cli_ctrl = ctrl
            print(f"[i2p] client session ready after {attempt} attempt(s)", flush=True)
            break
        except Exception as e:
            print(f"[i2p] client session attempt {attempt} failed ({e}) — retrying in 30s",
                  flush=True)
            time.sleep(30)


def client(peer_node: str) -> dict:
    cfg = load_config()
    peer_dest = cfg["nodes"][peer_node]["i2p_dest"]
    if not peer_dest:
        raise ValueError(f"i2p_dest not set for {peer_node} in config.json")
    sam_host = "127.0.0.1"
    sam_port = cfg["ports"]["i2p_sam"]
    t = cfg["test"]
    pings = t["latency_pings"]
    runs = t["throughput_runs"]
    n_bytes = t["throughput_mb"] * 1024 * 1024

    if _cli_ctrl is not None:
        # Reuse pre-created session — no SESSION CREATE delay.
        ctrl = _cli_ctrl
        session_id = _CLI_SID
        close_ctrl = False
    else:
        # Fall back: create on demand with short tunnels.
        privkey, _ = generate_or_load_keys(sam_host, sam_port)
        session_id = f"thru-cli-{os.getpid()}"
        ctrl = _open_sam(sam_host, sam_port, timeout=300)
        _sam_session_create(ctrl, "STREAM", session_id, "TRANSIENT",
                            extra=CLIENT_TUNNEL_OPTS)
        close_ctrl = True

    # Stream socket for the actual test data.
    # Retry up to 3 times on transient I2P failures (CANT_REACH_PEER, closed connection).
    MAX_CONNECT_ATTEMPTS = 3
    sock = None
    for attempt in range(1, MAX_CONNECT_ATTEMPTS + 1):
        try:
            s = _open_sam(sam_host, sam_port)
            _sam_stream_connect(s, session_id, peer_dest)
            sock = s
            break
        except (SAMError, ConnectionError, OSError) as e:
            print(f"[i2p] connect attempt {attempt}/{MAX_CONNECT_ATTEMPTS} failed: {e}", flush=True)
            try:
                s.close()
            except Exception:
                pass
            if attempt < MAX_CONNECT_ATTEMPTS:
                time.sleep(5)
    if sock is None:
        if close_ctrl:
            ctrl.close()
        raise ConnectionError(f"I2P STREAM CONNECT failed after {MAX_CONNECT_ATTEMPTS} attempts")

    # latency
    rtts = []
    for _ in range(pings):
        t0 = time.monotonic()
        sock.sendall(b"PING\n")
        resp = _recv_line_sock(sock)
        if resp != "PONG":
            raise ValueError(f"expected PONG got {resp!r}")
        rtts.append((time.monotonic() - t0) * 1000)

    lat_avg = statistics.mean(rtts)
    lat_min = min(rtts)
    lat_max = max(rtts)
    lat_jitter = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    # upload
    up_speeds = []
    payload = os.urandom(n_bytes)
    for _ in range(runs):
        t0 = time.monotonic()
        sock.sendall(f"UPLOAD {n_bytes}\n".encode())
        sock.sendall(payload)
        _recv_line_sock(sock)
        elapsed_ms = (time.monotonic() - t0) * 1000
        up_speeds.append(n_bytes * 8 / elapsed_ms / 1000)

    # download
    dn_speeds = []
    for _ in range(runs):
        sock.sendall(f"DOWNLOAD {n_bytes}\n".encode())
        t0 = time.monotonic()
        _recv_exact(sock, n_bytes)
        elapsed_ms = (time.monotonic() - t0) * 1000
        sock.sendall(f"ACK {n_bytes}\n".encode())
        dn_speeds.append(n_bytes * 8 / elapsed_ms / 1000)

    sock.sendall(b"BYE\n")
    sock.close()
    if close_ctrl:
        ctrl.close()

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
    elif sys.argv[1] == "keygen":
        cfg = load_config()
        generate_or_load_keys("127.0.0.1", cfg["ports"]["i2p_sam"])
    else:
        print(json.dumps(client(sys.argv[1]), indent=2))
