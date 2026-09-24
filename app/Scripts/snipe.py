#!/usr/bin/env python3
"""
snipe.py — Manual-trigger, dual-vehicle auction monitor + bidder.

Operator supplies two vehicles (product ID, slug, max) plus a shared
nonce and session cookie. Everything else is derived.

Usage:
    python snipe.py \
        --product1 56438 --slug1 kdk-156x-mercedes-benz-actros --max1 1500000 \
        --product2 56441 --slug2 kds-974y-isuzu-frr90         --max2 800000 \
        --nonce 86d2bd16fe \
        --cookie "wordpress_logged_in_xxx=...; woocommerce_cart_hash=..."

The read path (Store API, product HTML, clock sync) uses an anonymous
HTTP client with no cookies. The write path (bid POST) uses an
authenticated HTTP client carrying the operator's session.

Each vehicle runs in its own thread. They share the HTTP clients and
the clock offset. The endgame for each vehicle is independent — one
may enter it while the other is still in the monitor phase.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

@dataclass
class VehicleTarget:
    product_id: int = 0
    slug: str = ""
    max_bid: int = 0

    @property
    def label(self) -> str:
        return f"[{self.product_id}]"


@dataclass
class Config:
    base_url: str = "https://phillipsauctioneers.co.ke"
    vehicles: list[VehicleTarget] = field(default_factory=list)
    increment: int = 5000
    nonce: str = ""
    cookie: str = ""

    endgame_window_s: int = 120

    tick_early_s: int = 4
    tick_mid_s: int = 2
    tick_late_s: int = 1

    monitor_interval_s: int = 20

    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    )
    log_dir: Path = field(default_factory=lambda: Path("logs"))

    max_bids_per_vehicle: int = 30
    min_seconds_between_bids: float = 0.8


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

    def read_store(self, product_id: int) -> Optional[dict]:
        try:
            r = self.http.get(f"/wp-json/wc/store/v1/products/{product_id}")
        except httpx.HTTPError as e:
            self.log.warning("store read failed (%d): %s", product_id, e)
            return None

        if r.status_code != 200:
            self.log.warning("store read status %s (%d)", r.status_code, product_id)
            return None

        try:
            return r.json()
        except json.JSONDecodeError:
            self.log.warning("store read: invalid JSON (%d)", product_id)
            return None

    def read_price(self, product_id: int) -> Optional[int]:
        data = self.read_store(product_id)
        if not data:
            return None
        price_str = data.get("prices", {}).get("price")
        if price_str is None:
            return None
        try:
            return int(price_str)
        except (ValueError, TypeError):
            return None

    def read_state(self, product_id: int) -> Optional[str]:
        data = self.read_store(product_id)
        if not data:
            return None
        return data.get("add_to_cart", {}).get("text")

    def is_open(self, product_id: int) -> bool:
        return self.read_state(product_id) == "Bid now"

    def close(self) -> None:
        self.http.close()
        self.http_auth.close()


# ─────────────────────────────────────────────────────────────────
# Read-only monitor — per vehicle
# ─────────────────────────────────────────────────────────────────

class Monitor:
    def __init__(
        self,
        client: PhillipsClient,
        log: logging.Logger,
        cfg: Config,
        vehicle: VehicleTarget,
    ):
        self.client = client
        self.log = log
        self.cfg = cfg
        self.vehicle = vehicle
        self.ticks = 0

    def poll_once(self) -> tuple[Optional[int], Optional[str]]:
        price = self.client.read_price(self.vehicle.product_id)
        state = self.client.read_state(self.vehicle.product_id)
        self.ticks += 1
        self.log.info(
            "%s tick %3d  price=%s  state=%s",
            self.vehicle.label,
            self.ticks,
            f"{price:,}" if price is not None else "—",
            state or "—",
        )
        return price, state

    def run_until(self, stop_at_ts: float, interval_s: int) -> None:
        while self.client.server_now() < stop_at_ts:
            self.poll_once()
            jitter = random.uniform(0.8, 1.2)
            sleep_for = interval_s * jitter
            remaining = stop_at_ts - self.client.server_now()
            sleep_for = min(sleep_for, max(0.0, remaining))
            if sleep_for > 0:
                time.sleep(sleep_for)


# ─────────────────────────────────────────────────────────────────
# Bid POST — per vehicle
# ─────────────────────────────────────────────────────────────────

def fire_bid(
    client: PhillipsClient,
    cfg: Config,
    vehicle: VehicleTarget,
    amount: int,
) -> Optional[dict]:
    """
    POST to /wp-admin/admin-ajax.php with:
        action   = yith_wcact_add_bid
        security = <nonce>
        currency = KES
        bid      = <amount>
        product  = <vehicle.product_id>

    Uses client.http_auth. Returns parsed JSON or None on transport /
    parse failure. Does NOT raise on HTTP 4xx/5xx — the caller inspects
    the JSON, or the None.
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

    try:
        r = client.http_auth.post(
            "/wp-admin/admin-ajax.php",
            data=body,
            headers=headers,
        )
    except httpx.HTTPError as e:
        client.log.error("%s bid POST transport error: %s", vehicle.label, e)
        return None

    # Log the raw status once per call so we can see non-200s clearly.
    client.log.debug("%s bid POST → HTTP %s", vehicle.label, r.status_code)

    try:
        return r.json()
    except json.JSONDecodeError:
        client.log.warning(
            "%s bid POST non-JSON response (HTTP %s): %.200s",
            vehicle.label, r.status_code, r.text,
        )
        return None


