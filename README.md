# Throughput Tester

Measures upload speed, download speed, and latency across four independent
transport layers between a set of always-on nodes. Each transport is tested
in isolation so results are directly comparable.

## Transports

| Transport | How it works | What it measures |
|-----------|-------------|-----------------|
| **TCP** | Raw TCP socket on LAN IP | Baseline — bare metal throughput |
| **Yggdrasil** | TCP socket over Yggdrasil IPv6 address | Overlay network overhead |
| **I2P** | SAMv3 streaming via local i2pd daemon | Anonymising darknet layer |
| **Reticulum (RNS)** | RNS Link + Channel API over direct TCP | Reticulum protocol stack |

RNS uses direct LAN TCP between local nodes and public relay nodes
(reticulum.me, betweentheborders, dismail, beleth, styrene) for
cross-site connectivity. This keeps the Reticulum measurement
independent of the Yggdrasil measurement.

## Nodes

| CT | Label | Proxmox host | LAN IP |
|----|-------|-------------|--------|
| ct107 | ct107 on x12-2 | x12-2 @ 192.168.8.60 | 192.168.8.107 |
| ct152 | ct152 on x11-3 | x11-3 @ 192.168.8.40 | 192.168.8.152 |
| ct166 | ct166 on x12-4 | x12-4 @ 192.168.177.20 | 192.168.177.166 |

All three are Debian 12 LXC containers cloned from ct150. Each runs:
- **yggdrasil** — connected to the global Yggdrasil mesh
- **i2pd** — integrated I2P node (also carries transit tunnels for others)
- **throughput-tester.service** — this project's server + RNS mesh node

All services are enabled and survive reboot. The three RNS nodes form a
complete mesh via the public relay backbone and announce to each other
continuously.

## Measurements

Each test run collects, per transport:

- **Latency** — 10 ping-pong round trips; reports avg / min / max / jitter (ms)
- **Upload** — 3 × 2 MB from client to server; average Mbps reported
- **Download** — 3 × 2 MB from server to client; average Mbps reported

Results are stored in `results.db` (SQLite) for historical comparison.

## Usage

The `thru` wrapper script is installed at `/usr/local/bin/thru` on each CT
so you can run from any working directory.

```bash
# Test against a specific peer using all transports
thru test --to ct107 --transport all

# Test specific transports only
thru test --to ct152 --transport tcp,ygg,rns

# Show stored results (last 20 by default)
thru results

# Show more history
thru results --last 50
```

**Transport choices:** `tcp` `ygg` `i2p` `rns` `all`  
**Node choices:** `ct107` `ct152` `ct166`

Output example:
```
Testing  ct152 on x11-3  →  ct107 on x12-2
Transports: tcp, ygg, i2p, rns

--- TCP ---
  Latency:  avg 0.3 ms  min 0.2  max 0.5  jitter 0.1 ms
  Upload:   898.22 Mbps
  Download: 422.20 Mbps

--- I2P ---
  TIMEOUT after 180s — skipping

--- RNS ---
  Latency:  avg 1.2 ms  min 1.0  max 1.5  jitter 0.2 ms
  Upload:   92.00 Mbps
  Download: 105.19 Mbps

=== History (ct152 on x11-3 ↔ ct107 on x12-2) ===
...
```

A per-transport timeout (default 180s, set `transport_timeout_s` in
`config.json`) ensures a slow or unreachable transport does not block
the rest of the test.

## Configuration

`config.json` is **not in git** (it contains node-specific endpoint
addresses — Yggdrasil IPs, I2P destinations, RNS hashes). Use
`config.template.json` as the starting point for a new deployment.

Key fields:

```json
{
  "this_node": "ct107",
  "nodes": { ... },
  "ports": {
    "tcp_server": 9901,
    "ygg_server": 9902,
    "rns_port":   9903,
    "i2p_sam":    7656
  },
  "test": {
    "latency_pings":       10,
    "throughput_runs":      3,
    "throughput_mb":        2,
    "transport_timeout_s": 180
  }
}
```

## Architecture

```
runner.py server          ← systemd service, always running
  ├── tcp_tester.server()    binds LAN IP:9901
  ├── ygg_tester.server()    binds Yggdrasil IPv6:9902
  ├── i2p_tester.server()    SAMv3 session via i2pd:7656
  └── rns_tester.server()    RNS destination, TCP server on LAN IP:9903
                             + connections to public RNS relay nodes

runner.py test --to <node> --transport <...>
  └── for each transport:
        run client in thread with timeout
        print results
        save to results.db
        show history for this pair
```

The server and test client share the same RNS instance via
`share_instance = Yes` in `rns_config/config`. The RNS config sets
`enable_transport = True` making each node a full routing participant
in the Reticulum mesh.

## Project files

| File | Purpose |
|------|---------|
| `runner.py` | Unified CLI — `server` / `test` / `results` |
| `tcp_tester.py` | TCP baseline test |
| `ygg_tester.py` | Yggdrasil test |
| `i2p_tester.py` | I2P SAMv3 test |
| `rns_tester.py` | Reticulum RNS test |
| `socket_tester.py` | Shared TCP test logic (used by tcp + ygg) |
| `common.py` | Config loading, SQLite result storage, table formatting |
| `config.template.json` | Template config with blank endpoint addresses |
| `config.json` | Live config with real addresses — **gitignored** |
| `install.sh` | Provisioning script for new CTs |
| `throughput-tester.service` | systemd unit file |
| `playbook.md` | Full step-by-step deployment guide |
| `results.db` | SQLite results database — **gitignored** |

## Deployment (new site)

See `playbook.md` for the full procedure. In brief:

1. Clone a Debian 12 CT from ct150, set IP/hostname
2. `apt install git && git clone https://github.com/fotografm/throughput-tester /opt/throughput-tester`
3. `bash /opt/throughput-tester/install.sh <node-id>`
4. Collect Yggdrasil address, RNS hash, I2P destination from this node
5. Add the new node's block to `config.json` on all existing nodes
6. Copy the updated `config.json` to the new node (set `this_node` correctly)
7. `cp throughput-tester.service /etc/systemd/system/ && systemctl enable --now throughput-tester`

## I2P status

I2P requires the local i2pd node to have sufficient integration into the
I2P network to build reliable tunnels. Fresh nodes start with a low tunnel
success rate (~25%) that improves to 70–90% over several days of continuous
operation. Until then, the I2P test will timeout (cleanly skipped) and
recover automatically once integration improves.

## Future

Results are stored in `results.db` with a simple schema. A web interface
with per-pair graphs and on-demand test buttons can be added on top without
changes to the measurement layer.

```sql
CREATE TABLE results (
    id INTEGER PRIMARY KEY,
    transport TEXT, from_node TEXT, to_node TEXT,
    timestamp REAL,
    latency_avg_ms REAL, latency_min_ms REAL,
    latency_max_ms REAL, latency_jitter_ms REAL,
    upload_mbps REAL, download_mbps REAL,
    notes TEXT
);
```
