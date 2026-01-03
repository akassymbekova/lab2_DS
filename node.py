#!/usr/bin/env python3
"""
Lab 2 Starter Node (3-node ready)
Lamport Clock + Replicated Key–Value Store (LWW)

Endpoints:
  POST /put        {"key": "...", "value": ...}
  GET  /get?key=...
  POST /replicate  {"key":"...", "value":..., "ts": <lamport>, "origin":"A"}
  GET  /status
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request, parse
import argparse
import json
import threading
import time
import random
from typing import Dict, Any, Tuple, List

lock = threading.Lock()

LAMPORT = 0
STORE: Dict[str, Tuple[Any, int, str]] = {}  # key -> (value, ts, origin)
NODE_ID = ""
PEERS: List[str] = []  # base URLs, e.g. http://10.0.1.12:8000

DELAY_RULES = {
    ("A", "http://172.31.19.171:8002"): 2.0
}

SEND_COUNTER: Dict[str, int] = {}  # peer_url -> count (for Scenario A reorder)


def lamport_tick_local() -> int:
    """Increment Lamport clock for a local event and return new value."""
    global LAMPORT
    with lock:
        LAMPORT += 1
        return LAMPORT


def lamport_on_receive(received_ts: int) -> int:
    """On receive: L = max(L, received_ts) + 1. Return new value."""
    global LAMPORT
    with lock:
        LAMPORT = max(LAMPORT, received_ts) + 1
        return LAMPORT


def get_lamport() -> int:
    """Return current Lamport clock value."""
    with lock:
        return LAMPORT


def apply_lww(key: str, value: Any, ts: int, origin: str) -> bool:
    """
    Apply Last-Writer-Wins update using Lamport timestamp.
    Tie-breaker: origin lexicographic. Returns True if applied.
    """
    with lock:
        cur = STORE.get(key)
        if cur is None:
            STORE[key] = (value, ts, origin)
            return True
        _, cur_ts, cur_origin = cur
        if ts > cur_ts or (ts == cur_ts and origin > cur_origin):
            STORE[key] = (value, ts, origin)
            return True
        return False


def replicate_to_peers(key: str, value: Any, ts: int, origin: str, retries: int = 2, timeout_s: float = 2.0) -> None:
    """
    Send update to all peers via POST /replicate.
    Where to add code:
      - Artificial delay to one peer (Scenario A)
      - Exponential backoff (optional)
      - Drop simulation (optional)
    """
    payload = json.dumps({"key": key, "value": value, "ts": ts, "origin": origin}).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    for peer in PEERS:
        url = peer.rstrip("/") + "/replicate"

        delay_s = DELAY_RULES.get((NODE_ID, peer), 0.0)
        if delay_s > 0:
            # Scenario A: create reorder by delaying every OTHER message to this peer.
            with lock:
                SEND_COUNTER[peer] = SEND_COUNTER.get(peer, 0) + 1
                n = SEND_COUNTER[peer]
            if n % 2 == 1:
                print(f"[{NODE_ID}] DELAY to {peer} for {delay_s}s (msg #{n})")
                time.sleep(delay_s)

        for attempt in range(retries + 1):
            try:
                req = request.Request(url, data=payload, headers=headers, method="POST")
                with request.urlopen(req, timeout=timeout_s) as resp:
                    _ = resp.read()
                break
            except Exception as e:
                if attempt == retries:
                    print(f"[{NODE_ID}] WARN replicate failed to {url}: {e}")
                else:
                    # Exponential backoff + jitter
                    backoff = min(2.0, 0.2 * (2 ** attempt))
                    jitter = random.uniform(0.0, 0.1)
                    time.sleep(backoff + jitter)


def sync_from_peers(timeout_s: float = 2.0) -> None:
    """Scenario C helper: pull /status from peers and merge into local STORE using LWW."""
    for peer in PEERS:
        url = peer.rstrip("/") + "/status"
        try:
            with request.urlopen(url, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            store = data.get("store", {})
            # store is expected: key -> {"value":..., "ts":..., "origin":...}
            for k, meta in store.items():
                try:
                    ts = int(meta.get("ts", 0))
                    origin = str(meta.get("origin", ""))
                    v = meta.get("value", None)
                except Exception:
                    continue

                if not k or ts <= 0 or not origin:
                    continue

                # Treat as receiving a message carrying 'ts'
                lamport_on_receive(ts)
                applied = apply_lww(k, v, ts, origin)
                if applied:
                    print(f"[{NODE_ID}] SYNC applied key={k} ts={ts} origin={origin} from {peer}")
        except Exception as e:
            print(f"[{NODE_ID}] SYNC warn cannot reach {url}: {e}")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: Dict[str, Any]) -> None:
        """Serialize obj as JSON and send to client."""
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        """Handle GET /get?key=... and GET /status."""
        if self.path.startswith("/get"):
            qs = parse.urlparse(self.path).query
            params = parse.parse_qs(qs)
            key = params.get("key", [""])[0]
            with lock:
                cur = STORE.get(key)
            if cur is None:
                self._send(404, {"ok": False, "error": "key not found", "key": key, "lamport": get_lamport()})
            else:
                value, ts, origin = cur
                self._send(200, {"ok": True, "key": key, "value": value, "ts": ts, "origin": origin, "lamport": get_lamport()})
            return

        if self.path.startswith("/status"):
            with lock:
                snapshot = {k: {"value": v, "ts": ts, "origin": o} for k, (v, ts, o) in STORE.items()}
            self._send(200, {"ok": True, "node": NODE_ID, "lamport": get_lamport(), "peers": PEERS, "store": snapshot})
            return

        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        """Handle POST /put and POST /replicate."""
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send(400, {"ok": False, "error": "invalid json"})
            return

        if self.path == "/put":
            key = str(body.get("key", ""))
            value = body.get("value", None)
            if not key:
                self._send(400, {"ok": False, "error": "key required"})
                return

            ts = lamport_tick_local()
            applied = apply_lww(key, value, ts, NODE_ID)
            print(f"[{NODE_ID}] PUT key={key} value={value} lamport={ts} applied={applied}")

            t = threading.Thread(target=replicate_to_peers, args=(key, value, ts, NODE_ID), daemon=True)
            t.start()

            self._send(200, {"ok": True, "node": NODE_ID, "key": key, "value": value, "ts": ts, "applied": applied, "lamport": get_lamport()})
            return

        if self.path == "/replicate":
            key = str(body.get("key", ""))
            value = body.get("value", None)
            ts = int(body.get("ts", 0))
            origin = str(body.get("origin", ""))
            if not key or not origin or ts <= 0:
                self._send(400, {"ok": False, "error": "key, origin, ts required"})
                return

            new_clock = lamport_on_receive(ts)
            applied = apply_lww(key, value, ts, origin)
            print(f"[{NODE_ID}] RECV replicate key={key} value={value} ts={ts} origin={origin} -> lamport={new_clock} applied={applied}")

            # Optional: vector clocks would go here.

            self._send(200, {"ok": True, "node": NODE_ID, "lamport": get_lamport(), "applied": applied})
            return

        self._send(404, {"ok": False, "error": "not found"})

    def log_message(self, fmt, *args):
        return


def main():
    """Parse CLI args, set NODE_ID/PEERS, start HTTP server."""
    global NODE_ID, PEERS, LAMPORT
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="Node ID: A, B, or C")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--peers", default="", help="Comma-separated base URLs of peers")
    args = parser.parse_args()

    NODE_ID = args.id
    PEERS = [p.strip() for p in args.peers.split(",") if p.strip()]
    LAMPORT = 0

    # Scenario A (deterministic): delay A -> C (peer ending with :8002) by 2 seconds.
    # Adjust the ":8002" matcher if your C uses a different port.
    if NODE_ID == "A":
        for p in PEERS:
            if p.endswith(":8002"):
                DELAY_RULES[(NODE_ID, p)] = 2.0

    # Scenario C: initial catch-up sync on startup + periodic anti-entropy sync.
    sync_from_peers()

    def _periodic_sync():
        while True:
            time.sleep(3.0)
            sync_from_peers()

    threading.Thread(target=_periodic_sync, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[{NODE_ID}] listening on {args.host}:{args.port} peers={PEERS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
