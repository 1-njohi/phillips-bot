#!/usr/bin/env python3
"""
snipe.py — Manual-trigger auction monitor + bidder.

Reads at the maximum rate the server allows. Writes only when the
observed state demands it. Computes its hedge window from live
latencies rather than a fixed schedule.

Operator supplies one or two vehicles. If a second vehicle is supplied,
both run concurrently in their own threads.

Usage (two vehicles):
    python snipe.py \
        --armed-bid-id1 12 --vehicle-id1 5 \
        --product1 56438 --slug1 kdk-156x-mercedes-benz-actros --max1 1500000 \
        --armed-bid-id2 13 --vehicle-id2 6 \
        --product2 56441 --slug2 kds-974y-isuzu-frr90         --max2 800000 \
        --nonce 86d2bd16fe \
        --cookie "wordpress_logged_in_xxx=...; woocommerce_cart_hash=..." \
        --db-path database/database.sqlite

Usage (one vehicle):
    python snipe.py \
        --armed-bid-id1 12 --vehicle-id1 5 \
        --product1 56438 --slug1 kdk-156x-mercedes-benz-actros --max1 1500000 \
        --nonce 86d2bd16fe \
        --cookie "wordpress_logged_in_xxx=...; woocommerce_cart_hash=..." \
        --db-path database/database.sqlite

Reads (anonymous):
    GET /wp-json/wc/store/v1/products/{id}
    GET /product/{slug}/
    HEAD /

Writes (authenticated):
    POST /wp-admin/admin-ajax.php   (yith_wcact_add_bid)

Writes to the Laravel SQLite DB:
  - inserts into `bids` after every bid POST (accepted or rejected)
  - updates `armed_bids.status` and `armed_bids.our_bid_count`
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx


# ─────────────────────────────────────────────────────────────────
# SQLite writer
# ─────────────────────────────────────────────────────────────────

class Db:
    """
    Thin SQLite writer. Shared across vehicle threads, guarded by a
    mutex. WAL mode so Laravel can read concurrently while the sniper
    writes.
    """

    def __init__(self, db_path: Path, log: logging.Logger):
        self.log = log
        self.lock = threading.Lock()
        try:
            self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=3000")
        except sqlite3.Error as e:
            self.log.error("db connect failed (%s): %s", db_path, e)
            self.conn = None

    def record_bid_and_update_armed(
        self,
        vehicle_id: int,
        armed_bid_id: int,
        amount: int,
        response_json: dict,
        success: bool,
    ) -> None:
        if self.conn is None:
            return
        with self.lock:
            try:
                self.conn.execute(
                    """
                    INSERT INTO bids
                      (vehicle_id, armed_bid_id, amount, fired_at,
                       response_json, success, created_at)
                    VALUES (?, ?, ?, datetime('now'), ?, ?, datetime('now'))
                    """,
                    (
                        vehicle_id,
                        armed_bid_id,
                        amount,
                        json.dumps(response_json),
                        1 if success else 0,
                    ),
                )
                self.conn.execute(
                    """
                    UPDATE armed_bids
                    SET our_bid_count = our_bid_count + 1,
                        last_bid = ?,
                        last_bid_at = datetime('now'),
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (amount, armed_bid_id),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                self.log.warning("record_bid_and_update_armed failed: %s", e)

    def set_armed_status(self, armed_bid_id: int, status: str) -> None:
        if self.conn is None:
            return
        with self.lock:
            try:
                self.conn.execute(
                    """
                    UPDATE armed_bids
                    SET status = ?, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (status, armed_bid_id),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                self.log.warning("set_armed_status failed: %s", e)

    def close(self) -> None:
        if self.conn:
            self.conn.close()


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

@dataclass
class VehicleTarget:
    product_id: int = 0        # wp_product_id
    slug: str = ""
    max_bid: int = 0
    vehicle_id: int = 0        # local vehicles.id
    armed_bid_id: int = 0      # local armed_bids.id

    @property
    def label(self) -> str:
        return f"[{self.product_id}]"


@dataclass
class Config:
    base_url: str = "http://localhost:9000"
    vehicles: list[VehicleTarget] = field(default_factory=list)
    increment: int = 5000
    nonce: str = ""
    cookie: str = ""

    # Read cadence
    poll_quiet_ms: int = 200       # 5 Hz
    poll_contest_ms: int = 50      # 20 Hz

    # Contest window: how long after a price change to stay hot
    contest_window_s: float = 30.0

    # Tail safety margin, ms, added on top of measured latencies
    tail_safety_ms: int = 150

    # Minimum proactive tail, ms. Even if latencies measure to 0,
    # keep at least this much tail for the endgame hedge.
    tail_min_ms: int = 300

    # Latency measurement
    latency_window: int = 50       # rolling samples

    # Logging cadence for the latency summary
    summary_every_s: float = 5.0

    # Guardrails
    max_bids_per_vehicle: int = 200
    min_seconds_between_bids: float = 0.0

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )
    log_dir: Path = field(default_factory=lambda: Path("logs"))

    db_path: Path = field(default_factory=lambda: Path("database/database.sqlite"))


# ─────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────

def setup_logger(cfg: Config) -> logging.Logger:
    cfg.log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    ids = "-".join(str(v.product_id) for v in cfg.vehicles)
    logfile = cfg.log_dir / f"snipe-{ids}-{ts}.log"

    fmt = "%(asctime)s %(levelname)-7s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(logfile),
        ],
    )
    log = logging.getLogger("snipe")
    log.info("log file: %s", logfile)
    return log


# ─────────────────────────────────────────────────────────────────
# Latency tracking
# ─────────────────────────────────────────────────────────────────

class Latencies:
    """
    Rolling window of latency samples for one vehicle's thread. Also
    holds the most recent propagation sample (time from bid POST
    returning success to the Store API reflecting the new price).
    """

    def __init__(self, window: int):
        self.window = window
        self.reads: list[float] = []
        self.writes: list[float] = []
        self.propagations: list[float] = []

    def add_read(self, seconds: float) -> None:
        self.reads.append(seconds)
        if len(self.reads) > self.window:
            self.reads.pop(0)

    def add_write(self, seconds: float) -> None:
        self.writes.append(seconds)
        if len(self.writes) > self.window:
            self.writes.pop(0)

    def add_propagation(self, seconds: float) -> None:
        self.propagations.append(seconds)
        if len(self.propagations) > self.window:
            self.propagations.pop(0)

    @staticmethod
    def _p(samples: list[float], pct: float) -> float:
        if not samples:
            return 0.0
        s = sorted(samples)
        idx = min(len(s) - 1, int(len(s) * pct))
        return s[idx]

    def p50(self, samples: list[float]) -> float:
        return self._p(samples, 0.50)

    def p95(self, samples: list[float]) -> float:
        return self._p(samples, 0.95)


# ─────────────────────────────────────────────────────────────────
# HTTP clients
# ─────────────────────────────────────────────────────────────────

class PhillipsClient:
    def __init__(self, cfg: Config, log: logging.Logger):
        self.cfg = cfg
        self.log = log

        # Anonymous reader. Public endpoints only. No cookies.
        self.http = httpx.Client(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={
                "User-Agent": cfg.user_agent,
                "Accept": "application/json, text/html, */*",
                "Accept-Language": "en-KE,en;q=0.9",
            },
            follow_redirects=False,
            http2=True,
        )

        # Authenticated writer. Reserved for the bid POST.
        self.http_auth = httpx.Client(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={
                "User-Agent": cfg.user_agent,
                "Cookie": cfg.cookie,
                "Accept": "*/*",
                "X-Requested-With": "XMLHttpRequest",
            },
            follow_redirects=False,
            http2=True,
        )

        self._clock_offset: float = 0.0
        self._offset_samples: list[float] = []
        self._clock_lock = threading.Lock()

    def sync_clock(self) -> None:
        t0 = time.time()
        try:
            r = self.http.head("/")
        except httpx.HTTPError as e:
            self.log.warning("clock sync failed: %s", e)
            return
        t1 = time.time()

        date_hdr = r.headers.get("date")
        if not date_hdr:
            return

        try:
            server_dt = datetime.strptime(
                date_hdr, "%a, %d %b %Y %H:%M:%S %Z"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            return

        server_ts = server_dt.timestamp()
        rtt = t1 - t0
        estimated_server_now = server_ts + rtt / 2
        sample = estimated_server_now - t1

        with self._clock_lock:
            self._offset_samples.append(sample)
            self._offset_samples = self._offset_samples[-5:]
            self._offset_samples.sort()
            self._clock_offset = self._offset_samples[len(self._offset_samples) // 2]
            offset_snapshot = self._clock_offset

        self.log.info(
            "clock offset: %+.3fs  (rtt=%.0fms, samples=%d)",
            offset_snapshot,
            rtt * 1000,
            len(self._offset_samples),
        )

    @property
    def clock_offset(self) -> float:
        with self._clock_lock:
            return self._clock_offset

    def server_now(self) -> float:
        return time.time() + self.clock_offset

    def fetch_finish_time(self, slug: str) -> Optional[int]:
        try:
            r = self.http.get(f"/product/{slug}/")
        except httpx.HTTPError as e:
            self.log.error("product page fetch failed (%s): %s", slug, e)
            return None

        if r.status_code != 200:
            self.log.error("product page status %s (%s)", r.status_code, slug)
            return None

        m = re.search(r'data-finish-time="(\d+)"', r.text)
        if not m:
            self.log.error("data-finish-time not found in HTML (%s)", slug)
            return None

        return int(m.group(1))

    def read_store_timed(
        self, product_id: int, lat: Optional[Latencies] = None
    ) -> Optional[dict]:
        t0 = time.perf_counter()
        try:
            r = self.http.get(f"/wp-json/wc/store/v1/products/{product_id}")
        except httpx.HTTPError as e:
            self.log.warning("store read failed (%d): %s", product_id, e)
            if lat:
                lat.add_read(time.perf_counter() - t0)
            return None
        t1 = time.perf_counter()
        if lat:
            lat.add_read(t1 - t0)

        if r.status_code != 200:
            self.log.warning("store read status %s (%d)", r.status_code, product_id)
            return None

        try:
            return r.json()
        except json.JSONDecodeError:
            self.log.warning("store read: invalid JSON (%d)", product_id)
            return None

    def read_price_timed(
        self, product_id: int, lat: Optional[Latencies] = None
    ) -> Optional[int]:
        data = self.read_store_timed(product_id, lat)
        if not data:
            return None
        price_str = data.get("prices", {}).get("price")
        if price_str is None:
            return None
        try:
            return int(price_str)
        except (ValueError, TypeError):
            return None

    def read_state_timed(
        self, product_id: int, lat: Optional[Latencies] = None
    ) -> Optional[str]:
        data = self.read_store_timed(product_id, lat)
        if not data:
            return None
        return data.get("add_to_cart", {}).get("text")

    def close(self) -> None:
        self.http.close()
        self.http_auth.close()


# ─────────────────────────────────────────────────────────────────
# Bid POST — timed, records bid + armed_bids in one transaction
# ─────────────────────────────────────────────────────────────────

def fire_bid(
    client: PhillipsClient,
    cfg: Config,
    vehicle: VehicleTarget,
    amount: int,
    lat: Optional[Latencies] = None,
    db: Optional[Db] = None,
) -> tuple[Optional[dict], float]:
    """
    Fire one bid POST. Returns (parsed_response_or_None, elapsed_seconds).
    Always records the attempt to the DB.
    """
    body = {
        "action":   "yith_wcact_add_bid",
        "security": cfg.nonce,
        "currency": "KES",
        "bid":      str(amount),
        "product":  str(vehicle.product_id),
    }
    headers = {
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          f"{cfg.base_url}/product/{vehicle.slug}/",
    }

    t0 = time.perf_counter()
    try:
        r = client.http_auth.post(
            "/wp-admin/admin-ajax",
            data=body,
            headers=headers,
        )
    except httpx.HTTPError as e:
        elapsed = time.perf_counter() - t0
        if lat:
            lat.add_write(elapsed)
        client.log.error(
            "%s bid POST transport error (%.3fs): %s",
            vehicle.label, elapsed, e,
        )
        return None, elapsed

    elapsed = time.perf_counter() - t0
    if lat:
        lat.add_write(elapsed)

    try:
        resp = r.json()
    except json.JSONDecodeError:
        client.log.warning(
            "%s bid POST non-JSON response (HTTP %s, %.3fs): %.200s",
            vehicle.label, r.status_code, elapsed, r.text,
        )
        resp = None

    if db is not None and vehicle.vehicle_id and vehicle.armed_bid_id:
        db.record_bid_and_update_armed(
            vehicle_id=vehicle.vehicle_id,
            armed_bid_id=vehicle.armed_bid_id,
            amount=amount,
            response_json=resp if resp is not None else {"raw": r.text[:500]},
            success=bool(resp.get("success")) if resp else False,
        )

    return resp, elapsed


# ─────────────────────────────────────────────────────────────────
# Endgame loop
# ─────────────────────────────────────────────────────────────────

def run_endgame(
    client: PhillipsClient,
    cfg: Config,
    vehicle: VehicleTarget,
    finish_ts: int,
    log: logging.Logger,
    db: Optional[Db] = None,
) -> None:
    """
    The core loop.

    Modes:
      quiet    — poll slowly, fire only on strict outbid.
      contest  — poll fast, fire on strict outbid.
      tail     — poll fast, fire on every tick (subject to max).

    Tail length is computed live:
      tail_ms = p95(read) + p95(write) + p95(propagation) + safety
    and floored at tail_min_ms.
    """
    lat = Latencies(window=cfg.latency_window)

    last_bid = 0
    last_bid_at = 0.0
    last_price = None
    last_price_change_at = 0.0
    bid_count = 0
    last_summary_at = 0.0
    pending_propagation_at: Optional[float] = None
    pending_propagation_amount: Optional[int] = None

    log.info("%s === endgame begins ===", vehicle.label)

    while client.server_now() < finish_ts:
        now = client.server_now()
        remaining = finish_ts - now

        # ── Read current price and state ─────────────────────────
        current = client.read_price_timed(vehicle.product_id, lat)
        state = client.read_state_timed(vehicle.product_id, lat)

        if state is None or current is None:
            time.sleep(cfg.poll_quiet_ms / 1000.0)
            continue

        if state != "Bid now":
            log.info("%s auction closed (state=%s)", vehicle.label, state)
            break

        # ── Check propagation of a prior bid ─────────────────────
        if pending_propagation_at is not None and current >= pending_propagation_amount:
            prop = now - pending_propagation_at
            lat.add_propagation(prop)
            log.info(
                "%s propagation %.3fs (bid=%s now_visible)",
                vehicle.label, prop, f"{pending_propagation_amount:,}",
            )
            pending_propagation_at = None
            pending_propagation_amount = None

        # ── Detect price change ──────────────────────────────────
        if current != last_price:
            log.info(
                "%s price %s (my_last=%s, bids=%d, rem=%.1fs)",
                vehicle.label,
                f"{current:,}",
                f"{last_bid:,}" if last_bid else "—",
                bid_count,
                remaining,
            )
            last_price = current
            last_price_change_at = now

        # ── Compute tail ─────────────────────────────────────────
        p95_read = lat.p95(lat.reads)
        p95_write = lat.p95(lat.writes)
        p95_prop = lat.p95(lat.propagations)
        safety = cfg.tail_safety_ms / 1000.0
        tail = max(
            cfg.tail_min_ms / 1000.0,
            p95_read + p95_write + p95_prop + safety,
        )

        # ── Decide mode ──────────────────────────────────────────
        if remaining <= tail:
            mode = "tail"
        elif now - last_price_change_at <= cfg.contest_window_s:
            mode = "contest"
        else:
            mode = "quiet"

        # ── Decide whether to fire ───────────────────────────────
        should_fire = False
        reason = ""

        if current >= vehicle.max_bid:
            # Priced out; nothing to do
            break

        if mode == "tail":
            # Proactive: keep a fresh bid in flight
            if current + cfg.increment > last_bid:
                should_fire = True
                reason = "tail"
        else:
            # Reactive: only fire on strict outbid
            if current > last_bid:
                should_fire = True
                reason = mode

        # ── Fire, if warranted ───────────────────────────────────
        if should_fire:
            amount = min(current + cfg.increment, vehicle.max_bid)

            if amount <= last_bid:
                # Already at or above this amount; nothing new to say
                should_fire = False

        if should_fire:
            if bid_count >= cfg.max_bids_per_vehicle:
                log.warning(
                    "%s bid cap reached (%d) — giving up",
                    vehicle.label, bid_count,
                )
                break

            since_last = now - last_bid_at
            if (
                cfg.min_seconds_between_bids > 0
                and last_bid_at
                and since_last < cfg.min_seconds_between_bids
            ):
                time.sleep(cfg.poll_contest_ms / 1000.0)
                continue

            resp, elapsed = fire_bid(client, cfg, vehicle, amount, lat, db)
            bid_count += 1
            last_bid_at = client.server_now()

            if resp is None:
                log.warning(
                    "%s fire %s (%s) → no response (%.3fs)",
                    vehicle.label, f"{amount:,}", reason, elapsed,
                )
            else:
                success = bool(resp.get("success", False))
                log.info(
                    "%s fire %s (%s) → success=%s (%.3fs)",
                    vehicle.label, f"{amount:,}", reason, success, elapsed,
                )
                if success:
                    last_bid = amount
                    pending_propagation_at = client.server_now()
                    pending_propagation_amount = amount

        # ── Periodic summary ─────────────────────────────────────
        if now - last_summary_at >= cfg.summary_every_s:
            last_summary_at = now
            log.info(
                "%s summary: mode=%s tail=%.0fms rem=%.1fs "
                "reads=%d p50=%.0fms p95=%.0fms "
                "writes=%d p50=%.0fms p95=%.0fms "
                "prop=%d p50=%.0fms p95=%.0fms "
                "bids=%d last=%s current=%s",
                vehicle.label, mode, tail * 1000, remaining,
                len(lat.reads),
                lat.p50(lat.reads) * 1000, lat.p95(lat.reads) * 1000,
                len(lat.writes),
                lat.p50(lat.writes) * 1000, lat.p95(lat.writes) * 1000,
                len(lat.propagations),
                lat.p50(lat.propagations) * 1000,
                lat.p95(lat.propagations) * 1000,
                bid_count,
                f"{last_bid:,}" if last_bid else "—",
                f"{current:,}" if current else "—",
            )

        # ── Sleep according to mode ──────────────────────────────
        if mode in ("contest", "tail"):
            time.sleep(cfg.poll_contest_ms / 1000.0)
        else:
            time.sleep(cfg.poll_quiet_ms / 1000.0)

    log.info(
        "%s endgame done: bids=%d last_bid=%s",
        vehicle.label, bid_count,
        f"{last_bid:,}" if last_bid else "—",
    )


# ─────────────────────────────────────────────────────────────────
# Per-vehicle thread worker
# ─────────────────────────────────────────────────────────────────

def vehicle_worker(
    client: PhillipsClient,
    cfg: Config,
    vehicle: VehicleTarget,
    log: logging.Logger,
    db: Optional[Db] = None,
) -> None:
    log.info("%s starting  max=%s", vehicle.label, f"{vehicle.max_bid:,}")

    try:
        finish_ts = client.fetch_finish_time(vehicle.slug)
        if finish_ts is None:
            log.error("%s could not determine finish time", vehicle.label)
            if db and vehicle.armed_bid_id:
                db.set_armed_status(vehicle.armed_bid_id, "errored")
            return

        remaining = finish_ts - client.server_now()
        log.info(
            "%s finish_time=%d  remaining=%.0fs",
            vehicle.label, finish_ts, remaining,
        )

        state = client.read_state_timed(vehicle.product_id)
        if state is None:
            log.error("%s store read failed — cookie may be invalid", vehicle.label)
            if db and vehicle.armed_bid_id:
                db.set_armed_status(vehicle.armed_bid_id, "errored")
            return
        log.info("%s initial state=%s", vehicle.label, state)

        price = client.read_price_timed(vehicle.product_id)
        log.info(
            "%s initial price=%s",
            vehicle.label,
            f"{price:,}" if price else "—",
        )

        if db and vehicle.armed_bid_id:
            db.set_armed_status(vehicle.armed_bid_id, "firing")

        # One more clock sync before the endgame.
        client.sync_clock()

        run_endgame(client, cfg, vehicle, finish_ts, log, db=db)

        if db and vehicle.armed_bid_id:
            db.set_armed_status(vehicle.armed_bid_id, "ended")

    except Exception as e:  # noqa: BLE001
        log.exception("%s worker crashed: %s", vehicle.label, e)
        if db and vehicle.armed_bid_id:
            db.set_armed_status(vehicle.armed_bid_id, "errored")


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def parse_args() -> Config:
    p = argparse.ArgumentParser()

    # Vehicle 1 is always required.
    p.add_argument("--armed-bid-id1", type=int, required=True)
    p.add_argument("--vehicle-id1",    type=int, required=True)
    p.add_argument("--product1", type=int, required=True)
    p.add_argument("--slug1", type=str, required=True)
    p.add_argument("--max1", type=int, required=True)

    # Vehicle 2 is optional. If any of these is provided, all must be.
    p.add_argument("--armed-bid-id2", type=int, default=None)
    p.add_argument("--vehicle-id2",    type=int, default=None)
    p.add_argument("--product2", type=int, default=None)
    p.add_argument("--slug2", type=str, default=None)
    p.add_argument("--max2", type=int, default=None)

    p.add_argument("--nonce", type=str, required=True)
    p.add_argument("--cookie", type=str, required=True)
    p.add_argument("--db-path", type=str, default="database/database.sqlite")

    # Strategy tuning. Defaults match the Config dataclass, so passing
    # nothing here keeps the current behavior.
    p.add_argument("--base-url",         type=str, default="http://localhost:9000")
    p.add_argument("--increment",        type=int, default=5000)
    p.add_argument("--poll-quiet-ms",    type=int, default=200)
    p.add_argument("--poll-contest-ms",  type=int, default=50)
    p.add_argument("--contest-window-s", type=int, default=30)
    p.add_argument("--tail-safety-ms",   type=int, default=150)
    p.add_argument("--tail-min-ms",      type=int, default=300)

    a = p.parse_args()

    if a.max1 % a.increment != 0:
        sys.exit(f"max1 must be a multiple of {a.increment}")

    vehicles = [
        VehicleTarget(
            product_id=a.product1,
            slug=a.slug1,
            max_bid=a.max1,
            vehicle_id=a.vehicle_id1,
            armed_bid_id=a.armed_bid_id1,
        ),
    ]

    provided_second = [a.armed_bid_id2, a.vehicle_id2, a.product2, a.slug2, a.max2]
    if any(v is not None for v in provided_second):
        if not all(v is not None for v in provided_second):
            sys.exit(
                "--armed-bid-id2, --vehicle-id2, --product2, --slug2 and --max2 "
                "must be provided together"
            )

        if a.product2 == a.product1:
            sys.exit("product2 must differ from product1")

        if a.max2 % a.increment != 0:
            sys.exit(f"max2 must be a multiple of {a.increment}")

        vehicles.append(
            VehicleTarget(
                product_id=a.product2,
                slug=a.slug2,
                max_bid=a.max2,
                vehicle_id=a.vehicle_id2,
                armed_bid_id=a.armed_bid_id2,
            )
        )

    return Config(
        base_url=a.base_url,
        increment=a.increment,
        vehicles=vehicles,
        nonce=a.nonce,
        cookie=a.cookie,
        db_path=Path(a.db_path),
        poll_quiet_ms=a.poll_quiet_ms,
        poll_contest_ms=a.poll_contest_ms,
        contest_window_s=float(a.contest_window_s),
        tail_safety_ms=a.tail_safety_ms,
        tail_min_ms=a.tail_min_ms,
    )

def main() -> None:
    cfg = parse_args()
    log = setup_logger(cfg)

    for v in cfg.vehicles:
        log.info(
            "target: product=%d  vehicle_id=%d  armed_bid_id=%d  max=%s",
            v.product_id, v.vehicle_id, v.armed_bid_id, f"{v.max_bid:,}",
        )

    db = Db(cfg.db_path, log)
    client = PhillipsClient(cfg, log)

    try:
        client.sync_clock()
        client.sync_clock()

        threads: list[threading.Thread] = []
        for v in cfg.vehicles:
            t = threading.Thread(
                target=vehicle_worker,
                args=(client, cfg, v, log, db),
                name=f"vehicle-{v.product_id}",
                daemon=False,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

    except KeyboardInterrupt:
        log.info("interrupted by user")
    finally:
        client.close()
        db.close()

    log.info("done")


if __name__ == "__main__":
    main()