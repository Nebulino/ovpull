#!/usr/bin/env python3
"""Smoke tests for ovpull: link parsing + the whole git sync logic against a
local bare repository that mimics Overleaf's fast-forward-only rule."""
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("OVPULL_WEB_BASE", "https://www.overleaf.com")

import ovpull  # noqa: E402

PID = "6970f8756ffc675a957579fb"
FAILS = []


def check(label, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + label + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        FAILS.append(label)


def git(*args, cwd=None, check=True):
    p = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and p.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr}")
    return p


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


# ---------------------------------------------------------------- link parsing
print("== link parsing ==")
for link, kind, expected in [
    (f"https://www.overleaf.com/project/{PID}", "project", PID),
    (f"https://git.overleaf.com/{PID}", "project", PID),
    (f"  \"https://www.overleaf.com/project/{PID}#abc\"  ", "project", PID),
    (PID, "project", PID),
    ("https://www.overleaf.com/read/jpkfdbgsnbsj", "read", "jpkfdbgsnbsj"),
    ("https://www.overleaf.com/read/jpkfdbgsnbsj#0e7df0", "read", "jpkfdbgsnbsj"),
]:
    got = ovpull.parse_link(link)
    key = "project_id" if kind == "project" else "read_token"
    check(f"parse {link.strip()[:52]!r}", got["kind"] == kind and got[key] == expected, str(got))
try:
    ovpull.parse_link("https://example.com/hello")
    check("reject foreign url", False)
except ovpull.OvError:
    check("reject foreign url", True)

# ------------------------------------------------------------- git sync logic
print("\n== git route against a fast-forward-only 'Overleaf' ==")
tmp = Path(tempfile.mkdtemp(prefix="ovpull-test-"))
origin = tmp / "origin" / PID          # stands in for git.overleaf.com/<pid>
origin.mkdir(parents=True)
git("init", "--bare", "--initial-branch=master", str(origin))
# Overleaf only accepts a forward step - reproduce that rule
git("-C", str(origin), "config", "receive.denyNonFastForwards", "true")

# seed the "Overleaf" project with one commit (co-author edits in the web UI)
seed = tmp / "seed"
git("init", "--initial-branch=master", str(seed))
write(seed / "main.tex", "\\documentclass{article}\n\\begin{document}Hello\\end{document}\n")
git("-C", str(seed), "add", ".")
git("-C", str(seed), "-c", "user.email=a@b.c", "-c", "user.name=seed", "commit", "-qm", "Overleaf: initial")
coauthor = git("-C", str(seed), "rev-parse", "HEAD").stdout.strip()
git("-C", str(seed), "remote", "add", "origin", str(origin))
git("-C", str(seed), "push", "-q", "origin", "master")

# point ovpull at the fake host
os.environ["OVPULL_GIT_BASE"] = str(origin.parent)
os.environ["OVPULL_TOKEN_FILE"] = str(tmp / "no-token")

dest = tmp / "clone"
with ovpull.Creds(None) as env:
    ovpull.clone(PID, dest, "git", env)
check("clone creates the working copy", (dest / "main.tex").exists())

# 1. nothing to do
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest, PID, "master", "git", env)
check("clean tree reports up-to-date", st["action"] == "up-to-date", st)

# 2. co-author pushes in the web editor -> we fast-forward
write(seed / "extra.tex", "co-author file\n")
git("-C", str(seed), "add", ".")
git("-C", str(seed), "-c", "user.email=a@b.c", "-c", "user.name=seed", "commit", "-qm", "Overleaf: co-author edit")
coauthor2 = git("-C", str(seed), "rev-parse", "HEAD").stdout.strip()
git("-C", str(seed), "push", "-q", "origin", "master")
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest, PID, "master", "git", env)
check("fast-forwards onto the co-author commit",
      st["action"] == "fast-forwarded" and (dest / "extra.tex").exists()
      and ovpull.rev(dest, "HEAD") == coauthor2, st)

# 3. local commit -> plain fast-forward push
write(dest / "mine.tex", "my work\n")
git("-C", str(dest), "add", ".")
git("-C", str(dest), "-c", "user.email=m@x.y", "-c", "user.name=me", "commit", "-qm", "local work")
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest, PID, "master", "git", env, push=True)
origin_head = git("-C", str(origin), "rev-parse", "master").stdout.strip()
remote_now = subprocess.run(["git", "ls-remote", str(origin), "master"], text=True,
                            capture_output=True).stdout.split()[0]
