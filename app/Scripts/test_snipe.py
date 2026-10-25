#!/usr/bin/env python3
"""
snipe_min.py — stripped-down auction sniper.

- Credentials, vehicle details, and tuning are HARDCODED below.
- Bid success is verified by polling the WooCommerce Store API,
  NOT by trusting the YITH AJAX response.
- Optional --end-time overrides the scraped auction finish time,
  so you can test the endgame before the real close.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import httpx


# ─────────────────────────────────────────────────────────────────
# HARDCODED CONFIG — edit these
# ─────────────────────────────────────────────────────────────────

BASE_URL = "https://phillipsauctioneers.co.ke"

USERNAME = "kodembe@yahoo.com"
PASSWORD = "maurice@otieno"

# One or more vehicles. product_id is the WooCommerce product id;
# slug is the product page slug; max_bid is the hard ceiling (KES).
VEHICLES = [
    {
        "product_id": 57045,
        "slug":       "kcq-444m-range-rover-evogue-2",
        "max_bid":    300000,
    },
    # Add more dicts here to snipe more vehicles concurrently:
    # {"product_id": 56441, "slug": "kds-974y-isuzu-frr90", "max_bid": 800000},
]

INCREMENT = 5000

# Cadence
POLL_QUIET_S   = 0.200   # 5 Hz
POLL_CONTEST_S = 0.050   # 20 Hz
CONTEST_WINDOW_S = 30.0  # stay "hot" this long after a price change

# Tail: start proactive bidding this many seconds before the close.
# Computed live from measured read/write latencies, floored here.
TAIL_MIN_S      = 1.0
TAIL_SAFETY_S   = 0.300

# Bid-landed verification
VERIFY_TIMEOUT_S = 5.0    # how long to poll Store API for our bid
VERIFY_POLL_S    = 0.100

MAX_BIDS_PER_VEHICLE = 200

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

# ─────────────────────────────────────────────────────────────────

log = logging.getLogger("snipe_min")


@dataclass
class Vehicle:
    product_id: int
    slug: str
    max_bid: int

    @property
    def label(self) -> str:
        return f"[{self.product_id}]"


# ─────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────

def login(cfg_cookie_out: dict) -> httpx.Client:
    """
    Log in via wp-login.php and return an authenticated client.
    Cookies are also copied into cfg_cookie_out (unused now, kept
    for debugging if you want to print them).
    """
    client = httpx.Client(
        base_url=BASE_URL,
        timeout=httpx.Timeout(20.0, connect=8.0),
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-KE,en;q=0.9"},
        follow_redirects=True,
        http2=True,
    )
    client.get("/wp-login.php")
    r = client.post(
        "/wp-login.php",
        data={
            "log": USERNAME,
            "pwd": PASSWORD,
            "wp-submit": "Log In",
            "redirect_to": f"{BASE_URL}/my-account/",
            "testcookie": "1",
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": f"{BASE_URL}/wp-login.php",
            "Origin": BASE_URL,
        },
    )
    log.info("[auth] wp-login HTTP %s → %s", r.status_code, r.url)

    # Verify by checking /my-account/ renders the logged-in body class.
    r2 = client.get("/my-account/")
    if not re.search(r'<body[^>]*class="[^"]*\blogged-in\b', r2.text):
        if "login_error" in r.text or "incorrect" in r.text.lower():
            raise RuntimeError("wp-login.php rejected credentials")
        raise RuntimeError("logged-in cookie not accepted by /my-account/")

    cfg_cookie_out.update({c.name: c.value for c in client.cookies.jar})
    log.info("[auth] logged in as %s", USERNAME)
    return client


# ─────────────────────────────────────────────────────────────────
# Reads (anonymous)
# ─────────────────────────────────────────────────────────────────

anon = httpx.Client(
    base_url=BASE_URL,
    timeout=httpx.Timeout(15.0, connect=5.0),
    headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html, */*"},
    follow_redirects=False,
    http2=True,
)


def read_price(product_id: int) -> Optional[int]:
    try:
        r = anon.get(f"/wp-json/wc/store/v1/products/{product_id}")
    except httpx.HTTPError as e:
        log.warning("store read failed (%d): %s", product_id, e)
        return None
    if r.status_code != 200:
        log.warning("store read status %s (%d)", r.status_code, product_id)
        return None
    try:
        price = r.json().get("prices", {}).get("price")
        return int(price) if price is not None else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def read_state(product_id: int) -> Optional[str]:
    try:
        r = anon.get(f"/wp-json/wc/store/v1/products/{product_id}")
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json().get("add_to_cart", {}).get("text")
    except json.JSONDecodeError:
        return None


def fetch_finish_time(slug: str) -> Optional[int]:
    try:
        r = anon.get(f"/product/{slug}/")
    except httpx.HTTPError as e:
        log.error("product page fetch failed (%s): %s", slug, e)
        return None
    if r.status_code != 200:
        log.error("product page status %s (%s)", r.status_code, slug)
        return None
    m = re.search(r'data-finish-time="(\d+)"', r.text)
    if not m:
        log.warning("data-finish-time not in HTML (%s)", slug)
        return None
    return int(m.group(1))


# ─────────────────────────────────────────────────────────────────
# Clock sync (simple: median of last 5 Date-header samples)
# ─────────────────────────────────────────────────────────────────

_offsets: list[float] = []


def sync_clock() -> None:
    t0 = time.time()
    try:
        r = anon.head("/")
    except httpx.HTTPError as e:
        log.warning("clock sync failed: %s", e)
        return
    t1 = time.time()
    date_hdr = r.headers.get("date")
    if not date_hdr:
        return
    try:
        server_ts = datetime.strptime(
            date_hdr, "%a, %d %b %Y %H:%M:%S %Z"
        ).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return
    sample = (server_ts + (t1 - t0) / 2) - t1
    _offsets.append(sample)
    _offsets[:] = _offsets[-5:]
    log.info("clock offset: %+.3fs", sorted(_offsets)[len(_offsets) // 2])


def server_now() -> float:
    if not _offsets:
        return time.time()
    return time.time() + sorted(_offsets)[len(_offsets) // 2]


# ─────────────────────────────────────────────────────────────────
# Nonce scrape (authenticated)
# ─────────────────────────────────────────────────────────────────

_NONCE_RE = re.compile(r'"add_bid"\s*:\s*"([a-f0-9]+)"')


def refresh_nonce(auth: httpx.Client, slug: str) -> Optional[str]:
    try:
        r = auth.get(
            f"/product/{slug}/",
            headers={"Accept": "text/html,application/xhtml+xml",
                     "Referer": f"{BASE_URL}/"},
        )
    except httpx.HTTPError as e:
        log.warning("[nonce] fetch failed (%s): %s", slug, e)
        return None
    if r.status_code != 200:
        log.warning("[nonce] product page HTTP %s (%s)", r.status_code, slug)
        return None
    m = _NONCE_RE.search(r.text)
    if not m:
        log.warning("[nonce] add_bid not found in HTML (%s)", slug)
        return None
    return m.group(1)


# ─────────────────────────────────────────────────────────────────
# Bid POST + Store-API verification
# ─────────────────────────────────────────────────────────────────

def fire_and_verify(
    auth: httpx.Client,
    v: Vehicle,
    nonce: str,
    amount: int,
) -> tuple[bool, float]:
    """
    POST the bid, then poll the Store API until the price reflects
    `amount` (bid landed) or VERIFY_TIMEOUT_S elapses.

    Returns (landed, elapsed_seconds). Does NOT interpret the YITH
    response beyond logging it.
    """
    body = {
        "action":   "yith_wcact_add_bid",
        "security": nonce,
        "currency": "KES",
        "bid":      str(amount),
        "product":  str(v.product_id),
    }
    headers = {
        "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer":          f"{BASE_URL}/product/{v.slug}/",
    }

    t0 = time.perf_counter()
    try:
        r = auth.post("/wp-admin/admin-ajax.php", data=body, headers=headers)
    except httpx.HTTPError as e:
        elapsed = time.perf_counter() - t0
        log.error("%s bid POST transport error (%.3fs): %s",
                  v.label, elapsed, e)
        return False, elapsed

    post_elapsed = time.perf_counter() - t0
    log.info("%s POST %s → HTTP %s in %.3fs; body=%.300s",
             v.label, f"{amount:,}", r.status_code, post_elapsed, r.text)

    # Verify via Store API — the source of truth.
    deadline = time.perf_counter() + VERIFY_TIMEOUT_S
    while time.perf_counter() < deadline:
        price = read_price(v.product_id)
        if price is not None and price >= amount:
            total = time.perf_counter() - t0
            log.info("%s VERIFIED landed: store price=%s >= bid=%s (%.3fs)",
                     v.label, f"{price:,}", f"{amount:,}", total)
            return True, total
        time.sleep(VERIFY_POLL_S)

    log.warning("%s NOT verified within %.1fs (bid=%s) — treating as not landed",
                v.label, VERIFY_TIMEOUT_S, f"{amount:,}")
    return False, time.perf_counter() - t0


# ─────────────────────────────────────────────────────────────────
# Endgame loop (single vehicle, single thread)
# ─────────────────────────────────────────────────────────────────

def run_endgame(auth: httpx.Client, v: Vehicle,
                finish_ts: int, nonce: str) -> None:
    last_bid = 0
    last_price = None
    last_price_change_at = 0.0
    bid_count = 0
    reads: list[float] = []
    writes: list[float] = []

    log.info("%s === endgame begins (finish=%s) ===",
             v.label, datetime.fromtimestamp(finish_ts, tz=timezone.utc))

    while server_now() < finish_ts:
        now = server_now()
        remaining = finish_ts - now

        t0 = time.perf_counter()
        current = read_price(v.product_id)
        state = read_state(v.product_id)
        reads.append(time.perf_counter() - t0)
        reads[:] = reads[-50:]

        if current is None or state is None:
            time.sleep(POLL_QUIET_S)
            continue

        if state != "Bid now":
            log.info("%s auction closed (state=%s)", v.label, state)
            break

        if current != last_price:
            log.info("%s price %s (my_last=%s, bids=%d, rem=%.1fs)",
                     v.label, f"{current:,}",
                     f"{last_bid:,}" if last_bid else "—",
                     bid_count, remaining)
            last_price = current
            last_price_change_at = now

        if current >= v.max_bid:
            log.info("%s priced out at max_bid=%s", v.label, f"{v.max_bid:,}")
            break

        # Tail from measured latencies.
        p95_read = sorted(reads)[min(len(reads) - 1, int(len(reads) * 0.95))] if reads else 0
        p95_write = sorted(writes)[min(len(writes) - 1, int(len(writes) * 0.95))] if writes else 0
        tail = max(TAIL_MIN_S, p95_read + p95_write + TAIL_SAFETY_S)

        if remaining <= tail:
            mode = "tail"
        elif now - last_price_change_at <= CONTEST_WINDOW_S:
            mode = "contest"
        else:
            mode = "quiet"

        should_fire = False
        reason = ""
        amount = min(current + INCREMENT, v.max_bid)

        if mode == "tail":
            if amount > last_bid:
                should_fire = True
                reason = "tail"
        else:
            if current > last_bid:
                should_fire = True
                reason = mode

        if should_fire and amount <= last_bid:
            should_fire = False

        if should_fire:
            if bid_count >= MAX_BIDS_PER_VEHICLE:
                log.warning("%s bid cap reached (%d) — giving up",
                            v.label, bid_count)
                break

            landed, elapsed = fire_and_verify(auth, v, nonce, amount)
            writes.append(elapsed)
            writes[:] = writes[-50:]
            bid_count += 1
            if landed:
                last_bid = amount

        log.info("%s mode=%s tail=%.0fms rem=%.1fs bids=%d last=%s current=%s",
                 v.label, mode, tail * 1000, remaining, bid_count,
                 f"{last_bid:,}" if last_bid else "—",
                 f"{current:,}" if current else "—")

        time.sleep(POLL_CONTEST_S if mode in ("contest", "tail") else POLL_QUIET_S)

    log.info("%s endgame done: bids=%d last_bid=%s",
             v.label, bid_count, f"{last_bid:,}" if last_bid else "—")


# ─────────────────────────────────────────────────────────────────
# --end-time parsing
# ─────────────────────────────────────────────────────────────────

def parse_end_time(s: str) -> int:
    """
    Accept a Unix timestamp, 'HH:MM:SS' (today, UTC), or ISO-8601.
    Returns Unix seconds.
    """
    s = s.strip()
    if s.isdigit():
        return int(s)
    # HH:MM:SS — today in UTC
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", s):
        hh, mm, ss = (int(x) for x in s.split(":"))
        now = datetime.now(tz=timezone.utc)
        return int(now.replace(hour=hh, minute=mm, second=ss,
                               microsecond=0).timestamp())
    # ISO-8601
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        f"cannot parse --end-time {s!r}; use unix ts, HH:MM:SS, or ISO-8601"
    )


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--end-time", type=parse_end_time, default=None,
                   help="Override auction finish time (unix ts, HH:MM:SS UTC, "
                        "or ISO-8601). Use to test the endgame before the real close.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    if args.end_time is not None:
        log.info("--end-time override: %s (%d)",
                 datetime.fromtimestamp(args.end_time, tz=timezone.utc),
                 args.end_time)

    try:
        auth = login({})
    except RuntimeError as e:
        log.error("[auth] %s", e)
        sys.exit(2)

    try:
        sync_clock()
        sync_clock()

        vehicles = [Vehicle(**v) for v in VEHICLES]
        for v in vehicles:
            log.info("target %s max=%s", v.label, f"{v.max_bid:,}")

            if args.end_time is not None:
                finish_ts = args.end_time
            else:
                finish_ts = fetch_finish_time(v.slug)
                if finish_ts is None:
                    log.error("%s could not determine finish time", v.label)
                    continue

            log.info("%s finish=%s remaining=%.0fs",
                     v.label,
                     datetime.fromtimestamp(finish_ts, tz=timezone.utc),
                     finish_ts - server_now())

            nonce = refresh_nonce(auth, v.slug)
            if not nonce:
                log.error("%s no add_bid nonce — skipping", v.label)
                continue
            log.info("%s nonce=%s", v.label, nonce)

            run_endgame(auth, v, finish_ts, nonce)
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        auth.close()
        anon.close()


if __name__ == "__main__":
    main()