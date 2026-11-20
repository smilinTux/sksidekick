# sksidekick archive format, `sksidekick.archive.v1`

A sidekick archive (`.sksk`) holds a person's whole story: memory, soul and personality, settings, journal
and reminders. It is an open format built only from standard pieces (tar, gzip, age), so you never need
sksidekick to get your story back.

## Layout

A `.sksk` file is a plain (uncompressed) POSIX tar with exactly three members, in this order:

| Member | Contents | Encrypted |
|---|---|---|
| `manifest.json` | `format` (`sksidekick.archive.v1`), `created` (UTC, ISO 8601), `purpose` (`export`, `backup` or `move`), `recipient` (the age public key), `payload_sha256` (sha256 of `payload.age`), `mac` (see below) | no; it names no files |
| `recovery.identity.age` | the sidekick's age X25519 secret key, encrypted with the recovery phrase (age scrypt passphrase mode) | yes, by the phrase |
| `payload.age` | a tar.gz of the story, encrypted to `recipient` (age X25519 mode) | yes, by the key |

Inside the decrypted payload:

- `story/<path>`: every story file at its path in the sidekick home (for example `story/memory/long-term/<id>.json`,
  `story/soul/base.json`, `story/journal.md`).
- `inner-manifest.json`: `format`, `created`, `instance_id` (which copy made it), and `files`: a map of each
  path to its sha256.

Not in an archive: the live key file, the state directory, and the search index (`index.db*`), which is
rebuilt from the memory files.

## Authentication (`mac`)

`mac` is HMAC-SHA256, hex, over the canonical JSON (keys sorted, no spaces) of the manifest fields `format`, `created`,
`purpose`, `recipient` and `payload_sha256`. Its key is `sha256("sksidekick.archive.v1 mac" + NUL + <age secret key text>)`.
Only someone holding the sidekick's secret key (the running sidekick, or anyone with the recovery phrase) can make a
valid `mac`. Because the payload is bound by `payload_sha256`, a valid `mac` proves the whole archive was made by
this sidekick. A reader refuses an archive whose `mac` is missing or wrong. Opening an archive by hand with stock
`age` (below) does not check the `mac`.

## What it protects and what it does not

- Without the recovery phrase (or the running sidekick's key), an archive cannot be opened. A backup
  destination, including our own hub, only ever holds ciphertext.
- A backup destination cannot forge an archive either: it knows the public `recipient` and holds a copy of the
  wrapped key, but cannot make the `mac`.
- What a destination CAN do: delete or withhold backups, or hand back an older genuine one. Each archive's
  `created` time is shown on import and in every drill, and backups go to more than one place when you set them up,
  so keep at least one destination you control.
- An archive's `purpose` says whether it was a backup, an export or a move. Only a `move` archive comes with the
  old copy already stopped; when you import a backup or export, stop the original first, because only one copy may answer.
- The archive travels with the phrase-wrapped key, so the phrase alone restores it anywhere.
- Permanent delete destroys the sidekick's keys. Archives made earlier can still be opened by someone who
  has both the archive and the recovery phrase, which is why the delete screen tells you to destroy any
  printed phrase.
- `sksidekick export --plain DIR` writes a decrypted, readable copy for leaving the ecosystem. Anyone who
  gets that folder can read it.

## Opening an archive with stock tools

You need `tar` and [age](https://age-encryption.org) (v1.1 or later). Type the phrase exactly as it was
printed: upper case, with the hyphens (`sksidekick` itself also accepts lower case and spaces).

```sh
tar xf my-sidekick.sksk                           # manifest.json, recovery.identity.age, payload.age
age -d -o key.txt recovery.identity.age           # asks for the recovery phrase
age -d -i key.txt payload.age | tar xzf -         # writes story/ and inner-manifest.json
rm key.txt                                        # the key opens every archive of this sidekick
```

Checked with age v1.2.1 and GNU tar on 2026-11-20.

## Rules a reader must follow

Treat every archive as hostile. A conforming reader refuses the whole archive when:

- the outer tar does not hold exactly the three members above, in order, as regular files;
- the manifest is not a JSON object, `payload_sha256` does not match, the `mac` is missing or wrong, or the payload does not decrypt;
- any payload member is not a regular file, has an absolute path, contains `..`, is outside `story/`
  (other than `inner-manifest.json`), lands in `keys/` or `state/`, or is a search index (`index.db*`);
- two members differ only in letter case (they would collide on macOS);
- the story would exceed the size limit (4 GiB by default, `SKSIDEKICK_MAX_STORY_BYTES`);
- the files present differ from `inner-manifest.json`, or any checksum differs.

It writes nothing until every check has passed, builds the restored home beside the target and moves it into place
in one step, and only restores into a sidekick home that holds no files at all. Restored files are readable only by
their owner (0600 in a 0700 directory).
