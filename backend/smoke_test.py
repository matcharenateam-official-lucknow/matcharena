"""MatchArena backend smoke test — run with the backend server up:

    ./venv/Scripts/python smoke_test.py

Covers: static serving + path-block, register/login, WhatsApp OTP (dev mode),
profile, avatar upload, FC card + upgrade, session refresh/logout, security headers.
Exit code 0 = all pass.
"""
import io
import json
import random
import sys

import requests
from PIL import Image

BASE = "http://127.0.0.1:8000"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok  " if cond else "  FAIL") + f"  {name}" + (f"  [{extra}]" if extra else ""))


def main() -> int:
    s = requests.Session()
    email = f"smoke{random.randint(10000, 99999)}@test.dev"
    password = "S3cure-pass!"

    # --- health + static site + security headers -------------------------
    r = s.get(f"{BASE}/api/health")
    check("health", r.ok and r.json().get("ok") is True)

    r = s.get(f"{BASE}/")
    check("index.html served", r.ok and "MatchArena" in r.text)
    check("CSP header", "default-src 'self'" in r.headers.get("content-security-policy", ""))
    check("nosniff header", r.headers.get("x-content-type-options") == "nosniff")

    check("style.css served", s.get(f"{BASE}/style.css").status_code == 200)
    check("app.js served", s.get(f"{BASE}/app.js").status_code == 200)
    check("backend/.env blocked", s.get(f"{BASE}/backend/.env").status_code == 404)
    check("backend source blocked", s.get(f"{BASE}/backend/main.py").status_code == 404)
    check("traversal blocked", s.get(f"{BASE}/..%2f..%2fbackend%2f.env").status_code in (404, 400))

    # --- register + login -------------------------------------------------
    r = s.post(f"{BASE}/api/auth/register", json={"name": "Smoke Tester", "email": email, "password": password})
    check("register", r.ok and r.json().get("ok") is True)
    check("access cookie set", "ma_at" in s.cookies)
    check("refresh cookie set", "ma_rt" in s.cookies)

    s2 = requests.Session()
    r = s2.post(f"{BASE}/api/auth/login", json={"email": email, "password": "wrong-password"})
    check("wrong password rejected", r.status_code == 401)
    r = s2.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    check("login ok", r.ok and r.json().get("ok") is True)
    check("no user enumeration", requests.post(
        f"{BASE}/api/auth/login", json={"email": "nobody@nowhere.dev", "password": "x"}
    ).status_code == 401)

    # --- WhatsApp OTP (DEV_MODE prints to console, returns dev_code) ------
    phone = f"98765{random.randint(10000, 99999)}"
    r = s.post(f"{BASE}/api/auth/otp/send", json={"channel": "whatsapp", "target": phone})
    body = r.json() if r.ok else {}
    check("otp send (dev)", r.ok and body.get("dev_code") and len(body["dev_code"]) == 6, str(body)[:80])
    code = body.get("dev_code", "")

    r = s.post(f"{BASE}/api/auth/otp/verify",
               json={"channel": "whatsapp", "target": phone, "code": "000000", "name": "OTP Player"})
    check("wrong otp rejected", r.status_code == 401)
    r = s.post(f"{BASE}/api/auth/otp/verify",
               json={"channel": "whatsapp", "target": phone, "code": code})
    check("name_required does NOT burn code", r.status_code == 422
          and r.json().get("detail") == "name_required", str(r.json()))
    r = s.post(f"{BASE}/api/auth/otp/verify",
               json={"channel": "whatsapp", "target": phone, "code": code, "name": "OTP Player"})
    check("otp verify + phone signup", r.ok and r.json().get("user", {}).get("phone_verified") is True)
    check("otp code single-use", s.post(
        f"{BASE}/api/auth/otp/verify",
        json={"channel": "whatsapp", "target": phone, "code": code, "name": "OTP Player"},
    ).status_code == 401)

    # --- profile + avatar + card -----------------------------------------
    s3 = requests.Session()
    s3.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    r = s3.get(f"{BASE}/api/me")
    check("me", r.ok and r.json()["user"]["email"] == email)

    r = s3.patch(f"{BASE}/api/me", json={"display_name": "Smoke Renamed"})
    check("rename", r.ok and r.json()["user"]["name"] == "Smoke Renamed")

    png = io.BytesIO()
    Image.new("RGB", (900, 900), (192, 154, 69)).save(png, format="PNG")
    r = s3.post(f"{BASE}/api/me/avatar", files={"file": ("me.png", png.getvalue(), "image/png")})
    check("avatar upload", r.ok and r.json().get("avatar_url", "").startswith("/uploads/avatars/"))
    av = s3.get(BASE + r.json()["avatar_url"])
    check("avatar served", av.ok and av.headers.get("content-type", "").startswith("image/"))
    r = s3.post(f"{BASE}/api/me/avatar", files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")})
    check("html upload rejected", r.status_code == 415)

    r = s3.get(f"{BASE}/api/me/card")
    card = r.json().get("card", {})
    check("fc card shape", r.ok and {"ovr", "position", "tier", "stats"} <= set(card)
          and set(card["stats"]) == {"pac", "sho", "pas", "dri", "def", "phy"}, str(card.get("ovr")))
    before_ovr = card.get("ovr", 0)

    r = s3.post(f"{BASE}/api/dev/match", json={"sport": "football", "won": True, "mvp": True})
    body = r.json() if r.ok else {}
    check("dev match recorded", r.ok and body.get("xp_gained") == 125)
    check("card upgraded", body.get("after", {}).get("ovr", 0) >= before_ovr,
          f"{before_ovr} -> {body.get('after', {}).get('ovr')}")
    check("card history grows", len(body.get("after", {}).get("history", [])) >= 1)

    r = s3.get(f"{BASE}/api/leaderboard?limit=25")
    check("leaderboard", r.ok and any(p["user_id"] == body.get("after", {}).get("user_id")
                                      for p in r.json().get("players", [])))

    # --- session lifecycle ------------------------------------------------
    before_rt = s3.cookies.get("ma_rt")
    r = s3.post(f"{BASE}/api/auth/refresh")
    check("refresh rotates session", r.ok and s3.cookies.get("ma_rt") != before_rt)
    r = s3.post(f"{BASE}/api/auth/refresh")   # replay the OLD cookie
    # (old cookie no longer in jar; simulate replay explicitly)
    r = requests.post(f"{BASE}/api/auth/refresh", cookies={"ma_rt": before_rt})
    check("replayed refresh rejected", r.status_code == 401)

    s3.post(f"{BASE}/api/auth/logout")
    check("logout clears session", s3.get(f"{BASE}/api/me").status_code == 401)

    # --- admin console: roles, venues, matches, promos, staff -------------
    import pathlib

    env = {}
    for line in (pathlib.Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    admin_email = env.get("ADMIN_EMAIL", "admin@matcharena.local")
    admin_pass = env.get("ADMIN_PASSWORD", "")

    r = s.get(f"{BASE}/admin")
    check("admin page served",
          r.ok and "Admin Console" in r.text and r.headers.get("content-type", "").startswith("text/html"))
    check("admin unauth -> 401", requests.get(f"{BASE}/api/admin/me").status_code == 401)

    # NOTE: session `s` was re-logged-in by the OTP block above (it is the OTP
    # player now), so the email account gets its own fresh session here.
    sp = requests.Session()
    r = sp.post(f"{BASE}/api/auth/login", json={"email": email, "password": password})
    check("player re-login for admin checks", r.ok)
    check("player blocked from admin", sp.get(f"{BASE}/api/admin/me").status_code == 403)

    sa = requests.Session()
    r = sa.post(f"{BASE}/api/auth/login", json={"email": admin_email, "password": admin_pass})
    check("superadmin login", r.ok, str(r.status_code))
    r = sa.get(f"{BASE}/api/admin/me")
    check("superadmin me", r.ok and r.json().get("staff", {}).get("role") == "superadmin")

    r = sa.post(f"{BASE}/api/admin/venues",
                json={"name": "Smoke Turf", "city": "Pune", "format": "5v5",
                      "price_per_slot": 500, "ground_cost": 2000})
    check("create venue", r.ok and r.json()["venue"]["id"] > 0)
    vid = r.json()["venue"]["id"]

    # --- time-based, per-format venue pricing (from the HOF design refs) ---
    r = sa.patch(f"{BASE}/api/admin/venues/{vid}",
                 json={"phone": "+91 90000 00000", "morning_cutoff": 12,
                       "format_costs": {"5v5": {"base": 550, "weekday_morning": 350,
                                                "weekend": 700, "weekend_morning": 450}}})
    v = r.json().get("venue", {})
    check("venue pricing saved",
          r.ok and v.get("morning_cutoff") == 12
          and v.get("phone") == "+91 90000 00000"
          and json.loads(v.get("format_costs") or "{}").get("5v5", {}).get("base") == 550,
          str(v.get("format_costs"))[:80])

    def auto_price(starts_at):
        rb = sa.post(f"{BASE}/api/admin/matches",
                     json={"title": "Auto Priced", "sport": "football", "venue_id": vid,
                           "sides": [{"name": "Red"}, {"name": "Blue"}], "price": 0,
                           "starts_at": starts_at})
        return rb.json()["match"]["price"] if rb.ok else -1

    # 2026-10-10 = Saturday, 2026-10-12 = Monday (naive = venue-local kickoff)
    check("auto price weekday evening (base)", auto_price("2026-10-12T20:00") == 550)
    check("auto price weekday morning", auto_price("2026-10-13T09:00") == 350)
    check("auto price weekend evening", auto_price("2026-10-10T20:00") == 700)
    check("auto price weekend morning", auto_price("2026-10-11T10:00") == 450)
    r = sa.post(f"{BASE}/api/admin/matches",
                json={"title": "Explicit Price", "sport": "football", "venue_id": vid,
                      "sides": [{"name": "Red"}, {"name": "Blue"}], "price": 999,
                      "starts_at": "2026-10-12T20:00"})
    check("explicit price wins over auto", r.ok and r.json()["match"]["price"] == 999)

    # secret visibility + game controller (from the HOF create-match refs)
    admin_uid = sa.get(f"{BASE}/api/admin/me").json()["user"]["id"]
    r = sa.post(f"{BASE}/api/admin/matches",
                json={"title": "Hidden Fixture", "sport": "football", "venue_id": vid,
                      "sides": [{"name": "Red"}, {"name": "Blue"}], "visibility": "secret",
                      "controller_id": admin_uid})
    hm = r.json().get("match", {})
    check("secret match with controller",
          r.ok and hm.get("visibility") == "secret" and hm.get("controller_id") == admin_uid,
          str(hm)[:100])
    r = sa.patch(f"{BASE}/api/admin/matches/{hm['id']}",
                 json={"visibility": "public", "controller_id": 0})
    check("patch visibility + controller",
          r.ok and r.json()["match"]["visibility"] == "public"
          and r.json()["match"]["controller_id"] is None, str(r.json())[:100])
    r = sa.post(f"{BASE}/api/admin/matches",
                json={"title": "Bad Vis", "sport": "football", "venue_id": vid,
                      "sides": [{"name": "A"}, {"name": "B"}], "visibility": "wat"})
    check("bad visibility rejected", r.status_code == 422)

    body = {"title": "Smoke Showdown", "sport": "football", "venue_id": vid,
            "sides": [{"name": "Alpha"}, {"name": "Bravo"}], "capacity": 10,
            "price": 400, "ground_cost": 1500, "chief_cost": 300}
    r = sa.post(f"{BASE}/api/admin/matches", json=body)
    check("create match", r.ok and r.json()["match"]["venue"] == "Smoke Turf", str(r.json())[:100])
    mid = r.json()["match"]["id"]

    r = sa.get(f"{BASE}/api/admin/matches?status=active&page_size=13")
    check("list matches", r.ok and any(m["id"] == mid for m in r.json()["items"]))

    det = sa.get(f"{BASE}/api/admin/matches/{mid}").json()["match"]
    check("match detail earnings",
          det["revenue"] == det["price"] * det["enrolled"]
          and det["platform_revenue"] == det["revenue"] - det["ground_cost"] - det["chief_cost"],
          f"rev={det['revenue']} net={det['platform_revenue']}")

    pid = sp.get(f"{BASE}/api/me").json()["user"]["id"]
    r = sa.post(f"{BASE}/api/admin/matches/{mid}/result",
                json={"scores": {"Alpha": 3, "Bravo": 1}, "mvp_user_id": pid,
                      "participants": [{"user_id": pid, "side": "Alpha"}]})
    check("record result", r.ok and r.json()["results"][0]["xp_gained"] == 125)
    det = sa.get(f"{BASE}/api/admin/matches/{mid}").json()["match"]
    check("enrolled + revenue after result",
          det["enrolled"] == 1 and det["revenue"] == 400 and det["status"] == "completed",
          f"enr={det['enrolled']} rev={det['revenue']}")
    card = sp.get(f"{BASE}/api/me/card").json()["card"]
    check("card upgraded via admin result", card["matches"] >= 2 and card["wins"] >= 2,
          f"m={card['matches']} w={card['wins']}")
    r = sa.post(f"{BASE}/api/admin/matches/{mid}/result",
                json={"scores": {"Alpha": 1, "Bravo": 0},
                      "participants": [{"user_id": pid, "side": "Alpha"}]})
    check("result recorded only once", r.status_code == 409)

    r = sa.get(f"{BASE}/api/admin/overview")
    st = r.json().get("stats", {})
    check("overview stats", r.ok and st.get("venues", 0) >= 1
          and "active_matches" in st and "revenue" in st)

    r = sa.get(f"{BASE}/api/admin/participants?q={email.split('@')[0]}")
    check("participants searchable", r.ok and any(p["id"] == pid for p in r.json()["items"]))

    code = f"SMK{random.randint(100, 999)}"
    r = sa.post(f"{BASE}/api/admin/promos", json={"code": code, "percent": 25, "max_uses": 5})
    check("create promo", r.ok, str(r.json())[:80])
    promo_id = r.json()["promo"]["id"]
    r = sa.patch(f"{BASE}/api/admin/promos/{promo_id}", json={"active": False})
    check("pause promo", r.ok and r.json()["promo"]["active"] == 0)
    check("non-staff cannot create promo",
          sp.post(f"{BASE}/api/admin/promos", json={"code": "HACK1", "percent": 90}).status_code == 403)

    r = sa.post(f"{BASE}/api/admin/tags",
                json={"name": f"smoke-{random.randint(100, 999)}", "color": "jade"})
    check("create tag", r.ok)
    tid = r.json()["tag"]["id"]
    check("delete tag", sa.delete(f"{BASE}/api/admin/tags/{tid}").ok)

    r = sa.get(f"{BASE}/api/admin/chiefs?period=month")
    check("chiefs leaderboard", r.ok and isinstance(r.json()["items"], list))

    # --- role model: football chief, then venue admin scoping -------------
    chief_email = f"chief{random.randint(10000, 99999)}@test.dev"
    sc = requests.Session()
    sc.post(f"{BASE}/api/auth/register",
            json={"name": "Chief Smoke", "email": chief_email, "password": password})
    chief_uid = sc.get(f"{BASE}/api/me").json()["user"]["id"]
    check("non-staff chief blocked", sc.get(f"{BASE}/api/admin/me").status_code == 403)

    r = sa.post(f"{BASE}/api/admin/staff", json={"email": chief_email, "role": "football_chief"})
    check("grant football_chief", r.ok and r.json()["user"]["id"] == chief_uid)
    me = sc.get(f"{BASE}/api/admin/me").json()
    check("chief session active", me.get("staff", {}).get("role") == "football_chief")
    check("chief cannot create match",
          sc.post(f"{BASE}/api/admin/matches", json=body).status_code == 403)
    check("chief cannot create venue",
          sc.post(f"{BASE}/api/admin/venues", json={"name": "Nope Arena", "city": "X"}).status_code == 403)
    check("chief cannot manage staff", sc.get(f"{BASE}/api/admin/staff").status_code == 403)
    seen = sc.get(f"{BASE}/api/admin/matches").json()["items"]
    check("chief sees only assigned matches", all(m["id"] != mid for m in seen), str(len(seen)))

    r = sa.post(f"{BASE}/api/admin/venues", json={"name": "Other Turf", "city": "Delhi"})
    vid2 = r.json()["venue"]["id"]
    r = sa.post(f"{BASE}/api/admin/matches",
                json={"title": "Delhi Clash", "sport": "football", "venue_id": vid2,
                      "sides": [{"name": "A"}, {"name": "B"}]})
    mid2 = r.json()["match"]["id"]

    r = sa.post(f"{BASE}/api/admin/staff",
                json={"email": email, "role": "venue_admin", "venue_id": vid})
    check("grant venue_admin", r.ok, str(r.json())[:100])
    me = sp.get(f"{BASE}/api/admin/me").json()
    check("venue admin me", me.get("staff", {}).get("role") == "venue_admin",
          str(me)[:120])
    seen = sp.get(f"{BASE}/api/admin/matches").json()["items"]
    check("venue admin scoped to own venue",
          seen and all(m["venue_id"] == vid for m in seen), str(len(seen)))
    check("venue admin cannot edit other venue",
          sp.patch(f"{BASE}/api/admin/matches/{mid2}", json={"status": "cancelled"}).status_code == 403)
    check("venue admin cannot grant roles",
          sp.post(f"{BASE}/api/admin/staff",
                  json={"email": email, "role": "football_chief"}).status_code == 403)

    rh = sa.get(f"{BASE}/api/admin/roleholders").json()["items"]
    check("roleholders listed", any(x["user_id"] == chief_uid for x in rh))

    staff_rows = sa.get(f"{BASE}/api/admin/staff").json()["items"]
    sid = next((x["id"] for x in staff_rows if x["user_id"] == chief_uid), 0)
    check("staff listed", sid > 0)
    check("revoke role", sid > 0 and sa.delete(f"{BASE}/api/admin/staff/{sid}").ok)
    check("chief loses access after revoke", sc.get(f"{BASE}/api/admin/me").status_code == 403)

    # --- google not configured -> clean 503 -------------------------------
    r = requests.post(f"{BASE}/api/auth/google", json={"credential": "x"})
    check("google clean error when unconfigured", r.status_code == 503)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
