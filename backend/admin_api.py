"""Admin console API — role-gated routes under /api/admin/*.

Built as a factory so main.py can inject its auth/rate-limit helpers without a
circular import.  Roles (from the MatchArena admin design refs):

    superadmin      everything, incl. staff grants
    venue_admin     the venue(s) they are attached to
    football_chief  the matches they are assigned to

The bootstrapped account (users.role = 'admin') is treated as superadmin.
"""
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request

import cards
import db

SPORTS = {"football", "cricket", "badminton", "basketball", "pickleball"}
ROLES = {"superadmin", "venue_admin", "football_chief"}
TAG_COLORS = {"gold", "jade", "amber", "violet", "ember", "teal"}
COST_FORMATS = ("5v5", "6v6", "7v7", "8v8", "9v9", "10v10", "11v11")
COST_KEYS = ("base", "weekday_morning", "weekend", "weekend_morning")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _parse_costs(raw) -> dict:
    """Validate/normalise the per-format cost matrix sent by the venue form."""
    if raw in (None, ""):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raise HTTPException(422, detail="bad_format_costs")
    out = {}
    for fmt, entry in raw.items():
        if fmt not in COST_FORMATS or not isinstance(entry, dict):
            continue
        clean = {}
        for k in COST_KEYS:
            if entry.get(k) not in (None, ""):
                clean[k] = max(0, int(entry.get(k) or 0))
        if clean:
            out[fmt] = clean
    return out


def _venue_price(venue: dict | None, fmt: str, starts_at: str | None,
                 fallback: int = 0) -> int:
    """Resolve a slot price from the venue's time-based, per-format pricing.

    Matches starting before `morning_cutoff` use morning pricing; weekends use
    weekend pricing.  Falls back to the venue's base price_per_slot, then to
    whatever price was passed in.
    """
    if not venue:
        return max(0, int(fallback or 0))
    venue = dict(venue)          # sqlite3.Row has no .get()
    raw = venue.get("format_costs") or "{}"
    if isinstance(raw, str):
        try:
            costs = json.loads(raw)
        except Exception:
            costs = {}
    elif isinstance(raw, dict):
        costs = raw
    else:
        costs = {}
    fc = costs.get(fmt or venue.get("format")) or {}
    if fc.get("base") is None:
        # no matrix row for this format -> classic per-slot price
        return max(0, int(venue.get("price_per_slot") or fallback))
    morning = weekend = False
    if starts_at:
        try:
            dt = datetime.fromisoformat(str(starts_at).replace("Z", "+00:00").replace(" ", "T"))
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)   # kickoff as venue-local time
            weekend = dt.weekday() >= 5
            morning = dt.hour < max(0, min(23, int(venue.get("morning_cutoff") or 12)))
        except Exception:
            pass
    price = fc["base"]
    if weekend and morning and fc.get("weekend_morning") is not None:
        price = fc["weekend_morning"]
    elif weekend and fc.get("weekend") is not None:
        price = fc["weekend"]
    elif morning and fc.get("weekday_morning") is not None:
        price = fc["weekday_morning"]
    return max(0, int(price))


