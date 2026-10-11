#!/usr/bin/env python3
"""
ovpull - pull an Overleaf project down from a link.

Two routes, picked automatically from the link and from what credentials
are available:

  git  (Overleaf premium / institutional licence + an `olp_...` git token)
       git clone / fetch+merge, optional push back. Incremental, keeps
       real version history.

  zip  (no account needed)
       opens a read-only share link in a real browser (Playwright) and
       fetches the project .zip from inside that session.
       Note: Overleaf answers a plain HTTP client (curl, requests) with
       403 "restricted" for this URL, so the browser step is required
       here, not a convenience.

The token is never a command-line argument, never in the remote URL and
never written into the repo: it comes from $OVERLEAF_GIT_TOKEN or from
~/.config/ovpull/token (chmod 600) and reaches git through a throw-away
GIT_ASKPASS helper that is deleted as soon as git is done.

Examples
--------
  ovpull https://www.overleaf.com/project/6970f8756ffc675a957579fb
  ovpull https://www.overleaf.com/read/jpkfdbgsnbsj ./paper      # zip route
  ovpull https://git.overleaf.com/6970f8756ffc675a957579fb --update
  ovpull <project-id> --status
  ovpull <project-id> --push -m "Sync local edits"
  ovpull <link> --zip --dest ./inbox
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PID_RE = re.compile(r"[0-9a-f]{24}")
READ_RE = re.compile(r"overleaf\.[a-z.]+/read/([A-Za-z0-9._~-]{4,})", re.I)
PROJECT_RE = re.compile(r"(?:overleaf\.[a-z.]+/project/|git\.overleaf\.[a-z.]+/)([0-9a-f]{24})", re.I)
BARE_RE = re.compile(r"^[0-9a-f]{24}$", re.I)


class OvError(Exception):
    """Anything the user can act on."""


def web_base() -> str:
    return os.environ.get("OVPULL_WEB_BASE", "https://www.overleaf.com").rstrip("/")


def git_base() -> str:
    return os.environ.get("OVPULL_GIT_BASE", "https://git.overleaf.com").rstrip("/")


def token_file() -> Path:
    return Path(os.environ.get("OVPULL_TOKEN_FILE", "~/.config/ovpull/token")).expanduser()


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------
# link parsing
# --------------------------------------------------------------------------

def parse_link(link: str) -> dict:
    """Normalise any Overleaf share link into {'kind', 'project_id'|'read_token', 'url'}."""
    s = link.strip().strip('"').strip("'").split("#", 1)[0].strip()
    if not s:
        raise OvError("empty link")
    if BARE_RE.match(s):
        pid = s.lower()
        return {"kind": "project", "project_id": pid, "url": f"{web_base()}/project/{pid}"}
    m = READ_RE.search(s)
    if m:
        tok = m.group(1)
        return {"kind": "read", "read_token": tok, "project_id": None,
                "url": f"{web_base()}/read/{tok}"}
    m = PROJECT_RE.search(s)
    if m:
        pid = m.group(1).lower()
        return {"kind": "project", "project_id": pid, "url": f"{web_base()}/project/{pid}"}
    raise OvError(
        f"unrecognised Overleaf link: {link!r}\n"
        f"  expected {web_base()}/project/<id>, git.<host>/<id> or {web_base()}/read/<token>"
    )


# --------------------------------------------------------------------------
# git plumbing
# --------------------------------------------------------------------------

def run_git(args, cwd=None, env=None, check=True, no_helper=False):
    cmd = ["git"]
    if no_helper:                      # our token wins over a stale keychain entry
        cmd += ["-c", "credential.helper="]
    cmd += list(args)
    full_env = os.environ.copy()
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        full_env.update(env)
    p = subprocess.run(cmd, cwd=cwd, env=full_env, text=True, capture_output=True)
    if check and p.returncode != 0:
        detail = (p.stderr or p.stdout).strip()
        raise OvError(f"git {' '.join(args)} failed:\n{detail}")
    return p


class Creds:
    """GIT_ASKPASS shim holding the token; removed on exit."""

    def __init__(self, token: str | None):
        self.token = token
        self._path: str | None = None

    def __enter__(self):
        if not self.token:
            return {}
        fd, path = tempfile.mkstemp(prefix="ovpull-askpass-", suffix=".sh")
        with os.fdopen(fd, "w") as fh:
            fh.write('#!/bin/sh\nprintf "%s\\n" "$OVPULL_GIT_TOKEN"\n')
        os.chmod(path, 0o700)
        self._path = path
        return {"GIT_ASKPASS": path, "OVPULL_GIT_TOKEN": self.token}

    def __exit__(self, *exc):
        if self._path and os.path.exists(self._path):
            os.remove(self._path)
        return False


def read_token(path: Path) -> str | None:
    env_token = os.environ.get("OVERLEAF_GIT_TOKEN", "").strip()
    if env_token:
        return env_token
    if path.exists():
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            log(f"! {path} is readable by others - run: chmod 600 {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line
    return None


def remote_url(pid: str, user: str) -> str:
    base = git_base()
    if base.startswith("http"):
        host = re.sub(r"^https?://", "", base)
        return f"https://{user}@{host}/{pid}"
    return f"{base}/{pid}"


def remotes(dest: Path) -> dict:
    p = run_git(["-C", str(dest), "remote", "-v"], check=False)
    found = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            found.setdefault(parts[0], parts[1])
    return found


def ensure_remote(dest: Path, pid: str, user: str, env: dict) -> str:
    """Return the name of the Overleaf remote, creating it if needed."""
    found = remotes(dest)
    if "overleaf" in found:
        return "overleaf"
    for name, url in found.items():                     # a clone that kept the url it came from
        if url.rstrip("/").endswith(pid):
            return name
    host = re.sub(r"^https?://", "", git_base()).split("/")[0]
    if host:
        for name, url in found.items():
            if host in url:
                return name
    run_git(["-C", str(dest), "remote", "add", "overleaf", remote_url(pid, user)],
            env=env, no_helper=bool(env))
    return "overleaf"


def rev(dest: Path, spec: str) -> str:
    return run_git(["-C", str(dest), "rev-parse", spec]).stdout.strip()


def is_ancestor(dest: Path, older: str, newer: str) -> bool:
    return run_git(["-C", str(dest), "merge-base", "--is-ancestor", older, newer],
                   check=False).returncode == 0


def dirty(dest: Path) -> bool:
    return bool(run_git(["-C", str(dest), "status", "--porcelain"]).stdout.strip())


def clone(pid: str, dest: Path, user: str, env: dict) -> None:
    run_git(["clone", remote_url(pid, user), str(dest)], env=env, no_helper=bool(env))
    # one stable remote name, so --update/--push behave the same on a fresh clone
    if "overleaf" not in remotes(dest) and "origin" in remotes(dest):
        run_git(["-C", str(dest), "remote", "rename", "origin", "overleaf"], env=env,
                no_helper=bool(env))


# --------------------------------------------------------------------------
# git route
# --------------------------------------------------------------------------

def git_pull(dest: Path, pid: str, branch: str, user: str, env: dict,
             push: bool = False, message: str | None = None) -> dict:
    """Fetch, then fast-forward / merge / push so both sides converge."""
    if not (dest / ".git").exists():
        raise OvError(f"{dest} is not a git repository")
    if dirty(dest):
        raise OvError(f"{dest} has uncommitted changes - commit or stash them first")
    remote = ensure_remote(dest, pid, user, env)
    run_git(["-C", str(dest), "fetch", "--quiet", remote, branch], env=env, no_helper=bool(env))
    tracking = f"{remote}/{branch}"
    ours, theirs = rev(dest, "HEAD"), rev(dest, tracking)

    result = {"route": "git", "dest": str(dest), "branch": branch, "remote": remote,
              "head": ours[:12], "remote_head": theirs[:12]}
    if ours == theirs:
        result["action"] = "up-to-date"
        return result

    if is_ancestor(dest, ours, theirs):                     # Overleaf is ahead
        run_git(["-C", str(dest), "merge", "--ff-only", tracking], env=env, no_helper=bool(env))
        result["action"] = "fast-forwarded"
        result["head"] = rev(dest, "HEAD")[:12]
        return result

    if not push:
        result["action"] = "local-ahead" if is_ancestor(dest, theirs, ours) else "diverged"
        result["hint"] = ("local commits are ahead - rerun with --push to send them"
                          if result["action"] == "local-ahead"
                          else "histories diverged - rerun with --push (creates a merge) "
                               "or cherry-pick manually")
        return result

    # push
    if is_ancestor(dest, theirs, ours):                    # plain fast-forward
        run_git(["-C", str(dest), "push", remote, f"HEAD:refs/heads/{branch}"],
                env=env, no_helper=bool(env))
        result["action"] = "pushed"
    else:                                                  # diverged: merge, then push
        msg = message or f"Sync from {os.uname().nodename}"
        merge = run_git(["-C", str(dest), "merge", "--no-edit", "-m", msg, tracking],
                        env=env, no_helper=bool(env), check=False)
        if merge.returncode != 0:
            run_git(["-C", str(dest), "merge", "--abort"], check=False)
            raise OvError("merge conflicts - resolve them by hand, then push again")
        run_git(["-C", str(dest), "push", remote, f"HEAD:refs/heads/{branch}"],
                env=env, no_helper=bool(env))
        result["action"] = "merged-and-pushed"
    result["head"] = rev(dest, "HEAD")[:12]
    result["remote_head"] = rev(dest, f"{remote}/{branch}")[:12]
    return result


def git_status(dest: Path, pid: str, branch: str, user: str, env: dict) -> dict:
    return git_pull(dest, pid, branch, user, env, push=False)


# --------------------------------------------------------------------------
# zip route (no account needed)
# --------------------------------------------------------------------------

def slugify(text: str) -> str:
    s = re.sub(r"[^\w.\- ]+", "", text, flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", s) or "overleaf-project"


def fetch_zip(link: str) -> dict:
    """Open `link` in a real browser and pull the project .zip from that session."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise OvError(
            "the zip route needs a browser:\n"
            "  pip install playwright && playwright install chromium\n"
            "  (Google Chrome / Edge already installed are used as-is)"
        ) from exc

    base = web_base()
    attempts = ({"channel": "chrome"}, {"channel": "msedge"}, {})
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = None
        for kwargs in attempts:
            try:
                browser = pw.chromium.launch(headless=True, **kwargs)
                break
            except Exception as exc:                      # noqa: BLE001
                errors.append(f"{kwargs or 'bundled chromium'}: {str(exc).splitlines()[0]}")
        if browser is None:
            raise OvError("could not launch a browser:\n  " + "\n  ".join(errors))

        ctx = browser.new_context()
        page = ctx.new_page()
        try:
            page.goto(link, wait_until="domcontentloaded", timeout=90_000)
            match = re.search(rf"/project/({PID_RE.pattern})", page.url)
            if not match:
                try:
                    page.wait_for_url(re.compile(rf"/project/{PID_RE.pattern}"), timeout=60_000)
                    match = re.search(rf"/project/({PID_RE.pattern})", page.url)
                except Exception:                          # noqa: BLE001
                    raise OvError(
                        f"{link} did not resolve to a project (got {page.url}).\n"
                        "  The read link may have expired, or it needs you to sign in first."
                    )
            pid = match.group(1)
            resp = ctx.request.get(f"{base}/project/{pid}/download/zip", timeout=180_000)
            if not resp.ok:
                raise OvError(
                    f"Overleaf refused the .zip ({resp.status} {resp.status_text}).\n"
                    "  The read link may have expired, the owner may have switched downloads "
                    "off, or the project may require a signed-in account."
                )
            body = resp.body()
            label = ""                                   # Overleaf names the download for us
            disposition = resp.headers.get("content-disposition", "") or ""
            found = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition)
            if found:
                label = found.group(1).strip()
                if label.lower().endswith(".zip"):
                    label = label[:-4]
            for _ in range(10):                          # the tab carries the name a beat later
                if label:
                    break
                candidate = re.sub(r"\s*-\s*Online LaTeX Editor Overleaf\s*$", "",
                                   page.title() or "").strip()
                if candidate and candidate not in ("Overleaf", "Online LaTeX Editor Overleaf"):
                    label = candidate
                    break
                page.wait_for_timeout(1000)
        finally:
            ctx.close()
            browser.close()

    if not body.startswith(b"PK"):
        raise OvError("the download was not a .zip (Overleaf returned an HTML page)")
    return {"project_id": pid, "title": label or pid,
            "name": slugify(label) if label else f"overleaf-{pid[:8]}",
            "size": len(body), "body": body}


