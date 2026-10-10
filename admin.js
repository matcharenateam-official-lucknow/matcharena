/* ============================================================
   MatchArena Admin console — login (password / WhatsApp OTP) + ops app.
   No inline handlers (CSP: script-src 'self'); everything is delegated.
   ============================================================ */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rupee = (n) => "\u20B9" + Number(n || 0).toLocaleString("en-IN");

const ROLE_LABEL = {
  superadmin: "Superadmin",
  venue_admin: "Venue Admin",
  football_chief: "Football Chief",
};

const S = {
  me: null, perms: null, role: "", venues: [],
  page: "overview",
  list: { status: "", q: "", sport: "", page: 1, from: "", to: "" },
  otp: { target: "", dev: "", sent: false },
  pool: null,          // result-entry modal state
  bo: { period: "week", from: "" },
};

/* ---------------------------------------------------------------- api */
async function api(path, { method = "GET", body, retry = true } = {}) {
  const opt = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  let r = await fetch(path, opt);
  if (r.status === 401 && retry && !path.startsWith("/api/auth/")) {
    const rf = await fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" });
    if (rf.ok) r = await fetch(path, opt);
  }
  let data = null;
  try { data = await r.json(); } catch (_) { /* no body */ }
  if (!r.ok) {
    const err = new Error((data && data.detail) || `http_${r.status}`);
    err.status = r.status; err.data = data;
    if (r.status === 401 && S.me) { S.me = null; showLogin(); }
    throw err;
  }
  return data;
}

function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transition = "opacity .3s"; }, 3400);
  setTimeout(() => el.remove(), 3800);
}

/* ---------------------------------------------------------------- modal */
function openModal(html, wide) {
  const card = $("#modalCard");
  card.className = "modal-card" + (wide ? " wide" : "");
  card.innerHTML = html;
  $("#modal").hidden = false;
  document.body.style.overflow = "hidden";
  const first = card.querySelector("input,select,button.btn-gold,button.primary");
  if (first) setTimeout(() => first.focus(), 60);
}
function closeModal() {
  $("#modal").hidden = true;
  $("#modalCard").innerHTML = "";
  document.body.style.overflow = "";
  S.pool = null;
}
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("#modal").hidden) closeModal();
});

/* ---------------------------------------------------------------- login */
$$(".tab").forEach((t) => t.addEventListener("click", () => {
  $$(".tab").forEach((x) => {
    x.classList.toggle("is-on", x === t);
    x.setAttribute("aria-selected", x === t ? "true" : "false");
  });
  const otp = t.dataset.ltab === "otp";
  $("#pwForm").hidden = otp;
  $("#otpForm").hidden = !otp;
  $("#loginMsg").textContent = "";
  $("#loginMsg").className = "form-msg";
}));

function loginMsg(text, ok) {
  const el = $("#loginMsg");
  el.textContent = text || "";
  el.className = "form-msg" + (ok ? " ok" : "");
}

$("#pwForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true; loginMsg("");
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: { email: (f.get("email") || "").trim(), password: f.get("password") || "" },
    });
    await enterApp(true);
  } catch (err) {
    loginMsg(err.data?.detail === "bad_credentials"
      ? "That email and password don't match an account."
      : err.data?.detail === "account_locked"
        ? "Too many tries — this account is locked for 15 minutes."
        : "Sign-in failed (" + err.message + ").");
  } finally { btn.disabled = false; }
});

/* ---- WhatsApp OTP ---- */
$("#otpSend").addEventListener("click", async () => {
  const phone = ($("#otpPhone").value || "").replace(/\D/g, "");
  if (phone.length < 10) { loginMsg("Enter a valid WhatsApp number (10 digits)."); return; }
  const btn = $("#otpSend");
  btn.disabled = true; loginMsg("");
  try {
    const resp = await api("/api/auth/otp/send", {
      method: "POST", body: { channel: "whatsapp", target: phone },
    });
    S.otp.target = phone;
    S.otp.dev = resp.dev_code || "";
    S.otp.sent = true;
    $("#otpTarget").textContent = resp.target;
    $("#otpStep1").hidden = true;
    $("#otpStep2").hidden = false;
    $("#otpNameWrap").hidden = true;
    $("#otpName").value = "";
    clearBoxes();
    if (S.otp.dev) {
      $("#devBanner").hidden = false;
      $("#devCode").textContent = S.otp.dev;
    } else { $("#devBanner").hidden = true; }
    $$("#otpBoxes input")[0].focus();
  } catch (err) {
    const m = /rate_limited:(\d+)/.exec(err.message);
    loginMsg(m ? `Cooldown — one code per ${m[1]}s. Try again in ${m[1]} seconds.`
      : "Could not send the code (" + err.message + ").");
  } finally { btn.disabled = false; }
});
$("#otpChange").addEventListener("click", () => {
  $("#otpStep1").hidden = false; $("#otpStep2").hidden = true; loginMsg("");
});
$("#otpResend").addEventListener("click", () => {
  $("#otpStep1").hidden = false; $("#otpStep2").hidden = true;
  loginMsg("Request a new code.", true);
});
$("#devFill").addEventListener("click", () => {
  const boxes = $$("#otpBoxes input");
  S.otp.dev.split("").forEach((d, i) => { boxes[i].value = d; });
  boxes[5].focus();
});

function clearBoxes() { $$("#otpBoxes input").forEach((b) => { b.value = ""; }); }
function boxCode() { return $$("#otpBoxes input").map((b) => b.value).join(""); }
$$("#otpBoxes input").forEach((box, i, all) => {
  box.addEventListener("input", () => {
    box.value = box.value.replace(/\D/g, "").slice(0, 1);
    if (box.value && i < 5) all[i + 1].focus();
    if (boxCode().length === 6) $("#otpVerify").focus();
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Backspace" && !box.value && i > 0) all[i - 1].focus();
  });
  box.addEventListener("paste", (e) => {
    const d = (e.clipboardData.getData("text") || "").replace(/\D/g, "").slice(0, 6);
    if (d.length === 6) {
      e.preventDefault();
      all.forEach((b, j) => { b.value = d[j]; });
      all[5].focus();
    }
  });
});

$("#otpVerify").addEventListener("click", async () => {
  const code = boxCode();
  if (code.length !== 6) { loginMsg("Enter all six digits."); return; }
  const name = ($("#otpName").value || "").trim();
  const btn = $("#otpVerify");
  btn.disabled = true; loginMsg("");
  try {
    await api("/api/auth/otp/verify", {
      method: "POST",
      body: { channel: "whatsapp", target: S.otp.target, code, name },
    });
    await enterApp(true);
  } catch (err) {
    const d = err.data?.detail;
    if (d === "name_required") {
      $("#otpNameWrap").hidden = false;
      loginMsg("First time on MatchArena — add your name and verify again (same code).");
      $("#otpName").focus();
    } else if (d === "invalid_code") {
      loginMsg("That code is wrong or expired.");
      clearBoxes(); $$("#otpBoxes input")[0].focus();
    } else if (d === "code_attempts_exceeded") {
      loginMsg("Too many attempts — request a new code.");
    } else { loginMsg("Verification failed (" + err.message + ")."); }
  } finally { btn.disabled = false; }
});

