"""FIFA FC–style player card: attributes derived from real match activity.

Every recorded match pushes the six attributes up (and XP/level), so the
card visibly upgrades as the player plays.
"""
import db

XP_PLAYED = 25
XP_WIN = 40
XP_MVP = 60

TIERS = [  # (min_ovr, key, label)
    (85, "legend", "Legend"),
    (80, "elite", "Elite"),
    (70, "gold", "Gold"),
    (60, "silver", "Silver"),
    (0, "bronze", "Bronze"),
]


def _clamp(v: float) -> int:
    return max(1, min(99, int(round(v))))


def _stats_from(s: dict) -> dict:
    m, w, mv, st, xp = (
        s.get("matches", 0), s.get("wins", 0), s.get("mvps", 0),
        s.get("streak", 0), s.get("xp", 0),
    )
    losses = max(0, m - w)
    return {
        "pac": _clamp(48 + m * 1.4 + st * 1.5),
        "sho": _clamp(46 + w * 1.4 + mv * 2.0 + m * 0.5),
        "pas": _clamp(50 + m * 1.0 + mv * 1.5 + w * 0.4),
        "dri": _clamp(49 + m * 1.3 + xp * 0.012),
        "def": _clamp(47 + m * 0.9 + losses * 0.6),
        "phy": _clamp(50 + m * 1.6 + st * 1.0),
    }


def _position(stats: dict) -> str:
    ratings = [
        ("ST", stats["sho"] * 1.15 + stats["pac"] * 0.85),
        ("LW", stats["pac"] * 1.0 + stats["dri"] * 1.0),
        ("CAM", stats["pas"] * 1.1 + stats["dri"] * 0.9),
        ("CM", (stats["pas"] + stats["def"] + stats["dri"]) / 3 * 1.15),
        ("CB", stats["def"] * 1.2 + stats["phy"] * 0.8),
        ("GK", stats["def"] * 1.0 + stats["phy"] * 1.0),
    ]
    return max(ratings, key=lambda r: r[1])[0]


def _ovr(stats: dict) -> int:
    # attackers weight PAC/SHO/DRI, midfielders PAS, defenders DEF/PHY
    weights = {"pac": 0.18, "sho": 0.18, "pas": 0.17, "dri": 0.19, "def": 0.14, "phy": 0.14}
    return _clamp(sum(stats[k] * w for k, w in weights.items()))


def _tier(ovr: int) -> dict:
    for min_ovr, key, label in TIERS:
        if ovr >= min_ovr:
            return {"key": key, "label": label}
    return {"key": "bronze", "label": "Bronze"}


def build_card(user: dict, stats: dict | None = None) -> dict:
    """Public card payload for a users-row dict."""
    uid = user["id"]
    if stats is None:
        db.ensure_stats(uid)
        row = db.q("SELECT * FROM player_stats WHERE user_id = ?", (uid,), one=True)
        stats = dict(row) if row else {}
    s = {
        "matches": stats.get("matches", 0),
        "wins": stats.get("wins", 0),
        "mvps": stats.get("mvps", 0),
        "streak": stats.get("streak", 0),
        "xp": stats.get("xp", 0),
    }
    card_stats = _stats_from(s)
    ovr = _ovr(card_stats)
    xp = s["xp"]
    level = xp // 150 + 1
    xp_into = xp % 150
    history = [
        dict(r)
        for r in db.q(
            "SELECT sport, won, mvp, xp_awarded, created_at FROM match_log "
            "WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (uid,),
        )
    ]
    return {
        "user_id": uid,
        "name": user["display_name"],
        "avatar_url": user["avatar_url"],
        "ovr": ovr,
        "position": _position(card_stats),
        "tier": _tier(ovr),
        "stats": card_stats,
        "level": level,
        "xp": xp,
        "xp_into": xp_into,
        "xp_next": 150,
        "matches": s["matches"],
        "wins": s["wins"],
        "mvps": s["mvps"],
        "streak": s["streak"],
        "history": history,
    }


def record_match(user_id: int, sport: str, won: bool, mvp: bool, recorded_by: int | None = None) -> dict:
    """Record a played match; returns {before, after} cards for the upgrade animation."""
    db.ensure_stats(user_id)
    row = db.q("SELECT * FROM player_stats WHERE user_id = ?", (user_id,), one=True)
    before = build_card(db.q("SELECT * FROM users WHERE id = ?", (user_id,), one=True), dict(row))

    gained = XP_PLAYED + (XP_WIN if won else 0) + (XP_MVP if mvp else 0)
    db.q(
        "UPDATE player_stats SET matches = matches + 1, wins = wins + ?, mvps = mvps + ?, "
        "streak = CASE WHEN ? = 1 THEN streak + 1 ELSE 0 END, xp = xp + ?, updated_at = ? "
        "WHERE user_id = ?",
        (1 if won else 0, 1 if mvp else 0, 1 if won else 0, gained, db.utcnow(), user_id),
    )
    db.q_insert(
        "INSERT INTO match_log(user_id, sport, won, mvp, xp_awarded, recorded_by, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (user_id, sport, 1 if won else 0, 1 if mvp else 0, gained, recorded_by, db.utcnow()),
    )
    after = build_card(db.q("SELECT * FROM users WHERE id = ?", (user_id,), one=True))
    return {"before": before, "after": after, "xp_gained": gained}
