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
    base_url: str = "https://phillipsauctioneers.co.ke"
    vehicles: list[VehicleTarget] = field(default_factory=list)
    increment: int = 5000

    # Auth. Populated by parse_args; nonce is overwritten at runtime
    # by PhillipsClient.refresh_nonce().
    username: str = ""
    password: str = ""
    cookie: str = ""          # populated only if --cookie was given
    auth_mode: str = "login"  # "login" or "cookie"
    nonce: str = ""

    # Read cadence
    poll_quiet_ms: int = 200       # 5 Hz
    poll_contest_ms: int = 50      # 20 Hz

    # Contest window: how long after a price change to stay hot
    contest_window_s: float = 30.0

    # Tail safety margin, ms, added on top of measured latencies
    tail_safety_ms: int = 150

    # Minimum proactive tail, ms.
    tail_min_ms: int = 300

    # Latency measurement
    latency_window: int = 50

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

    force_bid: bool = False 

class AuthError(RuntimeError):
    pass


def login_and_get_cookies(
    cfg: Config,
    log: logging.Logger,
) -> httpx.Cookies:
    """
    Log in via wp-login.php on a throwaway client and return the
    resulting cookie jar. The caller transplants it into http_auth.

    Raises AuthError on any failure. Does not touch cfg.cookie.
    """
    client = httpx.Client(
        base_url=cfg.base_url,
        timeout=httpx.Timeout(20.0, connect=8.0),
        headers={
            "User-Agent": cfg.user_agent,
            "Accept-Language": "en-KE,en;q=0.9",
        },
        follow_redirects=True,
        http2=True,
    )
    try:
        # Prime: sets wordpress_test_cookie
        client.get("/wp-login.php")

        r = client.post(
            "/wp-login.php",
            data={
                "log": cfg.username,
                "pwd": cfg.password,
                "wp-submit": "Log In",
                "redirect_to": f"{cfg.base_url}/my-account/",
                "testcookie": "1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{cfg.base_url}/wp-login.php",
                "Origin": cfg.base_url,
            },
        )
        log.info("[auth] wp-login HTTP %s → %s", r.status_code, r.url)

        has_cookie = any(
            c.name.startswith("wordpress_logged_in")
            for c in client.cookies.jar
        )
        # Authoritative check: /my-account/ must render the logged-in view.
        r2 = client.get("/my-account/")
        logged_in = bool(re.search(
            r'<body[^>]*class="[^"]*\blogged-in\b', r2.text
        ))

        if not (has_cookie and logged_in):
            if "login_error" in r.text or "incorrect" in r.text.lower():
                raise AuthError("wp-login.php rejected credentials")
            if not has_cookie:
                raise AuthError("no wordpress_logged_in cookie set")
            raise AuthError(
                "cookie present but /my-account/ still shows login form"
            )

        log.info("[auth] logged in as %s", cfg.username)
        return client.cookies
    finally:
        client.close()

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
        self._nonce_lock = threading.Lock()  

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
            # Fallback for testing: pretend the auction closes in 2 minutes.
            self.log.warning("data-finish-time not in HTML (%s); "
                             "falling back to now+120s", slug)
            return int(time.time()) + 120

        return int(m.group(1))

    _NONCE_RE = re.compile(r'"add_bid"\s*:\s*"([a-f0-9]+)"')

    def refresh_nonce(self, slug: str) -> Optional[str]:
        """
        Fetch the product page with the *authenticated* client and
        scrape the add_bid nonce. Returns the nonce, or None.
        """
        try:
            r = self.http_auth.get(
                f"/product/{slug}/",
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "Referer": f"{self.cfg.base_url}/",
                },
            )
        except httpx.HTTPError as e:
            self.log.warning("[nonce] fetch failed (%s): %s", slug, e)
            return None

        if r.status_code != 200:
            self.log.warning("[nonce] product page HTTP %s (%s)",
                             r.status_code, slug)
            return None

        m = self._NONCE_RE.search(r.text)
        if not m:
            self.log.warning("[nonce] add_bid not found in HTML (%s)", slug)
            return None

        nonce = m.group(1)
        with self._nonce_lock:
            self.cfg.nonce = nonce
        return nonce

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
            "/wp-admin/admin-ajax.php",
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
            if not cfg.force_bid:
                log.info("%s auction closed (state=%s)", vehicle.label, state)
                break
            # Forced mode: fire one bid and exit, regardless of state.
            amount = min(current + cfg.increment, vehicle.max_bid)
            log.warning(
                "%s FORCE-BID: state=%s, firing %s anyway",
                vehicle.label, state, f"{amount:,}",
            )
            resp, elapsed = fire_bid(client, cfg, vehicle, amount, lat, db)
            if resp is None:
                log.warning(
                    "%s force fire %s → no response (%.3fs)",
                    vehicle.label, f"{amount:,}", elapsed,
                )
            else:
                log.info(
                    "%s force fire %s → raw response: %s (%.3fs)",
                    vehicle.label, f"{amount:,}",
                    json.dumps(resp, ensure_ascii=False), elapsed,
                )
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

            # Closed-auction short-circuit: YITH returns {"url": ...}
            # instead of {"success": ...} when the auction is not
            # accepting bids anymore.
            # ── Classify the response ────────────────────────────────
            # YITH's success shape is a flat object:
            #   {"user_id": 24, "product_id": "56922", "bid": "65000.00",
            #    "date": "2026-10-16 09:12:12", "url": ".../product/<slug>/"}
            # Failure shapes we care about:
            #   {"success": false, "data": {"message": "..."}}   → explicit reject
            #   {"url": ".../my-account/..."}                    → session expired
            # Anything else is unrecognized and should be logged loudly,
            # not silently interpreted as "closed".

            is_success = (
                resp is not None
                and "user_id" in resp
                and "bid" in resp
            )

            is_session_expired = (
                resp is not None
                and "url" in resp
                and "/my-account/" in str(resp.get("url", ""))
            )

            is_explicit_failure = (
                resp is not None
                and resp.get("success") is False
            )

            # ── Act on the classification ────────────────────────────
            if resp is None:
                log.warning(
                    "%s fire %s (%s) → no response (%.3fs)",
                    vehicle.label, f"{amount:,}", reason, elapsed,
                )
                # last_bid NOT updated — the bid did not land.

            elif is_session_expired:
                log.error(
                    "%s fire %s (%s) → session expired, redirect to %s — stopping; raw=%s",
                    vehicle.label, f"{amount:,}", reason,
                    resp.get("url"), json.dumps(resp, ensure_ascii=False),
                )
                break

            elif is_explicit_failure:
                log.warning(
                    "%s fire %s (%s) → rejected: %s (%.3fs)",
                    vehicle.label, f"{amount:,}", reason,
                    json.dumps(resp.get("data"), ensure_ascii=False), elapsed,
                )
                # Bid was refused by the server. Keep looping — the state
                # may change, or the rejection may be transient.

            elif is_success:
                log.info(
                    "%s fire %s (%s) → success user_id=%s bid=%s (%.3fs) url=%s",
                    vehicle.label, f"{amount:,}", reason,
                    resp.get("user_id"), resp.get("bid"), elapsed,
                    resp.get("url"),
                )
                last_bid = amount
                pending_propagation_at = client.server_now()
                pending_propagation_amount = amount

            else:
                # Unknown shape. Log the whole thing and stop — we don't
                # want to keep firing into an unrecognized state.
                log.warning(
                    "%s fire %s (%s) → UNRECOGNIZED response (%.3fs); raw=%s",
                    vehicle.label, f"{amount:,}", reason, elapsed,
                    json.dumps(resp, ensure_ascii=False),
                )
                break
                
            if resp is None:
                log.warning(
                    "%s fire %s (%s) → no response (%.3fs)",
                    vehicle.label, f"{amount:,}", reason, elapsed,
                )
            else:
                success = bool(resp.get("success", False))
                log.info(
                    "%s fire %s (%s) → success=%s (%.3fs) data=%s",
                    vehicle.label, f"{amount:,}", reason, success, elapsed,
                    json.dumps(resp.get("data"), ensure_ascii=False),
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

        if not client.refresh_nonce(vehicle.slug):
            log.error("%s no add_bid nonce — aborting", vehicle.label)
            return

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

    # Authentication.
    #   Primary path: username + password. The sniper logs in via
    #   wp-login.php at startup, harvests the session cookies, and
    #   scrapes the add_bid nonce from the product page.
    #   Escape hatch: --cookie, which bypasses login entirely.
    #   If --cookie is given, --user/--pass are not required.
    p.add_argument("--user", type=str, default=None)
    p.add_argument("--pass", dest="password", type=str, default=None)
    p.add_argument("--cookie", type=str, default=None,
                   help="Optional. If set, skip login and use this "
                        "Cookie header verbatim. Useful when wp-login.php "
                        "is blocked or the site is mid-deploy.")

    p.add_argument("--db-path", type=str, default="database/database.sqlite")

    # Strategy tuning. Defaults match the Config dataclass.
    p.add_argument("--base-url",         type=str, default="https://phillipsauctioneers.co.ke")
    p.add_argument("--increment",        type=int, default=5000)
    p.add_argument("--poll-quiet-ms",    type=int, default=200)
    p.add_argument("--poll-contest-ms",  type=int, default=50)
    p.add_argument("--contest-window-s", type=int, default=30)
    p.add_argument("--tail-safety-ms",   type=int, default=150)
    p.add_argument("--tail-min-ms",      type=int, default=300)

    p.add_argument("--force-bid", action="store_true",
                help="Skip the 'Bid now' state gate and fire one bid "
                    "anyway. For testing against closed auctions.")

    a = p.parse_args()

    # ── Auth mode resolution ────────────────────────────────────
    if a.cookie:
        # Cookie override wins. Credentials become optional.
        auth_mode = "cookie"
    elif a.user and a.password:
        auth_mode = "login"
    else:
        sys.exit(
            "authentication required: pass --user and --pass, or --cookie "
            "(or both; --cookie takes precedence)"
        )
    # ── end auth resolution ─────────────────────────────────────

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
        # Auth fields. `nonce` is no longer supplied by the operator —
        # it's harvested at runtime and written into the Config by
        # PhillipsClient.refresh_nonce().
        username=a.user or "",
        password=a.password or "",
        cookie=a.cookie or "",
        auth_mode=auth_mode,
        nonce="",
        db_path=Path(a.db_path),
        poll_quiet_ms=a.poll_quiet_ms,
        poll_contest_ms=a.poll_contest_ms,
        contest_window_s=float(a.contest_window_s),
        tail_safety_ms=a.tail_safety_ms,
        tail_min_ms=a.tail_min_ms,
        force_bid=a.force_bid,
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
        # ── 1. Authenticate ─────────────────────────────────────
        # Two paths, chosen by parse_args and reported via
        # cfg.auth_mode:
        #   "login"  — hit wp-login.php, harvest cookies.
        #   "cookie" — operator pasted a live Cookie header; skip
        #              the login POST entirely.
        if cfg.auth_mode == "cookie":
            log.info("[auth] mode=cookie (operator-supplied header)")
            installed = 0
            for pair in cfg.cookie.split(";"):
                if "=" not in pair:
                    continue
                name, _, value = pair.strip().partition("=")
                if not name:
                    continue
                client.http_auth.cookies.set(
                    name, value,
                    domain=cfg.base_url.split("//", 1)[-1].split("/", 1)[0],
                )
                installed += 1
            if installed == 0:
                log.error("[auth] --cookie parsed to zero cookies; aborting")
                sys.exit(2)
            log.info("[auth] %d cookies installed on http_auth "
                     "(from --cookie)", installed)

            # Sanity check: the cookie must actually be live. Hit
            # /my-account/ on the authenticated client and look for
            # the logged-in body class. If it fails, bail now rather
            # than discovering it mid-endgame.
            try:
                r = client.http_auth.get("/my-account/")
                if not re.search(
                    r'<body[^>]*class="[^"]*\blogged-in\b', r.text
                ):
                    log.error(
                        "[auth] --cookie supplied but /my-account/ still "
                        "shows the login form — cookie is stale or wrong"
                    )
                    sys.exit(2)
                log.info("[auth] cookie verified against /my-account/")
            except httpx.HTTPError as e:
                log.error("[auth] verification fetch failed: %s", e)
                sys.exit(2)

        elif cfg.auth_mode == "login":
            log.info("[auth] mode=login (wp-login.php)")
            try:
                cookies = login_and_get_cookies(cfg, log)
            except AuthError as e:
                log.error("[auth] %s", e)
                log.error(
                    "[auth] hint: retry with --cookie '<browser Cookie header>' "
                    "if wp-login.php is blocked"
                )
                sys.exit(2)

            # Transplant harvested cookies into the authenticated
            # client. The anonymous reader (client.http) stays
            # anonymous — that's the whole point of the two clients.
            for c in cookies.jar:
                client.http_auth.cookies.jar.set_cookie(c)
            log.info("[auth] %d cookies installed on http_auth "
                     "(from login)", len(client.http_auth.cookies.jar))

        else:
            log.error("[auth] unknown auth_mode=%r — refusing to run",
                      cfg.auth_mode)
            sys.exit(2)

        # ── 2. Clock sync ───────────────────────────────────────
        # Both calls are on the anonymous client; login state is
        # irrelevant to the Date header we're sampling.
        client.sync_clock()
        client.sync_clock()

        # ── 3. Spawn one thread per vehicle ─────────────────────
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