def _jloads(raw, default):
    import json
    try:
        v = json.loads(raw or "")
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def build_router(current_user, check_rate):
    router = APIRouter(prefix="/api/admin")

    # ------------------------------------------------------------ role model
    def staff_info(user: dict) -> dict | None:
        """None = not staff. Else {role, venues: list[int] | None(all)}."""
        if user.get("role") in ("admin", "superadmin"):
            return {"role": "superadmin", "venues": None}
        rows = db.q("SELECT * FROM staff WHERE user_id = ?", (user["id"],))
        if not rows:
            return None
        roles = {r["role"] for r in rows}
        role = "superadmin" if "superadmin" in roles else (
            "venue_admin" if "venue_admin" in roles else "football_chief")
        venues = [r["venue_id"] for r in rows if r["role"] == "venue_admin" and r["venue_id"]]
        if role == "superadmin":
            venues = None
        return {"role": role, "venues": venues}

    def require_staff(request: Request) -> tuple[dict, dict]:
        user = current_user(request)
        info = staff_info(user)
        if not info:
            raise HTTPException(403, detail="no_admin_access")
        return user, info

    def can_write_matches(info: dict) -> bool:
        return info["role"] in ("superadmin", "venue_admin")

    def match_scope_sql(info: dict, user: dict) -> tuple[str, list]:
        if info["role"] == "superadmin":
            return "1=1", []
        if info["role"] == "venue_admin":
            if not info["venues"]:
                return "0=1", []
            return f"m.venue_id IN ({','.join('?' * len(info['venues']))})", list(info["venues"])
        return "m.chief_id = ?", [user["id"]]

    def venue_scope_sql(info: dict, user: dict) -> tuple[str, list]:
        if info["role"] == "superadmin":
            return "1=1", []
        if info["role"] == "venue_admin":
            if not info["venues"]:
                return "0=1", []
            return f"v.id IN ({','.join('?' * len(info['venues']))})", list(info["venues"])
        return ("v.id IN (SELECT venue_id FROM staff WHERE user_id = ? AND venue_id IS NOT NULL "
                "UNION SELECT venue_id FROM matches WHERE chief_id = ?)"), [user["id"], user["id"]]

    def permissions(info: dict) -> dict:
        r = info["role"]
        return {
            "role": r,
            "matches_write": r in ("superadmin", "venue_admin"),
            "matches_result": True,          # every staff role records results
            "venues_write": r in ("superadmin", "venue_admin"),
            "participants_view": True,
            "chiefs_view": True,
            "tags_write": r == "superadmin",
            "promos_write": r == "superadmin",
            "staff_manage": r == "superadmin",
        }

    def _venue_or_404(venue_id, info: dict) -> dict:
        v = db.q("SELECT * FROM venues WHERE id = ?", (venue_id,), one=True) if venue_id else None
        if venue_id and not v:
            raise HTTPException(404, detail="venue_not_found")
        if v and info["venues"] is not None and v["id"] not in info["venues"]:
            raise HTTPException(403, detail="venue_out_of_scope")
        return v

    def _match_or_404(match_id: int, info: dict, user: dict) -> dict:
        m = db.q("SELECT * FROM matches WHERE id = ?", (match_id,), one=True)
        if not m:
            raise HTTPException(404, detail="match_not_found")
        if info["role"] == "venue_admin":
            if info["venues"] is None or m["venue_id"] not in info["venues"]:
                raise HTTPException(403, detail="out_of_scope")
        elif info["role"] == "football_chief" and m["chief_id"] != user["id"]:
            raise HTTPException(403, detail="out_of_scope")
        return m

    def _name(uid) -> str:
        if not uid:
            return ""
        u = db.user_by(id=int(uid))
        return u["display_name"] if u else ""

    def match_row(m: dict) -> dict:
        venue = db.q("SELECT name, city FROM venues WHERE id = ?", (m["venue_id"],), one=True) if m["venue_id"] else None
        return {
            "id": m["id"], "title": m["title"], "sport": m["sport"],
            "type": m["match_type"], "status": m["status"],
            "venue_id": m["venue_id"],
            "venue": venue["name"] if venue else "—",
            "city": m["city"] or (venue["city"] if venue else ""),
            "sides": _jloads(m["sides"], []),
            "hero_tags": _jloads(m["hero_tags"], []),
            "visibility": m["visibility"] if "visibility" in m.keys() else "public",
            "capacity": m["capacity"], "enrolled": m["enrolled"],
            "price": m["price"], "ground_cost": m["ground_cost"], "chief_cost": m["chief_cost"],
            "revenue": m["price"] * m["enrolled"],
            "chief_id": m["chief_id"], "chief": _name(m["chief_id"]) or "—",
            "controller_id": m["controller_id"], "controller": _name(m["controller_id"]) or "—",
            "creator": _name(m["creator_id"]) or "—",
            "starts_at": m["starts_at"], "ends_at": m["ends_at"],
            "recurring_days": m["recurring_days"],
            "created_at": m["created_at"],
        }

    # ------------------------------------------------------------ session
    @router.get("/me")
    def admin_me(request: Request):
        user, info = require_staff(request)
        return {
            "ok": True,
            "user": {
                "id": user["id"], "name": user["display_name"], "email": user["email"],
                "phone": user["phone"], "avatar_url": user["avatar_url"],
            },
            "staff": info,
            "permissions": permissions(info),
        }

    # ------------------------------------------------------------ overview
    @router.get("/overview")
    def overview(request: Request):
        user, info = require_staff(request)
        w, args = match_scope_sql(info, user)
        vw, vargs = venue_scope_sql(info, user)
        g = lambda sql, a: db.q(sql, a, one=True)[0] or 0  # noqa: E731
        stats = {
            "active_matches": g(f"SELECT COUNT(*) FROM matches m WHERE {w} AND m.status='active'", args),
            "completed_matches": g(f"SELECT COUNT(*) FROM matches m WHERE {w} AND m.status='completed'", args),
            "upcoming": g(
                f"SELECT COUNT(*) FROM matches m WHERE {w} AND m.status='active' "
                "AND m.starts_at IS NOT NULL AND m.starts_at >= ?", args + [_now()]),
            "venues": g(f"SELECT COUNT(*) FROM venues v WHERE {vw} AND v.status='active'", vargs),
            "players": db.q("SELECT COUNT(*) FROM users", one=True)[0],
            "revenue": g(
                f"SELECT COALESCE(SUM(m.price * m.enrolled),0) FROM matches m "
                f"WHERE {w} AND m.status='completed'", args),
            "chiefs": db.q("SELECT COUNT(*) FROM staff WHERE role='football_chief'", one=True)[0],
        }
        recent = [match_row(dict(r)) for r in db.q(
            f"SELECT m.* FROM matches m WHERE {w} ORDER BY m.id DESC LIMIT 6", args)]
        return {"ok": True, "stats": stats, "recent": recent,
                "role": info["role"], "permissions": permissions(info)}

    # ------------------------------------------------------------ matches
    @router.get("/matches")
    def list_matches(request: Request, status: str = "", q: str = "", venue_id: int = 0,
                      sport: str = "", date_from: str = "", date_to: str = "",
                      page: int = 1, page_size: int = 12):
        user, info = require_staff(request)
        w, args = match_scope_sql(info, user)
        conds, cargs = [w], list(args)
        if status:
            conds.append("m.status = ?"); cargs.append(status)
        if sport and sport in SPORTS:
            conds.append("m.sport = ?"); cargs.append(sport)
        if venue_id:
            conds.append("m.venue_id = ?"); cargs.append(venue_id)
        if q:
            like = f"%{q.strip()}%"
            conds.append("(m.title LIKE ? OR m.city LIKE ? OR EXISTS ("
                         "SELECT 1 FROM venues v2 WHERE v2.id = m.venue_id AND v2.name LIKE ?))")
            cargs += [like, like, like]
        if date_from:
            conds.append("COALESCE(m.starts_at, m.created_at) >= ?"); cargs.append(date_from)
        if date_to:
            conds.append("COALESCE(m.starts_at, m.created_at) <= ?"); cargs.append(date_to + "~")
        where = " AND ".join(conds)
        where_args = tuple(cargs)
        total = db.q(f"SELECT COUNT(*) FROM matches m WHERE {where}", where_args, one=True)[0]
        page = max(1, int(page)); page_size = max(1, min(int(page_size), 50))
        rows = db.q(
            f"SELECT m.* FROM matches m WHERE {where} "
            "ORDER BY CASE m.status WHEN 'active' THEN 0 WHEN 'completed' THEN 1 ELSE 2 END, "
            "m.starts_at IS NULL, m.starts_at DESC, m.id DESC LIMIT ? OFFSET ?",
            where_args + (page_size, (page - 1) * page_size),
        )
        return {"ok": True, "items": [match_row(dict(r)) for r in rows],
                "total": total, "page": page, "pages": max(1, -(-total // page_size))}

    @router.post("/matches")
    def create_match(request: Request, body: dict):
        user, info = require_staff(request)
        if not can_write_matches(info):
            raise HTTPException(403, detail="forbidden")
        check_rate(request, "admin-match", 30, 3600)
        title = (body.get("title") or "").strip()
        sport = (body.get("sport") or "football").lower()
        mtype = body.get("type") or "single"
        if not (2 <= len(title) <= 80):
            raise HTTPException(422, detail="bad_title")
        visibility = body.get("visibility") or "public"
        if visibility not in ("public", "secret"):
            raise HTTPException(422, detail="bad_visibility")
        if sport not in SPORTS:
            raise HTTPException(422, detail="bad_sport")
        if mtype not in ("single", "recurring"):
            raise HTTPException(422, detail="bad_type")
        sides = body.get("sides") or []
        names = [(s.get("name") or "").strip() for s in sides if isinstance(s, dict)]
        names = [n for n in names if n]
        if not (2 <= len(names) <= 6) or len(set(names)) != len(names):
            raise HTTPException(422, detail="bad_sides")
        venue = _venue_or_404(int(body.get("venue_id") or 0), info)
        tags = [str(t).strip()[:24] for t in (body.get("hero_tags") or []) if str(t).strip()][:5]
        starts_at = body.get("starts_at") or None
        fmt = (body.get("format") or (venue["format"] if venue else "")) or ""
        if body.get("price") not in (None, "", 0, "0"):
            price = max(0, int(body.get("price") or 0))
        else:
            # no explicit price -> derive it from the venue's time-based,
            # per-format pricing (morning / weekend / base) like the HOF refs
            price = _venue_price(venue, fmt, starts_at,
                                 venue["price_per_slot"] if venue else 0)
        ground = max(0, int(body.get("ground_cost") or (venue["ground_cost"] if venue else 0)))
        chief_cost = max(0, int(body.get("chief_cost") or 0))
        capacity = max(len(names), int(body.get("capacity") or len(names) * 2))
        chief_id = int(body.get("chief_id") or 0) or None
        if chief_id and not db.user_by(id=chief_id):
            raise HTTPException(422, detail="bad_chief")
        import json
        mid = db.q_insert(
            "INSERT INTO matches(title, sport, match_type, venue_id, city, sides, capacity, price, "
            "ground_cost, chief_cost, hero_tags, visibility, chief_id, controller_id, creator_id, starts_at, "
            "ends_at, recurring_days, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (title, sport, mtype, venue["id"] if venue else None,
             (body.get("city") or (venue["city"] if venue else ""))[:60],
             json.dumps([{"name": n} for n in names]), capacity, price, ground, chief_cost,
             json.dumps(tags), visibility, chief_id, int(body.get("controller_id") or 0) or None,
             user["id"], body.get("starts_at") or None, body.get("ends_at") or None,
             ",".join(body.get("recurring_days") or []) if mtype == "recurring" else "",
             db.utcnow()),
        )
        logline = "admin match created id=%s by=%s role=%s"
        import logging
        logging.getLogger("matcharena").info(logline, mid, user["id"], info["role"])
        return {"ok": True, "match": match_row(db.q("SELECT * FROM matches WHERE id=?", (mid,), one=True))}

    @router.get("/matches/{match_id}")
    def match_detail(request: Request, match_id: int):
        user, info = require_staff(request)
        m = _match_or_404(match_id, info, user)
        row = match_row(dict(m))
        players = [dict(r) for r in db.q(
            "SELECT mp.side, mp.won, mp.mvp, u.id, u.display_name, u.avatar_url "
            "FROM match_players mp JOIN users u ON u.id = mp.user_id WHERE mp.match_id = ? "
            "ORDER BY mp.side, u.display_name", (match_id,))]
        row["players"] = players
        row["platform_revenue"] = row["revenue"] - row["ground_cost"] - row["chief_cost"]
        return {"ok": True, "match": row}

    @router.patch("/matches/{match_id}")
    def update_match(request: Request, match_id: int, body: dict):
        user, info = require_staff(request)
        m = _match_or_404(match_id, info, user)
        if info["role"] == "football_chief":
            # a chief may only change status of their own match
            allowed = {"status", "starts_at", "ends_at"}
            if set(body) - allowed:
                raise HTTPException(403, detail="forbidden")
        fields, args = [], []
        if "status" in body:
            st = body["status"]
            if st not in ("active", "completed", "cancelled"):
                raise HTTPException(422, detail="bad_status")
            fields.append("status = ?"); args.append(st)
        if "title" in body:
            t = (body["title"] or "").strip()
            if not (2 <= len(t) <= 80):
                raise HTTPException(422, detail="bad_title")
            fields.append("title = ?"); args.append(t)
        for k in ("starts_at", "ends_at", "recurring_days"):
            if k in body:
                fields.append(f"{k} = ?"); args.append((body.get(k) or "") or None)
        if "chief_id" in body and can_write_matches(info):
            cid = int(body.get("chief_id") or 0) or None
            if cid and not db.user_by(id=cid):
                raise HTTPException(422, detail="bad_chief")
            fields.append("chief_id = ?"); args.append(cid)
        if "controller_id" in body and can_write_matches(info):
            ctl = int(body.get("controller_id") or 0) or None
            if ctl and not db.user_by(id=ctl):
                raise HTTPException(422, detail="bad_controller")
            fields.append("controller_id = ?"); args.append(ctl)
        if "visibility" in body and can_write_matches(info):
            if body["visibility"] not in ("public", "secret"):
                raise HTTPException(422, detail="bad_visibility")
            fields.append("visibility = ?"); args.append(body["visibility"])
        if not fields:
            raise HTTPException(422, detail="nothing_to_update")
        db.q(f"UPDATE matches SET {', '.join(fields)} WHERE id = ?", tuple(args + [match_id]))
        return {"ok": True, "match": match_row(db.q("SELECT * FROM matches WHERE id=?", (match_id,), one=True))}

    @router.post("/matches/{match_id}/result")
    def save_result(request: Request, match_id: int, body: dict):
        """Record the final score + participants -> upgrades every player's FC card."""
        user, info = require_staff(request)
        m = _match_or_404(match_id, info, user)
        if m["status"] == "completed":
            raise HTTPException(409, detail="result_already_recorded")
        if m["status"] != "active":
            raise HTTPException(422, detail="match_not_active")
        scores = body.get("scores") or {}
        sides = [s["name"] for s in _jloads(m["sides"], [])]
        if not isinstance(scores, dict) or set(scores) - set(sides):
            raise HTTPException(422, detail="bad_scores")
        participants = body.get("participants") or []
        if not (1 <= len(participants) <= 60):
            raise HTTPException(422, detail="bad_participants")
        seen = set()
        for p in participants:
            pid = int(p.get("user_id") or 0)
            side = (p.get("side") or "").strip()
            if not pid or side not in sides or pid in seen or not db.user_by(id=pid):
                raise HTTPException(422, detail="bad_participant")
            seen.add(pid)
        mvp = int(body.get("mvp_user_id") or 0) or None
        if mvp and mvp not in seen:
            raise HTTPException(422, detail="bad_mvp")
        winner = None
        if scores:
            top = max(int(v) for v in scores.values())
            leaders = [s for s, v in scores.items() if int(v) == top]
            if len(leaders) == 1:
                winner = leaders[0]

        import json, logging
        results = []
        db.q("DELETE FROM match_players WHERE match_id = ?", (match_id,))
        for p in participants:
            pid, side = int(p["user_id"]), p["side"]
            won = winner is not None and side == winner
            is_mvp = mvp == pid
            db.q_insert(
                "INSERT INTO match_players(match_id, user_id, side, won, mvp) VALUES(?,?,?,?,?)",
                (match_id, pid, side, 1 if won else 0, 1 if is_mvp else 0))
            res = cards.record_match(pid, m["sport"], won, is_mvp, user["id"])
            results.append({"user_id": pid, **res})
        db.q(
            "UPDATE matches SET status='completed', result=?, enrolled=?, starts_at=COALESCE(starts_at, ?) "
            "WHERE id=?",
            (json.dumps({"scores": scores, "mvp_user_id": mvp, "winner": winner}),
             len(participants), db.utcnow(), match_id),
        )
        logging.getLogger("matcharena").info(
            "match %s completed by user_id=%s players=%d winner=%s",
            match_id, user["id"], len(participants), winner)
        return {"ok": True, "results": results,
                "match": match_row(db.q("SELECT * FROM matches WHERE id=?", (match_id,), one=True))}

    # ------------------------------------------------------------ participants
    @router.get("/participants")
    def participants(request: Request, q: str = "", page: int = 1, page_size: int = 15):
        user, info = require_staff(request)
        conds, args = [], []
        if info["role"] == "venue_admin":
            if not info["venues"]:
                return {"ok": True, "items": [], "total": 0, "page": 1, "pages": 1}
            conds.append("u.id IN (SELECT mp.user_id FROM match_players mp "
                         "JOIN matches m ON m.id = mp.match_id WHERE m.venue_id IN ("
                         + ",".join("?" * len(info["venues"])) + "))")
            args += list(info["venues"])
        elif info["role"] == "football_chief":
            conds.append("u.id IN (SELECT mp.user_id FROM match_players mp "
                         "JOIN matches m ON m.id = mp.match_id WHERE m.chief_id = ?)")
            args.append(user["id"])
        if q:
            like = f"%{q.strip()}%"
            conds.append("(u.display_name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?)")
            args += [like, like, like]
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        total = db.q(f"SELECT COUNT(*) FROM users u{where}", tuple(args), one=True)[0]
        page = max(1, int(page)); page_size = max(1, min(int(page_size), 50))
        rows = db.q(
            "SELECT u.*, COALESCE(s.matches,0) AS p_matches, COALESCE(s.wins,0) AS p_wins, "
            "COALESCE(s.mvps,0) AS p_mvps, COALESCE(s.xp,0) AS p_xp "
            f"FROM users u LEFT JOIN player_stats s ON s.user_id = u.id{where} "
            "ORDER BY p_xp DESC, p_matches DESC, u.id LIMIT ? OFFSET ?",
            tuple(args) + (page_size, (page - 1) * page_size),
        )
        items = []
        for r in rows:
            u = dict(r)
            card = cards.build_card(u)
            items.append({
                "id": u["id"], "name": u["display_name"], "email": u["email"],
                "phone": u["phone"], "avatar_url": u["avatar_url"],
                "role": u["role"], "created_at": u["created_at"],
                "matches": u["p_matches"], "wins": u["p_wins"], "mvps": u["p_mvps"],
                "xp": u["p_xp"], "ovr": card["ovr"], "position": card["position"],
                "tier": card["tier"]["key"],
            })
        return {"ok": True, "items": items, "total": total, "page": page,
                "pages": max(1, -(-total // page_size))}

    # ------------------------------------------------------------ venues
    @router.get("/venues")
    def list_venues(request: Request, q: str = "", status: str = ""):
        user, info = require_staff(request)
        w, args = venue_scope_sql(info, user)
        conds, cargs = [w], list(args)
        if status:
            conds.append("v.status = ?"); cargs.append(status)
        if q:
            conds.append("(v.name LIKE ? OR v.city LIKE ?)")
            like = f"%{q.strip()}%"; cargs += [like, like]
        rows = db.q(
            f"SELECT v.*, (SELECT COUNT(*) FROM matches m WHERE m.venue_id = v.id) AS match_count "
            f"FROM venues v WHERE {' AND '.join(conds)} ORDER BY v.status, v.name",
            tuple(cargs))
        return {"ok": True, "items": [dict(r) for r in rows]}

    @router.post("/venues")
    def create_venue(request: Request, body: dict):
        user, info = require_staff(request)
        if not info["role"] in ("superadmin", "venue_admin"):
            raise HTTPException(403, detail="forbidden")
        check_rate(request, "admin-venue", 30, 3600)
        name = (body.get("name") or "").strip()
        if not (2 <= len(name) <= 80):
            raise HTTPException(422, detail="bad_name")
        vid = db.q_insert(
            "INSERT INTO venues(name, city, address, format, phone, slot_minutes, price_per_slot, "
            "ground_cost, morning_cutoff, format_costs, status, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, (body.get("city") or "").strip()[:60], (body.get("address") or "").strip()[:160],
             (body.get("format") or "5v5")[:12], (body.get("phone") or "").strip()[:20],
             max(15, min(int(body.get("slot_minutes") or 60), 300)),
             max(0, int(body.get("price_per_slot") or 0)), max(0, int(body.get("ground_cost") or 0)),
             max(0, min(23, int(body.get("morning_cutoff") or 12))),
             json.dumps(_parse_costs(body.get("format_costs"))),
             "active", user["id"], db.utcnow()),
        )
        # a venue_admin creating a venue is automatically attached to it
        if info["role"] == "venue_admin":
            db.q_insert("INSERT OR IGNORE INTO staff(user_id, role, venue_id, granted_by, created_at) "
                        "VALUES(?, 'venue_admin', ?, ?, ?)",
                        (user["id"], vid, user["id"], db.utcnow()))
        return {"ok": True, "venue": dict(db.q("SELECT * FROM venues WHERE id=?", (vid,), one=True))}

    @router.patch("/venues/{venue_id}")
    def update_venue(request: Request, venue_id: int, body: dict):
        user, info = require_staff(request)
        v = _venue_or_404(venue_id, info)
        if not v:
            raise HTTPException(404, detail="venue_not_found")
        if info["role"] not in ("superadmin", "venue_admin"):
            raise HTTPException(403, detail="forbidden")
        fields, args = [], []
        if "name" in body:
            n = (body["name"] or "").strip()
            if not (2 <= len(n) <= 80):
                raise HTTPException(422, detail="bad_name")
            fields.append("name = ?"); args.append(n)
        for k in ("city", "address", "format"):
            if k in body:
                fields.append(f"{k} = ?"); args.append((body.get(k) or "").strip()[:160])
        if "phone" in body:
            fields.append("phone = ?"); args.append((body.get("phone") or "").strip()[:20])
        if "status" in body:
            if body["status"] not in ("active", "paused", "closed"):
                raise HTTPException(422, detail="bad_status")
            fields.append("status = ?"); args.append(body["status"])
        for k in ("price_per_slot", "ground_cost", "slot_minutes"):
            if k in body:
                fields.append(f"{k} = ?"); args.append(max(0, int(body.get(k) or 0)))
        if "morning_cutoff" in body:
            fields.append("morning_cutoff = ?")
            args.append(max(0, min(23, int(body.get("morning_cutoff") or 12))))
        if "format_costs" in body:
            fields.append("format_costs = ?")
            args.append(json.dumps(_parse_costs(body.get("format_costs"))))
        if not fields:
            raise HTTPException(422, detail="nothing_to_update")
        db.q(f"UPDATE venues SET {', '.join(fields)} WHERE id = ?", tuple(args + [venue_id]))
        return {"ok": True, "venue": dict(db.q("SELECT * FROM venues WHERE id=?", (venue_id,), one=True))}

    # ------------------------------------------------------------ match tags
    @router.get("/tags")
    def list_tags(request: Request):
        require_staff(request)
        return {"ok": True, "items": [dict(r) for r in db.q("SELECT * FROM match_tags ORDER BY name")]}

    @router.post("/tags")
    def create_tag(request: Request, body: dict):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        name = (body.get("name") or "").strip()
        color = (body.get("color") or "gold")
        if not (2 <= len(name) <= 24) or color not in TAG_COLORS:
            raise HTTPException(422, detail="bad_tag")
        if db.q("SELECT id FROM match_tags WHERE name = ?", (name,), one=True):
            raise HTTPException(409, detail="tag_exists")
        tid = db.q_insert("INSERT INTO match_tags(name, color, created_at) VALUES(?,?,?)",
                          (name, color, db.utcnow()))
        return {"ok": True, "tag": dict(db.q("SELECT * FROM match_tags WHERE id=?", (tid,), one=True))}

    @router.delete("/tags/{tag_id}")
    def delete_tag(request: Request, tag_id: int):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        db.q("DELETE FROM match_tags WHERE id = ?", (tag_id,))
        return {"ok": True}

    # ------------------------------------------------------------ Ballon d'Or (chiefs)
    @router.get("/chiefs")
    def chiefs_leaderboard(request: Request, period: str = "week", date_from: str = "", date_to: str = ""):
        user, info = require_staff(request)
        if period == "month":
            since = _iso_days_ago(30)
        elif period == "custom" and date_from:
            since = date_from
        else:
            since = _iso_days_ago(7)
        # chiefs = staff with the football_chief role; falls back to anyone who
        # has hosted matches (so the board is never empty on a fresh install)
        rows = db.q(
            "SELECT u.id, u.display_name AS name, u.avatar_url, "
            "COUNT(ml.id) AS hosted, COALESCE(SUM(ml.xp_awarded),0) AS impact, "
            "COALESCE(SUM(CASE WHEN ml.won=1 THEN 1 ELSE 0 END),0) AS wins "
            "FROM users u JOIN match_log ml ON ml.recorded_by = u.id "
            "WHERE ml.created_at >= ? GROUP BY u.id ORDER BY hosted DESC, impact DESC LIMIT 20",
            (since,))
        items = [dict(r) for r in rows]
        # match_log has no venue column, so only a chief's own row can be
        # narrowed further; venue admins share the house board.
        if info["role"] == "football_chief":
            items = [i for i in items if i["id"] == user["id"]]
        for idx, it in enumerate(items):
            it["rank"] = idx + 1
        return {"ok": True, "items": items, "period": period, "since": since}

    # ------------------------------------------------------------ promos
    @router.get("/promos")
    def list_promos(request: Request):
        require_staff(request)
        rows = db.q("SELECT * FROM promos ORDER BY id DESC")
        out = []
        for r in rows:
            d = dict(r)
            d["expired"] = bool(d["expires_at"] and d["expires_at"] <= db.utcnow())
            d["exhausted"] = bool(d["max_uses"] and d["uses"] >= d["max_uses"])
            out.append(d)
        return {"ok": True, "items": out}

    @router.post("/promos")
    def create_promo(request: Request, body: dict):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        check_rate(request, "admin-promo", 30, 3600)
        code = (body.get("code") or "").strip().upper()
        if not (3 <= len(code) <= 20) or not code.replace("-", "").isalnum():
            raise HTTPException(422, detail="bad_code")
        percent = int(body.get("percent") or 0)
        if not (1 <= percent <= 90):
            raise HTTPException(422, detail="bad_percent")
        max_uses = body.get("max_uses")
        max_uses = max(1, int(max_uses)) if max_uses else None
        expires = (body.get("expires_at") or "").strip() or None
        if db.q("SELECT id FROM promos WHERE code = ?", (code,), one=True):
            raise HTTPException(409, detail="code_exists")
        pid = db.q_insert(
            "INSERT INTO promos(code, percent, max_uses, expires_at, created_at) VALUES(?,?,?,?,?)",
            (code, percent, max_uses, expires, db.utcnow()))
        return {"ok": True, "promo": dict(db.q("SELECT * FROM promos WHERE id=?", (pid,), one=True))}

    @router.patch("/promos/{promo_id}")
    def update_promo(request: Request, promo_id: int, body: dict):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        p = db.q("SELECT * FROM promos WHERE id = ?", (promo_id,), one=True)
        if not p:
            raise HTTPException(404, detail="promo_not_found")
        if "active" in body:
            db.q("UPDATE promos SET active = ? WHERE id = ?", (1 if body["active"] else 0, promo_id))
        return {"ok": True, "promo": dict(db.q("SELECT * FROM promos WHERE id=?", (promo_id,), one=True))}

    @router.delete("/promos/{promo_id}")
    def delete_promo(request: Request, promo_id: int):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        db.q("DELETE FROM promos WHERE id = ?", (promo_id,))
        return {"ok": True}

    # ------------------------------------------------------------ staff & roles
    @router.get("/roleholders")
    def roleholders(request: Request):
        """People who already hold a staff role (for assigning chiefs etc.)."""
        require_staff(request)
        rows = db.q(
            "SELECT s.role, s.venue_id, u.id AS user_id, u.display_name AS name "
            "FROM staff s JOIN users u ON u.id = s.user_id ORDER BY u.display_name")
        return {"ok": True, "items": [dict(r) for r in rows]}

    @router.get("/staff")
    def list_staff(request: Request):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        rows = db.q(
            "SELECT s.id, s.role, s.venue_id, s.created_at, u.id AS user_id, u.display_name AS name, "
            "u.email, u.phone, v.name AS venue "
            "FROM staff s JOIN users u ON u.id = s.user_id "
            "LEFT JOIN venues v ON v.id = s.venue_id ORDER BY s.role, u.display_name")
        return {"ok": True, "items": [dict(r) for r in rows]}

    @router.post("/staff")
    def grant_staff(request: Request, body: dict):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        check_rate(request, "admin-staff", 30, 3600)
        role = body.get("role") or ""
        if role not in ROLES - {"superadmin"}:
            raise HTTPException(422, detail="bad_role")
        target = None
        if body.get("user_id"):
            target = db.user_by(id=int(body["user_id"]))
        elif body.get("email"):
            target = db.user_by(email=(body["email"] or "").strip().lower())
        elif body.get("phone"):
            from main import normalize_phone
            try:
                target = db.user_by(phone=normalize_phone(body["phone"]))
            except ValueError:
                raise HTTPException(422, detail="bad_phone")
        if not target:
            raise HTTPException(404, detail="user_not_found")
        venue_id = None
        if role == "venue_admin":
            venue_id = int(body.get("venue_id") or 0)
            if not venue_id or not db.q("SELECT id FROM venues WHERE id=?", (venue_id,), one=True):
                raise HTTPException(422, detail="venue_required")
        exists = db.q(
            "SELECT id FROM staff WHERE user_id=? AND role=? AND IFNULL(venue_id,0)=IFNULL(?,0)",
            (target["id"], role, venue_id), one=True)
        if exists:
            raise HTTPException(409, detail="already_staff")
        sid = db.q_insert(
            "INSERT INTO staff(user_id, role, venue_id, granted_by, created_at) VALUES(?,?,?,?,?)",
            (target["id"], role, venue_id, user["id"], db.utcnow()))
        import logging
        logging.getLogger("matcharena").info(
            "staff granted user_id=%s role=%s venue=%s by=%s", target["id"], role, venue_id, user["id"])
        return {"ok": True, "staff": dict(db.q("SELECT * FROM staff WHERE id=?", (sid,), one=True)),
                "user": {"id": target["id"], "name": target["display_name"]}}

    @router.delete("/staff/{staff_id}")
    def revoke_staff(request: Request, staff_id: int):
        user, info = require_staff(request)
        if info["role"] != "superadmin":
            raise HTTPException(403, detail="forbidden")
        row = db.q("SELECT * FROM staff WHERE id = ?", (staff_id,), one=True)
        if not row:
            raise HTTPException(404, detail="not_found")
        db.q("DELETE FROM staff WHERE id = ?", (staff_id,))
        return {"ok": True}

    return router
