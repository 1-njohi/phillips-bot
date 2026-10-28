#!/usr/bin/env python3
"""
snipe3.py — three-lot auction sniper.

- Exactly three vehicles are required (V1, V2, V3).
- Credentials via --user/--pass or --cookie.
- Optional --end-time (unix ts, HH:MM:SS UTC, or ISO-8601) overrides
  the scraped data-finish-time. If absent, the finish time is derived
  from the product page.
- Bid success is determined by polling the WooCommerce Store API
  (the source of truth), NOT by interpreting the YITH AJAX response.
- Every bid attempt (landed or not) is written to SQLite.

Usage:
    python snipe3.py \
      --user you@example.com --pass 'secret' \
      --v1 56922 kde-429z-subaru-forester 465000 \
      --v2 56920 kdw-500l-mercedes-vito    920000 \
      --v3 56441 kds-974y-isuzu-frr90      800000 \
      --db-path bids.sqlite \
      [--end-time 2026-10-20T14:30:00Z] \
      [--increment 5000] [--arm-window-s 60]
"""

from __future__ import annotations

import argparse
import http.cookiejar as cookiejar
import json
import logging
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx


# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

NONCE_RE = re.compile(r'"add_bid"\s*:\s*"([a-f0-9]+)"')
FINISH_RE = re.compile(r'data-finish-time="(\d+)"')
LOGIN_OK_RE = re.compile(r'<body[^>]*class="[^"]*\blogged-in\b')

# Bid-landed verification (Store API poll after POST).
VERIFY_FIRST_POLL_S = 0.050   # read immediately, then poll at this cadence
VERIFY_TIMEOUT_S    = 2.0     # generous cap; first read usually suffices


# ─────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────

@dataclass
class Vehicle:
    product_id: int
    slug: str
    max_bid: int

    @property
    def label(self) -> str:
        return f"[{self.product_id}]"


@dataclass
class Config:
    base_url: str
    vehicles: list[Vehicle]
    username: str = ""
    password: str = ""
    cookie: str = ""
    auth_mode: str = "login"      # "login" | "cookie"
    end_time_override: Optional[int] = None
    increment: int = 5000
    arm_window_s: int = 60
    poll_wait_ms: int = 1000
    poll_arm_ms: int = 300
    max_bids: int = 200
    db_path: Path = Path("bids.sqlite")
    log_dir: Path = Path("logs")


class AuthError(RuntimeError):
    pass


# ─────────────────────────────────────────────────────────────────
# SQLite audit log — self-creating
# ─────────────────────────────────────────────────────────────────