/* ---------------------------------------------------------------- session */
function showLogin() {
  $("#appRoot").hidden = true;
  $("#loginRoot").hidden = false;
  loginMsg("");
}
async function enterApp(fromLogin) {
  let me;
  try { me = await api("/api/admin/me"); }
  catch (err) {
    if (err.status === 403) {
      loginMsg("Signed in, but this account has no staff role yet.\n" +
        "Ask a superadmin to grant you one from Staff & roles.");
    } else if (err.status === 401) {
      loginMsg("Sign-in didn't stick — please try again.");
    } else { loginMsg("Could not open the console (" + err.message + ")."); }
    return;
  }
  S.me = me.user; S.perms = me.permissions; S.role = me.staff.role;
  S.venues = me.staff.venues || [];
  $("#loginRoot").hidden = true;
  $("#appRoot").hidden = false;
  $("#roleChip").textContent = ROLE_LABEL[S.role] || S.role;
  $("#userName").textContent = S.me.name;
  const av = $("#userAv");
  if (S.me.avatar_url) { av.src = S.me.avatar_url; av.hidden = false; } else { av.hidden = true; }
  $("#navStaff").hidden = !S.perms.staff_manage;
  const scope = S.role === "superadmin" ? "Every venue, match and role."
    : S.role === "venue_admin" ? `${S.venues.length} venue(s) attached to you.`
      : "Only the matches you are assigned to lead.";
  $("#sbFoot").innerHTML = `<b>${esc(ROLE_LABEL[S.role] || S.role)}</b>${esc(scope)}`;
  if (!location.hash || location.hash === "#") location.hash = "#/overview";
  if (fromLogin) toast(`Welcome, ${S.me.name.split(" ")[0]} — ${ROLE_LABEL[S.role]}.`, "ok");
  route();
}

/* ---------------------------------------------------------------- router */
const PAGES = {};   /* filled by the page modules below */

