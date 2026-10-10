"""SQLite storage: schema creation and thin query helpers."""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE,
  phone TEXT UNIQUE,
  google_sub TEXT UNIQUE,
  password_hash TEXT,
  display_name TEXT NOT NULL,
  avatar_url TEXT,
  role TEXT NOT NULL DEFAULT 'player',
  email_verified INTEGER NOT NULL DEFAULT 0,
  phone_verified INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS otps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel TEXT NOT NULL,           -- whatsapp | email
  target TEXT NOT NULL,            -- E.164 phone or email (lowercased)
  purpose TEXT NOT NULL DEFAULT 'login',
  code_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  consumed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_otps_target ON otps(channel, target, purpose);
CREATE TABLE IF NOT EXISTS refresh_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT UNIQUE NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_stats (
  user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  matches INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  mvps INTEGER NOT NULL DEFAULT 0,
  streak INTEGER NOT NULL DEFAULT 0,
  xp INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS match_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sport TEXT NOT NULL,
  won INTEGER NOT NULL DEFAULT 0,
  mvp INTEGER NOT NULL DEFAULT 0,
  xp_awarded INTEGER NOT NULL DEFAULT 0,
  recorded_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_match_log_user ON match_log(user_id, created_at);

-- ---------- admin console (venues / matches / staff roles) ----------
CREATE TABLE IF NOT EXISTS venues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  city TEXT NOT NULL DEFAULT '',
  address TEXT DEFAULT '',
  format TEXT DEFAULT '5v5',
  phone TEXT DEFAULT '',
  slot_minutes INTEGER DEFAULT 60,
  price_per_slot INTEGER NOT NULL DEFAULT 0,   -- base / fallback per-slot price
  ground_cost INTEGER NOT NULL DEFAULT 0,
  morning_cutoff INTEGER NOT NULL DEFAULT 12,  -- hours before this = morning pricing
  format_costs TEXT DEFAULT '{}',              -- JSON {format:{base,weekday_morning,weekend,weekend_morning}}
  status TEXT NOT NULL DEFAULT 'active',       -- active | paused | closed
  created_by INTEGER,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  sport TEXT NOT NULL DEFAULT 'football',
  match_type TEXT NOT NULL DEFAULT 'single',   -- single | recurring
  venue_id INTEGER REFERENCES venues(id),
  city TEXT DEFAULT '',
  sides TEXT DEFAULT '[]',                     -- JSON [{name}] 2..6
  capacity INTEGER NOT NULL DEFAULT 0,
  enrolled INTEGER NOT NULL DEFAULT 0,
  price INTEGER NOT NULL DEFAULT 0,
  ground_cost INTEGER NOT NULL DEFAULT 0,
  chief_cost INTEGER NOT NULL DEFAULT 0,
  hero_tags TEXT DEFAULT '[]',                 -- JSON [tag name]
  visibility TEXT NOT NULL DEFAULT 'public',   -- public | secret (invite only)
  status TEXT NOT NULL DEFAULT 'active',       -- active | completed | cancelled
  result TEXT DEFAULT '',                      -- JSON {mvp_user_id, scores:{side:goals}}
  chief_id INTEGER REFERENCES users(id),
  controller_id INTEGER REFERENCES users(id),
  creator_id INTEGER REFERENCES users(id),
  starts_at TEXT,
  ends_at TEXT,
  recurring_days TEXT DEFAULT '',              -- 'Mon,Wed,Fri'
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status, starts_at);
CREATE INDEX IF NOT EXISTS idx_matches_venue ON matches(venue_id);
CREATE TABLE IF NOT EXISTS match_players (
  match_id INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  side TEXT NOT NULL DEFAULT '',
  won INTEGER NOT NULL DEFAULT 0,
  mvp INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (match_id, user_id)
);
CREATE TABLE IF NOT EXISTS match_tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  color TEXT NOT NULL DEFAULT 'gold',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  percent INTEGER NOT NULL DEFAULT 10,
  uses INTEGER NOT NULL DEFAULT 0,
  max_uses INTEGER,
  expires_at TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
-- role assignments: superadmin | venue_admin | football_chief (scoped to a venue)
CREATE TABLE IF NOT EXISTS staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  venue_id INTEGER REFERENCES venues(id) ON DELETE SET NULL,
  granted_by INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(user_id, role, venue_id)
);
CREATE INDEX IF NOT EXISTS idx_staff_user ON staff(user_id);
"""

_lock = threading.Lock()


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn():
    """One connection per operation; WAL mode allows concurrent reads."""
    c = sqlite3.connect(config.DATA_DIR / "app.db", timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with _lock, conn() as c:
        c.executescript(SCHEMA)
        # lightweight migrations for DBs created before a column existed
        cols = {r["name"] for r in c.execute("PRAGMA table_info(venues)")}
        for decl in (
            "phone TEXT DEFAULT ''",
            "morning_cutoff INTEGER NOT NULL DEFAULT 12",
            "format_costs TEXT DEFAULT '{}'",
        ):
            if decl.split()[0] not in cols:
                c.execute(f"ALTER TABLE venues ADD COLUMN {decl}")
        mcols = {r["name"] for r in c.execute("PRAGMA table_info(matches)")}
        if "visibility" not in mcols:
            c.execute("ALTER TABLE matches ADD COLUMN visibility TEXT NOT NULL DEFAULT 'public'")


def q(sql: str, args: tuple = (), one: bool = False):
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        rows = cur.fetchall()
        if one:
            return rows[0] if rows else None
        return rows


def q_insert(sql: str, args: tuple = ()) -> int:
    with _lock, conn() as c:
        cur = c.execute(sql, args)
        return cur.lastrowid


def user_by(**conds):
    keys = list(conds)
    if not keys:
        return None
    where = " AND ".join(f"{k} = ?" for k in keys)
    return q(f"SELECT * FROM users WHERE {where} LIMIT 1", tuple(conds[k] for k in keys), one=True)


def ensure_stats(user_id: int) -> None:
    q(
        "INSERT OR IGNORE INTO player_stats(user_id, updated_at) VALUES(?, ?)",
        (user_id, utcnow()),
    )
