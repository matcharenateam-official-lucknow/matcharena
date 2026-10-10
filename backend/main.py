"""MatchArena backend — FastAPI app.

Run from the backend/ folder:   python main.py   (or: uvicorn main:app)
Serves the static site + /api/* on one origin (no CORS needed in production).
"""
import logging
import logging.handlers
import re
import secrets
from io import BytesIO
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import cards
import config
import db
import providers
import security as sec

# ---------------------------------------------------------------- logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            config.LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        ),
    ],
)
log = logging.getLogger("matcharena")

db.init_db()

app = FastAPI(title="MatchArena API", docs_url="/api/docs", openapi_url="/api/openapi.json")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
SPORTS = {"football", "cricket", "badminton", "basketball", "pickleball"}

# ---------------------------------------------------------------- startup: admin bootstrap
if config.ADMIN_EMAIL and config.ADMIN_PASSWORD:
    if not db.user_by(email=config.ADMIN_EMAIL.lower()):
        db.q_insert(
            "INSERT INTO users(email, password_hash, display_name, role, email_verified, created_at) "
            "VALUES(?, ?, ?, 'admin', 1, ?)",
            (
                config.ADMIN_EMAIL.lower(),
                sec.hash_password(config.ADMIN_PASSWORD),
                "Arena Admin",
                db.utcnow(),
            ),
        )
        log.info("bootstrapped admin account %s", config.ADMIN_EMAIL)


# ---------------------------------------------------------------- middleware
CSP = (
    "default-src 'self'; "
    "script-src 'self' https://accounts.google.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "frame-src https://accounts.google.com; "
    "connect-src 'self' https://accounts.google.com; "
    "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def guard(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method == "OPTIONS":
        resp = JSONResponse({}, status_code=204)
    else:
        resp = await call_next(request)

    # security headers on everything
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    if config.IS_SECURE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    # CORS for split-origin development only (site on :8765, API on :8000)
    if origin and origin in config.ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, OPTIONS"
        resp.headers["Vary"] = "Origin"

    if request.method in ("POST", "PATCH", "PUT", "DELETE") and origin:
        if origin not in config.ALLOWED_ORIGINS:
            return JSONResponse({"ok": False, "error": "bad_origin"}, status_code=403)

    # body size cap (avatar uploads are limited further in the route)
    length = request.headers.get("content-length")
    if length and int(length) > 6_000_000:
        return JSONResponse({"ok": False, "error": "payload_too_large"}, status_code=413)
    return resp


# ---------------------------------------------------------------- helpers
def _mask_phone(p: str) -> str:
    return p[:5] + "•" * max(0, len(p) - 9) + p[-4:] if len(p) >= 9 else p


def _mask_email(e: str) -> str:
    name, _, dom = e.partition("@")
    return (name[:2] + "•" * max(1, len(name) - 2)) + "@" + dom


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) < 11 or len(digits) > 15:
        raise ValueError("bad_phone")
    return "+" + digits


def current_user(request: Request) -> dict:
    token = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.cookies.get("ma_at", "")
    if not token:
        raise HTTPException(401, detail="not_authenticated")
    claims = sec.decode_access_token(token)
    if not claims:
        raise HTTPException(401, detail="bad_token")
    user = db.user_by(id=int(claims["sub"]))
    if not user:
        raise HTTPException(401, detail="bad_token")
    return dict(user)


def issue_session(request: Request, response: Response, user: dict) -> str:
    access = sec.issue_access_token(user["id"], user["role"])
    raw, token_hash = sec.new_refresh_token()
    ua = (request.headers.get("user-agent") or "")[:200]
    db.q_insert(
        "INSERT INTO refresh_tokens(user_id, token_hash, expires_at, user_agent, created_at) "
        "VALUES(?, ?, ?, ?, ?)",
        (user["id"], token_hash, sec.utcnow_plus(days=config.REFRESH_DAYS), ua, db.utcnow()),
    )
    secure = config.IS_SECURE
    response.set_cookie(
        "ma_at", access, max_age=config.ACCESS_MINUTES * 60,
        httponly=True, samesite="lax", secure=secure, path="/",
    )
    response.set_cookie(
        "ma_rt", raw, max_age=config.REFRESH_DAYS * 86400,
        httponly=True, samesite="lax", secure=secure, path="/api/auth",
    )
    return access


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "name": u["display_name"],
        "email": u["email"],
        "phone": u["phone"],
        "avatar_url": u["avatar_url"],
        "role": u["role"],
        "email_verified": bool(u["email_verified"]),
        "phone_verified": bool(u["phone_verified"]),
        "created_at": u["created_at"],
    }


