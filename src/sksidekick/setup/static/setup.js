// First-run setup page. No framework, no build step, nothing from the internet.
// Every URL is relative (the page may sit under a path prefix). The setup token is read from the
// address (or, after a reload, from this tab's sessionStorage, gone when the tab closes) and dropped
// from the address bar. The recovery phrase lives only on the phrase screen, in memory and the DOM,
// and only until that screen is left or confirmed; it is never stored anywhere.
"use strict";

(function () {
  const STEPS = ["tier", "channels", "personality", "phrase", "backup"];
  const FIELD_LABELS = {
    NEXTCLOUD_TALK_SERVER_URL: "Server address",
    NEXTCLOUD_TALK_USERNAME: "User name",
    NEXTCLOUD_TALK_APP_PASSWORD: "App password",
    TELEGRAM_BOT_TOKEN: "Bot token",
    TELEGRAM_ALLOWED_USERS: "Your Telegram user id",
  };
  const DIGITS = { TELEGRAM_ALLOWED_USERS: true };
  const SECRET = /PASSWORD|TOKEN|KEY/;

  const TOKEN_KEY = "sksidekick-setup-token";
  const OPEN_KEY = "sksidekick-phrase-open"; // a flag only: the phrase screen was showing groups
  // sessionStorage may be blocked (private mode, policy): then the token lives in memory only.
  function stored(k) { try { return sessionStorage.getItem(k) || ""; } catch (e) { return ""; } }
  function store(k, v) { try { if (v) sessionStorage.setItem(k, v); else sessionStorage.removeItem(k); } catch (e) { /* memory only */ } }

  let token = new URLSearchParams(location.search).get("t") || stored(TOKEN_KEY);
  store(TOKEN_KEY, token);
  history.replaceState(null, "", location.pathname + location.hash);

  let st = null; // the last GET api/state
  let current = null; // the screen on show
  let held = null; // {groups, check} between phrase/new and a good confirm; never anywhere else

  function $(id) { return document.getElementById(id); }
  function byId(id) { return document.getElementById(id); }
  function el(tag, attrs, text) {
    const e = document.createElement(tag);
    for (const k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  // ---- talking to the setup server ----
  async function get(path) {
    const r = await fetch(path, { credentials: "same-origin", cache: "no-store" });
    let body = {};
    try { body = await r.json(); } catch (e) { body = {}; }
    return { ok: r.ok, status: r.status, body: body };
  }

  async function post(path, data) {
    const r = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers: { "Content-Type": "application/json", "X-Setup-Token": token },
      body: JSON.stringify(data || {}),
    });
    let body = {};
    try { body = await r.json(); } catch (e) { body = {}; }
    return { ok: r.ok, status: r.status, body: body };
  }

  async function send(path, data, errorFallback) {
    let r;
    try {
      r = await post(path, data);
    } catch (e) {
      show("gone");
      return null;
    }
    if (r.ok) return r;
    if (r.status === 403) { token = ""; store(TOKEN_KEY, ""); show("lost"); return null; }
    if (r.status === 409) {
      await refresh();
      say(r.body.error || "That step is already done.");
      return null;
    }
    if (r.status === 422) { fieldError(r.body.field, r.body.error, errorFallback); return r; }
    fieldError(null, "Something went wrong. Try again.", errorFallback);
    return null;
  }

  // ---- messages and errors ----
  function say(text) {
    const s = $("status-msg");
    s.textContent = text || "";
    s.hidden = !text;
  }

  function clearErrors(section) {
    section.querySelectorAll(".error").forEach(function (p) { p.textContent = ""; p.hidden = true; });
    section.querySelectorAll("[aria-invalid]").forEach(function (i) { i.removeAttribute("aria-invalid"); });
  }

  function fieldError(field, message, fallback) {
    const target = (field && byId("err-" + field)) || byId("err-" + fallback);
    if (!target) { say(message); return; }
    target.textContent = message || "Check this and try again.";
    target.hidden = false;
    const input = field && byId("f-" + field);
    if (input) { input.setAttribute("aria-invalid", "true"); input.focus(); }
  }

  // ---- screens ----
  function done(step) { return !!st && st.completed.indexOf(step) >= 0; }

  function show(name) {
    if (current === "phrase" && name !== "phrase") { forgetPhrase(); store(OPEN_KEY, ""); }
    document.querySelectorAll(".screen").forEach(function (s) { s.hidden = true; });
    say("");
    const section = byId("s-" + name);
    section.hidden = false;
    current = name;
    document.querySelectorAll("#progress li").forEach(function (li) {
      const step = li.getAttribute("data-step");
      li.classList.toggle("is-done", done(step));
      if (step === name) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    $("progress").hidden = STEPS.indexOf(name) < 0 && name !== "done";
    if (STEPS.indexOf(name) >= 0) markDone(name);
    const h = section.querySelector("h2");
    if (h) h.focus();
  }

  function markDone(step) {
    const isDone = done(step);
    byId(step + "-done").hidden = !isDone;
    const section = byId("s-" + step);
    section.querySelectorAll("fieldset").forEach(function (f) { f.disabled = isDone; });
    section.querySelectorAll("button[type=submit]").forEach(function (b) { b.hidden = isDone; });
    section.querySelectorAll(".next").forEach(function (b) { b.hidden = !isDone; });
    if (step === "phrase") {
      $("phrase-next").hidden = !isDone;
      if (isDone) { $("phrase-start").hidden = true; $("phrase-show").hidden = true; $("phrase-confirm").hidden = true; }
      else phraseView("start");
    }
  }

  function go(offset) {
    const i = STEPS.indexOf(current);
    const j = i + offset;
    if (j < 0) return;
    if (j >= STEPS.length) { show("done"); return; }
    if (offset > 0 && !done(STEPS[i])) return; // forward only past a finished step
    show(STEPS[j]);
  }

  // resume: true only for the first load of the page. A refresh after a 409 or a finished step never
  // starts a new phrase on its own (a lasting 409 on phrase/new would otherwise loop).
  async function refresh(resume) {
    let r;
    try { r = await get("api/state"); } catch (e) { show("gone"); return false; }
    if (r.status === 403) { token = ""; store(TOKEN_KEY, ""); show("lost"); return false; }
    if (!r.ok) { show("gone"); return false; }
    st = r.body;
    render();
    show(st.next && st.next !== "done" ? st.next : "done");
    // a reload while the groups were on screen: the old phrase is gone (the server shreds its key), show a new one
    if (resume === true && current === "phrase" && !done("phrase") && stored(OPEN_KEY)) {
      store(OPEN_KEY, ""); // cleared first: newPhrase sets it again only once a phrase is on screen
      newPhrase();
    }
    return true;
  }

  // ---- step 1: the model ----
  function renderTiers() {
    const box = $("tier-options");
    clear(box);
    Object.keys(st.tiers).forEach(function (id) {
      const t = st.tiers[id];
      const enabled = t.enabled !== false;
      const wrap = el("div", { class: "card" + (enabled ? "" : " is-disabled") });
      const label = el("label", { class: "choice" });
      const input = el("input", { type: "radio", name: "tier", value: id, id: "tier-" + id });
      if (!enabled) input.disabled = true;
      label.appendChild(input);
      label.appendChild(el("strong", {}, t.title));
      wrap.appendChild(label);
      wrap.appendChild(el("p", { class: "privacy" }, t.label));
      if (!enabled && t.disabled_label) wrap.appendChild(el("p", { class: "hint" }, t.disabled_label));
      box.appendChild(wrap);
      input.addEventListener("change", tierFields);
    });
    tierFields();
  }

  function chosen(name) {
    const c = document.querySelector("input[name=" + name + "]:checked");
    return c ? c.value : "";
  }

  function tierFields() {
    const t = chosen("tier");
    const needs = t && t !== "shared" && !done("tier") ? st.tiers[t].needs : [];
    ["base_url", "model", "api_key"].forEach(function (f) { byId("row-" + f).hidden = needs.indexOf(f) < 0; });
  }

  $("form-tier").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    clearErrors($("s-tier"));
    const tier = chosen("tier");
    if (!tier) { fieldError("tier", "Choose one of the options.", "tier"); return; }
    const body = { tier: tier };
    if (tier !== "shared") {
      body.base_url = $("f-base_url").value;
      body.model = $("f-model").value;
      if (tier === "own") body.api_key = $("f-api_key").value;
    }
    say("Checking that the model answers.");
    const r = await send("api/tier", body, "tier");
    if (r && r.ok) { $("f-api_key").value = ""; await refresh(); }
    else if (current === "tier") say("");
  });

  // ---- step 2: channels ----
  function renderChannels() {
    const box = $("channel-list");
    clear(box);
    st.channels.forEach(function (c) {
      const enabled = c.enabled !== false;
      const wrap = el("div", { class: "card" + (enabled ? "" : " is-disabled") });
      const label = el("label", { class: "choice" });
      const input = el("input", { type: "checkbox", name: "channel", value: c.id, id: "ch-" + c.id });
      if (!enabled) input.disabled = true;
      label.appendChild(input);
      label.appendChild(el("strong", {}, c.title));
      wrap.appendChild(label);
      wrap.appendChild(el("p", { class: "privacy" }, c.label));
      if (!enabled && c.disabled_reason) wrap.appendChild(el("p", { class: "hint" }, c.disabled_reason));
      if (c.configured) wrap.appendChild(el("p", { class: "hint" }, "Already set up on this computer."));
      const fields = el("div", { class: "sub", id: "chf-" + c.id });
      fields.hidden = true;
      c.fields.forEach(function (name) {
        const row = el("div", { class: "row" });
        row.appendChild(el("label", { for: "f-" + name }, FIELD_LABELS[name] || name));
        const help = c.help && c.help[name];
        const attrs = { id: "f-" + name, type: SECRET.test(name) ? "password" : "text",
          autocomplete: "off", spellcheck: "false", "aria-describedby": (help ? "help-" + name + " " : "") + "err-" + name,
          "data-field": name };
        if (DIGITS[name]) attrs.inputmode = "numeric";
        const f = el("input", attrs);
        row.appendChild(f);
        if (help) row.appendChild(el("p", { id: "help-" + name, class: "hint" }, help));
        const err = el("p", { id: "err-" + name, class: "error", role: "alert" });
        err.hidden = true;
        row.appendChild(err);
        fields.appendChild(row);
      });
      wrap.appendChild(fields);
      input.addEventListener("change", function () { fields.hidden = !input.checked; });
      box.appendChild(wrap);
    });
  }

  $("form-channels").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    clearErrors($("s-channels"));
    const items = [];
    document.querySelectorAll("input[name=channel]:checked").forEach(function (input) {
      const fields = {};
      byId("chf-" + input.value).querySelectorAll("input[data-field]").forEach(function (f) {
        fields[f.getAttribute("data-field")] = f.value;
      });
      items.push({ id: input.value, fields: fields });
    });
    const r = await send("api/channels", { channels: items }, "channels");
    if (r && r.ok) {
      $("channel-list").querySelectorAll("input[type=password]").forEach(function (f) { f.value = ""; });
      await refresh();
    }
  });

  // ---- step 3: personality and voice ----
  let playing = null;

  function stopAudio() {
    if (playing) {
      playing.audio.pause();
      playing.audio.currentTime = 0;
      playing.button.textContent = playing.text;
      playing.button.setAttribute("aria-pressed", "false");
      playing = null;
    }
  }

  function playButton(p, voice) {
    const text = "Play the " + voice + " voice";
    const b = el("button", { type: "button", class: "play", "aria-pressed": "false",
      "aria-label": text + " for " + p.title }, text);
    const audio = el("audio", { preload: "none", src: p.audio[voice] });
    audio.addEventListener("ended", stopAudio);
    b.addEventListener("click", function () {
      const was = playing && playing.audio === audio;
      stopAudio();
      if (was) return;
      playing = { audio: audio, button: b, text: text };
      b.textContent = "Stop";
      b.setAttribute("aria-pressed", "true");
      audio.play().catch(stopAudio);
    });
    return [b, audio];
  }

  function renderPersonalities() {
    const box = $("personality-list");
    clear(box);
    st.personalities.forEach(function (p) {
      const wrap = el("div", { class: "card" });
      const label = el("label", { class: "choice" });
      label.appendChild(el("input", { type: "radio", name: "personality", value: p.id, id: "p-" + p.id }));
      label.appendChild(el("strong", {}, p.title));
      label.appendChild(el("span", { class: "tagline" }, ", " + p.tagline));
      wrap.appendChild(label);
      wrap.appendChild(el("p", {}, p.description));
      const ul = el("ul", { class: "samples" });
      p.samples.forEach(function (s) { ul.appendChild(el("li", {}, "“" + s + "”")); });
      wrap.appendChild(ul);
      const row = el("div", { class: "actions inline" });
      st.voices.forEach(function (v) {
        if (!p.audio[v]) return;
        playButton(p, v).forEach(function (n) { row.appendChild(n); });
      });
      wrap.appendChild(row);
      box.appendChild(wrap);
    });
  }

  $("form-personality").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    stopAudio();
    clearErrors($("s-personality"));
    const type = chosen("personality");
    const voice = chosen("voice");
    if (!type) { fieldError("personality", "Choose one of the four types.", "personality"); return; }
    if (!voice) { fieldError("voice", "Choose a female or a male voice.", "voice"); return; }
    const r = await send("api/personality", { type: type, voice: voice }, "personality");
    if (r && r.ok) await refresh();
  });

  // ---- step 4: the recovery phrase ----
  function phraseView(which) {
    $("phrase-start").hidden = which !== "start";
    $("phrase-show").hidden = which !== "show";
    $("phrase-confirm").hidden = which !== "confirm";
  }

  function hideGroups() { clear($("phrase-groups")); }

  function forgetPhrase() {
    held = null;
    hideGroups();
    $("f-g1").value = "";
    $("f-g2").value = "";
    if (!done("phrase")) phraseView("start");
  }

  function showGroups() {
    const ol = $("phrase-groups");
    clear(ol);
    held.groups.forEach(function (g, i) {
      const li = el("li");
      li.appendChild(el("span", { class: "n", "aria-hidden": "true" }, String(i + 1)));
      li.appendChild(el("span", { class: "g" }, g));
      ol.appendChild(li);
    });
    phraseView("show");
    $("btn-phrase-written").focus();
  }

  async function newPhrase() {
    clearErrors($("s-phrase"));
    forgetPhrase();
    const r = await send("api/phrase/new", {}, "phrase");
    if (!r || !r.ok) { store(OPEN_KEY, ""); return; }
    held = { groups: String(r.body.phrase).split("-"), check: r.body.check.slice() };
    r.body.phrase = "";
    store(OPEN_KEY, "1");
    showGroups();
  }

  $("btn-phrase-show").addEventListener("click", newPhrase);

  $("btn-phrase-written").addEventListener("click", function () {
    hideGroups();
    $("lbl-g1").textContent = "Group " + (held.check[0] + 1);
    $("lbl-g2").textContent = "Group " + (held.check[1] + 1);
    phraseView("confirm");
    $("f-g1").focus();
  });

  $("btn-phrase-again").addEventListener("click", function () {
    clearErrors($("s-phrase"));
    if (held) showGroups();
  });

  $("phrase-confirm").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    clearErrors($("s-phrase"));
    if (!held) { phraseView("start"); return; }
    const groups = {};
    groups[String(held.check[0])] = $("f-g1").value;
    groups[String(held.check[1])] = $("f-g2").value;
    const r = await send("api/phrase/confirm", { groups: groups }, "groups");
    if (r && r.ok) { forgetPhrase(); store(OPEN_KEY, ""); await refresh(); return; }
    if (r && r.body && r.body.new_phrase_needed) {
      forgetPhrase();
      store(OPEN_KEY, "");
      const m = $("err-phrase");
      m.textContent = r.body.reason === "too_many_tries"
        ? "That phrase was thrown away after too many tries. Show a new one and write it down again."
        : "That phrase expired (it is kept for 30 minutes, and only while setup runs). Show a new one and write it down again.";
      m.hidden = false;
      $("btn-phrase-show").textContent = "Show a new recovery phrase";
      $("btn-phrase-show").focus();
    }
  });

  window.addEventListener("pagehide", forgetPhrase);

  // ---- step 5: backups ----
  function backupRows() {
    const k = chosen("kind");
    $("row-dir").hidden = k !== "dir";
    $("row-rclone").hidden = k !== "rclone";
    $("backup-later-note").hidden = k !== "later" || !st.backup_warning;
  }

  ["kind-dir", "kind-rclone", "kind-later"].forEach(function (id) { byId(id).addEventListener("change", backupRows); });

  $("form-backup").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    clearErrors($("s-backup"));
    const kind = chosen("kind");
    if (!kind) { fieldError("kind", "Choose a folder, an rclone remote, or later.", "kind"); return; }
    const body = { kind: kind };
    if (kind === "dir") body.target = $("f-dir").value;
    if (kind === "rclone") body.target = $("f-rclone").value;
    const r = await send("api/backup", body, "kind");
    if (!r || !r.ok) return;
    const warning = (kind === "later" && st.backup_warning) || (r.body && r.body.warning) || "";
    if (warning) {
      $("done-warning").textContent = warning;
      $("done-warning").hidden = false;
    }
    doneNext();
    st.completed = STEPS.slice();
    token = "";
    store(TOKEN_KEY, "");
    show("done"); // setup has shut down: no more requests
  });

  // What to do next, by the channels chosen: with none, the command that talks to it on this computer.
  function doneNext() {
    const chans = (st.choices && st.choices.channels && st.choices.channels.channels) || [];
    const lines = [];
    if (chans.indexOf("telegram") >= 0) lines.push("Message your bot on Telegram to talk to it. It answers only the user id you gave.");
    if (chans.indexOf("nextcloud-talk") >= 0) lines.push("Write to it in Nextcloud Talk.");
    if (!chans.length) lines.push("You chose no channels, so you talk to it on this computer. Once it has started, run this in a terminal:");
    $("done-next").textContent = lines.join(" ");
    $("done-command-text").textContent = st.chat_command || "";
    $("done-command").hidden = !!chans.length || !st.chat_command;
  }

  // Copy that depends on where setup runs: a container (its 127.0.0.1 is its own) and the computer's system.
  function placeHints() {
    const box = !!st.in_container;
    // the examples sit in the page as data-placeholder-* attributes; the plain placeholder is the default
    [$("f-base_url"), $("f-dir")].forEach(function (f) {
      if (f.dataset.placeholderDefault === undefined) f.dataset.placeholderDefault = f.placeholder;
      const alt = box ? f.dataset.placeholderContainer : st.platform === "mac" ? f.dataset.placeholderMac : undefined;
      f.placeholder = alt || f.dataset.placeholderDefault;
    });
    $("hint-base_url").hidden = box;
    $("hint-base_url-container").hidden = !box;
    $("hint-dir-container").hidden = !box;
  }

  // ---- wiring ----
  // A finished step shows the choice made there (ids only; secrets are never sent back).
  function checkIf(id) { const e = byId(id); if (e) e.checked = true; }

  function showChoices() {
    const c = st.choices || {};
    if (c.tier) checkIf("tier-" + c.tier.tier);
    if (c.channels) c.channels.channels.forEach(function (id) { checkIf("ch-" + id); });
    if (c.personality) { checkIf("p-" + c.personality.type); checkIf("voice-" + c.personality.voice); }
    tierFields();
  }

  function render() {
    renderTiers();
    renderChannels();
    renderPersonalities();
    showChoices();
    $("backup-later-note").textContent = st.backup_warning || "";
    backupRows();
    placeHints();
    if (done("backup")) doneNext();
  }

  document.querySelectorAll(".back").forEach(function (b) { b.addEventListener("click", function () { go(-1); }); });
  document.querySelectorAll(".next").forEach(function (b) { b.addEventListener("click", function () { go(1); }); });

  if (!token) { show("lost"); return; }
  refresh(true);
})();
