# Plan 3: the first-run setup page

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A person turns on a new sidekick from a small web page the sidekick serves itself: they pick where the
thinking happens (with the honest privacy label), connect a channel, pick one of 4 personalities and a voice
after hearing them, see and confirm their recovery phrase on their own screen, and choose a backup destination.

**Architecture:** A new `sksidekick setup` command runs a stdlib `http.server` on 127.0.0.1 that serves one
page and a small JSON API, guarded by a one-time token printed to the terminal (or container log). Each step
writes into the sidekick home through the Plan 1 functions (`keys`, `backup`) and three new writers (Hermes
model and channel config, the soul file, the voice). `sidekick-start.sh` starts `setup` instead of waiting for
`docker exec ... init`, and the installer opens the page instead of printing the phrase in the terminal.

**Tech Stack:** Python 3.11+ stdlib only (`http.server`, `secrets`, `json`, `string.Template`), one static
HTML file with plain JS (no build step, no CDN), the 8 Piper MP3 samples bundled as package data; pytest with
`http.client` against a server on port 0.

**Spec:** sksidekick design, approved by Chef 2026-11-20 (private design doc,
`skhub-nor:GTD/projects/sidekick/sksidekick/sksidekick-design-2026-11-15.md`), sections "Privacy tiers",
"Channels", "First run: personality and voice", "The 4 personality types" and "Calls made by Lumina"
(first run is a small web page served by the sidekick; the recovery phrase is generated on the user's screen,
never in a chat channel). Plans 1 and 2 (`docs/plans/`) are the code this builds on.

## Global Constraints

- The recovery phrase appears only in the page, once, over 127.0.0.1 (or the user's own port mapping). It is
  never written to disk, a log, a channel or an HTTP response other than the one that shows it.
- "Write it down" is enforced: the user retypes 2 of the 8 groups (chosen at random) before setup continues.
  A phrase never confirmed is destroyed with its keys (`keys.shred_keys`), as `sksidekick init` does today.
- Privacy copy is the design's wording, verbatim where the design gives it (tier and channel labels below).
- Nothing phones home. The page loads nothing from the internet (fonts, scripts and audio are bundled).
- Bound to 127.0.0.1 by default; `--host` exists for a container, where the image publishes the port only on
  the host's loopback (`-p 127.0.0.1:8765:8765`). Every request needs the setup token.
- The page works under a path prefix (relative URLs only), so Plan 5 can show it inside Nextcloud.
- Setup is first run only. Once finished it refuses to run on that home (exit 1, "already set up").
- No em or en dashes in any file. Python `>=3.11`; Linux and macOS.
- Out of scope here: connecting accounts (gog, skservices) is the roadmap's "later" row, and the shared
  gateway credential comes from a seat in Plan 4. The page leaves room for an accounts step after channels.

## Fixed content (from the spec)

Tiers (`setup/content.py`, `TIERS`), each `{id, title, label, needs}`:
- `private`: "Private", label "A model running on hardware you control. Nothing readable leaves your hardware.
  We hold only encrypted memory we cannot open." Needs: an OpenAI-compatible base URL (for example Ollama's
  `http://127.0.0.1:11434/v1`) and a model name.
- `own`: "Own account", label "You bring your own cloud model account. We still cannot read anything; the
  provider sees your messages under your own terms with that provider." Needs: provider base URL, model, API key.
- `shared`: "Shared gateway", label "Our gateway, with message logging off for sidekicks. This is a promise,
  not a lock, and the model provider sees your messages. The provider sees our account, never your name, email
  or IP; we can see how much your sidekick uses and when, never what it says." Needs: the gateway URL and client
  key from the seat (env `SKSIDEKICK_GATEWAY_URL`, `SKSIDEKICK_GATEWAY_KEY`). Without them the option is shown
  disabled with "Available when we invite you (a seat)". Plan 4 provides them.

Channels (`CHANNELS`), each `{id, title, label, fields}`:
- `nextcloud-talk`: label "Stays inside our own server." Fields per the nctalk plugin's env names (read them from
  the plugin's README in the image; Task 3 pins them in a test).
- `telegram`: label "Telegram also sees the conversation." Field `TELEGRAM_BOT_TOKEN`.
- "Add more channels later" is always allowed (zero channels is valid; the sidekick then answers only locally).

Personalities: the 4 types, descriptions and 3 sample lines each, exactly as in the design's
"The 4 personality types" (Companion, Assistant, Creative, Mentor). Voices: `female` = Piper `en_US-amy-medium`,
`male` = Piper `en_US-ryan-medium` (the voices the samples were made with; Task 4 confirms the ryan model name
against rhasspy/piper-voices v1.0.0). Audio: `setup/static/audio/<type>-<female|male>.mp3`, the 8 files in
`skhub-nor:tmp/sksidekick-personality-samples/`.

