# Seats: the operator's runbook

A seat is one person's right to use your shared model gateway from their sidekick. It is one credential, one
pseudonymous client id, and one line in a private record that says which person holds it. This page is for the
operator who runs the gateway and hands out seats.

A seat is optional. A sidekick works without one, on the person's own keys or a local model. A seat is only
what the person gets when they pick the shared option at setup.

## What the operator can and cannot see

- The gateway sees a client id such as `sk-0123456789ab`, the model, token counts, cost and time. Nothing else
  is kept per request (no prompts, no replies, no caller address, no session id).
- Usage is read per client id. The map from client id to person lives only in the seats record, on the
  operator's host (mode 0600, in a directory with mode 0700). It is never in the gateway, a log or this repo.
- The operator cannot read a sidekick's memory, and cannot export or delete it remotely.
- The model provider behind the gateway still sees the text of each request that is sent to it. Say so when
  you hand out a seat.

## One-time setup: a sidekick gateway

Run a separate skgateway instance for sidekick seats, on a skgateway release with client auth and
privacy.content_logs (v1.1.23 or later). Do not turn client auth on for a gateway your own agents already use: every
caller of it would then need a credential.

In the instance's `skgateway.yaml`:

```yaml
client_auth:
  enabled: true
  credentials_file: /srv/sidekick-gateway/clients.json
  expected_owner_uid: 1001            # the user the gateway runs as
  agent_header: x-agent-id
  client_header: x-sk-client-id
  revision_header: x-sk-credential-revision

operator_auth:                         # with client auth on, every /admin route needs an operator credential
  enabled: true
  credentials_file: /srv/sidekick-gateway/operators.json
  expected_owner_uid: 1001

privacy:
  content_logs: false

evidence_budget_monitoring:
  enabled: true
```

- `clients.json` must be owned by that user with mode 0600 (or 0640 with `expected_group_gid` set). The
  gateway refuses any other mode or owner.
- `evidence_budget_monitoring` is what serves `/admin/clients/usage`. Without it there is no usage to read.
- `operators.json` has the same format and rules as `clients.json`, with one credential for you (the
  operator). Without `operator_auth` the gateway refuses every `/admin` route once client auth is on.
- With client auth on, the gateway meters by the verified client id and ignores the caller's `x-app` header,
  so a sidekick cannot charge another seat.
- Give the instance a public HTTPS name, for example `https://gateway.example.com`. Invites refuse a plain
  `http://` address unless the host is loopback or a private address.
- The gateway rereads the credentials file when it receives SIGHUP. No restart is needed to add or remove a
  seat.
- The gateway must serve the model name `sk-default` (an alias or a route to the model you pay for). Setup
  sets the sidekick to that name when the person picks the shared option.
- Turn off the access log of any reverse proxy in front of the gateway (nginx, Caddy, Traefik), or keep it
  without the client address. Otherwise the proxy keeps every caller's IP address, which the gateway itself
  never stores and never passes on to the model provider.

### The operator credential (operators.json)

Run this once as the gateway user, in the directory where you keep the operator's private files. It makes a
token, writes it only into `operator.headers` (mode 0600, used below to read usage) and writes its SHA-256
into `operators.json`. The token is never printed, so it never lands in a terminal log:

```sh
umask 077 && python3 -c '
import hashlib, json, secrets
t = secrets.token_urlsafe(32)
open("operator.headers", "w").write("Authorization: Bearer " + t + "\nx-sk-operator-id: operator\nx-sk-operator-client-id: operator-1\nx-sk-operator-credential-revision: op-r1\n")
json.dump({"schema_version": 1, "revision": "ops-1", "credentials": [{"agent_id": "operator", "client_id": "operator-1", "token_sha256": hashlib.sha256(t.encode()).hexdigest(), "credential_revision": "op-r1", "revoked": False, "expires_at": "2027-12-31T00:00:00Z"}]}, open("/srv/sidekick-gateway/operators.json", "w"), indent=2)
'
```

The resulting `operators.json` has this shape (the hash is the SHA-256 of the token, as hex):

```json
{
  "schema_version": 1,
  "revision": "ops-1",
  "credentials": [
    {
      "agent_id": "operator",
      "client_id": "operator-1",
      "token_sha256": "<64 hex characters>",
      "credential_revision": "op-r1",
      "revoked": false,
      "expires_at": "2027-12-31T00:00:00Z"
    }
  ]
}
```

To replace the operator credential, run the command again with a new `credential_revision` and `revision`,
then send the gateway SIGHUP.

## Run it as the gateway user

The seats record and the credentials file must stay private. Use these shell variables in the commands below:

```sh
SEATS=/srv/sidekick-seats                    # the private seats record
ESTATE=example                               # the estate this record belongs to
REGISTRY=/srv/sidekick-gateway/clients.json  # the gateway's credentials file
INVITE=/srv/sidekick-seats/invite-new.json   # where the invite is written
NEW_INVITE=/srv/sidekick-seats/invite-2.json # a rotation writes a new file; it never overwrites one
USAGE_JSON=usage.json                        # the saved usage response
CLIENT_ID=sk-0123456789ab                    # the id that `add` and `list` print
```

`--seats-dir` and `--estate` come before the command. One seats directory belongs to one estate. The tool
refuses to act when `--estate`, or the host's `SKWORLD_ESTATE`, does not match the directory.

## Create the record

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" init
```

## Add a seat

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" add --owner "Example Person" --place container --registry "$REGISTRY" --gateway-url https://gateway.example.com/v1 --invite-out "$INVITE"
```

- `--place` is `container`, `mac`, `linux` or `nextcloud`.
- `--contact` stores a way to reach the person, in the private record only.
- `--days` sets how long the credential lasts. The default is 365.
- Without `--invite-out` the invite is written inside the seats directory.
- The tool prints the new client id. It never prints the token.