def touch_login(user_id: int) -> None:
    db.q(
        "UPDATE users SET last_login_at = ?, failed_logins = 0, locked_until = NULL WHERE id = ?",
        (db.utcnow(), user_id),
    )


def ip_of(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def check_rate(request: Request, key: str, max_hits: int, window: int) -> None:
    ok, retry = sec.limiter.allow(f"{key}:{ip_of(request)}", max_hits, window)
    if not ok:
        raise HTTPException(429, detail=f"rate_limited:{retry}")


# ================================================================ AUTH: email + password
@app.post("/api/auth/register")
def register(request: Request, response: Response, body: dict):
    check_rate(request, "register", 5, 3600)
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not (2 <= len(name) <= 60):
        raise HTTPException(422, detail="bad_name")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(422, detail="bad_email")
    if len(password) < 8 or len(password) > 128:
        raise HTTPException(422, detail="bad_password")
    if db.user_by(email=email):
        raise HTTPException(409, detail="email_taken")
    uid = db.q_insert(
        "INSERT INTO users(email, password_hash, display_name, created_at) VALUES(?, ?, ?, ?)",
        (email, sec.hash_password(password), name, db.utcnow()),
    )
    db.ensure_stats(uid)
    user = db.user_by(id=uid)
    issue_session(request, response, user)
    log.info("register email=%s ip=%s", email, ip_of(request))
    return {"ok": True, "user": public_user(user)}


@app.post("/api/auth/login")
def login(request: Request, response: Response, body: dict):
    check_rate(request, "login", config.LOGIN_MAX_PER_15MIN, 900)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = db.user_by(email=email)
    now = db.utcnow()
    if not user:
        sec.dummy_verify()
        log.info("login FAIL (no such user) email=%s ip=%s", email, ip_of(request))
        raise HTTPException(401, detail="bad_credentials")
    if user["locked_until"] and user["locked_until"] > now:
        raise HTTPException(429, detail="account_locked")
    if not user["password_hash"] or not sec.verify_password(password, user["password_hash"]):
        failed = user["failed_logins"] + 1
        locked = sec.utcnow_plus(minutes=15) if failed >= 10 else None
        db.q(
            "UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
            (failed, locked, user["id"]),
        )
        log.info("login FAIL email=%s ip=%s failed=%d", email, ip_of(request), failed)
        raise HTTPException(401, detail="bad_credentials")
    touch_login(user["id"])
    user = db.user_by(id=user["id"])
    issue_session(request, response, user)
    log.info("login OK email=%s ip=%s", email, ip_of(request))
    return {"ok": True, "user": public_user(user)}


# ================================================================ AUTH: OTP (WhatsApp / email)
def _otp_channel_target(channel: str, target: str) -> tuple[str, str]:
    if channel == "whatsapp":
        try:
            return "whatsapp", normalize_phone(target)
        except ValueError:
            raise HTTPException(422, detail="bad_phone")
    if channel == "email":
        t = (target or "").strip().lower()
        if not EMAIL_RE.match(t):
            raise HTTPException(422, detail="bad_email")
        return "email", t
    raise HTTPException(422, detail="bad_channel")


@app.post("/api/auth/otp/send")
def otp_send(request: Request, body: dict):
    channel, target = _otp_channel_target(body.get("channel"), body.get("target"))
    check_rate(request, f"otp-cooldown-{channel}", 1, config.OTP_SEND_COOLDOWN)
    check_rate(request, f"otp-day-{channel}", config.OTP_MAX_PER_DAY, 86400)
    ok_t, _ = sec.limiter.allow(f"otp-tgt:{target}", 1, config.OTP_SEND_COOLDOWN)
    if not ok_t:
        raise HTTPException(429, detail="rate_limited:90")

    code = sec.new_otp_code()
    expire_min = 5
    db.q(
        "UPDATE otps SET consumed_at = ? WHERE channel = ? AND target = ? AND consumed_at IS NULL",
        (db.utcnow(), channel, target),
    )
    db.q_insert(
        "INSERT INTO otps(channel, target, purpose, code_hash, expires_at, created_at) "
        "VALUES(?, ?, 'login', ?, ?, ?)",
        (channel, target, sec.otp_hash(code, target, "login"),
         sec.utcnow_plus(minutes=expire_min), db.utcnow()),
    )
    try:
        mode = providers.send_otp(channel, target, code, expire_min)
    except providers.ProviderError as e:
        log.error("OTP send failed channel=%s target=%s err=%s", channel, target, e)
        raise HTTPException(502, detail="otp_send_failed")
    log.info("OTP sent channel=%s target=%s via=%s", channel, _mask_phone(target) if channel == "whatsapp" else _mask_email(target), mode)
    resp = {
        "ok": True,
        "channel": channel,
        "target": _mask_phone(target) if channel == "whatsapp" else _mask_email(target),
        "cooldown": config.OTP_SEND_COOLDOWN,
    }
    if mode == "dev":
        resp["dev_code"] = code
        resp["warning"] = "DEV_MODE: code also printed to the server console"
    return resp


@app.post("/api/auth/otp/verify")
def otp_verify(request: Request, response: Response, body: dict):
    check_rate(request, "otp-verify", 12, 300)
    channel, target = _otp_channel_target(body.get("channel"), body.get("target"))
    code = (body.get("code") or "").strip()
    if not re.fullmatch(r"\d{6}", code):
        raise HTTPException(422, detail="bad_code")

    row = db.q(
        "SELECT * FROM otps WHERE channel = ? AND target = ? AND purpose = 'login' "
        "AND consumed_at IS NULL ORDER BY id DESC LIMIT 1",
        (channel, target),
        one=True,
    )
    if not row:
        raise HTTPException(401, detail="invalid_code")
    if row["attempts"] >= 5:
        raise HTTPException(429, detail="code_attempts_exceeded")
    if row["expires_at"] <= db.utcnow() or not sec.otp_matches(code, target, "login", row["code_hash"]):
        db.q("UPDATE otps SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        log.info("OTP FAIL channel=%s target=%s ip=%s", channel, target, ip_of(request))
        raise HTTPException(401, detail="invalid_code")
    # validate the payload BEFORE burning the code: a new phone/email user
    # must be able to answer "name_required" and retry with the SAME code.
    lookup = {"phone": target} if channel == "whatsapp" else {"email": target}
    user = db.user_by(**lookup)
    name = (body.get("name") or "").strip()
    password = body.get("password") or ""
    if not user:
        if not (2 <= len(name) <= 60):
            raise HTTPException(422, detail="name_required")
        if password and len(password) < 8:
            raise HTTPException(422, detail="bad_password")

    # code matched and payload is sane -> consume exactly once
    db.q("UPDATE otps SET consumed_at = ? WHERE id = ?", (db.utcnow(), row["id"]))

    if not user:
        cols, vals = ["display_name", "created_at"], [name, db.utcnow()]
        if channel == "whatsapp":
            cols += ["phone", "phone_verified"]
            vals += [target, 1]
        else:
            cols += ["email", "email_verified"]
            vals += [target, 1]
        if password:
            cols.append("password_hash")
            vals.append(sec.hash_password(password))
        uid = db.q_insert(
            f"INSERT INTO users({', '.join(cols)}) VALUES({', '.join('?' * len(vals))})",
            tuple(vals),
        )
        db.ensure_stats(uid)
        user = db.user_by(id=uid)
    else:
        if channel == "whatsapp":
            db.q("UPDATE users SET phone_verified = 1 WHERE id = ?", (user["id"],))
        else:
            db.q("UPDATE users SET email_verified = 1 WHERE id = ?", (user["id"],))
    touch_login(user["id"])
    user = db.user_by(id=user["id"])
    issue_session(request, response, user)
    log.info("OTP OK channel=%s target=%s ip=%s", channel, target, ip_of(request))
    return {"ok": True, "user": public_user(user)}


# ================================================================ AUTH: Google
@app.post("/api/auth/google")
def google_signin(request: Request, response: Response, body: dict):
    check_rate(request, "google", 20, 300)
    if not config.GOOGLE_CLIENT_ID:
        raise HTTPException(503, detail="google_not_configured")
    credential = body.get("credential") or ""
    try:
        from google.auth.transport import requests as ga_requests
        from google.oauth2 import id_token as ga_idtoken

        info = ga_idtoken.verify_oauth2_token(
            credential, ga_requests.Request(), audience=config.GOOGLE_CLIENT_ID
        )
    except Exception:
        log.warning("google token verify failed ip=%s", ip_of(request))
        raise HTTPException(401, detail="bad_google_token")
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise HTTPException(401, detail="bad_google_token")

    sub, email = info.get("sub"), (info.get("email") or "").lower()
    user = db.user_by(google_sub=sub) or (db.user_by(email=email) if email else None)
    if user:
        db.q(
            "UPDATE users SET google_sub = ?, email_verified = 1, avatar_url = COALESCE(?, avatar_url) "
            "WHERE id = ?",
            (sub, info.get("picture"), user["id"]),
        )
    else:
        uid = db.q_insert(
            "INSERT INTO users(email, google_sub, display_name, avatar_url, email_verified, created_at) "
            "VALUES(?, ?, ?, ?, 1, ?)",
            (
                email or None, sub,
                (info.get("name") or email or "Player").strip()[:60],
                info.get("picture"), db.utcnow(),
            ),
        )
        db.ensure_stats(uid)
        user = db.user_by(id=uid)
    touch_login(user["id"])
    user = db.user_by(id=user["id"])
    issue_session(request, response, user)
    log.info("google OK sub=%s ip=%s", sub[:8], ip_of(request))
    return {"ok": True, "user": public_user(user)}


# ================================================================ AUTH: session lifecycle
@app.post("/api/auth/refresh")
def refresh(request: Request, response: Response):
    raw = request.cookies.get("ma_rt", "")
    if not raw:
        raise HTTPException(401, detail="no_session")
    token_hash = sec.sha256(raw)
    row = db.q("SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,), one=True)
    if not row:
        raise HTTPException(401, detail="no_session")
    if row["revoked_at"]:
        # replay of a rotated token -> assume theft, kill every session for this user
        db.q("UPDATE refresh_tokens SET revoked_at = ? WHERE user_id = ?", (db.utcnow(), row["user_id"]))
        log.warning("refresh replay detected user_id=%s ip=%s", row["user_id"], ip_of(request))
        raise HTTPException(401, detail="no_session")
    if row["expires_at"] <= db.utcnow():
        raise HTTPException(401, detail="no_session")
    user = db.user_by(id=row["user_id"])
    if not user:
        raise HTTPException(401, detail="no_session")
    db.q("UPDATE refresh_tokens SET revoked_at = ? WHERE id = ?", (db.utcnow(), row["id"]))
    issue_session(request, response, user)
    return {"ok": True, "user": public_user(user)}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    raw = request.cookies.get("ma_rt", "")
    if raw:
        db.q(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (db.utcnow(), sec.sha256(raw)),
        )
    response.delete_cookie("ma_at", path="/")
    response.delete_cookie("ma_rt", path="/api/auth")
    log.info("logout ip=%s", ip_of(request))
    return {"ok": True}


# ================================================================ PROFILE
@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {"ok": True, "user": public_user(user), "dev_mode": config.DEV_MODE}


@app.patch("/api/me")
def update_me(request: Request, body: dict, user: dict = Depends(current_user)):
    check_rate(request, "patch-me", 20, 300)
    if "display_name" in body:
        name = (body.get("display_name") or "").strip()
        if not (2 <= len(name) <= 60):
            raise HTTPException(422, detail="bad_name")
        db.q("UPDATE users SET display_name = ? WHERE id = ?", (name, user["id"]))
    return {"ok": True, "user": public_user(db.user_by(id=user["id"]))}


@app.post("/api/me/avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...), user: dict = Depends(current_user)):
    check_rate(request, "avatar", 10, 3600)
    data = await file.read()
    if not data or len(data) > 2_500_000:
        raise HTTPException(413, detail="avatar_too_big")

    from PIL import Image, ImageOps

    try:
        img = Image.open(BytesIO(data))
        img.load()
        fmt = (img.format or "").upper()
    except Exception:
        raise HTTPException(415, detail="bad_image")
    if fmt not in ("JPEG", "PNG", "WEBP", "GIF"):
        raise HTTPException(415, detail="bad_image")

    img = ImageOps.exif_transpose(img)          # fix orientation
    if getattr(img, "n_frames", 1) > 1:         # take first frame of animations
        img.seek(0)
    has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.thumbnail((512, 512))

    ext = "png" if has_alpha else "jpg"
    fname = f"{secrets.token_hex(16)}.{ext}"
    dest = config.UPLOADS_DIR / "avatars" / fname
    if has_alpha:
        img.save(dest, format="PNG", optimize=True)
    else:
        img.save(dest, format="JPEG", quality=88, optimize=True)

    old = user["avatar_url"] or ""
    if old.startswith("/uploads/avatars/"):
        old_path = (config.UPLOADS_DIR / "avatars" / old.rsplit("/", 1)[-1])
        try:
            if old_path.is_file() and old_path.parent == (config.UPLOADS_DIR / "avatars").resolve():
                old_path.unlink()
        except OSError:
            pass

    url = f"/uploads/avatars/{fname}"
    db.q("UPDATE users SET avatar_url = ? WHERE id = ?", (url, user["id"]))
    log.info("avatar uploaded user_id=%s bytes=%d", user["id"], len(data))
    return {"ok": True, "avatar_url": url}


@app.get("/uploads/avatars/{fname}")
def avatar_file(fname: str):
    if "/" in fname or "\\" in fname or ".." in fname:
        raise HTTPException(404)
    path = config.UPLOADS_DIR / "avatars" / fname
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(
        path,
        headers={"Cache-Control": "public, max-age=604800", "X-Content-Type-Options": "nosniff"},
    )


# ================================================================ FC CARD
@app.get("/api/me/card")
def my_card(user: dict = Depends(current_user)):
    return {"ok": True, "card": cards.build_card(user)}


@app.post("/api/dev/match")
def dev_match(request: Request, body: dict, user: dict = Depends(current_user)):
    """DEV ONLY: lets you watch your card upgrade. Disabled unless DEV_MODE=true."""
    if not config.DEV_MODE:
        raise HTTPException(404, detail="not_found")
    sport = (body.get("sport") or "football").lower()
    if sport not in SPORTS:
        raise HTTPException(422, detail="bad_sport")
    result = cards.record_match(
        user["id"], sport, bool(body.get("won")), bool(body.get("mvp")), user["id"]
    )
    log.info("DEV match recorded user_id=%s sport=%s", user["id"], sport)
    return {"ok": True, **result}


@app.post("/api/admin/match")
def admin_match(body: dict, user: dict = Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, detail="forbidden")
    sport = (body.get("sport") or "").lower()
    if sport not in SPORTS:
        raise HTTPException(422, detail="bad_sport")
    target_id = int(body.get("user_id") or 0)
    if not db.user_by(id=target_id):
        raise HTTPException(404, detail="no_user")
    result = cards.record_match(
        target_id, sport, bool(body.get("won")), bool(body.get("mvp")), user["id"]
    )
    return {"ok": True, **result}


@app.get("/api/leaderboard")
def leaderboard(limit: int = 10):
    limit = max(1, min(int(limit), 25))
    rows = db.q(
        "SELECT u.*, s.matches AS s_matches, s.wins, s.mvps, s.streak, s.xp, s.matches "
        "FROM player_stats s JOIN users u ON u.id = s.user_id "
        "ORDER BY s.xp DESC, s.matches DESC LIMIT ?",
        (limit,),
    )
    out = []
    for row in rows:
        u = dict(row)
        c = cards.build_card(u)
        c.pop("history", None)
        out.append(c)
    return {"ok": True, "players": out}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "matcharena", "dev_mode": config.DEV_MODE}


@app.get("/api/config")
def public_config():
    """Non-secret config the frontend needs (Google client ids are public)."""
    return {
        "ok": True,
        "google_client_id": config.GOOGLE_CLIENT_ID,
        "dev_mode": config.DEV_MODE,
    }


# ================================================================ static site
ALLOWED_EXT = {".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".webp", ".ico"}


def _site_headers() -> dict:
    return {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}


# ================================================================ admin console
import admin_api  # noqa: E402  (needs the helpers defined above)

app.include_router(admin_api.build_router(current_user, check_rate))


@app.api_route("/admin", methods=["GET", "HEAD"])
def admin_page():
    return FileResponse(config.ROOT_DIR / "admin.html", headers=_site_headers())


@app.api_route("/", methods=["GET", "HEAD"])
def site_index():
    return FileResponse(config.ROOT_DIR / "index.html", headers=_site_headers())


@app.api_route("/{name}", methods=["GET", "HEAD"])
def site_file(name: str):
    # only files sitting directly in the workspace root are served —
    # backend/.env, .py sources, logs and the DB are never reachable.
    path = config.ROOT_DIR / name
    if name and "/" not in name and "\\" not in name and ".." not in name:
        if path.is_file() and path.parent.resolve() == config.ROOT_DIR.resolve():
            if path.suffix.lower() in ALLOWED_EXT:
                return FileResponse(path, headers=_site_headers())
    raise HTTPException(404)


if __name__ == "__main__":
    print(f"\n  MatchArena backend -> {config.PUBLIC_ORIGIN}")
    print(f"  API docs            -> {config.PUBLIC_ORIGIN}/api/docs")
    print(f"  DEV_MODE            -> {config.DEV_MODE} (OTP codes go to console when no provider is set)\n")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
