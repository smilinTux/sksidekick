# sksidekick archive format, `sksidekick.archive.v1`

A sidekick archive (`.sksk`) holds a person's whole story: memory, soul and personality, settings, journal
and reminders. It is an open format built only from standard pieces (tar, gzip, age), so you never need
sksidekick to get your story back.

## Layout

A `.sksk` file is a plain (uncompressed) POSIX tar with exactly three members, in this order:

| Member | Contents | Encrypted |
|---|---|---|
| `manifest.json` | `format` (`sksidekick.archive.v1`), `created` (UTC, ISO 8601), `purpose` (`export`, `backup` or `move`), `recipient` (the age public key), `payload_sha256` (sha256 of `payload.age`), `wrapped_sha256` (sha256 of `recovery.identity.age`), `mac` (see below) | no; it names no files |
| `recovery.identity.age` | the sidekick's age X25519 secret key, encrypted (plain age X25519) to the key the recovery phrase derives, see "The recovery key" below | yes, by the phrase |
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
`purpose`, `recipient`, `payload_sha256` and `wrapped_sha256`. Its key is
`sha256("sksidekick.archive.v1 mac" + NUL + <age secret key text>)`. Only someone holding the sidekick's secret key
(the running sidekick, or anyone with the recovery phrase) can make a valid `mac`. Because both encrypted members are
bound by their sha256, a valid `mac` proves the archive's contents were sealed by a holder of this sidekick's key and
not changed since; it does not say which copy made it or when beyond `created`. A reader refuses an archive whose
`mac` is missing, is not 64 lower-case hex characters, or is wrong, so archives made before the `mac` existed are
refused too. Opening an archive by hand with stock `age` (below) does not check the `mac`.

The sidekick also keeps `keys/recovery.mac`, an HMAC of its own wrapped key under the live key. Every export and
every drill checks it, so a damaged recovery key is found while the sidekick still runs, not on the day the phrase
is the only way back.

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

## The recovery key

The phrase stands for an age identity: scrypt over the phrase in its printed form (upper case, hyphens), salt
`sksidekick recovery key v2`, N = 2^15, r = 8, p = 1, 32 bytes, written as an age secret key (bech32,
`AGE-SECRET-KEY-1...`). `recovery.identity.age` is the sidekick's own key encrypted to that identity.

The cost is fixed on purpose. age's passphrase mode picks its cost by timing the computer that encrypts and
refuses, on the computer that decrypts, any file costing more than 16 times its own one-second estimate; at the
cost a fast desktop picks it also needs 1 GiB of memory. So a correct phrase could fail on a small box or a busy
one. The phrase is 200 random bits, so it needs neither a salt nor a high cost to resist guessing.

Archives made before 2026-11-25 hold the key in age passphrase mode instead (`-> scrypt` in its header).
`sksidekick` still opens them; if age refuses one as too costly for this computer it says so, rather than
calling the phrase wrong.

## Opening an archive with stock tools

You need `tar`, [age](https://age-encryption.org) (v1.1 or later) and Python 3.8 or later (standard library
only). Type the phrase exactly as it was printed: upper case, with the hyphens (`sksidekick` itself also accepts
lower case and spaces).

```sh
tar xf my-sidekick.sksk                           # manifest.json, recovery.identity.age, payload.age
python3 phrase-key.py > phrase.key                # asks for the recovery phrase (script below)
age -d -i phrase.key -o key.txt recovery.identity.age
age -d -i key.txt payload.age | tar xzf -         # writes story/ and inner-manifest.json
rm phrase.key key.txt                             # either one opens every archive of this sidekick
```

`phrase-key.py`:

```python
import getpass, hashlib
p = getpass.getpass("recovery phrase: ").strip().upper()
seed = hashlib.scrypt(p.encode(), salt=b"sksidekick recovery key v2", n=2**15, r=8, p=1, maxmem=2**26, dklen=32)
cs, hrp = "qpzry9x8gf2tvdw0s3jn54khce6mua7l", "age-secret-key-"
acc = bits = 0; data = []
for b in seed:
    acc, bits = acc << 8 | b, bits + 8
    while bits >= 5: bits -= 5; data.append(acc >> bits & 31)
data.append(acc << (5 - bits) & 31)
def polymod(vs):
    c = 1
    for v in vs:
        t = c >> 25; c = (c & 0x1FFFFFF) << 5 ^ v
        for i, g in enumerate((0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)): c ^= g if t >> i & 1 else 0
    return c
pm = polymod([ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data + [0] * 6) ^ 1
print((hrp + "1" + "".join(cs[d] for d in data + [pm >> 5 * (5 - i) & 31 for i in range(6)])).upper())
```

For an archive made before 2026-11-25, skip the script: `age -d -o key.txt recovery.identity.age` asks for
the phrase itself.

## Rules a reader must follow

Treat every archive as hostile. A conforming reader refuses the whole archive when:

- the outer tar does not hold exactly the three members above, in order, as regular files;
- the manifest is not a JSON object, `payload_sha256` or `wrapped_sha256` does not match, the `mac` is missing,
  malformed or wrong, or the payload does not decrypt;
- any payload member is not a regular file, has an absolute path, contains `..`, is outside `story/`
  (other than `inner-manifest.json`), lands in `keys/` or `state/`, or is a search index (`index.db*`);
- two members differ only in letter case or Unicode normalization (they would collide on macOS);
- the story would exceed the size limit (4 GiB by default, `SKSIDEKICK_MAX_STORY_BYTES`);
- the files present differ from `inner-manifest.json`, or any checksum differs.

It writes nothing until every check has passed, and only restores into a sidekick home that holds no files at all.
It builds the restored home in a staging directory inside the target (so a mount point or a symlinked home works),
then renames each part into place, keys and state last; a failure removes what was moved. A staging directory left by
an interrupted import is ignored and cleared on the next try. Restored files are readable only by their owner (0600
in a 0700 directory) and are synced to disk.