class Db:
    """
    Records every bid attempt. WAL mode so a Laravel app (or anything
    else) can read while we write. Schema is created if missing.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS bid_attempts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id      INTEGER NOT NULL,
        slug            TEXT    NOT NULL,
        amount          INTEGER NOT NULL,
        fired_at_utc    TEXT    NOT NULL,
        reason          TEXT    NOT NULL,
        yith_http       INTEGER,
        yith_body       TEXT,
        store_price_after INTEGER,
        landed          INTEGER NOT NULL,   -- 1 if Store API confirmed
        elapsed_ms      INTEGER NOT NULL,
        mode            TEXT,
        created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_bid_attempts_product
        ON bid_attempts(product_id, fired_at_utc);
    """

    def __init__(self, path: Path, log: logging.Logger):
        self.log = log
        self.lock = threading.Lock()
        self.closed = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=3000")
            self.conn.executescript(self.SCHEMA)
            self.conn.commit()
            log.info("db ready at %s", path)
        except sqlite3.Error as e:
            log.error("db init failed (%s): %s", path, e)
            self.conn = None

    def record(self, *, product_id: int, slug: str, amount: int,
               reason: str, yith_http: Optional[int],
               yith_body: Optional[str], store_price_after: Optional[int],
               landed: bool, elapsed_ms: int, mode: str) -> None:
        if self.conn is None or self.closed:
            return
        with self.lock:
            try:
                self.conn.execute(
                    """
                    INSERT INTO bid_attempts
                      (product_id, slug, amount, fired_at_utc, reason,
                       yith_http, yith_body, store_price_after,
                       landed, elapsed_ms, mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (product_id, slug, amount,
                     datetime.now(tz=timezone.utc).isoformat(),
                     reason, yith_http,
                     (yith_body or "")[:1000],
                     store_price_after,
                     1 if landed else 0,
                     elapsed_ms, mode),
                )
                self.conn.commit()
            except sqlite3.Error as e:
                self.log.warning("db record failed: %s", e)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            if self.conn:
                self.conn.close()


# ─────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────

def login_and_get_cookies(cfg: Config, log: logging.Logger) -> httpx.Cookies:
    client = httpx.Client(
        base_url=cfg.base_url,
        timeout=httpx.Timeout(20.0, connect=8.0),
        headers={"User-Agent": USER_AGENT,
                 "Accept-Language": "en-KE,en;q=0.9"},
        follow_redirects=True, http2=True,
    )
    try:
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

        has_cookie = any(c.name.startswith("wordpress_logged_in")
                         for c in client.cookies.jar)
        r2 = client.get("/my-account/")
        if not (has_cookie and LOGIN_OK_RE.search(r2.text)):
            if "login_error" in r.text or "incorrect" in r.text.lower():
                raise AuthError("wp-login.php rejected credentials")
            if not has_cookie:
                raise AuthError("no wordpress_logged_in cookie set")
            raise AuthError("cookie present but /my-account/ shows login form")
        log.info("[auth] logged in as %s", cfg.username)
        return client.cookies
    finally:
        client.close()


def cookies_from_header(header: str, base_url: str) -> httpx.Cookies:
    domain = base_url.split("//", 1)[-1].split("/", 1)[0]
    jar = cookiejar.CookieJar()
    for pair in header.split(";"):
        if "=" not in pair:
            continue
        name, _, value = pair.strip().partition("=")
        if not name:
            continue
        jar.set_cookie(cookiejar.Cookie(
            version=0, name=name, value=value, port=None,
            port_specified=False, domain=domain, domain_specified=True,
            domain_initial_dot=False, path="/", path_specified=True,
            secure=True, expires=None, discard=True,
            comment=None, comment_url=None, rest={}, rfc2109=False,
        ))
    return httpx.Cookies(jar)


# ─────────────────────────────────────────────────────────────────
# Per-vehicle site (own clients, own nonce)
# ─────────────────────────────────────────────────────────────────

class Site:
    def __init__(self, cfg: Config, vehicle: Vehicle,
                 cookies: Optional[httpx.Cookies], log: logging.Logger):
        self.cfg = cfg
        self.vehicle = vehicle
        self.log = log
        self.nonce = ""
        self._clock_offset = 0.0
        self._offsets: list[float] = []

        self.http = httpx.Client(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": USER_AGENT,
                     "Accept": "application/json, text/html, */*"},
            follow_redirects=False, http2=True,
        )
        self.http_auth = httpx.Client(
            base_url=cfg.base_url,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                     "X-Requested-With": "XMLHttpRequest"},
            follow_redirects=False, http2=True,
        )
        if cookies is not None:
            for c in cookies.jar:
                self.http_auth.cookies.jar.set_cookie(c)

    # ── clock ───────────────────────────────────────────────────
    def sync_clock(self) -> None:
        t0 = time.time()
        try:
            r = self.http.head("/")
        except httpx.HTTPError as e:
            self.log.warning("%s clock sync failed: %s",
                             self.vehicle.label, e)
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
        self._offsets.append(sample)
        self._offsets[:] = self._offsets[-5:]
        self._clock_offset = sorted(self._offsets)[len(self._offsets) // 2]
        self.log.info("%s clock offset %+.3fs", self.vehicle.label,
                      self._clock_offset)

    def server_now(self) -> float:
        return time.time() + self._clock_offset

    # ── reads ───────────────────────────────────────────────────
    def fetch_finish_time(self) -> Optional[int]:
        try:
            r = self.http.get(f"/product/{self.vehicle.slug}/")
        except httpx.HTTPError as e:
            self.log.error("%s product page fetch failed: %s",
                           self.vehicle.label, e)
            return None
        if r.status_code != 200:
            self.log.error("%s product page HTTP %s",
                           self.vehicle.label, r.status_code)
            return None
        m = FINISH_RE.search(r.text)
        if not m:
            self.log.warning("%s no data-finish-time", self.vehicle.label)
            return None
        return int(m.group(1))

    def refresh_nonce(self) -> Optional[str]:
        ts = int(time.time() * 1000)
        try:
            r = self.http_auth.get(
                f"/product/{self.vehicle.slug}/?nocache={ts}",
                headers={"Accept": "text/html,application/xhtml+xml",
                         "Referer": f"{self.cfg.base_url}/",
                         "Cache-Control": "no-cache",
                         "Pragma": "no-cache"},
            )
        except httpx.HTTPError as e:
            self.log.warning("%s nonce fetch failed: %s",
                             self.vehicle.label, e)
            return None
        if r.status_code != 200:
            self.log.warning("%s nonce page HTTP %s",
                             self.vehicle.label, r.status_code)
            return None
        m = NONCE_RE.search(r.text)
        if not m:
            self.log.warning("%s add_bid not in HTML", self.vehicle.label)
            return None
        self.nonce = m.group(1)
        self.log.info("%s nonce=%s", self.vehicle.label, self.nonce)
        return self.nonce

    def read_store(self) -> Optional[dict]:
        try:
            r = self.http.get(
                f"/wp-json/wc/store/v1/products/{self.vehicle.product_id}"
            )
        except httpx.HTTPError:
            return None
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except json.JSONDecodeError:
            return None

    def read_price(self) -> Optional[int]:
        data = self.read_store()
        if not data:
            return None
        p = data.get("prices", {}).get("price")
        try:
            return int(p) if p is not None else None
        except (ValueError, TypeError):
            return None

    def read_state(self) -> Optional[str]:
        data = self.read_store()
        if not data:
            return None
        return data.get("add_to_cart", {}).get("text")

    # ── write + verify ──────────────────────────────────────────
    def fire_and_verify(self, amount: int) -> dict:
        """
        POST the bid, then poll the Store API until price >= amount
        (landed) or VERIFY_TIMEOUT_S elapses. Returns a result dict
        with everything needed for the DB row.
        """
        body = {
            "action":   "yith_wcact_add_bid",
            "security": self.nonce,
            "currency": "KES",
            "bid":      str(amount),
            "product":  str(self.vehicle.product_id),
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.cfg.base_url}/product/{self.vehicle.slug}/",
        }

        t0 = time.perf_counter()
        http_status: Optional[int] = None
        yith_body: str = ""
        try:
            r = self.http_auth.post(
                "/wp-admin/admin-ajax.php", data=body, headers=headers,
            )
            http_status = r.status_code
            yith_body = r.text
        except httpx.HTTPError as e:
            elapsed = time.perf_counter() - t0
            self.log.error("%s bid POST transport error (%.3fs): %s",
                           self.vehicle.label, elapsed, e)
            return {
                "landed": False, "store_price_after": None,
                "http_status": None, "yith_body": f"transport error: {e}",
                "elapsed_ms": int(elapsed * 1000),
            }
        post_elapsed = time.perf_counter() - t0

        self.log.info("%s POST %s → HTTP %s in %.3fs; body=%.300s",
                      self.vehicle.label, f"{amount:,}",
                      http_status, post_elapsed, yith_body)

        # Store API is the source of truth. First read at t≈post_elapsed.
        deadline = time.perf_counter() + VERIFY_TIMEOUT_S
        last_price: Optional[int] = None
        time.sleep(0)  # yield; first read is immediate
        while True:
            last_price = self.read_price()
            if last_price is not None and last_price >= amount:
                total_ms = int((time.perf_counter() - t0) * 1000)
                self.log.info("%s VERIFIED landed: store=%s >= bid=%s (%d ms)",
                              self.vehicle.label, f"{last_price:,}",
                              f"{amount:,}", total_ms)
                return {
                    "landed": True, "store_price_after": last_price,
                    "http_status": http_status, "yith_body": yith_body,
                    "elapsed_ms": total_ms,
                }
            if time.perf_counter() >= deadline:
                break
            time.sleep(VERIFY_FIRST_POLL_S)

        total_ms = int((time.perf_counter() - t0) * 1000)
        self.log.warning(
            "%s NOT verified within %.1fs (bid=%s, store=%s) — recorded as failed",
            self.vehicle.label, VERIFY_TIMEOUT_S, f"{amount:,}",
            f"{last_price:,}" if last_price is not None else "—",
        )
        return {
            "landed": False, "store_price_after": last_price,
            "http_status": http_status, "yith_body": yith_body,
            "elapsed_ms": total_ms,
        }

    def close(self) -> None:
        self.http.close()
        self.http_auth.close()


# ─────────────────────────────────────────────────────────────────
# Sniper loop — one per vehicle
# ─────────────────────────────────────────────────────────────────

def run_sniper(cfg: Config, site: Site, finish_ts: int,
               db: Db, log: logging.Logger) -> None:
    v = site.vehicle
    label = v.label
    my_bid = 0
    bid_count = 0
    last_price: Optional[int] = None
    last_summary = 0.0

    log.info("%s === sniper begins; finish in %.0fs ===",
             label, finish_ts - site.server_now())

    while True:
        now = site.server_now()
        remaining = finish_ts - now
        if remaining <= 0:
            log.info("%s end time reached", label)
            break

        # Wait phase
        if remaining > cfg.arm_window_s:
            if remaining - cfg.arm_window_s < 5:
                log.info("%s arms in %.0fs", label,
                         remaining - cfg.arm_window_s)
            time.sleep(cfg.poll_wait_ms / 1000.0)
            continue

        # Arm phase
        state = site.read_state()
        current = site.read_price()

        if state is not None and state != "Bid now":
            log.info("%s state=%s — auction closed", label, state)
            break
        if current is None:
            time.sleep(cfg.poll_arm_ms / 1000.0)
            continue

        if current != last_price:
            log.info("%s price %s (my_bid=%s, bids=%d, rem=%.1fs)",
                     label, f"{current:,}",
                     f"{my_bid:,}" if my_bid else "—",
                     bid_count, remaining)
            last_price = current

        if current >= v.max_bid:
            log.info("%s price %s >= max %s — stopping",
                     label, f"{current:,}", f"{v.max_bid:,}")
            break

        # Fire on outbid (or first bid)
        if my_bid == 0 or current >= my_bid:
            amount = current + cfg.increment
            if amount > v.max_bid:
                log.info("%s next %s exceeds max — stopping",
                         label, f"{amount:,}")
                break
            if bid_count >= cfg.max_bids:
                log.warning("%s bid cap %d reached", label, cfg.max_bids)
                break

            mode = "tail" if remaining <= cfg.arm_window_s else "contest"
            result = site.fire_and_verify(amount)
            bid_count += 1

            db.record(
                product_id=v.product_id, slug=v.slug, amount=amount,
                reason="outbid", yith_http=result["http_status"],
                yith_body=result["yith_body"],
                store_price_after=result["store_price_after"],
                landed=result["landed"],
                elapsed_ms=result["elapsed_ms"], mode=mode,
            )

            if result["landed"]:
                my_bid = amount
            # If not landed: my_bid stays behind current, next tick retries.

        if now - last_summary >= 5.0:
            last_summary = now
            log.info("%s summary rem=%.0fs price=%s my_bid=%s bids=%d",
                     label, remaining,
                     f"{current:,}" if current else "—",
                     f"{my_bid:,}" if my_bid else "—", bid_count)

        time.sleep(cfg.poll_arm_ms / 1000.0)

    log.info("%s sniper done: bids=%d my_bid=%s",
             label, bid_count, f"{my_bid:,}" if my_bid else "—")


# ─────────────────────────────────────────────────────────────────
# Worker
# ─────────────────────────────────────────────────────────────────

def worker(cfg: Config, vehicle: Vehicle,
           cookies: Optional[httpx.Cookies], db: Db,
           log: logging.Logger) -> None:
    label = vehicle.label
    log.info("%s start max=%s", label, f"{vehicle.max_bid:,}")
    site = Site(cfg, vehicle, cookies, log)
    try:
        site.sync_clock()
        site.sync_clock()

        finish_ts: Optional[int]
        if cfg.end_time_override is not None:
            finish_ts = cfg.end_time_override
            log.info("%s using --end-time override %s",
                     label,
                     datetime.fromtimestamp(finish_ts, tz=timezone.utc))
        else:
            finish_ts = site.fetch_finish_time()
            if finish_ts is None:
                log.error("%s no finish time and no --end-time; aborting",
                          label)
                return
            log.info("%s derived finish %s (rem %.0fs)",
                     label,
                     datetime.fromtimestamp(finish_ts, tz=timezone.utc),
                     finish_ts - site.server_now())

        state = site.read_state()
        if state != "Bid now":
            log.warning("%s state=%s — not open; aborting", label, state)
            return

        if not site.refresh_nonce():
            log.error("%s no nonce; aborting", label)
            return

        run_sniper(cfg, site, finish_ts, db, log)
    except Exception as e:  # noqa: BLE001
        log.exception("%s worker crashed: %s", label, e)
    finally:
        site.close()


# ─────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────

def parse_end_time(s: str) -> int:
    s = s.strip()
    if s.isdigit():
        return int(s)
    if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", s):
        hh, mm, ss = (int(x) for x in s.split(":"))
        return int(datetime.now(tz=timezone.utc).replace(
            hour=hh, minute=mm, second=ss, microsecond=0).timestamp())
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"cannot parse --end-time {s!r}; use unix ts, HH:MM:SS, or ISO-8601")


def parse_args() -> Config:
    p = argparse.ArgumentParser(description="Three-lot auction sniper.")
    p.add_argument("--base-url", default="https://phillipsauctioneers.co.ke")

    # Three vehicles, required. Each is a 3-tuple: product_id slug max_bid.
    for i in (1, 2, 3):
        p.add_argument(f"--v{i}", nargs=3, required=True,
                       metavar=("PRODUCT_ID", "SLUG", "MAX_BID"),
                       help=f"vehicle {i}: product_id slug max_bid")

    # Auth
    p.add_argument("--user", default=None)
    p.add_argument("--pass", dest="password", default=None)
    p.add_argument("--cookie", default=None)

    # Optional end-time override
    p.add_argument("--end-time", type=parse_end_time, default=None,
                   help="Override finish time (unix ts, HH:MM:SS UTC, "
                        "or ISO-8601). Omit to derive from product page.")

    # Tuning
    p.add_argument("--increment", type=int, default=5000)
    p.add_argument("--arm-window-s", type=int, default=60)
    p.add_argument("--poll-wait-ms", type=int, default=1000)
    p.add_argument("--poll-arm-ms", type=int, default=300)
    p.add_argument("--max-bids", type=int, default=200)
    p.add_argument("--db-path", default="bids.sqlite")

    a = p.parse_args()

    if a.cookie:
        auth_mode = "cookie"
    elif a.user and a.password:
        auth_mode = "login"
    else:
        sys.exit("authentication required: --user/--pass or --cookie")

    vehicles: list[Vehicle] = []
    seen = set()
    for i in (1, 2, 3):
        pid, slug, mx = getattr(a, f"v{i}")
        pid, mx = int(pid), int(mx)
        if pid in seen:
            sys.exit(f"duplicate product id {pid}")
        seen.add(pid)
        if mx % a.increment != 0:
            sys.exit(f"v{i} max_bid {mx} must be a multiple of {a.increment}")
        vehicles.append(Vehicle(product_id=pid, slug=slug, max_bid=mx))

    return Config(
        base_url=a.base_url.rstrip("/"),
        vehicles=vehicles,
        username=a.user or "",
        password=a.password or "",
        cookie=a.cookie or "",
        auth_mode=auth_mode,
        end_time_override=a.end_time,
        increment=a.increment,
        arm_window_s=a.arm_window_s,
        poll_wait_ms=a.poll_wait_ms,
        poll_arm_ms=a.poll_arm_ms,
        max_bids=a.max_bids,
        db_path=Path(a.db_path),
    )


def setup_logger(cfg: Config) -> logging.Logger:
    cfg.log_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    logfile = cfg.log_dir / f"snipe3-{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout),
                  logging.FileHandler(logfile)],
    )
    log = logging.getLogger("snipe3")
    log.info("log file: %s", logfile)
    return log


def main() -> None:
    cfg = parse_args()
    log = setup_logger(cfg)
    for v in cfg.vehicles:
        log.info("target product=%d slug=%s max=%s",
                 v.product_id, v.slug, f"{v.max_bid:,}")
    if cfg.end_time_override is not None:
        log.info("end-time override: %s",
                 datetime.fromtimestamp(cfg.end_time_override, tz=timezone.utc))

    db = Db(cfg.db_path, log)

    # ── auth ────────────────────────────────────────────────────
    cookies: Optional[httpx.Cookies] = None
    if cfg.auth_mode == "cookie":
        log.info("[auth] mode=cookie")
        cookies = cookies_from_header(cfg.cookie, cfg.base_url)
        if len(cookies.jar) == 0:
            log.error("[auth] --cookie parsed to zero cookies")
            sys.exit(2)
        # Sanity check via a throwaway client
        probe = httpx.Client(
            base_url=cfg.base_url,
            headers={"User-Agent": USER_AGENT,
                     "Cookie": cfg.cookie},
            follow_redirects=True, http2=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
        )
        try:
            r = probe.get("/my-account/")
            if not LOGIN_OK_RE.search(r.text):
                log.error("[auth] --cookie is stale or wrong "
                          "(/my-account/ shows login form)")
                sys.exit(2)
            log.info("[auth] cookie verified against /my-account/")
        except httpx.HTTPError as e:
            log.error("[auth] verification fetch failed: %s", e)
            sys.exit(2)
        finally:
            probe.close()
    elif cfg.auth_mode == "login":
        log.info("[auth] mode=login")
        try:
            cookies = login_and_get_cookies(cfg, log)
        except AuthError as e:
            log.error("[auth] %s", e)
            log.error("[auth] hint: retry with --cookie '<browser Cookie>'")
            sys.exit(2)

    # ── run ─────────────────────────────────────────────────────
    threads = []
    for v in cfg.vehicles:
        t = threading.Thread(
            target=worker, args=(cfg, v, cookies, db, log),
            name=f"v-{v.product_id}", daemon=False,
        )
        t.start()
        threads.append(t)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        log.info("interrupted")
    finally:
        db.close()
        log.info("done")


if __name__ == "__main__":
    main()