def write_zip(payload: dict, dest_root: Path) -> dict:
    name = payload["name"]
    dest_root.mkdir(parents=True, exist_ok=True)
    archive = dest_root / f"{name}.zip"
    archive.write_bytes(payload["body"])
    target = dest_root / name
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        members = [m for m in zf.namelist() if not m.endswith("/")]
        zf.extractall(target)
    return {"route": "zip", "dest": str(target), "zip": str(archive),
            "title": payload["title"], "project_id": payload["project_id"],
            "files": len(members), "size": payload["size"]}


def zip_route(link: str, dest_root: Path) -> dict:
    return write_zip(fetch_zip(link), dest_root)


# --------------------------------------------------------------------------
# optional: drop the double-click Claude launchers into the pulled project
# --------------------------------------------------------------------------

LAUNCHER_FILES = ("launch-claude.sh", "launch-claude.command",
                  "launch-claude.ps1", "launch-claude.bat")


def launcher_source(explicit: str | None = None) -> Path:
    src = Path(explicit).expanduser() if explicit else Path(__file__).resolve().parent / "launchers"
    missing = [f for f in LAUNCHER_FILES if not (src / f).is_file()]
    if missing:
        raise OvError(
            f"launcher files not found in {src} (missing: {', '.join(missing)})\n"
            "  they ship with the ovpull repo - clone it, or point --launcher-from at them"
        )
    return src


