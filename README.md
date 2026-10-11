# ovpull

Pull an Overleaf project down from a link — one command, two routes.

```
ovpull <link> [dest]
```

* **git route** — for accounts with Overleaf **premium / institutional** git access
  (which is what hands out an `olp_…` token). Clones the project once, then
  `--update` fast-forwards your copy to whatever the co-authors did in the web
  editor, and `--push` sends your local commits back. Real incremental sync.
* **zip route** — for everyone else. It opens a **read-only share link** in a real
  browser and saves the project `.zip` from inside that session. No account, no
  token, no premium.

The link type decides the route; both accept exactly what you copy out of Overleaf.

## Install

```sh
# git route only: nothing to install, plain Python 3.9+ stdlib
python3 ovpull.py --help

# zip route: needs a browser automation layer
pip install playwright          # then either:
playwright install chromium     #   bundled chromium, or
#   ...nothing: an installed Google Chrome / Edge is picked up automatically
ln -s "$PWD/ovpull.py" /usr/local/bin/ovpull    # optional, gives you `ovpull`
```

## Use

```sh
# share link -> files on disk (no account needed)
ovpull https://www.overleaf.com/read/jpkfdbgsnbsj ./papers

# project link -> git clone (needs the token, see below)
ovpull https://www.overleaf.com/project/6970f8756ffc675a957579fb ./paper
ovpull https://git.overleaf.com/6970f8756ffc675a957579fb

# later: pull the co-authors' edits / send yours
ovpull <same link> ./paper --update
ovpull <same link> ./paper --status
ovpull <same link> ./paper --push -m "Sync my edits"

# force a route, or get machine-readable output
ovpull <read link> --zip --json
ovpull <project link> --print-url
```

Destination defaults: git route → `./<project-id>`, zip route → the current
directory (it writes `<Title>.zip` and extracts it into `<Title>/`).

## The token (git route only)

Overleaf → *Account Settings → Git Integration → Generate token* (shown once).
Store it so it never touches your shell history or the repo:

```sh
mkdir -p ~/.config/ovpull && umask 077 && printf '%s\n' 'olp_XXXXXXXXXXXXXXXX' > ~/.config/ovpull/token
```

`$OVERLEAF_GIT_TOKEN` works too and wins over the file. The token is handed to
git through a throw-away `GIT_ASKPASS` helper that is deleted right after, so it
never lands in `.git/config`, in `ps`, or in a commit.

**The premium gate is real:** free Overleaf accounts have no git remote and no
token, so there is no way around it — use the zip route there.

## Why the zip route needs a browser

`https://www.overleaf.com/project/<id>/download/zip` answers a plain HTTP client
(`curl`, `requests`) with **403 `{"message":"restricted"}`**, even after the
read-only session cookie has been collected. The same URL returns
`200 application/zip` when it is requested from *inside* a browser page that has
loaded the read link. So the browser is part of the mechanism, not a convenience
wrapper. A read link also cannot be git-cloned at all: it carries a share token,
not a project id.

## Claude launcher — one persistent session per project

`launchers/` opens a Claude Code session in this repo and, from the second time
on, returns to **the same conversation** instead of starting a new one.

| OS | double-click this | or from a shell |
|---|---|---|
| macOS | `launchers/launch-claude.command` | `./launchers/launch-claude.sh` |
| Linux | — | `./launchers/launch-claude.sh` |
| Windows | `launchers/launch-claude.bat` | `.\launchers\launch-claude.ps1` |

```
<launcher> --new              # start a fresh conversation instead of continuing
<launcher> --dry-run          # print what would run, launch nothing
<launcher> --model opus       # everything else is forwarded to claude
```

How it decides: if `~/.claude/projects/<this-folder>/` already holds a transcript,
it runs `claude --continue`; otherwise it runs `claude` plain. The working
directory is always the repo root, so the session is scoped to this project.
The `.bat` bypasses the execution policy for that one call only (nothing on the
machine is changed) and keeps its window open; `CLAUDE_LAUNCHER_HOLD=0` turns
that pause off for scripting. Set `CLAUDE_BIN` if `claude` is not on `PATH`.

Both sides are covered by tests that never start a real session — a stub binary
plays Claude:

```sh
bash tests/test_launchers.sh                                              # macOS / Linux
powershell -NoProfile -ExecutionPolicy Bypass -File tests\test_launchers.ps1   # Windows
```

## Tests

```sh
python3 tests/test_ovpull.py                    # link parsing + git logic, offline
OVPULL_TEST_ZIP=1 python3 tests/test_ovpull.py  # + the real zip route (network)
```

The git tests run against a local bare repo configured with
`receive.denyNonFastForwards=true`, which reproduces Overleaf's rule that only a
forward step is accepted. They cover: clone, no-op, fast-forward, plain push,
diverged histories (merged, then pushed as a forward step), a control case
proving a bare non-fast-forward push *is* refused, and refusal on a dirty tree.

## Environment

| Variable | Meaning |
|---|---|
| `OVERLEAF_GIT_TOKEN` | the `olp_…` token (overrides the file) |
| `OVPULL_TOKEN_FILE` | where the token lives (default `~/.config/ovpull/token`) |
| `OVPULL_GIT_BASE` | git host (default `https://git.overleaf.com`; set it for on-premises Overleaf) |
| `OVPULL_WEB_BASE` | web host (default `https://www.overleaf.com`) |
| `OVERLEAF_GIT_USER` | git username, default `git` |

## Limits, honestly

* One-shot for the zip route: it re-downloads, it does not merge.
* The zip route needs Playwright (or a working headless Chrome/Edge).
* `--update`/`--push` refuse to run on a dirty working tree instead of guessing.
* A diverged history is reconciled with a **merge** commit, which is still a
  fast-forward step from Overleaf's point of view; genuine conflicts abort with
  the working tree untouched.
* Overleaf does not preserve the exec bit and has no Git LFS.

## License

MIT.
