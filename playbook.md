# Throughput Tester — Playbook

Tests upload speed, download speed, and latency across four transport layers
(TCP baseline, Yggdrasil, I2P, Reticulum) between any two of three nodes.

> **Note:** CT numbers, server names, hostnames, and LAN IP addresses in this
> playbook are specific to this deployment. On a different system these will
> all be different — only the software setup, service names, and port
> assignments are transferable.

## Node Inventory

| CT    | Label          | Proxmox host           | LAN IP            |
|-------|----------------|------------------------|-------------------|
| ct107 | ct107 on x12-2 | x12-2 @ 192.168.8.60   | 192.168.8.107     |
| ct152 | ct152 on x11-3 | x11-3 @ 192.168.8.40   | 192.168.8.152     |
| ct166 | ct166 on x12-4 | x12-4 @ 192.168.177.20 | 192.168.177.166   |

Donor template: **ct150** on x11-3 (192.168.8.40)
- 1 vCPU, 512 MB RAM, 8 GB disk, unprivileged, has TUN device, Yggdrasil pre-installed

ct107 and ct152 share the `192.168.8.0/24` LAN. ct166 is on a separate
Proxmox host (`192.168.177.0/24`) and requires WireGuard for initial
provisioning; after that it is reachable via Yggdrasil and I2P.

**ct152 is the designated autotest node.** In addition to the server listeners
it runs the web dashboard and the 30-minute autotest timer (see Phase 6).

## Test Parameters

| Parameter       | Value  |
|-----------------|--------|
| Latency pings   | 10 RTT |
| Throughput runs | 3      |
| Data per run    | 2 MB   |
| TCP server port | 9901   |
| Ygg server port | 9902   |
| I2P SAM port    | 7656   |
| RNS server port | 9903   |

---

## Phase 1 — Provision CTs

### 1.1  Create ct152 on x11-3 (local clone, same host as donor)

```bash
ssh root@192.168.8.40
pct clone 150 152 --hostname thru-test --storage local --full
pct set 152 --memory 512 --cores 1
pct set 152 --net0 name=eth0,bridge=vmbr0,gw=192.168.8.1,ip=192.168.8.152/24,type=veth
pct start 152
# push desktop SSH key (run from desktop, not proxmox)
ssh-copy-id -i ~/.ssh/id_ed25519.pub root@192.168.8.152
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
git clone https://github.com/fotografm/throughput-tester /opt/throughput-tester
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
# Set this_node correctly on each CT after copying.
```

---

## Phase 4 — Enable Server Service (all nodes)

```bash
# On each CT:
cp /opt/throughput-tester/throughput-tester.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now throughput-tester
systemctl status throughput-tester
```

---

## Phase 5 — Enable Cleanup Timer (all nodes)

The daily cleanup timer trims `results.db` to 7 days and purges old i2pd
routing data that can otherwise grow to 20 GB+ and fill the container disk.

```bash
# On each CT:
cp /opt/throughput-tester/throughput-tester-cleanup.service /etc/systemd/system/
cp /opt/throughput-tester/throughput-tester-cleanup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now throughput-tester-cleanup.timer

# Verify it runs cleanly:
systemctl start throughput-tester-cleanup.service
journalctl -u throughput-tester-cleanup --no-pager -n 20
```

The timer fires daily at 03:15 UTC. What it cleans:

| Target | Retention |
|--------|-----------|
| `results.db` rows | 7 days |
| `/var/lib/i2pd/peerProfiles/` | 30 days |
| `/var/lib/i2pd/netDb/` | 14 days |
| `/var/lib/i2pd/tags/` | 7 days |

If `/var/lib/i2pd` exceeds 200 MB after cleanup a warning is written to the
journal under the `thru-cleanup` syslog tag:
```bash
journalctl -t thru-cleanup --since yesterday
```

Note: i2pd log rotation is handled separately by logrotate (daily, 5 rotations,
compressed), which is installed and configured by the i2pd package.

---

## Phase 6 — Web Dashboard and Autotest (ct152 only)

ct152 serves as the central monitoring node. It runs a Flask web dashboard
on port 80 and a systemd timer that fires every 30 minutes to run the full
test suite against ct166 and store results.

### 6.1  Enable the web dashboard

```bash
# On ct152:
cp /opt/throughput-tester/throughput-tester-web.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now throughput-tester-web
```

The dashboard is accessible at `http://<ct152-ip>/`. It shows rolling 24-hour
graphs for latency, upload, and download per transport pair, and provides
on-demand test buttons.

### 6.2  Enable the autotest timer

```bash
# On ct152:
cp /opt/throughput-tester/throughput-tester-autotest.service /etc/systemd/system/
cp /opt/throughput-tester/throughput-tester-autotest.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now throughput-tester-autotest.timer
```

The timer fires 5 minutes after boot and then every 30 minutes thereafter.
Each run executes:
```
thru test --to ct166 --transport ygg,rns,i2p
```

Output is appended to `/var/log/throughput-tester-autotest.log`.

### 6.3  I2P warmup behaviour during autotest

I2P LeaseSet entries expire every 10 minutes. If the autotest timer fires at
the exact moment the remote node's LeaseSet has expired, the SAM
`STREAM CONNECT` returns `CANT_REACH_PEER`, causing the retry logic to kick in
and inflating the measured latency by tens of seconds.

To prevent this, `runner.py test` calls `warmup_i2p()` before starting any
timed measurement. This opens a throwaway SAM stream to the target, sends a
PING, and closes it. The LeaseSet fetch and tunnel build happen during this
warmup rather than during the measurement, so the recorded latency reflects the
actual tunnel quality and not the lookup overhead. The warmup output is visible
in the autotest log:

```
[i2p] warmup: probing ct166...
[i2p] warmup OK in 4823 ms (attempt 1)
```

If all three warmup attempts fail (e.g. i2pd just restarted), the test proceeds
anyway — the failure is already evident in the results.

---

## Phase 7 — Run Tests

```bash
# From any CT, test against a specific peer:
thru test --to ct107 --transport all
thru test --to ct166 --transport ygg,rns,i2p
thru test --to ct152 --transport tcp,ygg

# Show last 20 stored results:
thru results

# Show last 50:
thru results --last 50
```

---

## Deactivating WireGuard After Provisioning

Once ct166 is reachable via Yggdrasil and I2P, the WireGuard VPN used
for initial provisioning can be deactivated:
```bash
# On the desktop or wherever WG is running:
wg-quick down <interface>
systemctl disable wg-quick@<interface>
```

WireGuard must be brought back up any time you need to SSH directly to ct166
for maintenance (e.g. `scp` files, emergency restarts).

---

## Ports Reference

| Transport  | Port  | Binds to                | Protocol |
|------------|-------|-------------------------|----------|
| TCP        | 9901  | LAN IP                  | TCP      |
| Yggdrasil  | 9902  | Yggdrasil IPv6 address  | TCP      |
| I2P        | 7656  | 127.0.0.1 (SAM bridge)  | TCP→I2P  |
| Reticulum  | 9903  | configured in rns_config | RNS     |
| Web UI     | 80    | 0.0.0.0 (ct152 only)    | HTTP     |

---

## Adding a New Node

1. Clone this repo to `/opt/throughput-tester` on the new CT
2. Run `bash install.sh <new_node_id>`
3. Add the new node's block to `config.json` on all existing nodes
4. Collect ygg_addr, rns_hash, i2p_dest from the new node
5. Distribute updated config.json everywhere (remember to set `this_node` on each)
6. Enable `throughput-tester.service` and `throughput-tester-cleanup.timer`