def install_launcher(dest: Path, explicit: str | None = None) -> dict:
    """Copy the launchers into `dest` so the pulled project is double-clickable.

    In a git clone the paths are added to .git/info/exclude: that file is local
    only, so the pulled project stays clean and --update keeps working.
    """
    src = launcher_source(explicit)
    target = dest / "launchers"
    if target.exists() and any(target.iterdir()):
        raise OvError(f"{target} already exists and is not empty - left untouched")
    target.mkdir(parents=True, exist_ok=True)
    for name in LAUNCHER_FILES:
        to = target / name
        shutil.copyfile(src / name, to)
        if name.endswith((".sh", ".command")):
            to.chmod(0o755)                      # double-clickable on macOS/Linux
    excluded = False
    if (dest / ".git").is_dir():
        exclude = dest / ".git" / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if "launchers/" not in text.split():
            with exclude.open("a", encoding="utf-8") as fh:
                if text and not text.endswith("\n"):
                    fh.write("\n")
                fh.write("# added by ovpull --launcher: keep the tree clean for git pulls\n"
                         "launchers/\n")
            excluded = True
    return {"launcher": str(target), "launcher_files": len(LAUNCHER_FILES),
            "launcher_git_excluded": excluded}


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="ovpull",
        description="Pull an Overleaf project from a link (git route or anonymous .zip route).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Token: $OVERLEAF_GIT_TOKEN or ~/.config/ovpull/token (chmod 600).",
    )
    ap.add_argument("link", help="Overleaf link or project id")
    ap.add_argument("dest", nargs="?", default=None,
                    help="destination folder (git route: repo dir; zip route: parent folder)")
    ap.add_argument("-u", "--update", action="store_true", help="update an existing clone instead of cloning")
    ap.add_argument("--push", action="store_true", help="send local commits to Overleaf")
    ap.add_argument("--status", action="store_true", help="report local/remote state, change nothing")
    ap.add_argument("--zip", dest="force_zip", action="store_true", help="force the anonymous .zip route")
    ap.add_argument("--git", dest="force_git", action="store_true", help="force the git route")
    ap.add_argument("-b", "--branch", default="master", help="Overleaf branch (default: master)")
    ap.add_argument("-m", "--message", default=None, help="commit message for the sync commit")
    ap.add_argument("--user", default=os.environ.get("OVERLEAF_GIT_USER", "git"),
                    help="git username for git.overleaf.com (default: git)")
    ap.add_argument("--token-file", default=None, help="read the git token from this file")
    ap.add_argument("--launcher", action="store_true",
                    help="also drop the double-click Claude launchers into the pulled project")
    ap.add_argument("--launcher-from", default=None,
                    help="directory holding the launcher scripts (default: ./launchers next to ovpull.py)")
    ap.add_argument("--print-url", action="store_true", help="print the git URL (no token) and exit")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    def emit(payload: dict) -> None:
        if args.json:
            print(json.dumps({k: v for k, v in payload.items() if not k.startswith("_")}))
        elif not args.quiet:
            for key, value in payload.items():
                if not key.startswith("_"):
                    log(f"{key}: {value}")

    def note(msg: str) -> None:
        """progress chatter - kept off stdout when the caller wants JSON."""
        print(msg, file=sys.stderr if args.json else sys.stdout, flush=True)

    try:
        info = parse_link(args.link)
        token = read_token(Path(args.token_file).expanduser() if args.token_file else token_file())

        if args.print_url:
            if not info["project_id"]:
                raise OvError("a read-only /read/<token> link has no git URL - use the project link")
            log(remote_url(info["project_id"], args.user))
            return 0

        use_zip = args.force_zip or (info["kind"] == "read" and not args.force_git)
        if use_zip:
            if args.status or args.push:
                raise OvError("--status/--push do not apply to the .zip route")
            dest_root = Path(args.dest).expanduser() if args.dest else Path.cwd()
            if not args.quiet:
                note(f"opening {info['url']} in a browser ...")
            result = zip_route(info["url"], dest_root)
            if args.launcher:
                result.update(install_launcher(Path(result["dest"]), args.launcher_from))
            emit(result)
            return 0

        if info["kind"] == "read":
            raise OvError("a /read/<token> link cannot be cloned with git - use the project link "
                           "or drop --git to take the .zip route")

        pid = info["project_id"]
        dest = Path(args.dest).expanduser() if args.dest else Path.cwd() / pid

        with Creds(token) as env:
            if not token and not args.quiet:
                note("! no git token found: $OVERLEAF_GIT_TOKEN or ~/.config/ovpull/token\n"
                     "! the git route is an Overleaf premium feature - grabbing the token needs one")
            if args.status:
                emit(git_status(dest, pid, args.branch, args.user, env))
                return 0
            if args.update or (dest / ".git").exists():
                result = git_pull(dest, pid, args.branch, args.user, env,
                                  push=args.push, message=args.message)
                if args.launcher:
                    result.update(install_launcher(dest, args.launcher_from))
                emit(result)
                return 0
            if args.push:
                raise OvError("--push needs an existing clone (or use --update)")
            if dest.exists() and any(dest.iterdir()):
                raise OvError(f"{dest} exists and is not empty - pick another destination or use --update")
            if not args.quiet:
                note(f"cloning {remote_url(pid, args.user)} -> {dest}")
            clone(pid, dest, args.user, env)
            result = {"route": "git", "action": "cloned", "dest": str(dest),
                      "project_id": pid, "branch": args.branch}
            if args.launcher:
                result.update(install_launcher(dest, args.launcher_from))
            emit(result)
        return 0
    except OvError as exc:
        print(f"ovpull: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