function route() {
  if (!S.me) return;
  const key = (location.hash.replace(/^#\//, "") || "overview").split("?")[0];
  const page = PAGES[key] ? key : "overview";
  S.page = page;
  const navKey = page === "matches/new" ? "matches" : page;
  $$("#nav a").forEach((a) => a.classList.toggle("is-on", a.dataset.nav === navKey));
  $("#sidebar").classList.remove("open");
  const view = $("#view");
  view.scrollTop = 0;
  PAGES[page](view).catch((err) => {
    view.innerHTML = `<div class="empty"><div class="big">!</div>
      <h4>Could not load this page</h4><p>${esc(err.message)}</p>
      <button class="btn outline" data-act="retry-page">Retry</button></div>`;
    view.querySelector("button").addEventListener("click", () => PAGES[page](view));
  });
}
window.addEventListener("hashchange", route);

/* ---------------------------------------------------------------- nav toggle */
$("#navToggle").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

/* ---------------------------------------------------------------- boot */
(async function boot() {
  try {
    await enterApp(false);
    if (!S.me) showLogin();
  } catch (_) { showLogin(); }
})();

/* ---------------------------------------------------------------- shared */
function pageHead(eyebrow, title, sub, actions = "") {
  return `<div class="page-head"><div>
    <p class="eyebrow">${esc(eyebrow)}</p><h1>${title}</h1>
    ${sub ? `<p class="page-sub">${sub}</p>` : ""}</div>
    ${actions ? `<div class="head-actions">${actions}</div>` : ""}</div>`;
}
const pill = (st) => `<span class="pill ${esc(st)}">${esc(st)}</span>`;
function when(iso, short) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return esc(iso);
  return d.toLocaleString("en-IN", short
    ? { day: "2-digit", month: "short" }
    : { day: "2-digit", month: "short", hour: "numeric", minute: "2-digit" });
}
const initials = (n) => esc((n || "?").trim().charAt(0).toUpperCase());
function avatarCell(u) {
  return u.avatar_url
    ? `<img class="chief-av" src="${esc(u.avatar_url)}" alt="" style="width:34px;height:34px">`
    : `<span class="chief-av" style="width:34px;height:34px;font-size:13px">${initials(u.name)}</span>`;
}
function pager(page, pages, total) {
  if (pages <= 1) return total ? `<div class="pager"><span>${total} record${total === 1 ? "" : "s"}</span><span></span></div>` : "";
  return `<div class="pager">
    <span>${total} records · page ${page} of ${pages}</span>
    <span style="display:flex;gap:8px">
      <button class="btn outline sm" data-act="page" data-p="${page - 1}" ${page <= 1 ? "disabled" : ""}>← Prev</button>
      <button class="btn outline sm" data-act="page" data-p="${page + 1}" ${page >= pages ? "disabled" : ""}>Next →</button>
    </span></div>`;
}
const SPORT_DOT = (s) => `<span class="sport-dot ${esc(s)}"></span>`;

/* ---------------------------------------------------------------- overview */
PAGES.overview = async (view) => {
  const d = await api("/api/admin/overview");
  const st = d.stats;
  const first = (S.me.name || "").split(" ")[0];
  const P = [
    ["Active matches", st.active_matches ?? 0, st.upcoming ? `${st.upcoming} kickoff${st.upcoming === 1 ? "" : "s"} queued` : "nothing scheduled"],
    ["Venues live", st.venues ?? 0, `${st.chiefs} Football Chief${st.chiefs === 1 ? "" : "s"} on roll`],
    ["Players", st.players ?? 0, `${st.completed_matches} matches completed`],
    ["Completed revenue", rupee(st.revenue), "gross slot value, all time"],
  ];
  view.innerHTML =
    pageHead("Control room", `Good to see you, <em>${esc(first)}</em>`,
      "Everything happening across your arenas right now.",
      `<button class="btn outline" data-act="go" data-go="#/venues">Venues</button>
       ${S.perms.matches_write
         ? `<button class="btn primary" data-act="go" data-go="#/matches/new">New match +</button>` : ""}`) +
    `<div class="scoreboard">${P.map(([l, n, h]) => `
      <div class="sb-cell"><div class="sb-label">${esc(l)}</div>
        <div class="sb-num">${n}</div><div class="sb-hint">${esc(h)}</div></div>`).join("")}
    </div>
    <div class="grid cols-2" style="margin-top:18px">
      <section class="panel"><div class="panel-head"><h3>Recent fixtures</h3>
        <button class="btn soft sm" data-act="go" data-go="#/matches">Open match desk</button></div>
        <div class="panel-body flush">${d.recent.length ? d.recent.map((m) => `
          <div class="recent-row" data-act="open-match" data-id="${m.id}" role="button" tabindex="0">
            ${pill(m.status)}
            <div class="rr-main"><div class="rr-title">${SPORT_DOT(m.sport)}#${m.id} · ${esc(m.title)}</div>
              <div class="rr-sub">${esc(m.venue)}${m.city ? " · " + esc(m.city) : ""} · chief ${esc(m.chief)} · ${when(m.starts_at)}</div></div>
            <span class="rr-amount">${rupee(m.revenue)}</span></div>`).join("") : `
          <div class="empty"><div class="big">\u26BD</div><h4>No fixtures yet</h4>
            <p>Create your first match and it will show up here live.</p></div>`}
        </div></section>
      <section class="panel"><div class="panel-head"><h3>Your access</h3>
        <span class="ph-sub">${esc(ROLE_LABEL[S.role])}</span></div>
        <div class="panel-body"><ul class="perm-list">${[
          ["Create &amp; edit matches", d.permissions.matches_write],
          ["Record results (upgrades FC cards)", d.permissions.matches_result],
          ["Manage venues &amp; pricing", d.permissions.venues_write],
          ["View participants of your games", d.permissions.participants_view],
          ["Run promo codes &amp; match tags", d.permissions.promos_write],
          ["Grant roles to other people", d.permissions.staff_manage],
        ].map(([label, ok]) => `<li><span class="${ok ? "perm-yes" : "perm-no"}">${ok ? "\u2713" : "\u2013"}</span>
          <span>${label}</span></li>`).join("")}</ul></div></section>
    </div>`;
};

/* ---------------------------------------------------------------- matches */
async function renderMatchList(view, opts) {
  const L = S.list;
  const p = new URLSearchParams();
  if (L.status) p.set("status", L.status);
  if (L.q) p.set("q", L.q);
  if (L.sport) p.set("sport", L.sport);
  if (opts.dates) { if (L.from) p.set("date_from", L.from); if (L.to) p.set("date_to", L.to); }
  p.set("page", L.page); p.set("page_size", 12);
  const d = await api("/api/admin/matches?" + p);
  const segs = opts.segs.map(([val, label]) =>
    `<button class="${L.status === val ? "is-on" : ""}" data-act="mstatus" data-v="${val}">${label}</button>`).join("");
  const dateBar = opts.dates ? `
    <input type="date" class="date-in" data-act="mfrom" value="${esc(L.from)}" aria-label="From date">
    <span style="color:var(--faint)">→</span>
    <input type="date" class="date-in" data-act="mto" value="${esc(L.to)}" aria-label="To date">` : "";
  const rows = d.items.map((m) => `
    <tr data-act="open-match" data-id="${m.id}" style="cursor:pointer">
      <td><span class="row-id">#${m.id}</span></td>
      <td><span class="strong">${SPORT_DOT(m.sport)}${esc(m.title)}</span>
        <span class="cell-sub">${esc(m.venue)}${m.city ? " · " + esc(m.city) : ""}</span>
        ${m.visibility === "secret" ? `<span class="warn-chip">Secret</span>` : ""}
        ${m.venue_id ? "" : `<span class="warn-chip">Ground not booked</span>`}</td>
      <td>${pill(m.status)}</td>
      <td>${m.type === "recurring" ? `Recurring<span class="cell-sub">${esc(m.recurring_days || "weekly")}</span>` : "Single"}</td>
      <td class="num">${m.enrolled} / ${m.capacity}
        <span class="enr-bar ${m.capacity && m.enrolled >= m.capacity ? "full" : ""}"><i style="width:${
          Math.min(100, m.capacity ? Math.round((m.enrolled / m.capacity) * 100) : 0)}%"></i></span></td>
      <td class="num">${when(m.starts_at)}</td>
      <td>${esc(m.chief)}</td>
      <td class="num">${rupee(m.price)}</td>
      <td><button class="btn outline sm" data-act="open-match" data-id="${m.id}">Open</button></td>
    </tr>`).join("");
  view.innerHTML =
    pageHead(opts.eyebrow, opts.title, opts.sub, opts.dates ? "" :
      (S.perms.matches_write ? `<button class="btn primary" data-act="go" data-go="#/matches/new">New match +</button>` : "")) +
    `<div class="toolbar"><div class="seg">${segs}</div>
      <div class="search"><input id="mSearch" placeholder="Search match, city, venue…"
        value="${esc(L.q)}" aria-label="Search matches"></div>
      <select class="filter" data-act="msport" aria-label="Sport">
        <option value="">All sports</option>
        ${["football", "cricket", "badminton", "basketball", "pickleball"]
          .map((s) => `<option ${L.sport === s ? "selected" : ""} value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("")}
      </select>${dateBar}</div>
    <section class="panel"><div class="panel-body flush">
      ${d.items.length ? `<table class="tbl"><thead><tr>
        <th>#</th><th>Match</th><th>Status</th><th>Type</th><th>Size</th>
        <th>Kickoff</th><th>Football Chief</th><th>Price</th><th></th></tr></thead>
        <tbody>${rows}</tbody></table>` : `
        <div class="empty"><div class="big">\u26BD</div>
          <h4>${L.q || L.sport || L.from ? "Nothing matches those filters" : "No fixtures in this view"}</h4>
          <p>${L.q || L.sport || L.from ? "Loosen the search or clear the dates." : "Fixtures you create appear here with live status."}</p>
          ${S.perms.matches_write && !L.q ? `<button class="btn primary" data-act="go" data-go="#/matches/new">Create a match +</button>` : ""}</div>`}
    </div>${pager(d.page, d.pages, d.total)}</section>`;
  const si = $("#mSearch");
  if (si) {
    si.addEventListener("input", () => {
      clearTimeout(si._t);
      si._t = setTimeout(() => { S.list.q = si.value.trim(); S.list.page = 1; S._focusSearch = true; route(); }, 320);
    });
    if (S._focusSearch) { si.focus(); S._focusSearch = false; }
  }
}

PAGES.matches = async (view) => {
  if (S.list.status === "completed" || !S._initList) {
    S.list = { status: "active", q: "", sport: "", page: 1, from: "", to: "" };
    S._initList = true;
  }
  await renderMatchList(view, {
    segs: [["active", "Active"], ["", "All"]], dates: false,
    eyebrow: "Match desk", title: "Matches",
    sub: "Live and upcoming fixtures across your arenas.",
  });
};

PAGES.old = async (view) => {
  if (S.list.status !== "completed") {
    S.list = { status: "completed", q: "", sport: "", page: 1, from: "", to: "" };
  }
  await renderMatchList(view, {
    segs: [["completed", "Completed"], ["cancelled", "Cancelled"]], dates: true,
    eyebrow: "Archive", title: "Old matches",
    sub: "Completed and cancelled fixtures with date filters.",
  });
};

/* ---------------------------------------------------------------- match detail + result */
async function openMatch(id) {
  const d = await api("/api/admin/matches/" + id);
  const m = d.match;
  const canResult = S.perms.matches_result && m.status === "active";
  const canCancel = S.perms.matches_write && m.status === "active";
  const money = `
    <div class="money">
      <div class="money-cell"><div class="sb-label">Slot revenue</div><div class="mv">${rupee(m.revenue)}</div></div>
      <div class="money-cell cost"><div class="sb-label">Ground cost</div><div class="mv">-${rupee(m.ground_cost)}</div></div>
      <div class="money-cell cost"><div class="sb-label">Chief cost</div><div class="mv">-${rupee(m.chief_cost)}</div></div>
      <div class="money-cell net"><div class="sb-label">Platform margin</div><div class="mv">${rupee(m.platform_revenue)}</div></div>
    </div>`;
  const roster = m.players.length ? `
    <div><p class="eyebrow" style="margin-bottom:8px">Roster · ${m.players.length} players</p>
    <div class="player-pool">${m.players.map((p) => `
      <span class="pool-chip">${p.mvp ? "\u2605 " : ""}${esc(p.display_name)}
        <b style="color:var(--muted);font-weight:500">${esc(p.side)}</b>
        <span style="color:${p.won ? "var(--jade)" : "var(--faint)"}">${p.won ? "W" : "L"}</span></span>`).join("")}</div></div>` : "";
  openModal(`
    <div class="modal-head"><div><p class="eyebrow">Match #${m.id} · ${esc(m.sport)}</p>
      <h3>${esc(m.title)}</h3></div>
      <button class="modal-x" data-act="modal-close" aria-label="Close">×</button></div>
    <div class="modal-body">
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        ${pill(m.status)}
        <span class="tag-chip">${m.type === "recurring" ? "Recurring · " + esc(m.recurring_days || "weekly") : "Single match"}</span>
        ${m.hero_tags.map((t) => `<span class="tag-chip" data-c="jade">${esc(t)}</span>`).join("")}
        ${m.visibility === "secret" ? `<span class="tag-chip" data-c="violet">Secret · invite only</span>` : ""}
        ${m.venue_id ? "" : `<span class="warn-chip">Ground not booked</span>`}
      </div>
      <div class="money">
        <div class="money-cell"><div class="sb-label">Venue</div><div class="mv" style="font-size:15px">${esc(m.venue)}</div></div>
        <div class="money-cell"><div class="sb-label">Kickoff</div><div class="mv" style="font-size:15px">${when(m.starts_at)}</div></div>
        <div class="money-cell"><div class="sb-label">Enrolled</div><div class="mv">${m.enrolled}/${m.capacity}</div></div>
        <div class="money-cell"><div class="sb-label">Football Chief</div><div class="mv" style="font-size:15px">${esc(m.chief)}</div>
          <div class="sb-label" style="margin-top:7px">Game Controller</div><div class="mv" style="font-size:15px">${esc(m.controller || "—")}</div></div>
      </div>
      ${money}
      <div><p class="eyebrow" style="margin-bottom:8px">Sides</p>
        <div class="tag-grid">${m.sides.map((s) => `<span class="tag-item"><span class="dot"></span>${esc(s.name)}</span>`).join("")}</div></div>
      ${roster}
    </div>
    <div class="modal-foot">
      ${canCancel ? `<button class="btn danger" data-act="cancel-match" data-id="${m.id}">Cancel match</button>` : ""}
      ${m.status === "active" ? `<button class="btn outline" data-act="modal-close">Close</button>` : ""}
      ${canResult ? `<button class="btn primary" data-act="result-open" data-id="${m.id}">Record result \u2192</button>` : ""}
    </div>`, true);
}

async function openResult(id) {
  const d = await api("/api/admin/matches/" + id);
  const m = d.match;
  S.pool = { match: m, picked: [], mvp: null };
  const sides = m.sides.map((s) => `
    <div class="side-col"><h5>${esc(s.name)}
      <input class="score-in" type="number" min="0" max="99" value="0"
        data-side-score="${esc(s.name)}" aria-label="Goals for ${esc(s.name)}"></h5></div>`).join("");
  openModal(`
    <div class="modal-head"><div><p class="eyebrow">Record result · match #${m.id}</p>
      <h3>${esc(m.title)}</h3></div>
      <button class="modal-x" data-act="modal-close" aria-label="Close">×</button></div>
    <div class="modal-body">
      <div class="side-cols">${sides}</div>
      <div>
        <p class="eyebrow" style="margin-bottom:8px">Who played?</p>
        <div class="player-search">
          <div class="search" style="max-width:none"><input id="rpSearch"
            placeholder="Search players by name, email or phone…" aria-label="Search players"></div>
          <div class="search-pop" id="rpPop" hidden></div>
        </div>
        <div class="player-pool" id="rpPool"></div>
      </div>
      <p class="page-sub">Saving marks the match completed, stores the score, and upgrades every
        player's FC card (${"+25 played / +40 win / +60 MVP"} XP).</p>
    </div>
    <div class="modal-foot">
      <button class="btn outline" data-act="modal-close">Back</button>
      <button class="btn primary" id="rpSave">Save result</button>
    </div>`, true);
  renderPool();
  const si = $("#rpSearch");
  si.addEventListener("input", () => {
    clearTimeout(si._t);
    si._t = setTimeout(async () => {
      const q = si.value.trim();
      if (q.length < 2) { $("#rpPop").hidden = true; return; }
      try {
        const res = await api("/api/admin/participants?q=" + encodeURIComponent(q) + "&page_size=8");
        const pop = $("#rpPop");
        if (!res.items.length) { pop.innerHTML = `<button disabled>No players match “${esc(q)}”</button>`; }
        else pop.innerHTML = res.items.filter((p) => !S.pool.picked.some((x) => x.user_id === p.id))
          .map((p) => `<button type="button" data-act="rp-add" data-id="${p.id}"
            data-name="${esc(p.name)}"><span>${esc(p.name)}</span>
            <span class="sp-sub">${p.matches} m · OVR ${p.ovr}</span></button>`).join("") ||
          `<button disabled>Everyone matching is already in</button>`;
        pop.hidden = false;
      } catch (_) { /* toast via api */ }
    }, 260);
  });
  $("#rpSave").addEventListener("click", async () => {
    if (!S.pool.picked.length) { toast("Add at least one player first.", "err"); return; }
    const scores = {};
    $$('[data-side-score]').forEach((i) => { scores[i.dataset.sideScore] = Math.max(0, parseInt(i.value || "0", 10)); });
    const btn = $("#rpSave"); btn.disabled = true;
    try {
      const resp = await api(`/api/admin/matches/${m.id}/result`, {
        method: "POST",
        body: { scores, mvp_user_id: S.pool.mvp, participants: S.pool.picked },
      });
      closeModal();
      const gain = resp.results.reduce((a, r) => a + (r.xp_gained || 0), 0);
      toast(`Result saved — ${resp.results.length} cards updated (+${gain} XP).`, "ok");
      route();
    } catch (err) {
      btn.disabled = false;
      toast(err.data?.detail === "result_already_recorded"
        ? "This match already has a result." : "Could not save (" + err.message + ").", "err");
    }
  });
}

function renderPool() {
  const el = $("#rpPool");
  if (!el) return;
  const sides = S.pool.match.sides.map((s) => s.name);
  if (!S.pool.picked.length) {
    el.innerHTML = `<span style="font-size:12.5px;color:var(--faint)">No players yet — search above to add them.</span>`;
    return;
  }
  el.innerHTML = S.pool.picked.map((p) => `
    <span class="pool-chip">${esc(p.name)}
      <select data-act="rp-side" data-id="${p.user_id}" aria-label="Side">
        ${sides.map((s) => `<option ${p.side === s ? "selected" : ""}>${esc(s)}</option>`).join("")}
      </select>
      <button type="button" class="star ${S.pool.mvp === p.user_id ? "on" : ""}"
        data-act="rp-mvp" data-id="${p.user_id}" title="Mark MVP">\u2605</button>
      <button type="button" class="rp-x" data-act="rp-del" data-id="${p.user_id}"
        title="Remove">×</button></span>`).join("");
}

/* ---------------------------------------------------------------- create match */
PAGES["matches/new"] = async (view) => {
  if (!S.perms.matches_write) { toast("Your role cannot create matches.", "err"); location.hash = "#/matches"; return; }
  const [vd, td, rd] = await Promise.all([
    api("/api/admin/venues"), api("/api/admin/tags").catch(() => ({ items: [] })),
    api("/api/admin/roleholders").catch(() => ({ items: [] })),
  ]);
  const venues = vd.items.filter((v) => v.status !== "closed");
  const chiefs = rd.items.filter((r) => r.role === "football_chief");
  view.innerHTML =
    pageHead("Match desk", "New <em>match</em>", "Set the fixture, the ground, the money and who leads it.",
      `<button class="btn outline" data-act="go" data-go="#/matches">Back to matches</button>`) +
    `<section class="panel"><div class="panel-body">
      <form id="mForm" class="form-grid">
        <label class="field span2"><span>Match title</span>
          <input name="title" placeholder="Friday Night Football · Turf 1" maxlength="80" required></label>
        <label class="field"><span>Sport</span><select name="sport">
          ${["football", "cricket", "badminton", "basketball", "pickleball"]
            .map((s) => `<option value="${s}">${s[0].toUpperCase() + s.slice(1)}</option>`).join("")}
        </select></label>
        <label class="field"><span>Type</span><select name="type" id="mType">
          <option value="single">Single fixture</option>
          <option value="recurring">Recurring</option></select></label>
        <label class="field span2" id="recurDays" hidden><span>Repeats on</span>
          <div class="tag-grid">${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            .map((d) => `<label class="tag-item" style="cursor:pointer">
              <input type="checkbox" name="day" value="${d}"> ${d}</label>`).join("")}</div></label>
        <label class="field"><span>Venue</span><select name="venue_id" id="mVenue">
          <option value="">— no venue booked yet —</option>
          ${venues.map((v) => `<option value="${v.id}" data-price="${v.price_per_slot}"
            data-ground="${v.ground_cost}" data-city="${esc(v.city)}"
            data-cutoff="${v.morning_cutoff ?? 12}" data-format="${esc(v.format || "")}"
            data-fc="${esc(typeof v.format_costs === "string" ? v.format_costs : JSON.stringify(v.format_costs || {}))}">${esc(v.name)} · ${esc(v.city)}</option>`).join("")}
        </select></label>
        <label class="field"><span>City</span><input name="city" placeholder="auto from venue"></label>
        <label class="field"><span>Kickoff</span><input type="datetime-local" name="starts_at"></label>
        <label class="field"><span>Capacity (players)</span><input type="number" name="capacity"
          min="2" max="120" value="10"></label>
        <div class="field span2"><span>Teams / sides (2–6)</span>
          <div id="sideList" class="tag-grid"></div>
          <div style="display:flex;gap:8px;margin-top:8px">
            <input id="sideIn" placeholder="Team name" maxlength="28" style="max-width:260px">
            <button type="button" class="btn soft sm" id="sideAdd">+ Add side</button></div></div>
        <label class="field"><span>Price per player (₹)</span><input type="number" name="price" min="0" value="0">
          <em id="priceHint">0 = auto pricing from the venue (morning / weekend aware).</em></label>
        <label class="field"><span>Ground cost (₹)</span><input type="number" name="ground_cost" min="0" value="0"></label>
        <label class="field"><span>Football Chief cost (₹)</span><input type="number" name="chief_cost" min="0" value="0"></label>
        <label class="field"><span>Football Chief</span><select name="chief_id">
          <option value="">— assign later —</option>
          ${chiefs.map((c) => `<option value="${c.user_id}">${esc(c.name)}</option>`).join("")}
        </select></label>
        <label class="field"><span>Game Controller</span><select name="controller_id">
          <option value="">— assign later —</option>
          ${rd.items.map((c) => `<option value="${c.user_id}">${esc(c.name)}</option>`).join("")}
        </select></label>
        <label class="field"><span>Visibility</span><select name="visibility">
          <option value="public">Public — listed for everyone</option>
          <option value="secret">Secret — invite only</option>
        </select><em>Secret matches stay off the public lobby until the code is shared.</em></label>
        ${td.items.length ? `<div class="field span2"><span>Hero tags</span><div class="tag-grid">
          ${td.items.map((t) => `<label class="tag-item" style="cursor:pointer">
            <input type="checkbox" name="tag" value="${esc(t.name)}"> ${esc(t.name)}</label>`).join("")}
        </div></div>` : ""}
        <div class="span2" style="display:flex;gap:10px;justify-content:flex-end;margin-top:6px">
          <button type="button" class="btn outline" data-act="go" data-go="#/matches">Cancel</button>
          <button type="submit" class="btn primary">Create match +</button></div>
      </form></div></section>`;

  let sides = [];
  const drawSides = () => {
    $("#sideList").innerHTML = sides.map((n, i) => `
      <span class="tag-item">${esc(n)}
        <button type="button" class="del" data-side-del="${i}" aria-label="Remove">×</button></span>`).join("") ||
      `<span style="font-size:12.5px;color:var(--faint)">No sides yet — add at least two.</span>`;
  };
  drawSides();
  $("#sideAdd").addEventListener("click", () => {
    const v = $("#sideIn").value.trim();
    if (!v) return;
    if (sides.length >= 6) { toast("Six sides max.", "err"); return; }
    if (sides.includes(v)) { toast("Side names must be unique.", "err"); return; }
    sides.push(v); $("#sideIn").value = ""; drawSides();
  });
  $("#sideIn").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#sideAdd").click(); } });
  $("#sideList").addEventListener("click", (e) => {
    const b = e.target.closest("[data-side-del]");
    if (b) { sides.splice(+b.dataset.sideDel, 1); drawSides(); }
  });
  $("#mType").addEventListener("change", (e) => { $("#recurDays").hidden = e.target.value !== "recurring"; });
  $("#mVenue").addEventListener("change", (e) => {
    const o = e.target.selectedOptions[0];
    const hint = $("#priceHint");
    if (!o || !o.value) {
      if (hint) hint.textContent = "0 = auto pricing from the venue (morning / weekend aware).";
      return;
    }
    const f = mForm;
    if (!f.ground_cost.value || f.ground_cost.value === "0") f.ground_cost.value = o.dataset.ground || 0;
    if (!f.city.value) f.city.value = o.dataset.city || "";
    if (hint) {
      let fc = {};
      try { fc = JSON.parse(o.dataset.fc || "{}"); } catch { fc = {}; }
      // the venue's default format row, else the first configured row
      const fmtRow = fc[o.dataset.format || ""] || Object.values(fc).find((r) => r && r.base != null) || null;
      if (fmtRow && fmtRow.base != null) {
        const bits = [`base ₹${fmtRow.base}`];
        if (fmtRow.weekday_morning != null) bits.push(`weekday morning ₹${fmtRow.weekday_morning}`);
        if (fmtRow.weekend != null) bits.push(`weekend ₹${fmtRow.weekend}`);
        if (fmtRow.weekend_morning != null) bits.push(`weekend morning ₹${fmtRow.weekend_morning}`);
        hint.textContent = `Auto: ${bits.join(" · ")} — morning before ${o.dataset.cutoff || 12}:00.`;
      } else {
        hint.textContent = `Auto: ₹${o.dataset.price || 0} per slot from this venue.`;
      }
    }
  });
  $("#mForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    if (sides.length < 2) { toast("Add at least two sides.", "err"); return; }
    const days = f.getAll("day");
    const body = {
      title: (f.get("title") || "").trim(), sport: f.get("sport"), type: f.get("type"),
      venue_id: f.get("venue_id") ? +f.get("venue_id") : 0,
      city: f.get("city"), capacity: +f.get("capacity") || 10,
      price: +f.get("price") || 0, ground_cost: +f.get("ground_cost") || 0,
      chief_cost: +f.get("chief_cost") || 0,
      chief_id: f.get("chief_id") ? +f.get("chief_id") : 0,
      controller_id: f.get("controller_id") ? +f.get("controller_id") : 0,
      visibility: f.get("visibility") || "public",
      sides: sides.map((n) => ({ name: n })),
      hero_tags: f.getAll("tag"),
      starts_at: f.get("starts_at") ? new Date(f.get("starts_at")).toISOString() : null,
      recurring_days: days,
    };
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const resp = await api("/api/admin/matches", { method: "POST", body });
      toast(`Match #${resp.match.id} created.`, "ok");
      S.list = { status: "active", q: "", sport: "", page: 1, from: "", to: "" };
      location.hash = "#/matches";
    } catch (err) {
      btn.disabled = false;
      const nice = { bad_title: "Give the match a title (2–80 chars).",
        bad_sides: "Sides must be 2–6 unique names.", venue_out_of_scope: "That venue isn't yours.",
        bad_chief: "That Football Chief doesn't exist." }[err.message] || "Could not create (" + err.message + ").";
      toast(nice, "err");
    }
  });
};

