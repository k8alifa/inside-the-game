"""itg-bake --web writes every overlay file, deterministically in template mode."""

import filecmp
import os
import re
from pathlib import Path

from insidethegame.agents.bake import main

ROOT = Path(__file__).parent.parent
SAMPLE = ROOT / "data" / "samples" / "NBA-HCF-7"
EXPECTED = ["manifest.json", "timeline.json", "stats.json", "facts.json",
            "overlay.analyst.json", "overlay.casual.json",
            "overlay.analyst.NBA.json", "overlay.analyst.HCF.json",
            "overlay.casual.NBA.json", "overlay.casual.HCF.json",
            "recap.analyst.json", "recap.casual.json"]


def run(tmp: Path, name: str) -> Path:
    env_before = os.environ.pop("ITG_MODEL_PROVIDER", None)
    try:
        assert main(["--match", str(SAMPLE), "--out", str(tmp / name / "baked"), "--web", str(tmp / name / "web")]) == 0
    finally:
        if env_before is not None:
            os.environ["ITG_MODEL_PROVIDER"] = env_before
    return tmp / name / "web"


def test_web_bundle_has_every_file(tmp_path):
    web = run(tmp_path, "a")
    out = web / "NBA-HCF-7"
    for f in EXPECTED:
        assert (out / f).is_file(), f
    players = sorted((out / "players").glob("*.json"))
    assert 6 <= len(players) <= 8
    assert (web / "index.json").is_file()


def test_template_bundle_is_byte_identical_across_runs(tmp_path):
    a, b = run(tmp_path, "a"), run(tmp_path, "b")
    cmp = filecmp.dircmp(a, b)
    def walk(c):
        assert not c.left_only and not c.right_only and not c.diff_files, (c.left, c.diff_files)
        for sub in c.subdirs.values():
            walk(sub)
    walk(cmp)


def test_committed_web_bundle_is_current(tmp_path):
    """web/data in the repo equals a fresh template bake (re-run itg-bake --web if this fails)."""
    fresh = run(tmp_path, "fresh") / "NBA-HCF-7"
    committed = ROOT / "web" / "data" / "NBA-HCF-7"
    for f in sorted(fresh.rglob("*.json")):
        rel = f.relative_to(fresh)
        assert (committed / rel).read_bytes() == f.read_bytes(), rel


def test_web_code_uses_relative_urls_only():
    for js in (ROOT / "web").glob("*.js"):
        src = js.read_text()
        for url in re.findall(r"""fetch\(\s*[`'"]([^`'"]+)""", src):
            assert not re.match(r"^(https?:)?//|^/", url), (js.name, url)
        assert "http://" not in src and "https://" not in src, js.name
