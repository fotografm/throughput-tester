#!/usr/bin/env bash
# Run as root on each throughput-tester CT after cloning.
# Usage: bash install.sh <node-id>   e.g.  bash install.sh ct107
set -euo pipefail

NODE=${1:?"Usage: $0 <node-id>  (ct107|ct152|ct166)"}

echo "=== throughput-tester install for $NODE ==="

# ---------- strip donor services ----------
systemctl disable --now mumble-server 2>/dev/null || true
apt-get remove -y mumble-server 2>/dev/null || true
systemctl disable --now tailscaled 2>/dev/null || true
apt-get remove -y tailscale 2>/dev/null || true

# ---------- regenerate Yggdrasil identity ----------
systemctl stop yggdrasil
rm -f /etc/yggdrasil/yggdrasil.conf
yggdrasil -genconf > /etc/yggdrasil/yggdrasil.conf
# Restore public peers from ct150 template
python3 - <<'PY'
import json, re, subprocess, sys

conf_path = "/etc/yggdrasil/yggdrasil.conf"
with open(conf_path) as f:
    conf = f.read()

PEERS = [
    "tls://london.sabretruth.org:18472",
    "tls://yggdrasil.neilalexander.dev:64648?key=ecbbcb3298e7d3b4196103333c3e839cfe47a6ca47602b94a6d596683f6bb358",
    "quic://ygg1.mk16.de:1339?key=0000000087ee9949eeab56bd430ee8f324cad55abf3993ed9b9be63ce693e18a",
]
peer_str = "\n    ".join(PEERS)
conf = re.sub(r'Peers: \[.*?\]', f'Peers: [\n    {peer_str}\n  ]', conf, flags=re.DOTALL)
with open(conf_path, "w") as f:
    f.write(conf)
print("Yggdrasil config updated with peers.")
PY
systemctl enable --now yggdrasil
echo "Waiting for Yggdrasil IPv6..."
sleep 5
YGG_ADDR=$(ip -6 addr show dev tun0 2>/dev/null | awk '/inet6 2[0-9a-f]{2}:/{print $2}' | cut -d/ -f1)
echo "Yggdrasil address: $YGG_ADDR"

# ---------- install i2pd ----------
apt-get update -qq
apt-get install -y i2pd curl

# Enable SAM bridge
if ! grep -q "^\[sam\]" /etc/i2pd/i2pd.conf 2>/dev/null; then
    cat >> /etc/i2pd/i2pd.conf <<'I2PD'

[sam]
enabled = true
address = 127.0.0.1
port = 7656
I2PD
fi
systemctl enable --now i2pd
echo "i2pd installed and running."

# ---------- install Python deps ----------
apt-get install -y python3-pip python3-venv
PROJ_DIR=/opt/throughput-tester
cd "$PROJ_DIR"
python3 -m venv venv
venv/bin/pip install -q --upgrade pip
venv/bin/pip install -q -r requirements.txt
echo "Python dependencies installed."

# ---------- set this_node in config.json ----------
python3 - "$NODE" <<'PY'
import json, sys
path = "/opt/throughput-tester/config.json"
with open(path) as f:
    cfg = json.load(f)
cfg["this_node"] = sys.argv[1]
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print(f"config.json: this_node = {sys.argv[1]}")
PY

# ---------- print addresses to put in config.json ----------
echo ""
echo "=== ADDRESSES FOR config.json ==="
echo "Node: $NODE"
echo "ygg_addr: $YGG_ADDR"
echo ""
echo "Next steps:"
echo "  1. Run: cd /opt/throughput-tester && venv/bin/python rns_tester.py identity"
echo "     -> paste rns_hash into config.json on all nodes"
echo "  2. Run: venv/bin/python i2p_tester.py keygen"
echo "     -> paste i2p_dest into config.json on all nodes"
echo "  3. Distribute updated config.json to all three CTs"
echo "  4. Start servers: venv/bin/python runner.py server"
echo "  5. Run tests:     venv/bin/python runner.py test --to ct152 --transport all"
