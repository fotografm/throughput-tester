"""
Shared TCP socket test logic used by both tcp_tester and ygg_tester.
Server: bind to bind_addr:port and handle incoming test sessions.
Client: connect to peer_addr:port and run the test suite.

Wire protocol (line-oriented text commands, binary data inline):
  PING\n          -> PONG\n                    (repeated latency_pings times)
  UPLOAD <n>\n    -> client sends n raw bytes  -> server replies DONE <n> <ms>\n
  DOWNLOAD <n>\n  -> server sends n raw bytes  -> client replies DONE <n> <ms>\n
  BYE\n           -> server closes
"""

import socket
import time
import statistics
import os

CHUNK = 65536


def _recv_line(sock: socket.socket) -> str:
    buf = b""
    while not buf.endswith(b"\n"):
        c = sock.recv(1)
        if not c:
            raise ConnectionError("socket closed mid-line")
        buf += c
    return buf.decode().strip()


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(min(CHUNK, n - len(buf)))
        if not chunk:
            raise ConnectionError("socket closed mid-data")
        buf += chunk
    return buf


def run_server(bind_addr: str, port: int, transport_label: str):
    srv = socket.socket(
        socket.AF_INET6 if ":" in bind_addr else socket.AF_INET,
        socket.SOCK_STREAM
    )
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((bind_addr, port))
    srv.listen(5)
    print(f"[{transport_label}] listening on [{bind_addr}]:{port}")
    while True:
        conn, addr = srv.accept()
        print(f"[{transport_label}] connection from {addr}")
        try:
            _handle_session(conn, transport_label)
        except Exception as e:
            print(f"[{transport_label}] session error: {e}")
        finally:
            conn.close()


def _handle_session(conn: socket.socket, label: str):
    while True:
        cmd = _recv_line(conn)
        if cmd == "PING":
            conn.sendall(b"PONG\n")
        elif cmd.startswith("UPLOAD "):
            n = int(cmd.split()[1])
            t0 = time.monotonic()
            _recv_exact(conn, n)
            elapsed_ms = (time.monotonic() - t0) * 1000
            conn.sendall(f"DONE {n} {elapsed_ms:.3f}\n".encode())
            print(f"[{label}] upload  {n/1e6:.1f} MB in {elapsed_ms:.0f} ms "
                  f"= {n*8/elapsed_ms/1000:.2f} Mbps")
        elif cmd.startswith("DOWNLOAD "):
            n = int(cmd.split()[1])
            t0 = time.monotonic()
            sent = 0
            data = os.urandom(min(CHUNK, n))
            while sent < n:
                block = data[:n - sent] if n - sent < len(data) else data
                conn.sendall(block)
                sent += len(block)
            elapsed_ms = (time.monotonic() - t0) * 1000
            ack = _recv_line(conn)
            print(f"[{label}] download {n/1e6:.1f} MB in {elapsed_ms:.0f} ms "
                  f"= {n*8/elapsed_ms/1000:.2f} Mbps  ack={ack}")
        elif cmd == "BYE":
            break
        else:
            print(f"[{label}] unknown command: {cmd!r}")
            break


def run_client(peer_addr: str, port: int, transport_label: str,
               pings: int, runs: int, mb: int) -> dict:
    n_bytes = mb * 1024 * 1024
    sock = socket.socket(
        socket.AF_INET6 if ":" in peer_addr else socket.AF_INET,
        socket.SOCK_STREAM
    )
    sock.settimeout(120)
    sock.connect((peer_addr, port))

    # --- latency ---
    rtts = []
    for _ in range(pings):
        t0 = time.monotonic()
        sock.sendall(b"PING\n")
        resp = _recv_line(sock)
        if resp != "PONG":
            raise ValueError(f"expected PONG got {resp!r}")
        rtts.append((time.monotonic() - t0) * 1000)

    lat_avg = statistics.mean(rtts)
    lat_min = min(rtts)
    lat_max = max(rtts)
    lat_jitter = statistics.stdev(rtts) if len(rtts) > 1 else 0.0

    # --- upload ---
    up_speeds = []
    payload = os.urandom(n_bytes)
    for _ in range(runs):
        t0 = time.monotonic()
        sock.sendall(f"UPLOAD {n_bytes}\n".encode())
        sock.sendall(payload)
        resp = _recv_line(sock)
        elapsed_ms = (time.monotonic() - t0) * 1000
        up_speeds.append(n_bytes * 8 / elapsed_ms / 1000)

    # --- download ---
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

    return {
        "latency_avg_ms":    lat_avg,
        "latency_min_ms":    lat_min,
        "latency_max_ms":    lat_max,
        "latency_jitter_ms": lat_jitter,
        "upload_mbps":       statistics.mean(up_speeds),
        "download_mbps":     statistics.mean(dn_speeds),
    }
