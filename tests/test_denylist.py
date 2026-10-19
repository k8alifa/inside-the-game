"""Always-on leak guard: no real club, scorer or competition name in committed text.

The denylist holds only SHA-256 hashes (tests/fixtures/denylist.sha256), built by
tools/calibrate/denylist.py from the local raw files. This test needs no raw files.
"""

import os
import subprocess
from pathlib import Path

from tools.calibrate.denylist import ngrams, sha, tokens

ROOT = Path(__file__).parent.parent
DENYLIST = Path(__file__).parent / "fixtures" / "denylist.sha256"
SCOPES = ("data", "docs", "web", "README.md")
BINARY = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".woff", ".woff2", ".pdf"}
ALLOWED = {"README.md": {"premier league"}}  # the hackathon's title, in the README only


def denylist() -> set[str]:
    return {l.strip() for l in DENYLIST.read_text().splitlines() if l.strip() and not l.startswith("#")}


def committed_text_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", *SCOPES],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
        files = [ROOT / f for f in out]
    except (OSError, subprocess.CalledProcessError):  # not a git checkout: walk, skipping raw data
        files = []
        for scope in SCOPES:
            p = ROOT / scope
            if p.is_file():
                files.append(p)
            for dirpath, dirnames, names in os.walk(p):
                dirnames[:] = [d for d in dirnames if d != "raw"]
                files += [Path(dirpath) / n for n in names]
    return [f for f in files if f.is_file() and f.suffix.lower() not in BINARY and "data/raw" not in f.as_posix()]


def test_denylist_is_hashes_only():
    entries = denylist()
    assert len(entries) > 100
    assert all(len(h) == 64 and all(c in "0123456789abcdef" for c in h) for h in entries)


def test_no_real_names_in_committed_text():
    deny = denylist()
    files = committed_text_files()
    assert any(f.name == "README.md" for f in files) and any("data/samples" in f.as_posix() for f in files)
    leaks = []
    for f in files:
        rel = f.relative_to(ROOT).as_posix()
        allowed = {sha(a) for a in ALLOWED.get(rel, set())}
        for gram in set(ngrams(tokens(f.read_text(encoding="utf-8", errors="ignore")))):
            h = sha(gram)
            if h in deny and h not in allowed:
                leaks.append((rel, h[:12]))  # report the hash, not the name
    assert leaks == [], f"real-name n-grams found (file, hash prefix): {leaks[:10]}"


def test_branding_allowed_only_in_readme(tmp_path):
    """The README allowance does not carry over to other files."""
    deny = denylist()
    assert sha("premier league") in deny
    assert not (ALLOWED.keys() - {"README.md"})