## Review Focus

1. The browser is closed or refreshed on the phrase step: no keys may survive whose phrase nobody confirmed; a
   fresh phrase is shown on return.
2. Someone else on the network, or another local process, reaches the port: without the token every request
   is 403 and nothing in the home changes.
3. Two tabs (or a double click) finish the same step: exactly one key set, one soul, one config; the second
   request gets 409.
4. A bad value (model URL that does not answer, a channel token with a newline, a backup folder that is not
   writable): a field error in plain words, and nothing half-written.
5. `setup` on a home that already holds a sidekick (imported or finished): it refuses, never touching keys.

---

### Task 1: the setup server, token and state machine

**Files:**
- Create: `src/sksidekick/setup/__init__.py`, `src/sksidekick/setup/server.py`, `src/sksidekick/setup/state.py`
- Modify: `src/sksidekick/cli.py` (new `setup` subcommand: `--host` default `127.0.0.1`, `--port` default `8765`)
- Test: `tests/test_setup_server.py`

**Interfaces:**
- Produces: `state.Steps = ("tier", "channels", "personality", "phrase", "backup", "done")`;
  `state.load(home) -> dict` and `state.advance(home, step: str) -> None` (file `state/setup.json`,
  written atomically; `advance` raises `state.OutOfOrder` unless `step` is the next one);
  `server.make_server(home, host, port, token) -> ThreadingHTTPServer`; `server.main_setup(home, host, port) -> int`
  (prints `Open http://<host>:<port>/?t=<token>` once, serves until `done`, then exits 0).
- Routes: `GET /` (the page), `GET /static/<file>`, `GET /api/state`, `POST /api/<step>` (JSON body).
  The token arrives as `?t=` on the first GET and is then an `HttpOnly; SameSite=Strict` cookie; POSTs also
  require the `X-Setup-Token` header (no cross-site form can send it).

- [ ] **Step 1: Write the failing tests**
  - `test_without_token_everything_is_403`: GET `/`, `/api/state`, POST `/api/tier` with no token or a wrong
    one → 403, `state/setup.json` absent.
  - `test_token_cookie_then_header`: GET `/?t=<token>` sets the cookie; POST with cookie but no header → 403;
    with both → handled.
  - `test_steps_in_order`: POST `/api/channels` before `tier` → 409 `{"error": "finish the earlier step first"}`.
  - `test_concurrent_same_step_one_wins`: two threads POST `/api/tier` at once → one 200, one 409.
  - `test_refuses_a_set_up_home`: a home with `state/instance.json` and no `state/setup.json` (imported) →
    `main_setup` returns 1 with "already set up", binds nothing.
  - `test_binds_loopback_by_default`: `make_server(home, "127.0.0.1", 0, t).server_address[0] == "127.0.0.1"`.
- [ ] **Step 2: Run, expect FAIL** (`pytest tests/test_setup_server.py -v`: module not found)
- [ ] **Step 3: Implement** `state.py` and `server.py`. Token: `secrets.token_urlsafe(32)`, compared with
  `hmac.compare_digest`. One `threading.Lock` around every POST (the step machine is the 409 source).
  Responses carry `Cache-Control: no-store` and `Content-Security-Policy: default-src 'self'`.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(setup): token-guarded setup server and step machine`

### Task 2: model tier

**Files:**
- Create: `src/sksidekick/setup/content.py` (TIERS, CHANNELS, PERSONALITIES, VOICES as above), `src/sksidekick/setup/hermes_config.py`
- Test: `tests/test_setup_tier.py`

**Interfaces:**
- Produces: `hermes_config.set_model(home, *, base_url: str, model: str, api_key: str | None) -> None` writes
  `hermes/config.yaml` keys `model.provider = custom`, `model.base_url`, `model.default`, and `CUSTOM_API_KEY`
  in `hermes/.env` (0600); `hermes_config.set_env(home, key: str, value: str) -> None` (rejects a value
  containing `\n`, `\r` or `\0` with `ValueError`); `POST /api/tier {tier, base_url?, model?, api_key?}`.
- Writes go through `hermes config set` with `HERMES_HOME=<home>/hermes` (the CLI Hermes ships; no YAML of our
  own), `.env` through `set_env`, which rewrites the file atomically and keeps other lines.

- [ ] **Step 1: Write the failing tests**
  - `test_private_tier_writes_model`: `set_model(home, base_url="http://127.0.0.1:11434/v1", model="llama3.2",
    api_key=None)` → `hermes config get model.base_url` prints it; `.env` has no `CUSTOM_API_KEY`.
  - `test_own_tier_key_goes_to_env_0600`: api_key `sk-x` → `.env` line `CUSTOM_API_KEY=sk-x`, mode 0600.
  - `test_shared_disabled_without_seat`: `GET /api/state` → `tiers.shared.enabled is False` when the two env
    vars are unset; `True` and pre-filled (key never echoed back) when set.
  - `test_env_value_with_newline_refused`: `set_env(home, "X", "a\nB=c")` raises; `.env` unchanged.
  - `test_unreachable_model_is_a_field_error`: `POST /api/tier` private with `http://127.0.0.1:9/v1` → 422
    `{"field": "base_url", "error": "nothing answered at that address"}` (one `GET <base_url>/models`, 5 s
    timeout); nothing written.
- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement.** Tests run `hermes` from a fake on `PATH` (a tiny script that records argv into a
  file) so the suite needs no Hermes; Task 6's image test proves the real one.
- [ ] **Step 4: Run, expect PASS**
- [ ] **Step 5: Commit** `feat(setup): model tier with honest labels`

### Task 3: channels

**Files:**
- Modify: `src/sksidekick/setup/content.py`, `src/sksidekick/setup/server.py`
- Test: `tests/test_setup_channels.py`

**Interfaces:**
- Consumes: `hermes_config.set_env`.
- Produces: `POST /api/channels {channels: [{id, fields: {NAME: value}}]}`; an empty list is valid.

- [ ] **Step 1: Write the failing tests**
  - `test_telegram_token_written`: `{id: "telegram", fields: {TELEGRAM_BOT_TOKEN: "123:abc"}}` → `.env` line.
  - `test_unknown_field_refused`: a field not in that channel's list → 422, nothing written.
  - `test_zero_channels_ok`: `[]` → 200, `.env` unchanged.
  - `test_channel_labels_present`: `GET /api/state` lists both channels with the labels above, verbatim.
  - `test_nextcloud_talk_fields_match_plugin`: the field names equal the env names in the image's nctalk
    plugin README (the test reads `NCTALK_ENV_NAMES` from content.py; the image test in Task 6 greps the
    plugin for each).
- [ ] **Step 2 to 4:** run (FAIL), implement, run (PASS)
- [ ] **Step 5: Commit** `feat(setup): channels with their privacy line`

### Task 4: personality and voice

**Files:**
- Create: `src/sksidekick/setup/soul.py`, `src/sksidekick/setup/static/audio/*.mp3` (the 8 samples)
- Modify: `pyproject.toml` (package data `setup/static/**`), `src/sksidekick/setup/server.py`
- Test: `tests/test_setup_soul.py`

**Interfaces:**
- Produces: `soul.write_soul(home, personality: str, voice: str) -> None`: writes `soul/personality.json`
  (`{"type", "voice", "version": 1}`, story, travels with exports) and renders `hermes/SOUL.md` from it (a
  short style block for the type plus its 3 sample lines as examples, then a `## Voice` note); sets
  `tts.provider = piper` and `tts.piper.voice` via `hermes config set`. `POST /api/personality {type, voice}`.
  `soul.switch(home, personality, voice)` is the same writer (the design: switching later keeps memory).

- [ ] **Step 1: Write the failing tests**
  - `test_four_types_exactly`: `[p["id"] for p in PERSONALITIES] == ["companion", "assistant", "creative", "mentor"]`,
    each with 3 sample lines equal to the design's text.
  - `test_soul_written_and_is_story`: after `write_soul(home, "mentor", "male")`, `soul/personality.json` is in
    `home.story_paths()`; `hermes/SOUL.md` contains "calm and patient" and all 3 Mentor lines.
  - `test_voice_sets_piper`: the fake `hermes` saw `config set tts.provider piper` and
    `config set tts.piper.voice en_US-ryan-medium`.
  - `test_switch_keeps_memory`: a file in `memory/` is unchanged after `switch(home, "creative", "female")`.
  - `test_audio_served`: `GET /static/audio/companion-female.mp3` → 200 `audio/mpeg`, 8 files present,
    each under 200 KB.
- [ ] **Step 2 to 4:** run (FAIL), implement, run (PASS). Confirm the ryan model name against
  rhasspy/piper-voices v1.0.0 and fix `VOICES` if it differs.
- [ ] **Step 5: Commit** `feat(setup): the four personalities and the voice`