# ─────────────────────────────────────────────────────────────────
# Endgame loop — per vehicle
# ─────────────────────────────────────────────────────────────────

def run_endgame(
    client: PhillipsClient,
    cfg: Config,
    vehicle: VehicleTarget,
    finish_ts: int,
    log: logging.Logger,
) -> None:
    """
    Drive one vehicle from T-endgame_window_s down to finish_ts.

    Strategy: on every tick, read current price + state from the
    anonymous Store API. If still open and we are under max, submit
    the minimum winning bid (current + increment). Only bid when the
    last bid we acknowledged is no longer enough to be leading.
    """
    last_bid = 0
    bid_count = 0
    last_bid_at = 0.0
    last_price_seen: Optional[int] = None

    while client.server_now() < finish_ts:
        current = client.read_price(vehicle.product_id)
        state = client.read_state(vehicle.product_id)

        if state is None:
            log.warning("%s endgame: store read failed", vehicle.label)
            time.sleep(cfg.tick_mid_s)
            continue

        if state != "Bid now":
            log.info("%s auction closed (state=%s)", vehicle.label, state)
            break

        if current is None:
            time.sleep(cfg.tick_mid_s)
            continue

        if current != last_price_seen:
            log.info(
                "%s price %s (state=%s, my_last=%s, bids=%d)",
                vehicle.label,
                f"{current:,}",
                state,
                f"{last_bid:,}" if last_bid else "—",
                bid_count,
            )
            last_price_seen = current

        if current >= vehicle.max_bid:
            log.info(
                "%s priced out at %s (max %s)",
                vehicle.label, f"{current:,}", f"{vehicle.max_bid:,}",
            )
            break

        # Only fire if the minimum winning next bid beats our last bid.
        needed = current + cfg.increment
        if needed > last_bid:
            amount = min(needed, vehicle.max_bid)

            if bid_count >= cfg.max_bids_per_vehicle:
                log.warning(
                    "%s bid cap reached (%d) — giving up",
                    vehicle.label, bid_count,
                )
                break

            since_last = client.server_now() - last_bid_at
            if last_bid_at and since_last < cfg.min_seconds_between_bids:
                # Too soon since the last POST; wait it out.
                time.sleep(cfg.tick_mid_s)
                continue

            resp = fire_bid(client, cfg, vehicle, amount)
            bid_count += 1
            last_bid_at = client.server_now()

            if resp is None:
                log.warning(
                    "%s fire %s → no response (HTTP failure)",
                    vehicle.label, f"{amount:,}",
                )
            else:
                success = bool(resp.get("success", False))
                data = resp.get("data", {})
                log.info(
                    "%s fire %s → success=%s data=%s",
                    vehicle.label, f"{amount:,}", success, data,
                )
                if success:
                    last_bid = amount
                else:
                    # Server rejected the bid. If the rejection message
                    # reveals the new minimum, don't hammer; let the
                    # next tick re-read the price and try again.
                    msg = ""
                    if isinstance(data, dict):
                        msg = str(data.get("message") or data.get("error") or "")
                    if msg:
                        log.info("%s server said: %s", vehicle.label, msg)

        # Adaptive tick.
        remaining = finish_ts - client.server_now()
        if remaining <= 30:
            tick = cfg.tick_late_s
        elif remaining <= 60:
            tick = cfg.tick_mid_s
        else:
            tick = cfg.tick_early_s

        time.sleep(max(0.0, tick * random.uniform(0.8, 1.2)))

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
) -> None:
    log.info("%s starting  max=%s", vehicle.label, f"{vehicle.max_bid:,}")

    try:
        finish_ts = client.fetch_finish_time(vehicle.slug)
        if finish_ts is None:
            log.error("%s could not determine finish time", vehicle.label)
            return

        remaining = finish_ts - client.server_now()
        log.info(
            "%s finish_time=%d  remaining=%.0fs",
            vehicle.label, finish_ts, remaining,
        )

        state = client.read_state(vehicle.product_id)
        if state is None:
            log.error("%s store read failed — cookie may be invalid", vehicle.label)
            return
        log.info("%s initial state=%s", vehicle.label, state)

        price = client.read_price(vehicle.product_id)
        log.info(
            "%s initial price=%s",
            vehicle.label,
            f"{price:,}" if price else "—",
        )

        monitor = Monitor(client, log, cfg, vehicle)

        endgame_start = finish_ts - cfg.endgame_window_s
        if client.server_now() < endgame_start:
            log.info(
                "%s monitoring until T-%ds...",
                vehicle.label, cfg.endgame_window_s,
            )
            monitor.run_until(endgame_start, interval_s=cfg.monitor_interval_s)

        # One more clock sync before this vehicle's endgame.
        client.sync_clock()

        log.info("%s === endgame begins ===", vehicle.label)
        run_endgame(client, cfg, vehicle, finish_ts, log)

    except Exception as e:  # noqa: BLE001
        log.exception("%s worker crashed: %s", vehicle.label, e)


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def parse_args() -> Config:
    p = argparse.ArgumentParser()

    p.add_argument("--product1", type=int, required=True)
    p.add_argument("--slug1", type=str, required=True)
    p.add_argument("--max1", type=int, required=True)

    p.add_argument("--product2", type=int, required=True)
    p.add_argument("--slug2", type=str, required=True)
    p.add_argument("--max2", type=int, required=True)

    p.add_argument("--nonce", type=str, required=True)
    p.add_argument("--cookie", type=str, required=True)

    a = p.parse_args()

    for name, val in (("max1", a.max1), ("max2", a.max2)):
        if val % 5000 != 0:
            sys.exit(f"{name} must be a multiple of 5000")

    if a.product1 == a.product2:
        sys.exit("the two vehicles must be different product IDs")

    return Config(
        vehicles=[
            VehicleTarget(product_id=a.product1, slug=a.slug1, max_bid=a.max1),
            VehicleTarget(product_id=a.product2, slug=a.slug2, max_bid=a.max2),
        ],
        nonce=a.nonce,
        cookie=a.cookie,
    )


def main() -> None:
    cfg = parse_args()
    log = setup_logger(cfg)

    for v in cfg.vehicles:
        log.info("target: product=%d  slug=%s  max=%s",
                 v.product_id, v.slug, f"{v.max_bid:,}")

    client = PhillipsClient(cfg, log)

    try:
        client.sync_clock()
        client.sync_clock()

        threads: list[threading.Thread] = []
        for v in cfg.vehicles:
            t = threading.Thread(
                target=vehicle_worker,
                args=(client, cfg, v, log),
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

    log.info("done")


if __name__ == "__main__":
    main()