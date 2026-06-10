#!/usr/bin/env bash
# Daily cleanup: trim i2pd storage and results database.
set -euo pipefail

I2PD_LIB=/var/lib/i2pd
DB=/opt/throughput-tester/results.db
WARN_MB=200
LOG_TAG="thru-cleanup"

log() { logger -t "$LOG_TAG" "$*"; echo "$*"; }

# --- results.db ---
if [[ -f "$DB" ]]; then
    before=$(du -k "$DB" | cut -f1)
    /opt/throughput-tester/venv/bin/python3 - <<'PY'
import sys
sys.path.insert(0, '/opt/throughput-tester')
from common import trim_results
trim_results(days=7)
PY
    after=$(du -k "$DB" | cut -f1)
    log "results.db: ${before}K -> ${after}K (7-day trim)"
fi

# --- i2pd peerProfiles (keep 30 days) ---
if [[ -d "$I2PD_LIB/peerProfiles" ]]; then
    count=$(find "$I2PD_LIB/peerProfiles" -type f -mtime +30 | wc -l)
    find "$I2PD_LIB/peerProfiles" -type f -mtime +30 -delete
    log "peerProfiles: removed $count files older than 30 days"
fi

# --- i2pd netDb (keep 14 days) ---
if [[ -d "$I2PD_LIB/netDb" ]]; then
    count=$(find "$I2PD_LIB/netDb" -type f -mtime +14 | wc -l)
    find "$I2PD_LIB/netDb" -type f -mtime +14 -delete
    log "netDb: removed $count files older than 14 days"
fi

# --- i2pd tags (keep 7 days) ---
if [[ -d "$I2PD_LIB/tags" ]]; then
    count=$(find "$I2PD_LIB/tags" -type f -mtime +7 | wc -l)
    find "$I2PD_LIB/tags" -type f -mtime +7 -delete
    log "tags: removed $count files older than 7 days"
fi

# --- storage warning ---
total_mb=$(du -sm "$I2PD_LIB" | cut -f1)
if [[ "$total_mb" -gt "$WARN_MB" ]]; then
    log "WARNING: $I2PD_LIB is ${total_mb}MB (threshold ${WARN_MB}MB)"
fi

log "cleanup done. i2pd lib: ${total_mb}MB"
