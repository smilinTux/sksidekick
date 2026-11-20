# sksidekick archive format, `sksidekick.archive.v1`

A sidekick archive (`.sksk`) holds a person's whole story: memory, soul and personality, settings, journal
and reminders. It is an open format built only from standard pieces (tar, gzip, age), so you never need
sksidekick to get your story back.

## Layout

A `.sksk` file is a plain (uncompressed) POSIX tar with exactly three members, in this order:

| Member | Contents | Encrypted |
|---|---|---|
| `manifest.json` | `format` (`sksidekick.archive.v1`), `created` (UTC, ISO 8601), `recipient` (the age public key), `payload_sha256` (sha256 of `payload.age`) | no; it names no files |
| `recovery.identity.age` | the sidekick's age X25519 secret key, encrypted with the recovery phrase (age scrypt passphrase mode) | yes, by the phrase |
| `payload.age` | a tar.gz of the story, encrypted to `recipient` (age X25519 mode) | yes, by the key |

Inside the decrypted payload:

- `story/<path>`: every story file at its path in the sidekick home (for example `story/memory/long-term/<id>.json`,
  `story/soul/base.json`, `story/journal.md`).
- `inner-manifest.json`: `format`, `created`, `instance_id` (which copy made it), and `files`: a map of each
  path to its sha256.

Not in an archive: the live key file, the state directory, and the search index (`index.db*`), which is
rebuilt from the memory files.

## What it protects and what it does not

- Without the recovery phrase (or the running sidekick's key), an archive cannot be opened. A backup
  destination, including our own hub, only ever holds ciphertext.
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
- `payload_sha256` does not match, or the payload does not decrypt;
- any payload member is not a regular file, has an absolute path, contains `..`, or is outside `story/`
  (other than `inner-manifest.json`);
- the files present differ from `inner-manifest.json`, or any checksum differs.

It writes nothing until every check has passed, and it only restores into an empty sidekick home.