### Task 5: the recovery phrase on screen, and backups

**Files:**
- Modify: `src/sksidekick/setup/server.py`, `src/sksidekick/cli.py` (`init` keeps working for headless use)
- Test: `tests/test_setup_phrase.py`

**Interfaces:**
- Consumes: `keys.create_keys(home) -> str`, `keys.shred_keys(home)`, `backup.load_config`/`backup.save_config`
  (append `{"kind": "dir", "path"}` or `{"kind": "rclone", "remote"}` to `destinations`, as `sksidekick backup
  add-dir|add-rclone` does in `cli.py`), `home.new_instance()`.
- Produces: `POST /api/phrase/new` → `{"phrase": "...", "check": [i, j]}` (creates keys; returns the phrase
  once; a second call while unconfirmed shreds and makes a new one); `POST /api/phrase/confirm {groups: {i: "...",
  j: "..."}}` → 200 and `advance("phrase")`, or 422 "those groups do not match" (3 misses → new phrase);
  `POST /api/backup {kind: "dir"|"rclone"|"later", target?}` → then `new_instance()`, `advance("done")`.
- An unconfirmed key set is shredded when: a new phrase is asked for, the server stops, or 30 minutes pass.

- [ ] **Step 1: Write the failing tests**
  - `test_phrase_shown_once`: the phrase is in exactly one response body; not in `GET /api/state`, not in
    any file under the home (grep every file for it), not in the captured server log.
  - `test_refresh_makes_a_new_phrase`: two `phrase/new` calls → different phrases; only the second opens
    `keys/recovery.identity.age`.
  - `test_unconfirmed_keys_shredded_on_stop`: `phrase/new`, then stop the server → `keys/` is empty.
  - `test_confirm_accepts_lookalikes`: the right groups typed lower case with `o` for `0` → 200.
  - `test_backup_dir_must_be_writable`: a read-only folder → 422 `{"field": "target"}`; `kind: "later"` → 200
    and `GET /api/state` shows the "No backups yet" warning the serve loop already sends.
  - `test_done_makes_it_live`: after backup, `home.instance()["status"] == "live"` and `main_setup` returns 0.
- [ ] **Step 2 to 4:** run (FAIL), implement, run (PASS)
- [ ] **Step 5: Commit** `feat(setup): recovery phrase on the user's screen, confirmed; backup destination`

### Task 6: the page, and wiring it into the image and installer

**Files:**
- Create: `src/sksidekick/setup/static/index.html`, `src/sksidekick/setup/static/setup.js`, `src/sksidekick/setup/static/setup.css`
- Modify: `packaging/sidekick-start.sh` (no instance and no setup done → `exec sksidekick setup --host 0.0.0.0`
  inside the container, then continue to `serve` when it exits 0), `packaging/install.sh` (after install:
  start `setup` and open the URL with `open` on macOS or `xdg-open` on Linux, else print it; `--no-init` keeps
  today's behaviour), `docs/MOVING.md` unchanged, `README.md` (first run section)
- Test: `tests/image/test_setup_page.sh`, `tests/installer/test_installer.sh` (setup path), `tests/test_setup_page.py`

- [ ] **Step 1: Write the failing tests**
  - `test_page_has_no_external_urls`: no `http://` or `https://` in any static file except the user's own
    input placeholders; no `<script src=` outside `static/`.
  - `test_page_uses_relative_urls`: no `href="/` or `src="/`, no `fetch("/` (Plan 5 path prefix).
  - `tests/image/test_setup_page.sh IMAGE`: run the image with `-p 127.0.0.1:18765:8765`; read the URL from the
    log; walk every step with curl (private tier against `tests/image/stub_model.py`, zero channels, mentor,
    female, phrase confirm, backup to a dir in the volume); then `serve` is running, the phrase is in no file
    under `/opt/data` and in no `docker logs` line, and a turn is stored as a memory (reuse `test_memory_turn.sh`'s
    check).
  - Installer test: `install.sh` on CI with `--setup-headless` (prints the URL instead of opening a browser),
    then the same curl walk.
- [ ] **Step 2 to 4:** run (FAIL), implement, run (PASS). The page is one screen per step with Back; the
  phrase screen says "Write these 8 groups down on paper. This is the only way to restore your sidekick if this
  computer is lost. We cannot recover it for you."
- [ ] **Step 5: Commit** `feat(setup): the first-run page; image and installer open it`
- [ ] **Step 6:** Add `tests/image/test_setup_page.sh` to `image.yml` (both arches) and the installer path to
  `installer.yml` (ubuntu and macOS). Commit, push, CI green on both.