check("local commit reaches Overleaf by fast-forward",
      st["action"] == "pushed" and remote_now == origin_head, f"{st} remote={remote_now[:12]}")

# 4. both sides moved -> merge commit, still a forward step -> accepted
def seed_sync():
    """the web editor picks up whatever was just pushed to 'Overleaf'."""
    git("-C", str(seed), "fetch", "-q", "origin")
    git("-C", str(seed), "reset", "-q", "--hard", "origin/master")


seed_sync()
write(seed / "extra.tex", "co-author v2\n")
git("-C", str(seed), "add", ".")
git("-C", str(seed), "-c", "user.email=a@b.c", "-c", "user.name=seed", "commit", "-qm", "Overleaf: another edit")
git("-C", str(seed), "push", "-q", "origin", "master")
write(dest / "mine2.tex", "more local work\n")
git("-C", str(dest), "add", ".")
git("-C", str(dest), "-c", "user.email=m@x.y", "-c", "user.name=me", "commit", "-qm", "local work 2")
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest, PID, "master", "git", env, push=True)
new_remote = subprocess.run(["git", "ls-remote", str(origin), "master"], text=True,
                            capture_output=True).stdout.split()[0]
check("diverged histories merge and push (fast-forward for Overleaf)",
      st["action"] == "merged-and-pushed"
      and subprocess.run(["git", "-C", str(origin), "merge-base", "--is-ancestor",
                          coauthor2, new_remote]).returncode == 0, st)

# 5. sanity: a plain non-fast-forward push is indeed refused by the fake Overleaf
seed_sync()
write(seed / "x.tex", "x\n")
git("-C", str(seed), "add", ".")
git("-C", str(seed), "-c", "user.email=a@b.c", "-c", "user.name=seed", "commit", "-qm", "web edit")
git("-C", str(seed), "push", "-q", "origin", "master")
write(dest / "mine3.tex", "third\n")
git("-C", str(dest), "add", ".")
git("-C", str(dest), "-c", "user.email=m@x.y", "-c", "user.name=me", "commit", "-qm", "local 3")
push = git("-C", str(dest), "push", str(origin), "HEAD:master", check=False)
check("(control) bare non-ff push is rejected by the fake Overleaf",
      push.returncode != 0 and re.search(r"reject|non-fast-forward|fetch first", push.stderr, re.I),
      push.stderr.strip()[:120])
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest, PID, "master", "git", env, push=True)
check("ovpull recovers by merging", st["action"] == "merged-and-pushed", st)

# 6. dirty tree is refused rather than silently mangled
write(dest / "main.tex", "uncommitted\n")
try:
    with ovpull.Creds(None) as env:
        ovpull.git_pull(dest, PID, "master", "git", env)
    check("dirty working tree refused", False)
except ovpull.OvError as exc:
    check("dirty working tree refused", "uncommitted" in str(exc))

# 7. CLI: --print-url never leaks a token, read link without --git takes the zip route
check("--print-url", ovpull.main([f"https://www.overleaf.com/project/{PID}", "--print-url", "-q"]) == 0)
rc = ovpull.main(["https://www.overleaf.com/read/jpkfdbgsnbsj", "--git"])
check("read link + --git is rejected with a clear message", rc == 2)

# ------------------------------------------------------------ launcher install
print("\n== Claude launcher dropped into the pulled project ==")
dest2 = tmp / "clone-with-launcher"
rc = ovpull.main([f"https://www.overleaf.com/project/{PID}", str(dest2), "--launcher", "-q"])
check("CLI clones and installs the launchers", rc == 0
      and (dest2 / "launchers" / "launch-claude.sh").exists()
      and (dest2 / "launchers" / "launch-claude.bat").exists(), rc)
check("the shell launcher is executable",
      os.access(dest2 / "launchers" / "launch-claude.sh", os.X_OK))
check("install is listed in .git/info/exclude",
      "launchers/" in (dest2 / ".git" / "info" / "exclude").read_text().split())
