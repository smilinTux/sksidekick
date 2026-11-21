# Moving your sidekick

Your sidekick can live in a container, on your own computer, or on a small box at home, and it can move between
them. Only one copy ever answers: the old place goes quiet the moment you start a move, and it is destroyed only
after the new place works.

You need your **recovery phrase** (the 8 groups of 5 letters and digits you wrote down at the start).

## 1. Pack it at the old place

Container:

```sh
docker exec <container> sksidekick move-out /opt/data/move.sksk
docker cp <container>:/opt/data/move.sksk ./move.sksk
```

Plain install (macOS or Linux):

```sh
sksidekick --home "<your sidekick home>" move-out ./move.sksk
```

From this moment the old copy no longer answers. Its backups and its data stay where they are.

## 2. Unpack it at the new place

Container (a new, empty volume):

```sh
docker run -d --name sidekick -v sidekick-data:/opt/data ghcr.io/smilintux/sksidekick:<version>
docker cp ./move.sksk sidekick:/opt/data/move.sksk
docker exec sidekick chown hermes:hermes /opt/data/move.sksk
docker exec -i sidekick sksidekick import /opt/data/move.sksk --phrase-stdin
```

Plain install (leave the home empty, then import into the home the installer printed):

```sh
bash packaging/install.sh --no-init
sksidekick --home "<your sidekick home>" import ./move.sksk --phrase-stdin
```

Type the phrase when asked (it is read from the keyboard, never from the command line). The new place starts
answering by itself once the import finishes. Check it with `sksidekick --home "<home>" status` (in a container:
`docker exec sidekick sksidekick status`): it should say `live`.

## 3. Retire the old place

Only when the new place works:

```sh
docker exec <old container> sksidekick retire --yes          # or
sksidekick --home "<old home>" retire --yes
```

Retire checks that the move archive is still where the move put it and still opens; if you moved the file, add
`--archive PATH`. It then destroys the old copy's keys and files.

## Changed your mind?

Before you retire, `sksidekick move-cancel` at the old place (with `--home` on a plain install) makes it answer
again. If you already imported the archive somewhere, delete that new copy (`sksidekick delete --yes` there):
only one copy may answer.

## What moves and what does not

Everything that is your sidekick moves: memories, personality, settings, journal, reminders, conversations and
channel logins. Things that are rebuilt at the new place do not: the search index, logs, caches and installed
tools. A plain install refuses to take over a Hermes that is already installed on the same computer; use the
container there.