To make the gateway pick up the change at once, add `--reload-unit NAME`, the systemd user unit of the
gateway. The tool then sends it SIGHUP:

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" add --owner "Example Person" --place linux --registry "$REGISTRY" --gateway-url https://gateway.example.com/v1 --reload-unit sidekick-gateway.service
```

Without it, the tool prints the command to run yourself.

## List seats

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" list
```

## Deliver the invite

The invite file is a secret. It holds the sidekick's key.

- Deliver it through a private share or a vault that only the person and you can open.
- Never paste it into a chat or an email.
- Setup deletes it after it installs the seat (unless the person asks to keep it). After that, delete your
  own copy too.
- A lost invite is not recovered. Rotate the seat (below) and send the new one.

## The person's side

The person picks the shared option at the model step of setup. The invite is read only when setup starts, and
installed when they save that option. If they pick another option, the file is left alone and not used. A
sidekick that is already set up does not use an invite.

**Container.** Mount the invite read only and point setup at it, on the first run of a new container:

```sh
docker run -d --name sidekick -v sidekick-data:/opt/data -v /path/to/invite.json:/run/invite:ro -e SKSIDEKICK_INVITE=/run/invite IMAGE
```

Setup runs in the container and its page is on the setup port. A missing or bad invite file is one line in
the container log. Setup cannot delete a read-only mount, so once the shared option is saved, delete the
invite file on the host yourself and leave out the `-v .../invite.json` and `-e SKSIDEKICK_INVITE` options the
next time you run the container. A container that is already set up never uses `SKSIDEKICK_INVITE`; it says so
in one line of its log.

**Mac and Linux installer.** Give the installer the invite. It passes it to the setup page it starts:

```sh
bash packaging/install.sh --invite /path/to/invite.json
```

Add `--keep-invite` to keep the file after it is installed. It is a secret, so the default is to delete it.
On a computer that already has the app, run the setup wrapper by its full path (`~/.local/bin` is not on a
default macOS PATH). The installer prints this command when it finishes:

```sh
"$HOME/Library/Application Support/sksidekick-app/bin/sksidekick-setup" --invite /path/to/invite.json   # macOS
"${XDG_DATA_HOME:-$HOME/.local/share}/sksidekick-app/bin/sksidekick-setup" --invite /path/to/invite.json   # Linux
```

A revoked or expired invite is reported at setup as turned off or expired, with a request to ask for a new
one.

## Read usage

The usage endpoint answers on loopback only, and only to your operator credential. Keep its four headers
(`Authorization: Bearer <operator token>`, `x-sk-operator-id`, `x-sk-operator-client-id`,
`x-sk-operator-credential-revision`) in a file with mode 0600, so the token is never on a command line. On the
gateway host, save the response, then read it with the tool:

```sh
curl -s -H @operator.headers http://127.0.0.1:PORT/admin/clients/usage -o usage.json
```

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" usage --json "$USAGE_JSON"
```

It prints calls, tokens and dollars for each client id, with the owner's name from the record. A client id
that is not in the record shows as "(not a seat)". The tool warns on stderr that attribution is only
trustworthy on a gateway that meters by the verified client id.

## Rotate a seat

Use this for a lost or leaked invite, or before a credential expires. It issues a new credential, writes a
new invite and revokes the old credential.

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" rotate "$CLIENT_ID" --registry "$REGISTRY" --gateway-url https://gateway.example.com/v1 --invite-out "$NEW_INVITE"
```

The person's sidekick stops working with the shared option until they put the new credential in. Send the
new invite the same way as the first. The person then runs the same command as before with the new file:
the setup wrapper by its full path with `--invite NEW_FILE` on the installer (see above), or
`docker exec sidekick sksidekick setup --invite /opt/data/new-invite.json` in a container (copy the file into
the volume first, for example with `docker cp`). A sidekick that is already set up takes the new invite only
for the same seat (the same client id); it keeps its model and deletes the file. Then the person restarts the
sidekick so it reads the new credential. Setup prints the command for where it runs:

- macOS: `launchctl kickstart -k gui/$(id -u)/io.skworld.sksidekick`
- Linux: `systemctl --user restart sksidekick`
- Container: `docker restart sidekick` (on the host)

An invite for another seat, or an expired one, is refused and the file is kept.

## Remove a seat

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" remove "$CLIENT_ID" --registry "$REGISTRY"
```

This revokes the credential and marks the seat removed in the record. From then on every turn is refused at
the gateway, and the sidekick answers each message with one plain sentence instead: the shared seat was
removed or is no longer valid, so ask whoever invited you for a new invite, or pick another model option on
the setup page. (While a seat is installed, `sksidekick serve` runs a small notice on the sidekick's own
loopback and sets it as Hermes' fallback provider; it asks the gateway whether it still takes the seat, and
says the gateway is not answering when the refusal was for another reason. It never calls a model.) The
sidekick answers normally again only when the person gets a new seat from an operator, or starts setup over
and picks another model option. The operator cannot reach into it. Tell the person to keep their story
first, then delete it if they want it gone:

```sh
sksidekick export FILE.sksk
sksidekick delete --yes
```

For a container the operator hosts, run both inside it, then stop and remove the container and its volume:

```sh
docker exec sidekick sksidekick export /opt/data/story.sksk
docker cp sidekick:/opt/data/story.sksk .
docker exec sidekick sksidekick delete --yes
docker rm -f sidekick
docker volume rm sidekick-data
```

Give the person the archive, and make sure the person has their recovery phrase, before you remove the
volume. You never had the phrase, and without it the archive cannot be opened.