clean = git("-C", str(dest2), "status", "--porcelain").stdout.strip() == ""
check("the git tree stays clean (so --update keeps working)", clean)
with ovpull.Creds(None) as env:
    st = ovpull.git_pull(dest2, PID, "master", "git", env)
check("--update still reports up-to-date afterwards", st["action"] == "up-to-date", st)
marker = json.loads((dest2 / ".claude" / "ovpull.json").read_text())
check("the .claude marker records this project",
      marker["project_id"] == PID and marker["route"] == "git" and marker["branch"] == "master"
      and Path(marker["project_dir"]) == dest2 and Path(marker["ovpull"]).is_file(), marker)
for name in ("overleaf-status.md", "overleaf-pull.md", "overleaf-push.md"):
    check(f".claude/commands/{name} written", (dest2 / ".claude" / "commands" / name).is_file())
push_md = (dest2 / ".claude" / "commands" / "overleaf-push.md").read_text()
check("the push command tells the session to ask first", "confirm" in push_md)
check("the push command carries the real invocation", "--push" in push_md and PID in push_md)

# run exactly what the generated command tells Claude to run
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc_status = ovpull.main([str(marker["link"]), str(dest2), "--status", "--json"])
check("the recorded command actually works",
      rc_status == 0 and json.loads(buf.getvalue().strip().splitlines()[-1])["action"] == "up-to-date",
      buf.getvalue())

# --status must never move anything, even when Overleaf is ahead
seed_sync()
write(seed / "late.tex", "late arrival\n")
git("-C", str(seed), "add", ".")
git("-C", str(seed), "-c", "user.email=a@b.c", "-c", "user.name=seed", "commit", "-qm", "web edit after status")
git("-C", str(seed), "push", "-q", "origin", "master")
head_before = ovpull.rev(dest2, "HEAD")
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    ovpull.main([str(marker["link"]), str(dest2), "--status", "--json"])
status = json.loads(buf.getvalue().strip().splitlines()[-1])
check("--status reports 'behind' without touching the copy",
      status["action"] == "behind" and ovpull.rev(dest2, "HEAD") == head_before, status)
check("...and says what to do about it", "update" in status.get("hint", ""))

try:
    ovpull.install_launcher(dest2)
    check("a non-empty launchers/ is left alone", False)
except ovpull.OvError as exc:
    check("a non-empty launchers/ is left alone", "not empty" in str(exc))
try:
    ovpull.install_launcher(dest2, explicit=str(tmp / "nowhere"))
    check("missing launcher source gives a clear error", False)
except ovpull.OvError as exc:
    check("missing launcher source gives a clear error", "not found" in str(exc))

# ---------------------------------------------------------------- PATH install
print("\n== putting `ovpull` on PATH ==")
bindir = tmp / "bin"
res = ovpull.install_command(bindir)
shim = Path(res["installed"])
check("install creates the command", shim.exists() and os.access(shim, os.X_OK), res)
if os.name != "nt":
    check("it points at this script", shim.is_symlink() and shim.resolve() == Path(ovpull.__file__).resolve())
out = subprocess.run([str(shim), "--print-url", f"https://www.overleaf.com/project/{PID}"],
                     text=True, capture_output=True)
check("the installed command actually runs", out.returncode == 0 and PID in out.stdout, out.stderr)
check("installing twice is harmless", Path(ovpull.install_command(bindir)["installed"]) == shim)
other = tmp / "bin2"
other.mkdir()
(other / "ovpull").write_text("not ours\n")
try:
    ovpull.install_command(other)
    check("a foreign file at that path is refused", False)
except ovpull.OvError as exc:
    check("a foreign file at that path is refused", "already exists" in str(exc))
check("--overwrite replaces it",
      Path(ovpull.install_command(other, overwrite=True)["installed"]).exists())
check("uninstall removes the command",
      ovpull.uninstall_command(bindir)["removed"] != "nothing found" and not shim.exists())
check("no link and no --install is a clear error", ovpull.main([]) == 2)

# --install stages the launchers too, so --launcher works from anywhere
staged = tmp / "staged"
os.environ["OVPULL_DATA_DIR"] = str(staged)
try:
    info = ovpull.install_command(tmp / "bin3")
    check("--install stages the launcher scripts", info["launchers"] == str(staged)
          and (staged / "launch-claude.sh").exists(), info)
    saved_file = ovpull.__file__
    ovpull.__file__ = str(tmp / "elsewhere" / "ovpull.py")   # pretend no launchers next to us
    try:
        found = ovpull.launcher_source()
    finally:
        ovpull.__file__ = saved_file
    check("they are found with no launchers next to the script", Path(found) == staged, found)
