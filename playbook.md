# Throughput Tester — Playbook

Tests upload speed, download speed, and latency across four transport layers
(TCP baseline, Yggdrasil, I2P, Reticulum) between any two of three nodes.

## Node Inventory

| CT    | Label          | Proxmox host        | LAN IP            |
|-------|----------------|---------------------|-------------------|
| ct107 | ct107 on x12-2 | x12-2 @ 192.168.8.60  | 192.168.8.107   |
| ct152 | ct152 on x11-3 | x11-3 @ 192.168.8.40  | 192.168.8.152   |
| ct166 | ct166 on x12-4 | x12-4 @ 192.168.177.20 | 192.168.177.166 |

Donor template: **ct150** on x11-3 (192.168.8.40)
- 1 vCPU, 512 MB RAM, 8 GB disk, unprivileged, has TUN device, Yggdrasil pre-installed

## Test Parameters

| Parameter       | Value  |
|-----------------|--------|
| Latency pings   | 10 RTT |
| Throughput runs | 3      |
| Data per run    | 2 MB   |
| TCP server port | 9901   |
| Ygg server port | 9902   |
| I2P SAM port    | 7656   |

---

## Phase 1 — Provision CTs

### 1.1  Create ct152 on x11-3 (local clone, same host as donor)

```bash
ssh root@192.168.8.40
pct clone 150 152 --hostname thru-test --storage local --full
pct set 152 --memory 512 --cores 1
pct set 152 --net0 name=eth0,bridge=vmbr0,gw=192.168.8.1,ip=192.168.8.152/24,type=veth
pct start 152
sleep 5
# push desktop SSH key
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.8.152  # from desktop, not proxmox
```

### 1.2  Create ct107 on x12-2 (cross-host: vzdump on x11-3, restore on x12-2)

```bash
# On x11-3: dump ct150
ssh root@192.168.8.40 "vzdump 150 --mode stop --compress zstd --dumpdir /var/lib/vz/dump"

# Find the dump file name
DUMP=$(ssh root@192.168.8.40 "ls -t /var/lib/vz/dump/vzdump-lxc-150-*.tar.zst | head -1")

# Transfer to x12-2
ssh root@192.168.8.40 "scp $DUMP root@192.168.8.60:/var/lib/vz/dump/"

# Restore as ct107 on x12-2
ssh root@192.168.8.60 "pct restore 107 /var/lib/vz/dump/$(basename $DUMP) \
  --storage local --hostname thru-test"
ssh root@192.168.8.60 "pct set 107 --memory 512 --cores 1"
ssh root@192.168.8.60 "pct set 107 \
  --net0 name=eth0,bridge=vmbr0,gw=192.168.8.1,ip=192.168.8.107/24,type=veth"
ssh root@192.168.8.60 "pct start 107"
```

Push desktop SSH key to ct107:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.8.107
```

### 1.3  Create ct166 on x12-4 (remote, via WireGuard)

WireGuard to 192.168.177.20 must be active before this step.

```bash
# Transfer dump to x12-4
DUMP=$(ssh root@192.168.8.40 "ls -t /var/lib/vz/dump/vzdump-lxc-150-*.tar.zst | head -1")
ssh root@192.168.8.40 "scp $DUMP root@192.168.177.20:/var/lib/vz/dump/"

ssh root@192.168.177.20 "pct restore 166 /var/lib/vz/dump/$(basename $DUMP) \
  --storage local --hostname thru-test"
ssh root@192.168.177.20 "pct set 166 --memory 512 --cores 1"
ssh root@192.168.177.20 "pct set 166 \
  --net0 name=eth0,bridge=vmbr0,gw=192.168.177.1,ip=192.168.177.166/24,type=veth"