/* ---------------------------------------------------------------- participants */
PAGES.participants = async (view) => {
  const L = S.list;
  const p = new URLSearchParams({ page: String(L.page), page_size: "15" });
  if (L.q) p.set("q", L.q);
  const d = await api("/api/admin/participants?" + p);
  const rows = d.items.map((u) => `
    <tr>
      <td><div style="display:flex;align-items:center;gap:11px">
        ${avatarCell(u)}
        <div><span class="strong">${esc(u.name)}</span>
          <span class="cell-sub">${esc(u.email || u.phone || "—")}</span></div></div></td>
      <td>${u.role === "player" ? `<span class="tag-chip">Player</span>`
        : `<span class="tag-chip" data-c="violet">${esc(u.role)}</span>`}</td>
      <td><span class="ovr-badge ${esc(u.tier)}">${u.ovr}</span>
        <span class="pos-tag" style="margin-left:7px">${esc(u.position)}</span></td>
      <td class="num">${u.matches}</td>
      <td class="num">${u.wins}</td>
      <td class="num">${u.mvps}</td>
      <td class="num" style="color:var(--gold-hi)">${u.xp}</td>
      <td class="num">${when(u.created_at, true)}</td>
    </tr>`).join("");
  view.innerHTML =
    pageHead("The people", "Participants", "Everyone who has played or signed up — searchable, with their live FC card rating.") +
    `<div class="toolbar">
      <div class="search" style="max-width:340px"><input id="pSearch"
        placeholder="Search name, email or phone…" value="${esc(L.q)}" aria-label="Search participants"></div>
      <span style="font:500 12.5px var(--font-label);color:var(--faint)">${d.total} total</span></div>
    <section class="panel"><div class="panel-body flush">
      ${d.items.length ? `<table class="tbl"><thead><tr>
        <th>Player</th><th>Role</th><th>Card</th><th>Matches</th><th>Wins</th>
        <th>MVPs</th><th>XP</th><th>Joined</th></tr></thead><tbody>${rows}</tbody></table>` : `
        <div class="empty"><div class="big">\uD83C\uDFC6</div><h4>${L.q ? "Nobody matches" : "No participants yet"}</h4>
          <p>${L.q ? "Try a shorter search." : "Players appear here as soon as they sign up or join a match."}</p></div>`}
    </div>${pager(d.page, d.pages, d.total)}</section>`;
  const si = $("#pSearch");
  if (si) {
    si.addEventListener("input", () => {
      clearTimeout(si._t);
      si._t = setTimeout(() => { S.list.q = si.value.trim(); S.list.page = 1; S._focusSearch = true; route(); }, 320);
    });
    if (S._focusSearch) { si.focus(); S._focusSearch = false; }
  }
};