finally:
    os.environ.pop("OVPULL_DATA_DIR", None)

# the one command a fresh clone runs
os.environ["OVPULL_DATA_DIR"] = str(tmp / "staged2")
try:
    sh_out = subprocess.run(["bash", str(HERE / "install.sh"), "--bin-dir", str(tmp / "shbin")],
                            text=True, capture_output=True)
finally:
    os.environ.pop("OVPULL_DATA_DIR", None)
check("install.sh installs the command in one go",
      sh_out.returncode == 0 and (tmp / "shbin" / "ovpull").exists(), sh_out.stderr[-300:])

print("\n== --help ==")
helped = subprocess.run([sys.executable, str(HERE / "ovpull.py"), "--help"], text=True,
                        capture_output=True)
check("--help exits 0 and mentions the install flag",
      helped.returncode == 0 and "--install" in helped.stdout, helped.stderr[-300:])

print("\n== choosing the interpreter for the Windows shim ==")
wbin = tmp / "winbin"
wbin.mkdir()
broken = wbin / "python3"
broken.write_text("#!/bin/sh\nexit 1\n")             # a stale scoop-style shim
broken.chmod(0o755)
launcher = wbin / "py"
launcher.write_text('#!/bin/sh\n[ "$1" = "-3" ] && shift\n[ "$1" = "--version" ] && echo "Python 3.12.9"\n')
launcher.chmod(0o755)
saved = os.environ.get("PATH", "")
try:
    os.environ["PATH"] = str(wbin)                    # only these exist for the probe
    picked = ovpull.windows_python()
finally:
    os.environ["PATH"] = saved
check("a working py -3 wins over a broken shim", picked.endswith("-3") and "py" in picked, picked)
launcher.unlink()                                    # only the broken shim is left
try:
    os.environ["PATH"] = str(wbin)
    picked = ovpull.windows_python()
finally:
    os.environ["PATH"] = saved
check("nothing usable falls back to plain `python`", picked == "python", picked)

print("\n== picking a python that has playwright ==")
fakebin = tmp / "pybin"
fakebin.mkdir()
fake = fakebin / "python3.14"
fake.write_text('#!/bin/sh\nexit 0\n')                       # says yes to every probe
fake.chmod(0o755)
saved_path = os.environ.get("PATH", "")
try:
    os.environ["PATH"] = f"{fakebin}{os.pathsep}{saved_path}"
    check("finds another interpreter on PATH", ovpull.python_with_playwright() == str(fake))
finally:
    os.environ["PATH"] = saved_path
os.environ["OVPULL_PYTHON"] = "/opt/forced/python"
check("OVPULL_PYTHON wins over detection", ovpull.python_with_playwright() == "/opt/forced/python")
del os.environ["OVPULL_PYTHON"]

print("\n== zip route (real Overleaf read link, needs network) ==")
if os.environ.get("OVPULL_TEST_ZIP") == "1":
    out = tmp / "zipout"
    res = ovpull.zip_route("https://www.overleaf.com/read/jpkfdbgsnbsj", out)
    tex = list(Path(res["dest"]).glob("*.tex"))
    check(f"downloaded and extracted {res['files']} files", res["files"] > 0 and tex,
          str(res))
    check("zip is a real archive", zipfile.is_zipfile(res["zip"]))
    launcher = ovpull.install_launcher(Path(res["dest"]), link="https://www.overleaf.com/read/jpkfdbgsnbsj",
                                       project_id=res["project_id"], route="zip")
    check("--launcher works on the zip route too",
          Path(launcher["launcher"], "launch-claude.sh").exists()
          and os.access(Path(launcher["launcher"], "launch-claude.sh"), os.X_OK))
    check("the zip route gets a refresh command, not a push one",
          Path(res["dest"], ".claude", "commands", "overleaf-refresh.md").is_file()
          and not Path(res["dest"], ".claude", "commands", "overleaf-push.md").exists())
else:
    print("skipped (set OVPULL_TEST_ZIP=1 to run against the network)")

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