ssh root@192.168.177.20 "pct start 166"
```

Push desktop SSH key to ct166:
```bash
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.177.166
```

---

## Phase 2 — Install Software on Each CT

Clone the repo and run the install script on each CT.

```bash
# On each CT (replace NODE with ct107 / ct152 / ct166):
apt-get install -y git
git clone https://github.com/YOUR_USERNAME/throughput-tester /opt/throughput-tester
cd /opt/throughput-tester
bash install.sh NODE
```

`install.sh` does:
- Removes Mumble server and Tailscale from the clone
- Regenerates Yggdrasil identity (new keypair → new IPv6 address)
- Installs i2pd with SAM bridge enabled
- Creates Python venv and installs `rns`
- Sets `this_node` in config.json
- Prints the node's Yggdrasil IPv6 address

---

## Phase 3 — Bootstrap Endpoint Identifiers

Each overlay protocol generates its own address at first run.
Collect these and distribute a complete config.json to all three CTs.

### 3.1  Yggdrasil addresses

After `install.sh` runs, note the printed `ygg_addr` on each CT, or:
```bash
ip -6 addr show dev tun0 | awk '/inet6 2[0-9a-f]{2}:/{print $2}' | cut -d/ -f1
```

### 3.2  RNS destination hashes

```bash
# On each CT:
cd /opt/throughput-tester
venv/bin/python rns_tester.py identity
# Prints a 64-char hex hash — paste into config.json nodes.<node>.rns_hash
```

### 3.3  I2P destinations

i2pd needs ~5 minutes to build tunnels after first start. Then:
```bash
# On each CT:
cd /opt/throughput-tester
venv/bin/python i2p_tester.py keygen
# Prints a long base64 string — paste into config.json nodes.<node>.i2p_dest
# Keys are saved to i2p_keys/tester.keys (gitignored)
```

### 3.4  Distribute completed config.json

Once you have all six values (ygg_addr × 3, rns_hash × 3, i2p_dest × 3),
edit config.json on one machine and copy to the others:
```bash
scp /opt/throughput-tester/config.json root@192.168.8.107:/opt/throughput-tester/
scp /opt/throughput-tester/config.json root@192.168.8.152:/opt/throughput-tester/
scp /opt/throughput-tester/config.json root@192.168.177.166:/opt/throughput-tester/
# Remember to set this_node correctly on each CT after copying.
```

---

## Phase 4 — Enable Server as a Systemd Service

```bash
# On each CT:
cp /opt/throughput-tester/throughput-tester.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now throughput-tester
systemctl status throughput-tester
```

---

## Phase 5 — Run Tests

```bash
# From any CT, test against a specific peer:
cd /opt/throughput-tester
venv/bin/python runner.py test --to ct152 --transport all
venv/bin/python runner.py test --to ct166 --transport ygg,rns
venv/bin/python runner.py test --to ct107 --transport tcp

# Show last 20 stored results:
venv/bin/python runner.py results

# Run only certain transports:
venv/bin/python runner.py test --to ct152 --transport tcp,ygg
```

---

## Deactivate WireGuard (after ct166 is up)

Once ct166 is reachable via Yggdrasil and I2P, the WireGuard VPN used
for initial provisioning can be deactivated:
```bash
# On the desktop or wherever WG is running:
wg-quick down <interface>
systemctl disable wg-quick@<interface>
```

---

## Ports Reference

| Transport  | Port  | Binds to               | Protocol |
|------------|-------|------------------------|----------|
| TCP        | 9901  | LAN IP (e.g. .107)     | TCP      |
| Yggdrasil  | 9902  | Yggdrasil IPv6 address | TCP      |
| I2P        | 7656  | 127.0.0.1 (SAM bridge) | TCP→I2P  |
| Reticulum  | 9903  | configured in rns_config| RNS     |

---

## Adding a New Site

1. Clone this repo to `/opt/throughput-tester`
2. Run `bash install.sh <new_node_id>`
3. Add the new node's block to `config.json` on all existing nodes
4. Collect ygg_addr, rns_hash, i2p_dest from the new node
5. Distribute updated config.json everywhere
6. Enable the systemd service

---

## Future: Web UI

Results are stored in `results.db` (SQLite). The schema:
```
results(id, transport, from_node, to_node, timestamp,
        latency_avg_ms, latency_min_ms, latency_max_ms, latency_jitter_ms,
        upload_mbps, download_mbps, notes)
```

A Flask/FastAPI web layer can query this directly. The `runner.py test` command
can be triggered via HTTP POST for on-demand testing from a browser.
Scheduled tests (via cron) will accumulate data for time-series graphs.