/* ---------------------------------------------------------------- venues */
PAGES.venues = async (view) => {
  const d = await api("/api/admin/venues");
  S._venues = d.items;
  const rows = d.items.map((v) => `
    <tr>
      <td><span class="strong">${esc(v.name)}</span><span class="cell-sub">${esc(v.address || "—")}</span></td>
      <td><span class="tag-chip">${esc(v.city || "—")}</span></td>
      <td class="num">${esc(v.format)} · ${v.slot_minutes}m</td>
      <td class="num">${rupee(v.price_per_slot)}</td>
      <td class="num">${rupee(v.ground_cost)}</td>
      <td class="num">${v.match_count}</td>
      <td>${pill(v.status)}</td>
      <td><button class="btn outline sm" data-act="venue-edit" data-id="${v.id}">Edit</button></td>
    </tr>`).join("");
  view.innerHTML =
    pageHead("Places to play", "Venues", "Grounds, pricing and status — the fixtures you create draw from these.",
      S.perms.venues_write ? `<button class="btn primary" data-act="venue-new">New venue +</button>` : "") +
    `<section class="panel"><div class="panel-body flush">
      ${d.items.length ? `<table class="tbl"><thead><tr>
        <th>Venue</th><th>City</th><th>Format</th><th>Price / slot</th><th>Ground cost</th>
        <th>Matches</th><th>Status</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : `
        <div class="empty"><div class="big">\uD83C\uDFDF\uFE0F</div><h4>No venues yet</h4>
          <p>Add your first ground so matches can be booked against it.</p>
          ${S.perms.venues_write ? `<button class="btn primary" data-act="venue-new">Add a venue +</button>` : ""}</div>`}
    </div></section>`;
};

const COST_FORMATS = ["5v5", "6v6", "7v7", "8v8", "9v9", "10v10", "11v11"];
const COST_COLS = [["base", "Base"], ["weekday_morning", "Weekday morning"],
  ["weekend", "Weekend"], ["weekend_morning", "Weekend morning"]];

function venueForm(v) {
  const isNew = !v;
  v = v || { name: "", city: "", address: "", format: "5v5", phone: "", slot_minutes: 60,
    price_per_slot: 0, ground_cost: 0, morning_cutoff: 12, format_costs: "{}", status: "active" };
  let fc = v.format_costs || {};
  if (typeof fc === "string") { try { fc = JSON.parse(fc || "{}"); } catch { fc = {}; } }
  const cell = (fmt, key) => ((fc[fmt] || {})[key] ?? "");
  openModal(`
    <div class="modal-head"><div><p class="eyebrow">${isNew ? "New" : "Edit"} venue</p>
      <h3>${isNew ? "Add a ground" : esc(v.name)}</h3></div>
      <button class="modal-x" data-act="modal-close" aria-label="Close">×</button></div>
    <form id="vForm"><div class="modal-body"><div class="form-grid">
      <label class="field span2"><span>Venue name</span>
        <input name="name" value="${esc(v.name)}" placeholder="Sunrise Turf" maxlength="80" required></label>
      <label class="field"><span>City</span><input name="city" value="${esc(v.city)}" placeholder="Pune"></label>
      <label class="field"><span>Phone number</span>
        <input name="phone" value="${esc(v.phone || "")}" placeholder="+91 98765 43210" maxlength="20"></label>
      <label class="field"><span>Default format</span><select name="format">
        ${[...COST_FORMATS, "Box cricket", "Badminton"]
          .map((f) => `<option ${v.format === f ? "selected" : ""}>${f}</option>`).join("")}</select></label>
      <label class="field span2"><span>Address</span>
        <input name="address" value="${esc(v.address)}" placeholder="Street, area, landmark"></label>
      <label class="field"><span>Slot length (min)</span>
        <input type="number" name="slot_minutes" min="15" max="300" value="${v.slot_minutes}"></label>
      <label class="field"><span>Base price per slot (₹)</span>
        <input type="number" name="price_per_slot" min="0" value="${v.price_per_slot}"></label>
      <label class="field"><span>Ground cost (₹)</span>
        <input type="number" name="ground_cost" min="0" value="${v.ground_cost}"></label>
      <label class="field"><span>Status</span><select name="status">
        ${["active", "paused", "closed"].map((s) => `<option ${v.status === s ? "selected" : ""}>${s}</option>`).join("")}
      </select></label>
      <label class="field span2"><span>Morning pricing ends at hour (0–23)</span>
        <input type="number" name="morning_cutoff" min="0" max="23" value="${v.morning_cutoff ?? 12}">
        <em>Morning prices apply to matches starting before this hour. 12 means matches before noon use morning pricing.</em></label>
      <div class="fmt-block span2"><div class="fmt-head"><span>Format costs (per match)</span>
        <em>Set a base cost per format (typically evening/regular) and optional morning / weekend overrides.
          Leave a row empty to use the base price per slot above.</em></div>
        <div class="fmt-matrix">
          <div class="fmt-row fmt-labels"><span>Format</span>${COST_COLS.map(([, l]) => `<span>${l}</span>`).join("")}</div>
          ${COST_FORMATS.map((fmt) => `<div class="fmt-row"><b>${fmt}</b>${COST_COLS.map(([k]) =>
            `<input type="number" min="0" inputmode="numeric" data-fmt="${fmt}" data-cost="${k}"
              value="${cell(fmt, k)}" placeholder="—" aria-label="${fmt} ${k} cost in rupees">`).join("")}</div>`).join("")}
        </div></div>
    </div></div><div class="modal-foot">
      <button type="button" class="btn outline" data-act="modal-close">Cancel</button>
      <button type="submit" class="btn primary">${isNew ? "Create venue" : "Save changes"}</button>
    </div></form>`, true);
  $("#vForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const body = Object.fromEntries(f.entries());
    ["slot_minutes", "price_per_slot", "ground_cost"].forEach((k) => { body[k] = +body[k] || 0; });
    body.morning_cutoff = Math.max(0, Math.min(23, +body.morning_cutoff || 12));
    const costs = {};
    e.target.querySelectorAll("[data-fmt]").forEach((inp) => {
      if (inp.value === "") return;
      costs[inp.dataset.fmt] = costs[inp.dataset.fmt] || {};
      costs[inp.dataset.fmt][inp.dataset.cost] = +inp.value || 0;
    });
    body.format_costs = costs;
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      if (isNew) await api("/api/admin/venues", { method: "POST", body });
      else await api("/api/admin/venues/" + v.id, { method: "PATCH", body });
      closeModal(); toast(isNew ? "Venue created." : "Venue updated.", "ok");
      route();
    } catch (err) {
      btn.disabled = false;
      toast(err.message === "bad_name" ? "Give the venue a name (2–80 chars)."
        : "Could not save (" + err.message + ").", "err");
    }
  });
}

/* ---------------------------------------------------------------- match tags */
PAGES.tags = async (view) => {
  const d = await api("/api/admin/tags");
  const can = S.perms.tags_write;
  view.innerHTML =
    pageHead("Vocabulary", "Match tags", "Hero tags you can pin to fixtures — finals, night games, newcomers welcome.") +
    `<section class="panel"><div class="panel-head"><h3>${d.items.length} tags</h3></div>
      <div class="panel-body">
        <div class="tag-grid">${d.items.map((t) => `
          <span class="tag-item"><span class="dot" style="background:var(--${t.color === "gold" ? "gold" : t.color})"></span>
            ${esc(t.name)}
            ${can ? `<button class="del" data-act="tag-del" data-id="${t.id}" aria-label="Delete ${esc(t.name)}">×</button>` : ""}
          </span>`).join("") || `<span style="color:var(--faint);font-size:13.5px">No tags yet.</span>`}</div>
        ${can ? `<form id="tagForm" style="display:flex;gap:10px;margin-top:20px;flex-wrap:wrap">
          <input name="name" placeholder="e.g. Night lights" maxlength="24" required
            style="background:var(--bg2);border:1px solid var(--line2);border-radius:11px;padding:11px 14px;max-width:240px;color:var(--cream)">
          <select name="color" class="filter">
            ${["gold", "jade", "amber", "violet", "ember", "teal"]
              .map((c) => `<option value="${c}">${c}</option>`).join("")}</select>
          <button class="btn primary" type="submit">Add tag +</button></form>`
        : `<p class="page-sub" style="margin-top:16px">Only a superadmin can change tags.</p>`}
      </div></section>`;
  const tf = $("#tagForm");
  if (tf) tf.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(tf);
    try {
      await api("/api/admin/tags", { method: "POST",
        body: { name: (f.get("name") || "").trim(), color: f.get("color") } });
      toast("Tag added.", "ok"); route();
    } catch (err) {
      toast(err.message === "tag_exists" ? "That tag already exists."
        : err.message === "bad_tag" ? "Tags need 2–24 characters."
        : "Could not add (" + err.message + ").", "err");
    }
  });
};

/* ---------------------------------------------------------------- Ballon d'Or */
PAGES.chiefs = async (view) => {
  const B = S.bo;
  const p = new URLSearchParams({ period: B.period });
  if (B.period === "custom" && B.from) p.set("date_from", B.from);
  const d = await api("/api/admin/chiefs?" + p);
  const medals = ["\uD83E\uDD47", "\uD83E\uDD48", "\uD83E\uDD49"];
  const segs = [["week", "This week"], ["month", "This month"], ["custom", "Custom"]]
    .map(([v, l]) => `<button class="${B.period === v ? "is-on" : ""}" data-act="bo-period" data-v="${v}">${l}</button>`).join("");
  view.innerHTML =
    pageHead("Honours", "Ballon d'Or", "Football Chief leaderboard — ranked by matches hosted in the selected period.",
      `<div class="seg">${segs}</div>${B.period === "custom" ?
        `<input type="date" class="date-in" data-act="bo-from" value="${esc(B.from)}" aria-label="From">` : ""}`) +
    (d.items.length ? `<div class="chiefs">${d.items.map((c) => `
      <div class="chief-row">
        <span class="chief-rank">${c.rank <= 3 ? medals[c.rank - 1] : c.rank}</span>
        ${c.avatar_url ? `<img class="chief-av" src="${esc(c.avatar_url)}" alt="">`
          : `<span class="chief-av">${initials(c.name)}</span>`}
        <div class="chief-main"><div class="chief-name">${esc(c.name)}</div>
          <div class="chief-sub">Football Chief · period since ${when(d.since, true)}</div></div>
        <div class="chief-stats">
          <span class="cs"><b>${c.hosted}</b><span>Hosted</span></span>
          <span class="cs"><b>${c.wins}</b><span>Wins seen</span></span>
          <span class="cs"><b>${c.impact}</b><span>XP given</span></span>
        </div></div>`).join("")}</div>` : `
      <div class="empty panel"><div class="big">\uD83C\uDFC6</div>
        <h4>The board is waiting</h4>
        <p>Once chiefs start recording results, the leaderboard fills itself.</p></div>`);
};

/* ---------------------------------------------------------------- promos */
PAGES.promos = async (view) => {
  const d = await api("/api/admin/promos");
  const can = S.perms.promos_write;
  const rows = d.items.map((p) => {
    const st = !p.active ? "paused" : p.expired || p.exhausted ? "cancelled" : "active";
    const label = !p.active ? "paused" : p.expired ? "expired" : p.exhausted ? "used up" : "live";
    return `<tr>
      <td><span class="mono strong" style="letter-spacing:.1em">${esc(p.code)}</span></td>
      <td class="num" style="color:var(--gold-hi)">${p.percent}% off</td>
      <td class="num">${p.uses}${p.max_uses ? " / " + p.max_uses : " uses"}</td>
      <td class="num">${p.expires_at ? when(p.expires_at, true) : "never"}</td>
      <td><span class="pill ${st}">${label}</span></td>
      <td>${can ? `<button class="btn outline sm" data-act="promo-toggle" data-id="${p.id}"
          data-on="${p.active ? 0 : 1}">${p.active ? "Pause" : "Resume"}</button>
        <button class="btn danger sm" data-act="promo-del" data-id="${p.id}">Delete</button>` : ""}</td>
    </tr>`;
  }).join("");
  view.innerHTML =
    pageHead("Offers", "Promo codes", "Discount codes players can use when booking a slot.",
      can ? `<button class="btn primary" data-act="promo-new">New promo +</button>` : "") +
    `<section class="panel"><div class="panel-body flush">
      ${d.items.length ? `<table class="tbl"><thead><tr>
        <th>Code</th><th>Discount</th><th>Used</th><th>Expires</th><th>Status</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table>` : `
        <div class="empty"><div class="big">%</div><h4>No promo codes</h4>
          <p>Create a code to run a discount for your players.</p>
          ${can ? `<button class="btn primary" data-act="promo-new">Create a promo +</button>` : ""}</div>`}
    </div></section>`;
};

function promoForm() {
  openModal(`
    <div class="modal-head"><div><p class="eyebrow">New promo</p><h3>Create a code</h3></div>
      <button class="modal-x" data-act="modal-close" aria-label="Close">×</button></div>
    <form id="pForm"><div class="modal-body"><div class="form-grid">
      <label class="field"><span>Code</span><input name="code" placeholder="NIGHT10" maxlength="20"
        style="text-transform:uppercase" required></label>
      <label class="field"><span>Discount (%)</span><input type="number" name="percent" min="1" max="90" value="10"></label>
      <label class="field"><span>Max uses (blank = unlimited)</span>
        <input type="number" name="max_uses" min="1" placeholder="∞"></label>
      <label class="field"><span>Expires (blank = never)</span>
        <input type="date" name="expires_at"></label>
    </div></div><div class="modal-foot">
      <button type="button" class="btn outline" data-act="modal-close">Cancel</button>
      <button type="submit" class="btn primary">Create promo</button></div></form>`);
  $("#pForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      await api("/api/admin/promos", { method: "POST", body: {
        code: (f.get("code") || "").trim().toUpperCase(),
        percent: +f.get("percent") || 10,
        max_uses: f.get("max_uses") || null,
        expires_at: f.get("expires_at") || null,
      } });
      closeModal(); toast("Promo created.", "ok"); route();
    } catch (err) {
      btn.disabled = false;
      toast(err.message === "code_exists" ? "That code already exists."
        : err.message === "bad_code" ? "Codes: 3–20 letters or digits."
        : err.message === "bad_percent" ? "Discount must be 1–90%."
        : "Could not create (" + err.message + ").", "err");
    }
  });
}

/* ---------------------------------------------------------------- staff & roles */
PAGES.staff = async (view) => {
  if (!S.perms.staff_manage) { toast("Only a superadmin manages roles.", "err"); location.hash = "#/overview"; return; }
  const d = await api("/api/admin/staff");
  const roleChip = (r) => `<span class="tag-chip" data-c="${r === "venue_admin" ? "jade" : r === "football_chief" ? "amber" : "violet"}">
    ${esc(ROLE_LABEL[r] || r)}</span>`;
  const rows = d.items.map((s) => `
    <tr>
      <td>${roleChip(s.role)}</td>
      <td><span class="strong">${esc(s.name)}</span>
        <span class="cell-sub">${esc(s.email || s.phone || "—")}</span></td>
      <td>${s.venue ? esc(s.venue) : `<span style="color:var(--faint)">—</span>`}</td>
      <td class="num">${when(s.created_at, true)}</td>
      <td><button class="btn danger sm" data-act="staff-revoke" data-id="${s.id}">Revoke</button></td>
    </tr>`).join("");
  view.innerHTML =
    pageHead("Access", "Staff &amp; roles", "Who runs what: venue admins own a ground, Football Chiefs lead their matches.",
      `<button class="btn primary" data-act="staff-new">Grant a role +</button>`) +
    `<section class="panel"><div class="panel-body flush">
      ${d.items.length ? `<table class="tbl"><thead><tr>
        <th>Role</th><th>Person</th><th>Venue scope</th><th>Granted</th><th></th>
      </tr></thead><tbody>${rows}</tbody></table>` : `
        <div class="empty"><div class="big">\uD83D\uDD10</div><h4>No staff roles yet</h4>
          <p>Grant someone a role and they can sign in to this console with their own account.</p>
          <button class="btn primary" data-act="staff-new">Grant the first role +</button></div>`}
    </div></section>`;
};

async function staffForm() {
  const vd = await api("/api/admin/venues").catch(() => ({ items: [] }));
  openModal(`
    <div class="modal-head"><div><p class="eyebrow">Staff &amp; roles</p><h3>Grant a role</h3></div>
      <button class="modal-x" data-act="modal-close" aria-label="Close">×</button></div>
    <form id="sForm"><div class="modal-body">
      <label class="field"><span>Person (their sign-in email or WhatsApp number)</span>
        <input name="who" placeholder="they must have an account already" required></label>
      <label class="field"><span>Role</span><select name="role" id="sRole">
        <option value="football_chief">Football Chief — leads matches</option>
        <option value="venue_admin">Venue Admin — owns a ground</option></select></label>
      <label class="field" id="sVenueWrap" hidden><span>Venue they manage</span>
        <select name="venue_id"><option value="">— choose —</option>
          ${vd.items.map((v) => `<option value="${v.id}">${esc(v.name)} · ${esc(v.city)}</option>`).join("")}
        </select></label>
      <p class="page-sub">The person signs in to <b>/admin</b> with their own password or WhatsApp OTP —
        roles are checked on every request.</p>
    </div><div class="modal-foot">
      <button type="button" class="btn outline" data-act="modal-close">Cancel</button>
      <button type="submit" class="btn primary">Grant role</button></div></form>`);
  $("#sRole").addEventListener("change", (e) => {
    $("#sVenueWrap").hidden = e.target.value !== "venue_admin";
  });
  $("#sForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const who = (f.get("who") || "").trim();
    const body = { role: f.get("role") };
    if (body.role === "venue_admin") {
      body.venue_id = +f.get("venue_id") || 0;
      if (!body.venue_id) { toast("Pick the venue they manage.", "err"); return; }
    }
    if (who.includes("@")) body.email = who;
    else if (/^[+\d][\d\s-]{7,}$/.test(who)) body.phone = who;
    else { toast("Enter an email address or a WhatsApp number.", "err"); return; }
    const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      const resp = await api("/api/admin/staff", { method: "POST", body });
      closeModal();
      toast(`${resp.user.name} is now a ${ROLE_LABEL[body.role]}.`, "ok");
      route();
    } catch (err) {
      btn.disabled = false;
      toast({ user_not_found: "No account with those details — ask them to sign in once first.",
        already_staff: "They already hold that role.",
        venue_required: "Pick the venue they manage.",
        bad_phone: "That phone number doesn't look right." }[err.message] ||
        "Could not grant (" + err.message + ").", "err");
    }
  });
}

/* ---------------------------------------------------------------- delegation */
document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act, id = t.dataset.id;
  try {
    switch (act) {
      case "modal-close": closeModal(); break;
      case "logout":
        api("/api/auth/logout", { method: "POST" }).catch(() => {});
        S.me = null; showLogin(); toast("Signed out.", "ok"); break;
      case "go": location.hash = t.dataset.go; break;
      case "open-match": await openMatch(+id); break;
      case "result-open": closeModal(); await openResult(+id); break;
      case "cancel-match":
        if (confirm("Cancel this match? Players will see it as cancelled.")) {
          await api("/api/admin/matches/" + id, { method: "PATCH", body: { status: "cancelled" } });
          closeModal(); toast("Match cancelled.", "ok"); route();
        }
        break;
      case "page": S.list.page = +t.dataset.p; S._focusSearch = false; route(); break;
      case "mstatus": S.list.status = t.dataset.v; S.list.page = 1; route(); break;
      case "rp-add": {
        const pop = $("#rpPop"); if (pop) pop.hidden = true;
        const si = $("#rpSearch"); if (si) si.value = "";
        const side = S.pool.match.sides[0].name;
        if (!S.pool.picked.some((x) => x.user_id === +id))
          S.pool.picked.push({ user_id: +id, name: t.dataset.name, side });
        renderPool();
        if (si) si.focus();
        break;
      }
      case "rp-del": S.pool.picked = S.pool.picked.filter((x) => x.user_id !== +id);
        if (S.pool.mvp === +id) S.pool.mvp = null; renderPool(); break;
      case "rp-mvp": S.pool.mvp = S.pool.mvp === +id ? null : +id; renderPool(); break;
      case "venue-new": venueForm(null); break;
      case "venue-edit": venueForm((S._venues || []).find((v) => v.id === +id)); break;
      case "tag-del":
        if (confirm("Delete this tag?")) {
          await api("/api/admin/tags/" + id, { method: "DELETE" });
          toast("Tag deleted.", "ok"); route();
        }
        break;
      case "promo-new": promoForm(); break;
      case "promo-toggle":
        await api("/api/admin/promos/" + id, { method: "PATCH", body: { active: t.dataset.on === "1" } });
        toast("Promo updated.", "ok"); route(); break;
      case "promo-del":
        if (confirm("Delete this promo code?")) {
          await api("/api/admin/promos/" + id, { method: "DELETE" });
          toast("Promo deleted.", "ok"); route();
        }
        break;
      case "staff-new": await staffForm(); break;
      case "staff-revoke":
        if (confirm("Revoke this role?")) {
          await api("/api/admin/staff/" + id, { method: "DELETE" });
          toast("Role revoked.", "ok"); route();
        }
        break;
      case "bo-period": S.bo.period = t.dataset.v; route(); break;
      default: break;
    }
  } catch (err) {
    toast(err.data?.detail === "forbidden" || err.status === 403
      ? "Your role doesn't allow that." : "Failed (" + err.message + ").", "err");
  }
});

document.addEventListener("change", (e) => {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act;
  if (act === "msport") { S.list.sport = t.value; S.list.page = 1; route(); }
  else if (act === "mfrom") { S.list.from = t.value; S.list.page = 1; route(); }
  else if (act === "mto") { S.list.to = t.value; S.list.page = 1; route(); }
  else if (act === "rp-side") {
    const p = S.pool && S.pool.picked.find((x) => x.user_id === +t.dataset.id);
    if (p) p.side = t.value;
  }
  else if (act === "bo-from") { S.bo.from = t.value; route(); }
});

/* refresh venues cache for the edit modal */

