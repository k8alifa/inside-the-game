"""Name registry: every club and player name the checkers need to recognize.

Built from the league in sim/teams.py (read only). For each club it holds the
full name and short name; for each player the full name, first name and
surname. It also holds an Arabic transliteration map. The translator is told
to use exactly these spellings, and the translation checker verifies them.

Arabic is the only non-Latin language with a map today. For Japanese and
Hindi, names in translations are not checked; numbers still are.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..sim.teams import TEAMS

# Arabic spellings. Consistency matters more than any single "correct"
# spelling: the translator is given this map and must use it.
AR_FIRST = {
    "Alex": "أليكس", "Ben": "بن", "Cal": "كال", "Dario": "داريو", "Eli": "إيلي", "Finn": "فين",
    "Gabe": "غيب", "Hugo": "هوغو", "Ivo": "إيفو", "Jonah": "جونا", "Kofi": "كوفي", "Leon": "ليون",
    "Mateo": "ماتيو", "Nico": "نيكو", "Omar": "عمر", "Pavel": "بافيل", "Rafi": "رافي", "Sami": "سامي",
    "Theo": "ثيو", "Yusuf": "يوسف", "Zane": "زين", "Luca": "لوكا", "Adam": "آدم", "Tariq": "طارق",
    "Emre": "إمري", "Joel": "جويل", "Kai": "كاي", "Milo": "ميلو",
}
AR_LAST = {
    "Ashwell": "آشويل", "Brennick": "برينيك", "Calloway": "كالواي", "Dunmore": "دنمور",
    "Elstead": "إلستيد", "Farrow": "فارو", "Galbraith": "غالبريث", "Holloway": "هولواي",
    "Ingram": "إنغرام", "Jessop": "جيسوب", "Kirkwood": "كيركوود", "Lindqvist": "ليندكفيست",
    "Marlow": "مارلو", "Northcott": "نورثكوت", "Okonjo": "أوكونجو", "Pemberton": "بيمبرتون",
    "Quill": "كويل", "Radley": "رادلي", "Sutcombe": "ساتكوم", "Thorne": "ثورن",
    "Upworth": "أبوورث", "Varga": "فارغا", "Whitlock": "ويتلوك", "Yardley": "ياردلي",
    "Zeller": "زيلر", "Abara": "أبارا", "Benhali": "بنهالي", "Castellan": "كاستيلان",
    "Delmonte": "ديلمونتي", "Esterly": "إسترلي", "Fenwick": "فينويك", "Haldane": "هالدين",
    "Amberley": "أمبرلي", "Blackmore": "بلاكمور", "Corriden": "كوريدن", "Dovecote": "دوفكوت",
    "Ebbsworth": "إبسورث", "Fairbank": "فيربانك", "Gorsuch": "غورساتش", "Hartwell": "هارتويل",
    "Isleworth": "آيلزورث", "Jardine": "جاردين", "Kettering": "كيترينغ", "Langstone": "لانغستون",
    "Merrow": "ميرو", "Nethercott": "نيذركوت", "Oakhurst": "أوكهيرست", "Pellow": "بيلو",
    "Rosslyn": "روسلين", "Selbourne": "سيلبورن", "Tamworth": "تامورث", "Underhill": "أندرهيل",
    "Vossberg": "فوسبيرغ", "Wexley": "ويكسلي", "Ackroyd": "أكرويد", "Bramhall": "برامهول",
    "Cardew": "كارديو", "Dellacourt": "ديلاكورت", "Eskdale": "إسكديل", "Fontaine": "فونتين",
    "Greystoke": "غريستوك", "Hambleton": "هامبلتون", "Ivers": "آيفرز", "Kessling": "كيسلينغ",
    "Lowther": "لاوذر", "Moorcroft": "موركروفت", "Nakagawa": "ناكاغاوا", "Ostrander": "أوستراندر",
    "Penhallow": "بنهالو", "Rinaldi": "رينالدي", "Stavros": "ستافروس", "Talbeck": "تالبيك",
}
AR_CLUB_SHORT = {
    "Northbridge": "نورثبريدج", "Harbor City": "هاربور سيتي",
    "Elmstead": "إلمستيد", "Kingsford": "كينغسفورد",
}
AR_CLUB_FULL = {
    "Northbridge Athletic": "نورثبريدج أثلتيك", "Harbor City FC": "هاربور سيتي إف سي",
    "Elmstead Rovers": "إلمستيد روفرز", "Kingsford Town": "كينغسفورد تاون",
}
TRANSLITERATIONS = {"ar": {**AR_FIRST, **AR_LAST, **AR_CLUB_SHORT, **AR_CLUB_FULL}}

_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ـ": None})


def normalize_script(text: str, lang: str) -> str:
    """Fold spelling variants so near-identical transliterations still match."""
    if lang == "ar":
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        return text.translate(_AR_FOLD)
    return text


@dataclass(frozen=True)
class PlayerName:
    id: str
    team: str
    full: str
    first: str
    last: str


@dataclass
class NameRegistry:
    players: list[PlayerName] = field(default_factory=list)
    clubs: dict[str, tuple[str, ...]] = field(default_factory=dict)  # team id -> aliases, longest first

    @classmethod
    def league(cls) -> "NameRegistry":
        reg = cls()
        for t in TEAMS.values():
            aliases = {t.name, t.short}
            reg.clubs[t.id] = tuple(sorted(aliases, key=len, reverse=True))
            for p in t.players:
                first, last = p.name.split(" ", 1)
                reg.players.append(PlayerName(p.id, t.id, p.name, first, last))
        return reg

    def club_aliases(self, team_id: str) -> tuple[str, ...]:
        return self.clubs.get(team_id, ())

    def translit(self, name: str, lang: str) -> str | None:
        return TRANSLITERATIONS.get(lang, {}).get(name)

    def glossary(self, text: str, lang: str) -> dict[str, str]:
        """Names found in an English text, with their spelling in `lang`."""
        table = TRANSLITERATIONS.get(lang)
        if not table:
            return {}
        found = {}
        for name in sorted(table, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", text):
                found[name] = table[name]
        return found


LEAGUE = NameRegistry.league()
