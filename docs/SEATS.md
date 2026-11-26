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

Run a separate skgateway instance for sidekick seats (release after
[skgateway PR 222](https://github.com/smilinTux/skgateway/pull/222)). Do not turn client auth on for a gateway
your own agents already use: every caller of it would then need a credential.

In the instance's `skgateway.yaml`:

```yaml
client_auth:
  enabled: true
  credentials_file: /srv/sidekick-gateway/clients.json
  expected_owner_uid: 1001            # the user the gateway runs as
  agent_header: x-agent-id
  client_header: x-sk-client-id
  revision_header: x-sk-credential-revision

privacy:
  content_logs: false

evidence_budget_monitoring:
  enabled: true
```

- `clients.json` must be owned by that user with mode 0600 (or 0640 with `expected_group_gid` set). The
  gateway refuses any other mode or owner.
- `evidence_budget_monitoring` is what serves `/admin/clients/usage`. Without it there is no usage to read.
- With client auth on, the gateway meters by the verified client id and ignores the caller's `x-app` header,
  so a sidekick cannot charge another seat.
- Give the instance a public HTTPS name, for example `https://gateway.example.com`. Invites refuse a plain
  `http://` address unless the host is loopback or a private address.
- The gateway rereads the credentials file when it receives SIGHUP. No restart is needed to add or remove a
  seat.

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

**Container.** Mount the invite read only and point setup at it:

```sh
docker run -d --name sidekick -v sidekick-data:/opt/data -v /path/to/invite.json:/run/invite:ro -e SKSIDEKICK_INVITE=/run/invite IMAGE
```

Setup runs in the container and its page is on the setup port. A missing or bad invite file is one line in
the container log.

**Mac and Linux installer.** The `sksidekick-setup` wrapper does not take `--invite` today. Run the same
command it runs, with the invite added. `$APP` is the install folder and `$SIDEKICK_HOME` is the sidekick's
home (both are printed by the installer):

```sh
env SKSIDEKICK_HOME="$SIDEKICK_HOME" SIDEKICK_SEED="$APP/hermes-seed" SIDEKICK_BIN="$APP/sksidekick/bin" SIDEKICK_SKMEMORY_JSON="$APP/skmemory.json" HERMES_HOME="$SIDEKICK_HOME/hermes" "$APP/bin/sksidekick" --home "$SIDEKICK_HOME" setup --port 0 --invite /path/to/invite.json
```

Add `--keep-invite` to keep the file after it is installed. It is a secret, so the default is to delete it.

A revoked or expired invite is reported at setup as turned off or expired, with a request to ask for a new
one.

## Read usage

The usage endpoint answers on loopback only. On the gateway host, save the response, then read it with the
tool:

```sh
curl -s http://127.0.0.1:PORT/admin/clients/usage -o usage.json
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
new invite the same way as the first.

## Remove a seat

```sh
sksidekick-seat --seats-dir "$SEATS" --estate "$ESTATE" remove "$CLIENT_ID" --registry "$REGISTRY"
```

This revokes the credential and marks the seat removed in the record. The sidekick keeps working on any
other model option the person has. The operator cannot reach into it. Tell the person to keep their story
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

Give the person the archive and their recovery phrase before you remove the volume. Without the phrase the
archive cannot be opened.
