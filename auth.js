/* ============================================================
   MatchArena — account layer (auth.js)
   Sign in with Google / email+password / WhatsApp OTP,
   profile drawer with avatar upload and the FIFA-FC player card.
   Talks to the backend on the SAME origin (see BACKEND_GUIDE.md).
   ============================================================ */
(() => {
  "use strict";

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];

  const state = {
    user: null,          // public user object
    devMode: false,
    googleId: "",
    emailMode: "login",  // login | register
    card: null,          // last FC card payload
  };

  /* ---------------------------------------------------------- api */
  async function api(path, opts = {}) {
    const cfg = { credentials: "include", headers: {} };
    if (opts.body && !(opts.body instanceof FormData)) {
      cfg.headers["Content-Type"] = "application/json";
      cfg.body = JSON.stringify(opts.body);
    } else if (opts.body) {
      cfg.body = opts.body;
    }
    cfg.method = opts.method || (cfg.body ? "POST" : "GET");
    let res = await fetch(path, cfg);
    if (res.status === 401 && !opts._retried && !path.includes("/auth/")) {
      // access cookie expired -> try one silent refresh, then retry
      const r = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
      if (r.ok) { opts._retried = true; return api(path, opts); }
    }
    let data = {};
    try { data = await res.json(); } catch (_) { /* empty body */ }
    if (!res.ok) {
      const err = new Error(data.error || data.detail || "request_failed");
      err.status = res.status;
      err.data = data;
      throw err;
    }
    return data;
  }

  const ERRORS = {
    bad_credentials: "Email or password is incorrect.",
    email_taken: "That email already has an account — try signing in.",
    bad_email: "Please enter a valid email address.",
    bad_password: "Password must be at least 8 characters.",
    bad_name: "Please enter your name (2–60 characters).",
    bad_phone: "Please enter a valid WhatsApp number.",
    bad_code: "The code is 6 digits.",
    invalid_code: "That code is wrong or expired — request a new one.",
    code_attempts_exceeded: "Too many attempts — request a new code.",
    account_locked: "Too many failed attempts. Try again in 15 minutes.",
    rate_limited: "Too many tries — please wait a minute and retry.",
    otp_send_failed: "Could not send the code right now. Try email instead.",
    name_required: "Enter your name to finish creating your account.",
    google_not_configured: "Google Sign-In isn't configured yet (backend/.env).",
    bad_google_token: "Google sign-in could not be verified.",
    not_authenticated: "Please sign in.",
    avatar_too_big: "Image must be under 2.5 MB.",
    bad_image: "That file isn't a supported image (PNG/JPG/WebP).",
  };
  function showErr(code) {
    const m = ERRORS[code] || (String(code).startsWith("rate_limited")
      ? `Too many tries — wait ${String(code).split(":")[1] || 60}s.`
      : "Something went wrong. Please try again.");
    const el = $("#authMsg");
    el.textContent = m;
    el.className = "auth-msg err show";
  }
  function showOk(text) {
    const el = $("#authMsg");
    el.textContent = text;
    el.className = "auth-msg ok show";
  }
  function toast(text) {
    const t = $("#toast");
    if (!t) return;
    t.textContent = text;
    t.classList.add("show");
    clearTimeout(t._tm);
    t._tm = setTimeout(() => t.classList.remove("show"), 3200);
  }

  /* ---------------------------------------------------------- modal */
  const authModal = $("#authModal");
  function openAuth(tab) {
    authModal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    if (tab) switchTab(tab);
    $("#authMsg").className = "auth-msg";
  }
  function closeAuth() {
    authModal.setAttribute("aria-hidden", "true");
    document.body.style.overflow = "";
  }
  $("#authBtn").addEventListener("click", () => openAuth());
  $$("[data-auth-close]").forEach((el) => el.addEventListener("click", closeAuth));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (authModal.getAttribute("aria-hidden") === "false") closeAuth();
      else if ($("#profDrawer").classList.contains("open")) closeProfile();
    }
  });

  function switchTab(name) {
    $$(".auth-tab").forEach((t) => {
      const on = t.dataset.atab === name;
      t.classList.toggle("active", on);
      t.setAttribute("aria-selected", String(on));
    });
    $$(".auth-pane").forEach((p) => { p.hidden = p.dataset.pane !== name; });
    $("#authMsg").className = "auth-msg";
    if (name === "google") renderGoogle();
  }
  $$(".auth-tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.atab)));

  /* ---------------------------------------------------------- google */
  let gisLoaded = false;
  function renderGoogle() {
    const mount = $("#gisMount");
    if (!state.googleId) {
      $("#gisNote").hidden = false;
      $("#gisFallback").disabled = true;
      return;
    }
    if (gisLoaded && window.google?.accounts?.id) {
      $("#gisFallback").hidden = true;
      $("#gisNote").hidden = true;
      window.google.accounts.id.initialize({
        client_id: state.googleId,
        callback: async (resp) => {
          try {
            showOk("Verifying with Google…");
            const data = await api("/api/auth/google", { body: { credential: resp.credential } });
            onSignedIn(data.user, "Welcome back!");
          } catch (e) { showErr(e.data?.detail || e.message); }
        },
      });
      window.google.accounts.id.renderButton(mount, {
        theme: "outline", size: "large", width: 320, text: "continue_with",
      });
      return;
    }
    if (!gisLoaded) {
      gisLoaded = true;
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client";
      s.async = true;
      s.onload = () => renderGoogle();
      s.onerror = () => { $("#gisNote").hidden = false; $("#gisNote").textContent = "Could not load Google Sign-In (offline?)."; };
      document.head.appendChild(s);
    }
  }

  /* ---------------------------------------------------------- email */
  const emailForm = $("#emailForm");
  $("#emailSwitch").addEventListener("click", () => {
    state.emailMode = state.emailMode === "login" ? "register" : "login";
    const reg = state.emailMode === "register";
    $$(".auth-name", emailForm).forEach((l) => { l.hidden = !reg; });
    $("input[name=ename]", emailForm).required = reg;
    $("#emailSubmit").innerHTML = (reg ? "Create account" : "Sign in") + " <span>→</span>";
    $("#emailSwitch").textContent = reg
      ? "Already have an account? Sign in →" : "New here? Create an account →";
    $("#authTitle").innerHTML = (reg ? "Create account" : "Sign in") + " <em>&amp; level up</em>";
    $("#authMsg").className = "auth-msg";
  });
  emailForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(emailForm);
    const email = (f.get("eemail") || "").trim();
    const password = f.get("epass") || "";
    const name = (f.get("ename") || "").trim();
    const reg = state.emailMode === "register";
    try {
      const data = await api(reg ? "/api/auth/register" : "/api/auth/login", {
        body: reg ? { name, email, password } : { email, password },
      });
      onSignedIn(data.user, reg ? "Account created — you're in!" : "Welcome back!");
    } catch (err) { showErr(err.data?.detail || err.message); }
  });

  /* ---------------------------------------------------------- phone / whatsapp OTP */
  const phoneForm = $("#phoneForm");
  const otpForm = $("#otpForm");
  let otpTarget = "";
  let otpTimer = null;

  phoneForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const target = new FormData(phoneForm).get("pnum") || "";
    try {
      const data = await api("/api/auth/otp/send", { body: { channel: "whatsapp", target } });
      otpTarget = target;
      phoneForm.hidden = true;
      otpForm.hidden = false;
      $("#otpResend").hidden = false;
      $("#otpBack").hidden = false;
      const note = $("#otpNote");
      note.hidden = false;
      if (data.dev_code) {
        note.innerHTML = `DEV MODE — code: <b>${data.dev_code}</b> (also printed in the server console).`;
        otpForm.querySelector("[name=ocode]").value = data.dev_code;
      } else {
        note.textContent = `Code sent to ${data.target} on WhatsApp. Valid 5 minutes.`;
      }
      startCooldown(data.cooldown || 90);
      showOk("OTP sent — enter the code.");
      otpForm.querySelector("[name=ocode]").focus();
    } catch (err) { showErr(err.data?.detail || err.message); }
  });

  function startCooldown(sec) {
    const btn = $("#otpResend");
    clearInterval(otpTimer);
    let left = sec;
    btn.disabled = true;
    btn.textContent = `Resend in ${left}s`;
    otpTimer = setInterval(() => {
      left--;
      if (left <= 0) {
        clearInterval(otpTimer);
        btn.disabled = false;
        btn.textContent = "Resend the code";
      } else btn.textContent = `Resend in ${left}s`;
    }, 1000);
  }

  $("#otpResend").addEventListener("click", async () => {
    try {
      const data = await api("/api/auth/otp/send", { body: { channel: "whatsapp", target: otpTarget } });
      if (data.dev_code) {
        $("#otpNote").innerHTML = `DEV MODE — code: <b>${data.dev_code}</b>`;
        otpForm.querySelector("[name=ocode]").value = data.dev_code;
      }
      startCooldown(data.cooldown || 90);
      showOk("New code sent.");
    } catch (err) { showErr(err.data?.detail || err.message); }
  });
  $("#otpBack").addEventListener("click", () => {
    otpForm.hidden = true;
    phoneForm.hidden = false;
    $("#otpResend").hidden = true;
    $("#otpBack").hidden = true;
    $("#otpNote").hidden = true;
    $("#authMsg").className = "auth-msg";
  });

  otpForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(otpForm);
    const code = (f.get("ocode") || "").trim();
    const name = (f.get("oname") || "").trim();
    try {
      const data = await api("/api/auth/otp/verify", {
        body: { channel: "whatsapp", target: otpTarget, code, name },
      });
      onSignedIn(data.user, "Verified — welcome to the arena!");
    } catch (err) {
      const d = err.data?.detail || err.message;
      if (d === "name_required") {
        $$(".auth-name", otpForm).forEach((l) => { l.hidden = false; });
        showErr(d);
      } else showErr(d);
    }
  });

  /* ---------------------------------------------------------- session state */
  async function onSignedIn(user, hello) {
    state.user = user;
    closeAuth();
    renderIdentity();
    if (hello) toast(`${hello} ${user.name}`);
    openProfile();          // straight into the locker -> shows the FC card
  }

  function initials(name = "?") {
    const parts = name.trim().split(/\s+/);
    return ((parts[0]?.[0] || "?") + (parts[1]?.[0] || "")).toUpperCase() || "?";
  }

  function renderIdentity() {
    const u = state.user;
    const authBtn = $("#authBtn");
    const chip = $("#profileChip");
    if (!u) {
      authBtn.hidden = false;
      chip.hidden = true;
      return;
    }
    authBtn.hidden = true;
    chip.hidden = false;
    $("#pcName").textContent = u.name;
    const pcA = $("#pcAvatar");
    if (u.avatar_url) {
      pcA.innerHTML = `<img src="${u.avatar_url}" alt="">`;
      pcA.classList.add("has-img");
    } else {
      pcA.textContent = initials(u.name);
      pcA.classList.remove("has-img");
    }
    $("#profName").textContent = u.name;
    $("#profSub").textContent = [u.email, u.phone && (u.phone_verified ? u.phone : u.phone + " (unverified)")]
      .filter(Boolean).join(" · ") || "—";
    $("#renameInput").value = u.name;
    const pi = $("#profInitials");
    const img = $("#profImg");
    if (u.avatar_url) { img.src = u.avatar_url; img.hidden = false; pi.hidden = true; }
    else { img.hidden = true; pi.hidden = false; pi.textContent = initials(u.name); }
  }

  /* ---------------------------------------------------------- profile drawer */
  const drawer = $("#profDrawer");
  function openProfile() {
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    $("#profOverlay").classList.add("show");
    loadCard();
  }
  function closeProfile() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    $("#profOverlay").classList.remove("show");
  }
  $("#profileChip").addEventListener("click", openProfile);
  $$("[data-prof-close]").forEach((el) => el.addEventListener("click", closeProfile));

  $("#renameBtn").addEventListener("click", async () => {
    if (!state.user) return;
    try {
      const data = await api("/api/me", { method: "PATCH", body: { display_name: $("#renameInput").value } });
      state.user = data.user;
      renderIdentity();
      if (state.card) { state.card.name = data.user.name; renderCard(state.card); }
      toast("Name updated.");
    } catch (e) { toast(e.data?.detail === "bad_name" ? "Name must be 2–60 characters." : "Could not save name."); }
  });

  $("#avatarInput").addEventListener("change", async (e) => {
    const file = e.target.files?.[0];
    if (!file || !state.user) return;
    const fd = new FormData();
    fd.append("file", file);
    try {
      const data = await api("/api/me/avatar", { body: fd });
      state.user.avatar_url = data.avatar_url;
      renderIdentity();
      if (state.card) { state.card.avatar_url = data.avatar_url; renderCard(state.card); }
      toast("Profile photo updated ⬆");
    } catch (err) { toast(err.data?.detail || "Upload failed."); }
    e.target.value = "";
  });

  $("#logoutBtn").addEventListener("click", async () => {
    try { await api("/api/auth/logout", { body: {} }); } catch (_) { /* already out */ }
    state.user = null;
    state.card = null;
    renderIdentity();
    closeProfile();
    toast("Signed out. See you on the pitch.");
  });

  $("#demoMatch").addEventListener("click", async () => {
    const btn = $("#demoMatch");
    btn.disabled = true;
    try {
      const sports = ["football", "cricket", "badminton", "basketball", "pickleball"];
      const sport = sports[Math.floor(Math.random() * sports.length)];
      const won = Math.random() > 0.25;
      const mvp = won && Math.random() > 0.6;
      const data = await api("/api/dev/match", { body: { sport, won, mvp } });
      await loadCard(data);
      animateUpgrade(data);
      toast(`${won ? "Victory" : "Played"} — ${data.xp_gained} XP! ${mvp ? "🏅 MVP bonus" : ""}`);
    } catch (e) {
      toast(e.status === 404 ? "Dev mode is off in production." : "Could not record the match.");
    } finally { btn.disabled = false; }
  });

  /* ---------------------------------------------------------- FC card */
  async function loadCard() {
    if (!state.user) return;
    try {
      const data = await api("/api/me/card");
      state.card = data.card;
      renderCard(data.card);
    } catch (_) { /* session refresh already attempted in api() */ }
  }

  const STAT_LABELS = [["pac", "PAC"], ["sho", "SHO"], ["pas", "PAS"], ["dri", "DRI"], ["def", "DEF"], ["phy", "PHY"]];

  function renderCard(c) {
    const wrap = $("#fcWrap");
    const stats = STAT_LABELS.map(([k, label]) =>
      `<div class="fc-stat" data-k="${k}"><b>${c.stats[k]}</b><span>${label}</span></div>`).join("");
    const photo = c.avatar_url
      ? `<img src="${c.avatar_url}" alt="">`
      : `<span>${initials(c.name)}</span>`;
    wrap.innerHTML = `
      <div class="fc-card tier-${c.tier.key}" id="fcCard">
        <div class="fc-shine" aria-hidden="true"></div>
        <div class="fc-top">
          <div class="fc-side">
            <span class="fc-ovr">${c.ovr}</span>
            <span class="fc-pos">${c.position}</span>
            <span class="fc-lvl">LVL ${c.level}</span>
          </div>
          <div class="fc-photo">${photo}</div>
        </div>
        <div class="fc-namebar"><b>${c.name.toUpperCase()}</b><span>⚡ MATCHARENA · ${c.tier.label.toUpperCase()}</span></div>
        <div class="fc-stats">${stats}</div>
        <div class="fc-xp" title="${c.xp_into}/${c.xp_next} XP to next level"><i style="width:${Math.round((c.xp_into / c.xp_next) * 100)}%"></i></div>
        <div class="fc-foot"><span>${c.xp} XP</span><span>${c.matches} played · ${c.wins} wins · ${c.mvps} MVP</span></div>
      </div>`;
  }

  /* after recording a match: flash every stat that went up and glow the card */
  function animateUpgrade(data) {
    const before = data.before, after = data.after;
    const card = $("#fcCard");
    if (!card) return;
    if (after.ovr > before.ovr) card.querySelector(".fc-ovr").classList.add("up");
    if (after.tier.key !== before.tier.key) card.classList.add("tier-up");
    STAT_LABELS.forEach(([k]) => {
      const gain = after.stats[k] - before.stats[k];
      if (gain > 0) {
        const el = card.querySelector(`.fc-stat[data-k="${k}"]`);
        if (!el) return;
        el.classList.add("up");
        el.setAttribute("data-gain", "+" + gain);
        setTimeout(() => { el.classList.remove("up"); el.removeAttribute("data-gain"); }, 2600);
      }
    });
    card.classList.remove("levelup");
    void card.offsetWidth;             // restart the animation
    card.classList.add("levelup");
  }

  /* ---------------------------------------------------------- boot */
  (async function init() {
    try {
      const cfg = await api("/api/config");
      state.googleId = cfg.google_client_id || "";
      state.devMode = !!cfg.dev_mode;
      $("#demoMatch").hidden = !state.devMode;
    } catch (_) { /* backend down: UI still loads */ }
    try {
      const me = await api("/api/me");
      state.user = me.user;
      state.devMode = state.devMode || !!me.dev_mode;
      $("#demoMatch").hidden = !state.devMode;
    } catch (_) { /* signed out */ }
    renderIdentity();
  })();
})();
