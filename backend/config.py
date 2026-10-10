"""Configuration: loads .env (no extra dependency) and exposes settings."""
import os
import secrets
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent          # workspace root (site files live here)
DATA_DIR = BACKEND_DIR / "data"        # sqlite db
UPLOADS_DIR = BACKEND_DIR / "uploads"  # avatars
LOG_DIR = BACKEND_DIR / "logs"


def load_env(path: Path = BACKEND_DIR / ".env") -> dict:
    """Parse KEY=VALUE lines into os.environ without overriding real env vars.

    Returns the parsed file values so server keys (HOST/PORT) can prefer the
    .env file — the parent shell may export junk like PORT=0 which must not
    hijack where the server listens.
    """
    file_vals: dict[str, str] = {}
    if not path.is_file():
        return file_vals
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if not key:
            continue
        file_vals[key] = val
        if key not in os.environ:
            os.environ[key] = val
    return file_vals


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


_ENV_FILE = load_env()

for _d in (DATA_DIR, UPLOADS_DIR / "avatars", LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# HOST/PORT come from .env first (an inherited PORT=0 from the parent shell
# would otherwise make uvicorn bind a random ephemeral port).
HOST = _ENV_FILE.get("HOST") or os.environ.get("HOST") or "127.0.0.1"
try:
    PORT = int(_ENV_FILE.get("PORT") or os.environ.get("PORT") or 8000)
except ValueError:
    PORT = 8000
if not (1 <= PORT <= 65535):       # 0 / garbage -> safe default
    PORT = 8000
PUBLIC_ORIGIN = (_ENV_FILE.get("PUBLIC_ORIGIN") or os.environ.get("PUBLIC_ORIGIN")
                 or f"http://{HOST}:{PORT}").rstrip("/")
ALLOWED_ORIGINS = set(
    o.strip()
    for o in os.environ.get(
        "ALLOWED_ORIGINS",
        f"{PUBLIC_ORIGIN},http://127.0.0.1:8765,http://localhost:8765,http://127.0.0.1:{PORT},http://localhost:{PORT}",
    ).split(",")
    if o.strip()
)

JWT_SECRET = os.environ.get("JWT_SECRET", "")
OTP_PEPPER = os.environ.get("OTP_PEPPER", "")
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() in ("1", "true", "yes")

if not JWT_SECRET or JWT_SECRET.startswith("CHANGE-ME"):
    # Dev convenience only: generate an ephemeral secret so the server still runs.
    JWT_SECRET = secrets.token_urlsafe(48)
    if not DEV_MODE:
        raise SystemExit(
            "FATAL: set JWT_SECRET in backend/.env (generate one with:\n"
            "  python -c \"import secrets;print(secrets.token_urlsafe(48))\")"
        )
    print("[config] WARNING: using an ephemeral JWT_SECRET because DEV_MODE=true. Sessions reset on restart.")
if not OTP_PEPPER or OTP_PEPPER.startswith("CHANGE-ME"):
    OTP_PEPPER = JWT_SECRET

ACCESS_MINUTES = _int("ACCESS_TOKEN_MINUTES", 15)
REFRESH_DAYS = _int("REFRESH_DAYS", 30)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

WA_TOKEN = os.environ.get("WA_TOKEN", "")
WA_PHONE_NUMBER_ID = os.environ.get("WA_PHONE_NUMBER_ID", "")
WA_TEMPLATE = os.environ.get("WA_TEMPLATE", "matcharena_otp")
WA_TEMPLATE_LANG = os.environ.get("WA_TEMPLATE_LANG", "en")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = _int("SMTP_PORT", 465)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
MAIL_FROM = os.environ.get("MAIL_FROM", "") or SMTP_USER

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@matcharena.local")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

OTP_SEND_COOLDOWN = _int("OTP_SEND_COOLDOWN_SECONDS", 90)
OTP_MAX_PER_DAY = _int("OTP_MAX_PER_DAY", 5)
LOGIN_MAX_PER_15MIN = _int("LOGIN_MAX_PER_15MIN", 10)

IS_SECURE = PUBLIC_ORIGIN.startswith("https://")
