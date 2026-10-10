"""Security primitives: password hashing, JWT, OTP codes, rate limiting."""
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

import jwt

import config

# ---------------------------------------------------------------- passwords
# scrypt from the stdlib: memory-hard, no native dependency.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32, maxmem=64 * 1024 * 1024,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(dk_hex) // 2,
            maxmem=64 * 1024 * 1024,
        )
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def dummy_verify() -> None:
    """Burn equal CPU when a user does not exist (anti-enumeration)."""
    verify_password("dummy", _DUMMY_HASH)


# ---------------------------------------------------------------- JWT access tokens
def issue_access_token(user_id: int, role: str) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "typ": "access",
            "iss": "matcharena",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=config.ACCESS_MINUTES)).timestamp()),
        },
        config.JWT_SECRET,
        algorithm="HS256",
    )


def decode_access_token(token: str) -> dict | None:
    try:
        claims = jwt.decode(
            token, config.JWT_SECRET, algorithms=["HS256"],
            issuer="matcharena", options={"require": ["exp", "sub", "typ"]},
        )
        if claims.get("typ") != "access":
            return None
        return claims
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------- refresh tokens
def new_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, sha256_hex) — only the hash is stored."""
    raw = secrets.token_urlsafe(48)
    return raw, sha256(raw)


def sha256(value: str) -> str:
    return hashlib.sha256((value + config.OTP_PEPPER).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- OTP codes
def new_otp_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def otp_hash(code: str, target: str, purpose: str) -> str:
    return hmac.new(
        config.OTP_PEPPER.encode("utf-8"),
        f"{code}|{target}|{purpose}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def otp_matches(code: str, target: str, purpose: str, stored: str) -> bool:
    return hmac.compare_digest(otp_hash(code, target, purpose), stored)


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """In-memory sliding-window limiter (single-process server)."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str, max_hits: int, window_seconds: int) -> tuple[bool, int]:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < window_seconds]
        if len(hits) >= max_hits:
            retry = int(window_seconds - (now - hits[0])) + 1
            self._hits[key] = hits
            return False, retry
        hits.append(now)
        self._hits[key] = hits
        if len(self._hits) > 5000:          # opportunistic prune
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < 86400}
        return True, 0


limiter = RateLimiter()


# ---------------------------------------------------------------- misc
def utcnow_plus(minutes: int = 0, days: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes, days=days)
    ).isoformat(timespec="seconds")


def safe_json_loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
