#!/usr/bin/env python3
"""Smoke tests for ovpull: link parsing + the whole git sync logic against a
local bare repository that mimics Overleaf's fast-forward-only rule."""
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

print("\n== zip route (real Overleaf read link, needs network) ==")
if os.environ.get("OVPULL_TEST_ZIP") == "1":
    out = tmp / "zipout"
    res = ovpull.zip_route("https://www.overleaf.com/read/jpkfdbgsnbsj", out)
    tex = list(Path(res["dest"]).glob("*.tex"))
    check(f"downloaded and extracted {res['files']} files", res["files"] > 0 and tex,
          str(res))
    check("zip is a real archive", zipfile.is_zipfile(res["zip"]))
else:
    print("skipped (set OVPULL_TEST_ZIP=1 to run against the network)")

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
