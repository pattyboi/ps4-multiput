# ps4/multiput/web

Web UI over `../multiput_push.py`: browse one or more configured library
directories, switch between them or add a new one from the UI, multi-select
`.pkg` files, and push to the console over parallel TCP. The dashboard
follows the repository's plain server-rendered-Jinja2 + vanilla-JS
convention, styled as a quiet vintage lowrider (cream, oxide red, chrome
trim, one gold pinstripe).

It calls `multiput_push.push_multistream()` and `multiput_inject.inject()`
directly. The receiver is auto-injected through GoldHEN PayLoader if its
control listener is not already running. Multiple comma-separated console
addresses are accepted; streams round-robin across them.

There is deliberately no FTP fallback.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```sh
python3 app.py                          # binds 0.0.0.0:8101
MULTIPUT_WEB_ROOTS="games=/mnt/x/games;other=/mnt/x/other" python3 app.py
```

Dashboard: `http://<host>:8101/`.

As a service:

```sh
sudo cp multiput-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now multiput-web
```

## Config (env vars)

| var | default | |
|---|---|---|
| `MULTIPUT_WEB_DOWNLOAD_BASE` | `/mnt/dietpi_userdata2/downloads` | trust boundary -- roots added from the UI or via `MULTIPUT_WEB_ROOTS` must resolve under this directory |
| `MULTIPUT_WEB_ROOTS` | `gamarr=/mnt/dietpi_userdata2/downloads/complete/gamarr;decypharr=/mnt/dietpi_userdata2/downloads/decypharr` | `label=path;label=path` -- the directories the "Directory" dropdown starts with; scanned for `*.pkg` recursively |
| `MULTIPUT_WEB_CONSOLE_HOST` | *(empty)* | pre-fills the console-address field; the console's LAN IP isn't stable session to session, so this is always editable in the UI regardless |
| `MULTIPUT_WEB_STREAMS` | `8` | pre-fills the Streams field; still editable per push, 1-64 |
| `MULTIPUT_WEB_HTTP_HOST` | `0.0.0.0` | |
| `MULTIPUT_WEB_HTTP_PORT` | `8101` | |

## Design notes

- Destination is flat under `/data/pkg` (`multiput_protocol.DESTINATION_ROOT`),
  the one path GoldHEN's Package Installer actually scans -- same convention
  the receiver and CLI sender already enforce on both ends.
- One push at a time: `/api/push` returns 409 while a push is running.
- Progress state is a single in-process dataclass, no persistence -- a
  daemon restart mid-push drops that transfer. MultiPut has no resume
  protocol (see the parent README's "Safety and limitations"): a push
  interrupted this way must be rerun from scratch for that file.
- No auth, binds `0.0.0.0` -- same LAN-trust-boundary assumption as
  grow-tent's dashboard and as MultiPut's own wire protocol. Don't expose
  this past the LAN without adding some.
- **Directory picker.** The "Directory" dropdown switches which configured
  root is scanned/pushed from; "Add a directory" (collapsed by default)
  lets the operator register a new one at runtime by typing a label and
  an absolute path -- validated server-side (`add_root()` in `app.py`)
  to exist, be a directory, and resolve under `MULTIPUT_WEB_DOWNLOAD_BASE`,
  same trust boundary as everything else this tool already reads from.
  Not persisted across a daemon restart -- re-add it, or bake it into
  `MULTIPUT_WEB_ROOTS` if it's permanent.
- **No verify-after-push option.** MultiPut's wire protocol has no
  read-back/RETR operation to re-hash a pushed file against (see
  `../openwiki/wire-protocol.md`) -- there's nothing here to wire a
  checkbox to, unlike a resumable-FTP tool that can re-`RETR` and hash.
- **Listener auto-inject.** Before the first push to a console this
  session, a bare connect-and-close probes port 9022 (MultiPut's own
  listener -- safe, unlike GoldHEN PayLoader's port 9090, see
  `_multiput_listener_up`'s docstring). If nothing answers,
  `multiput_inject.inject()` sends `multiput.elf` through GoldHEN PayLoader
  and waits for the real listener before the push proceeds.
