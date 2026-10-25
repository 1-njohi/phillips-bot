#!/usr/bin/env python3
"""
login_bid_test.py — log in, grab nonce, place one bid.

Usage:
    python login_bid_test.py \
        --user you@example.com --pass 'hunter2' \
        --product 56332 --slug kdh-018g-toyota-kluger \
        --increment 5000 \
        [--amount 645000] \
        [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys

import httpx


BASE = "https://phillipsauctioneers.co.ke"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)


# ─────────────────────────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────────────────────────

class Site:
    def __init__(self, verbose: bool = True):
        self.verbose = verbose
        self.logged_in = False
        self.http = httpx.Client(
            base_url=BASE,
            timeout=httpx.Timeout(20.0, connect=8.0),
            headers={
                "User-Agent": UA,
                "Accept-Language": "en-KE,en;q=0.9",
            },
            follow_redirects=True,
            http2=True,
        )

    # ── helpers ─────────────────────────────────────────────────

    def _log(self, *a):
        if self.verbose:
            print(*a, file=sys.stderr)

    def _has_logged_in_cookie(self) -> bool:
        return any(
            c.name.startswith("wordpress_logged_in")
            for c in self.http.cookies.jar
        )

    def _body_logged_in(self, html: str) -> bool:
        return bool(
            re.search(r'<body[^>]*class="[^"]*\blogged-in\b', html)
        )

    # ── 1. Login ────────────────────────────────────────────────

    def login(self, user: str, password: str) -> bool:
        # Prime the login page so wordpress_test_cookie is set.
        self.http.get("/wp-login.php")

        r = self.http.post(
            "/wp-login.php",
            data={
                "log": user,
                "pwd": password,
                "wp-submit": "Log In",
                "redirect_to": f"{BASE}/my-account/",
                "testcookie": "1",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{BASE}/wp-login.php",
                "Origin": BASE,
            },
        )
        self._log(f"[login] HTTP {r.status_code}, final URL: {r.url}")

        has_cookie = self._has_logged_in_cookie()
        r2 = self.http.get("/my-account/")
        body_ok = self._body_logged_in(r2.text)

        if has_cookie and body_ok:
            self.logged_in = True
            self._log("[login] OK")
            return True

        if "login_error" in r.text or "incorrect" in r.text.lower():
            self._log("[login] rejected — wrong credentials")
        elif not has_cookie:
            self._log("[login] no wordpress_logged_in cookie set")
        else:
            self._log("[login] cookie present but /my-account/ still shows login form")
        return False

    # ── 2. Nonce ────────────────────────────────────────────────

    _NONCE_RE = re.compile(r'"add_bid"\s*:\s*"([a-f0-9]+)"')

    def fetch_add_bid_nonce(self, slug: str) -> str | None:
        r = self.http.get(
            f"/product/{slug}/",
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Referer": f"{BASE}/",
            },
        )
        self._log(f"[nonce] product page HTTP {r.status_code} "
                  f"({len(r.text)} bytes)")
        if r.status_code != 200:
            return None
        m = self._NONCE_RE.search(r.text)
        if not m:
            self._log("[nonce] no add_bid nonce found in page HTML")
            return None
        nonce = m.group(1)
        self._log(f"[nonce] add_bid = {nonce}")
        return nonce

    # ── 3. Current price via Store API ──────────────────────────

    def read_current_price(self, product_id: int) -> int | None:
        r = self.http.get(f"/wp-json/wc/store/v1/products/{product_id}")
        if r.status_code != 200:
            self._log(f"[price] store read HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except json.JSONDecodeError:
            self._log("[price] store read: non-JSON body")
            return None

        price_str = (data.get("prices") or {}).get("price")
        if price_str is None:
            self._log("[price] no prices.price in store payload")
            return None
        try:
            price = int(price_str)
        except (TypeError, ValueError):
            return None

        state = (data.get("add_to_cart") or {}).get("text")
        self._log(f"[price] current = {price:,}  state = {state!r}")
        return price

    # ── 4. Bid ──────────────────────────────────────────────────

    def fire_bid(
        self,
        product_id: int,
        slug: str,
        amount: int,
        nonce: str,
    ) -> dict | None:
        body = {
            "action":   "yith_wcact_add_bid",
            "security": nonce,
            "currency": "KES",
            "bid":      str(amount),
            "product":  str(product_id),
        }
        headers = {
            "Content-Type":     "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Referer":          f"{BASE}/product/{slug}/",
            "Origin":           BASE,
            "Accept":           "*/*",
        }
        self._log(f"[bid] POST {amount:,} (nonce={nonce})")
        r = self.http.post(
            "/wp-admin/admin-ajax.php",
            data=body,
            headers=headers,
        )
        self._log(f"[bid] HTTP {r.status_code}")

        text = r.text.strip()
        if text == "-1":
            self._log("[bid] server returned -1 → nonce rejected")
            return {"success": False, "data": {"raw": "-1"}}

        try:
            return r.json()
        except json.JSONDecodeError:
            self._log(f"[bid] non-JSON response: {text[:300]}")
            return None

    # ── 5. Teardown ─────────────────────────────────────────────

    def close(self):
        self.http.close()


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--user", required=True)
    p.add_argument("--pass", dest="password", required=True)
    p.add_argument("--product", type=int, required=True)
    p.add_argument("--slug", required=True)
    p.add_argument("--increment", type=int, default=5000)
    p.add_argument("--amount", type=int, default=None,
                   help="Exact bid amount. Overrides current+increment.")
    p.add_argument("--dry-run", action="store_true",
                   help="Log in, fetch nonce, read price — do NOT bid.")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    site = Site(verbose=not args.quiet)

    try:
        if not site.login(args.user, args.password):
            sys.exit(2)

        nonce = site.fetch_add_bid_nonce(args.slug)
        if not nonce:
            sys.exit(3)

        current = site.read_current_price(args.product)
        if current is None:
            print("WARN: could not read current price", file=sys.stderr)

        if args.amount is not None:
            amount = args.amount
        elif current is not None:
            amount = current + args.increment
        else:
            print("ERROR: no --amount and no current price to derive from",
                  file=sys.stderr)
            sys.exit(4)

        print(f"planned bid: {amount:,} KES")

        if args.dry_run:
            print("dry-run: skipping POST")
            return

        resp = site.fire_bid(args.product, args.slug, amount, nonce)
        print("response:")
        print(json.dumps(resp, indent=2, ensure_ascii=False))

        if resp and resp.get("success"):
            print(f"\n✓ bid accepted at {amount:,} KES")
        else:
            print("\n✗ bid not accepted", file=sys.stderr)

    finally:
        site.close()


if __name__ == "__main__":
    main()