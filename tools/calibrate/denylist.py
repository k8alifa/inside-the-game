"""Build a hashed denylist of real names from the local raw files, for the always-on leak test.

    python -m tools.calibrate.denylist --raw data/raw --out tests/fixtures/denylist.sha256

What goes in, after normalizing (lower case, accents and punctuation removed):
  - club names and short forms (without FC/AFC, and the first word when it is distinctive)
  - scorer full names and surnames
  - the competition branding phrase from the file headers
Only SHA-256 hashes are written, never the names. The test in
tests/test_denylist.py hashes every 1-, 2- and 3-word sequence of every
committed text file under data/, docs/, web/ and README.md and fails on a match.

Left out on purpose (would match ordinary text, not a real name):
  - words of 3 letters or fewer, and the common English words in COMMON_WORDS
  - any word that is also an invented name in sim/teams.py (logged when it happens)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import unicodedata
from pathlib import Path

from .targets import read_results

COMMON_WORDS = {
    "white", "black", "brown", "green", "gray", "grey", "young", "king", "wood", "hall", "hill", "long",
    "little", "ward", "cash", "rice", "mount", "burn", "page", "bird", "rose", "will", "case", "mark",
    "james", "john", "cook", "baker", "archer", "fox", "lamb", "wolf", "hunt", "mills", "rivers", "bell",
    "bond", "banks", "ball", "knight", "march", "sharp", "strong", "wise", "hope", "love", "best", "field",
    "lane", "park", "west", "north", "south", "east", "city", "town", "united", "athletic", "rovers",
    "wanderers", "albion", "county", "forest", "palace", "villa", "crystal", "aston", "hotspur", "sporting",
    "real", "inter", "dean", "gordon", "nelson", "jackson", "wilson", "taylor", "palmer", "porter",
    "carter", "turner", "walker", "parker", "cooper", "fletcher", "mason", "morgan", "lewis", "harrison",
    "johnson", "thomas", "roberts", "evans", "hughes", "price", "summer", "winter", "money", "goal",
    "goals", "silva", "santos", "costa", "pedro", "lucas", "martin", "simon", "joseph", "daniel",
    "jordan", "kelly", "murphy", "barnes", "elliott", "adams", "smith", "jones", "davies", "edwards",
    "home", "away", "win", "draw", "league", "english", "premier", "england",
}
GENERIC_FIRST_WORDS = {"west", "crystal", "aston", "newcastle", "manchester", "nottingham", "sheffield",
                       "brighton", "leeds", "luton"}  # first words that are places or generic; full names still listed


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def tokens(text: str) -> list[str]:
    return normalize(text).split()


def ngrams(toks: list[str], max_n: int = 3):
    for n in range(1, max_n + 1):
        for i in range(len(toks) - n + 1):
            yield " ".join(toks[i: i + n])


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _scorer_names(raw: Path) -> set[str]:
    names = set()
    for f in sorted(raw.glob("*.txt")):
        buf, open_paren = [], False
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("(") or open_paren:
                buf.append(s)
                open_paren = s.count("(") > s.count(")") if s.startswith("(") else ")" not in s
        for piece in re.split(r"[,;]", " ".join(buf)):
            name = re.sub(r"\d.*$", "", piece.replace("(", " ").replace(")", " ")).strip()
            if re.search(r"[A-Za-z]", name):
                names.add(name)
    return names


def _branding(raw: Path) -> set[str]:
    out = set()
    for f in sorted(raw.glob("*.txt")):
        first = f.read_text(encoding="utf-8").splitlines()[:1]
        if first and first[0].startswith("="):
            words = [w for w in tokens(first[0]) if not w.isdigit() and not re.match(r"\d", w)]
            words = [w for w in words if w not in ("england",)]
            if len(words) >= 2:
                out.add(" ".join(words[-2:]))   # e.g. the competition's two-word name
                out.add(" ".join(words))
    return out


def invented_tokens() -> set[str]:
    from insidethegame.sim.teams import TEAMS

    out = set()
    for t in TEAMS.values():
        out |= set(tokens(t.name)) | set(tokens(t.short))
        for p in t.players:
            out |= set(tokens(p.name))
    return out


def build_entries(raw_dir: str | Path, log=print) -> set[str]:
    raw = Path(raw_dir)
    invented = invented_tokens()
    entries: set[str] = set()

    def add(phrase: str):
        norm = normalize(phrase)
        toks = norm.split()
        if not toks:
            return
        if len(toks) == 1:
            w = toks[0]
            if len(w) <= 3 or w in COMMON_WORDS:
                return
            if w in invented:
                log(f"skipped (also an invented name): {w}")
                return
        entries.add(norm)

    for club in {r.home for r in read_results(raw)} | {r.away for r in read_results(raw)}:
        add(club)
        bare = re.sub(r"^(afc|fc)\s+|\s+(afc|fc)$", "", normalize(club))
        add(bare)
        first = bare.split()[0] if bare else ""
        if first and first not in GENERIC_FIRST_WORDS:
            add(first)
    for name in _scorer_names(raw):
        add(name)
        add(name.split()[-1])
    for phrase in _branding(raw):
        add(phrase)
    return entries


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hashed denylist of real names for the leak test")
    p.add_argument("--raw", default="data/raw")
    p.add_argument("--out", default="tests/fixtures/denylist.sha256")
    args = p.parse_args(argv)
    entries = build_entries(args.raw)
    hashes = sorted({sha(e) for e in entries})
    header = ("# SHA-256 of normalized real names (clubs, short forms, scorers, competition branding).\n"
              "# Generated by tools/calibrate/denylist.py from local raw files; names are never stored.\n"
              f"# entries: {len(hashes)}\n")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(header + "\n".join(hashes) + "\n")
    print(f"{len(hashes)} hashes -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
