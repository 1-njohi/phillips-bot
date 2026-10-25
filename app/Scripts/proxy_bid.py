import httpx, re

# --- Step 1: log in exactly like the script does, on a throwaway client
c = httpx.Client(base_url="https://phillipsauctioneers.co.ke",
                 follow_redirects=True, http2=True)
c.get("/wp-login.php")
c.post("/wp-login.php", data={
    "log": "kodembe@yahoo.com",
    "pwd": "maurice@otieno",
    "wp-submit": "Log In",
    "redirect_to": "https://phillipsauctioneers.co.ke/my-account/",
    "testcookie": "1",
})
print("cookies:", [ck.name for ck in c.cookies.jar])

# --- Step 2: fetch the product page and scrape the nonce
r = c.get("/product/kde-429z-subaru-forester/")
m = re.search(r'"add_bid"\s*:\s*"([a-f0-9]+)"', r.text)
print("nonce from page:", m.group(1) if m else "NOT FOUND")
print("cf-cache-status:", r.headers.get("cf-cache-status"))

# --- Step 3: POST the bid with the same nonce
r2 = c.post("/wp-admin/admin-ajax.php", data={
    "action": "yith_wcact_add_bid",
    "security": m.group(1),
    "currency": "KES",
    "bid": "270000",
    "product": "56922",
}, headers={
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://phillipsauctioneers.co.ke/product/kde-429z-subaru-forester/",
})
print("status:", r2.status_code)
print("body:", r2.text[:500])
print("cookie header sent on POST:",
      "wordpress_sec_" in r2.request.headers.get("cookie", ""))