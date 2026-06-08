import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).parent / "config.json"
DB_PATH = Path(__file__).parent / "results.db"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


@dataclass
class TestResult:
    transport: str
    from_node: str
    to_node: str
    timestamp: float
    latency_avg_ms: float
    latency_min_ms: float
    latency_max_ms: float
    latency_jitter_ms: float
    upload_mbps: float
    download_mbps: float
    notes: str = ""


def save_result(r: TestResult):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transport TEXT, from_node TEXT, to_node TEXT,
            timestamp REAL,
            latency_avg_ms REAL, latency_min_ms REAL,
            latency_max_ms REAL, latency_jitter_ms REAL,
            upload_mbps REAL, download_mbps REAL,
            notes TEXT
        )
    """)
    d = asdict(r)
    conn.execute("""
        INSERT INTO results VALUES (
            NULL,:transport,:from_node,:to_node,:timestamp,
            :latency_avg_ms,:latency_min_ms,:latency_max_ms,:latency_jitter_ms,
            :upload_mbps,:download_mbps,:notes
        )
    """, d)
    conn.commit()
    conn.close()


def load_results(limit: int = 50) -> list[TestResult]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT transport,from_node,to_node,timestamp,"
        "latency_avg_ms,latency_min_ms,latency_max_ms,latency_jitter_ms,"
        "upload_mbps,download_mbps,notes "
        "FROM results ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    keys = ["transport","from_node","to_node","timestamp",
            "latency_avg_ms","latency_min_ms","latency_max_ms","latency_jitter_ms",
            "upload_mbps","download_mbps","notes"]
    return [TestResult(**dict(zip(keys, row))) for row in rows]


def format_table(results: list[TestResult]) -> str:
    if not results:
        return "No results stored."
    header = f"{'Time':<20} {'Transport':<12} {'From':<16} {'To':<16} {'Lat(avg)':<10} {'Up Mbps':<10} {'Dn Mbps':<10}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.timestamp))
        lines.append(
            f"{t:<20} {r.transport:<12} {r.from_node:<16} {r.to_node:<16} "
            f"{r.latency_avg_ms:<10.1f} {r.upload_mbps:<10.2f} {r.download_mbps:<10.2f}"
        )
    return "\n".join(lines)
