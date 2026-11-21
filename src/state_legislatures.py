"""
state_legislatures.py — Parse U.S. **state legislature** election results
(state senates + state houses/assemblies) from Wikipedia chamber articles,
fetched via the MediaWiki Action API.

For every year in [start_year, end_year] the overview article
``{year} United States state legislative elections`` is fetched and its
per-state ``{{main|<year> X Senate election|<year> X ... House election}}``
links are followed to discover every chamber article (regular cycles only;
special elections appear inside the same articles). Wikitext is then
retrieved in rate-limited batches of <= 50 titles.

Even years cover all states. Four states elect their legislature in ODD
years — New Jersey and Virginia (2-year House cycles), Louisiana and
Mississippi (4-year terms offset from the federal calendar) — so odd years
are processed for ODD_YEAR_STATES only (New Jersey seats its Senate under a
2-4-4 term system, so some odd cycles legitimately have no Senate race).
Nebraska's nonpartisan unicameral body is discovered from its
``{year} Nebraska Legislature election`` articles and emitted as chamber
"State Senate" with an empty party column.

Chamber-article format families handled:

* ``===District N===`` sections holding ``{{Election box ...}}`` templates
  (the vast majority),
* articles without district headers whose boxes carry the district inside
  the box ``|title=`` (e.g. ``[[California's 1st State Assembly district]]
  election, 2018`` or ``Vermont Senate Addison district general election``),
* top-two "open primary" boxes (Washington / California / Nebraska):
  ``{{Election box open primary begin}}`` holds the August primary rows,
  then the ``{{Election box open primary general election}}`` divider
  switches the box to its November general-election rows — only the rows
  after the divider are captured,
* Pennsylvania-style "inline" boxes: one ``{{Election box inline begin}}``
  wrapper with per-district ``{{Election box inline incumbent|race=...}}``
  headers followed by ``{{Election box inline candidate ...}}`` rows,
* ordinal-first district headers (``=== 140th legislative district ===``,
  Pennsylvania) and named-district headers inside a "Detailed Results"/
  "Results summary" zone (``===1st Barnstable===`` / ``=== 1st Bristol and
  Plymouth District ===``, Massachusetts),
* articles without election boxes at all: ``District | Party | Candidates |
  Votes | %`` summary tables (Minnesota) and plainlist candidate cells in
  both ``{{Plainlist|* item}}`` and ``{{plainlist}}…{{endplainlist}}``
  forms, including ``*{{Party stripe|...}}{{Aye}} '''[[Name]]''' (Party)
  56.62%`` items and Primary/General or First-round/Runoff round markers
  (Mississippi / Nebraska summary tables).

Row templates handled: winning candidate / candidate (with or without party
link), total, hold/gain/majority/swing (skipped). Party names such as
"Arizona Democratic Party" are normalised to "Democratic"; percentages given
as ``{{percentage|a|{{sum|a|b}}|2}}`` are computed from the vote counts.

Outputs (written under *output_dir*, default ``data/``):
    state_senate/state_senate_results_{year}.csv + _all.csv
    state_house/state_house_results_{year}.csv   + _all.csv

The combined _all.csv files are rebuilt from every per-year file on disk
after each run, so partial re-runs stay consistent.

Columns: year, state, state_code, chamber, district, race, candidate,
party, votes, percentage, winner, incumbent

Usage:
    python cli.py state-leg --start-year 2018 --end-year 2024
    python state_legislatures.py
"""

from __future__ import annotations

import glob
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

from wiki_utils import (
    WikiAPIClient,
    extract_incumbent_flag,
    get_default_client,
)
from house_elections import STATE_NAME_TO_CODE, _state_code

logger = logging.getLogger("state_legislatures")

# ────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ────────────────────────────────────────────────────────────────────────────

OVERVIEW_PAT = "{y} United States state legislative elections"

_CHAMBER_WORDS = (
    r"(?:State\s+)?(?:Senate|Senatorial|House\s+of\s+Representatives|"
    r"General\s+Assembly|State\s+Assembly|Assembly|House\s+of\s+Delegates|"
    r"State\s+House(?:\s+of\s+Representatives)?|Legislature|House)"
)

_COLUMNS = [
    "year", "state", "state_code", "chamber", "district", "race",
    "candidate", "party", "votes", "percentage", "winner", "incumbent",
]

_SKIP_ROW_KINDS = ("total", "hold", "gain", "majority", "swing", "elector")

_DISTRICT_HEADER_RE = re.compile(
    r"(?m)^={2,5}\s*District\s*([0-9]{1,3}[A-Za-z]?)[^=\n]*?={2,5}\s*$", re.I
)
# County-prefixed districts, e.g. '====Belknap 1====' (New Hampshire House):
# the whole '<name> <number>' token becomes the district so that repeated
# numbers across counties stay distinct.
_COUNTY_DISTRICT_HEADER_RE = re.compile(
    r"(?m)^={2,5}\s*(?!district\b)([A-Za-z][A-Za-z .'-]*?)\s+([0-9]{1,3}[A-Za-z]?)\s*=+\s*$",
    re.I,
)
# hyphenated county-number districts, e.g. '===Addison-1===' /
# '===Chittenden-6-7===' (Vermont House): the whole '<name>-<number>'
# token becomes the district (same token style as the article's own box
# titles, e.g. 'ORLEANS-1').
_HYPHEN_DISTRICT_HEADER_RE = re.compile(
    r"(?m)^={2,5}\s*([A-Za-z][A-Za-z .']*?-[0-9]{1,3}(?:-[0-9]{1,3})?)\s*=+\s*$",
    re.I,
)
_BOX_START_RE = re.compile(r"\{\{\s*[Ee]lection box[- ]", re.I)

# Odd-year legislative cycles: these four states elect their legislature in
# odd-numbered years (NJ Assembly 2-year terms + 2-4-4 Senate terms, VA
# House 2-year terms vs 4-year Senate terms, LA/MS 4-year terms offset from
# the federal calendar).  All other states are even-year only.
ODD_YEAR_STATES = frozenset({"NJ", "VA", "LA", "MS"})

# '=== 140th legislative district ===' / '=== 5th senatorial district ==='
# (Pennsylvania style: ordinal number BEFORE the word 'district').
_ORDINAL_DISTRICT_HEADER_RE = re.compile(
    r"(?m)^={2,5}\s*(\d{1,3})(?:st|nd|rd|th)?\s+"
    r"(?:legislative|senatorial|senate|assembly|house)?\s*district\s*=+\s*$",
    re.I,
)
# '===1st Barnstable===' / '=== Norfolk, Plymouth and Bristol ===' /
# '=== 1st Bristol and Plymouth District ===' (Massachusetts style: named
# districts, optionally ordinal-prefixed, optionally suffixed 'District').
# Only trusted inside a detailed-results zone (see _ZONE_MARKER_RE).
_NAMED_DISTRICT_HEADER_RE = re.compile(
    r"(?m)^={3,5}\s*((?:\d{1,3}(?:st|nd|rd|th)\s+)?[A-Za-z][A-Za-z ,'\u2019-]*?)"
    r"(?:\s+district)?\s*=+\s*$",
    re.I,
)
# level-2 headings that open a per-district results section
_ZONE_MARKER_RE = re.compile(
    r"(?m)^==(?!==)[^=\n]*(?:detailed (?:election )?results|results summary"
    r"|summary of results|results by district|results by .* district)"
    r"[^=\n]*==\s*$",
    re.I,
)
_ZONE_HEADING_RE = re.compile(r"(?m)^==(?!=)[^=\n]*==\s*$")

# generic headings that must never become named-district tokens
_NAMED_HEADER_EXCLUDE = {
    "election", "elections", "overview", "predictions", "see also",
    "references", "background", "notes", "external links", "candidates",
    "candidate", "district", "districts", "results", "detailed results",
    "summary", "close races", "closest races", "retirements",
    "special elections", "composition", "polls", "endorsements", "debates",
    "finances", "fundraising", "charts", "general election",
    "primary election", "primary", "general", "runoff", "redistricting",
    "democrats", "republicans", "independents", "competitive", "statewide",
    "by district", "withdrawals", "resignations", "deaths", "incumbents",
    "outgoing incumbents", "incoming incumbents", "new members",
}

# narrative rows leaking into election boxes (Nebraska 'Retirements' boxes:
# candidate = 'Incumbent term-limited. Democratic gain.').  The incumbent/
# retired words are anchored to the value start so real candidates written
# as '[[Name]] (incumbent' (unclosed paren, KS style) are not killed.
_BOX_JUNK_CAND_RE = re.compile(
    r"^\s*incumbent\b|term.?limited|^\s*retired\b|lost re.?election"
    r"|(?:democratic|republican|independent)\s+(?:gain|hold|loss)",
    re.I,
)

# ────────────────────────────────────────────────────────────────────────────
# SMALL TEXT HELPERS
# ────────────────────────────────────────────────────────────────────────────


def _strip_refs(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>", "", text)
    return re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)


def _clean_name(raw: str) -> str:
    """Wikitext candidate value -> plain name."""
    name = raw or ""
    name = re.sub(r"<ref[^>]*/>", "", name)
    name = re.sub(r"<ref[^>]*>.*?</ref>", "", name, flags=re.S | re.I)
    name = re.sub(r"\{\{(?:efn|notelist)[^{}]*(?:\{\{[^{}]*\}\}[^{}]*)*\}\}", "", name, flags=re.I)
    name = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", name)
    name = re.sub(r"'''", "", name)
    name = re.sub(r"''", "", name)
    name = re.sub(r"\{\{sortname\|([^|}]+)\|([^|}]+)[^}]*\}\}",
                  lambda m: f"{m.group(1).strip()} {m.group(2).strip()}", name, flags=re.I)
    # named-parameter form: {{Sort name|last=Harkins|first=Pat}} -> 'Pat Harkins'
    def _sort_named(m):
        body = m.group(1)
        last = re.search(r"last\s*=\s*([^|}]+)", body, re.I)
        first = re.search(r"first\s*=\s*([^|}]+)", body, re.I)
        if last and first:
            return f"{first.group(1).strip()} {last.group(1).strip()}"
        return body.strip()
    name = re.sub(r"\{\{sort\s*name\|([^}]*)\}\}", _sort_named, name, flags=re.I)
    name = re.sub(r"\{\{[^{}]*\}\}", "", name)  # leftover templates
    name = name.replace("&nbsp;", " ")
    name = re.sub(r"<[^>]+>", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ,;")
    return name


def _eval_number(raw: str) -> Optional[int]:
    """votes field: '60,586' / {{sum|60586|6920}} / '''71,017''' -> int."""
    if raw is None:
        return None
    t = raw.strip()
    m = re.search(r"\{\{\s*sum\s*\|([^}]*)\}\}", t, re.I)
    if m:
        total = 0
        ok = False
        for part in m.group(1).split("|"):
            digits = re.sub(r"[^\d]", "", part)
            if digits:
                total += int(digits)
                ok = True
        return total if ok else None
    digits = re.sub(r"[^\d]", "", t)
    return int(digits) if digits else None


def _digits(s: str) -> str:
    return re.sub(r"[^\d]", "", s or "")


def _eval_percentage(raw: str, votes: Optional[int]) -> Optional[float]:
    """percentage field: '48.5', '48.5%', \"'''{{percentage|a|{{sum|..}}|2}}'''\"
    -> float.  The ``{{percentage ...}}`` template is located anywhere in the
    value (PA inline boxes bold-wrap it); a bare-number fallback must not
    grab a prefix of a longer number (vote counts)."""
    if raw is None:
        return None
    t = raw.strip().replace("'''", "").replace("''", "").strip()
    m = re.search(r"\{\{\s*percentage\s*\|", t, re.I)
    if m:
        # balanced-scan the {{percentage ...}} template
        depth, j, end = 0, m.start(), None
        while j < len(t) - 1:
            if t.startswith("{{", j):
                depth += 1
                j += 2
            elif t.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    end = j
                    break
            else:
                j += 1
        if end is not None:
            inner = t[m.start() + 2:end - 2]
            args = _split_top_level(inner)[1:]
            num = _digits(args[0]) if args else ""
            den = 0
            if len(args) > 1:
                da = args[1].strip()
                if da.lower().startswith("{{sum"):
                    d_inner = da[2:-2] if da.endswith("}}") else da[2:]
                    sum_args = _split_top_level(d_inner)[1:]
                    den = sum(int(_digits(a) or 0) for a in sum_args)
                else:
                    den = int(_digits(da) or 0)
            if num and den:
                return round(int(num) / den * 100, 2)
            return None
    mm = re.search(r"(?<!\d)(\d{1,3}(?:\.\d+)?)\s*%?(?!\d)", t)
    if mm:
        return float(mm.group(1))
    return None


def normalize_party(raw: str) -> str:
    """'Arizona Democratic Party' / 'Republican Party (US)' -> short label."""
    p = _clean_name(raw or "")
    if not p:
        return ""
    key = re.sub(r"\s+", " ", p).strip().lower()
    key = re.sub(r"\s*\((?:us|united states|politician)\)\s*$", "", key)
    key = re.sub(r"[\u2013\u2014]", "-", key)   # en/em dashes -> hyphen
    key = re.sub(r"\s+(?:state\s+)?committee$", "", key)   # 'NY State Democratic Committee'
    key = re.sub(r"\s+of\s+[a-z '.-]+$", "", key)          # ... Party of New York State
    # multi-word party names that must not lose their first word
    bare = key[:-6] if key.endswith(" party") else key
    if bare in {"working families", "independent american", "legal marijuana",
                "democratic-npl", "democratic npl", "united utah", "independent politician",
                "nonpartisan politician", "american independent", "people's party"}:
        pass
    else:
        key = re.sub(r"^[a-z-]+ ([a-z-]+ )?(?=[a-z-]+ (?:party|committee)$)", "", key)  # state prefix
    key = re.sub(r"\s+party$", "", key).strip()
    key = re.sub(r"\s+(?:state\s+)?committee$", "", key).strip()
    # explicit map after stripping
    _MAP = {
        "democratic": "Democratic", "republican": "Republican",
        "libertarian": "Libertarian", "green": "Green",
        "constitution": "Constitution", "independent": "Independent",
        "conservative": "Conservative", "progressive": "Progressive",
        "independence": "Independence", "legal marijuana": "Legal Marijuana",
        "no party preference": "No party preference", "nonpartisan": "Nonpartisan",
        "working families": "Working Families", "union": "Union",
        "democratic-npl": "Democratic-NPL", "democratic npl": "Democratic-NPL",
        "dfl": "Democratic-Farmer-Labor", "democratic-farmer-labor": "Democratic-Farmer-Labor",
        "socialist": "Socialist", "write-in": "Write-in",
        "independent american": "Independent American",
        "independent politician": "Independent",
        "new york state democratic": "Democratic",
        "nonpartisan politician": "Nonpartisan", "nonpartisan": "Nonpartisan",
        "n/a": "", "na": "",
        "patriot": "Patriot", "reform": "Reform",
        "freedom": "Freedom", "people's": "People's",
        "natural law": "Natural Law", "american delta": "American Delta",
        "vermont progressive": "Progressive", "progressive (vermont)": "Progressive",
    }
    if key in _MAP:
        return _MAP[key]
    if not key:
        return ""
    return " ".join(w if w in ("of", "the") else w.capitalize() for w in key.split())


# ────────────────────────────────────────────────────────────────────────────
# ELECTION-BOX SCANNER
# ────────────────────────────────────────────────────────────────────────────

def _iter_box_templates(text: str) -> List[Tuple[str, str, int]]:
    """Yield (kind, full_template, start_pos) for every top-level
    ``{{Election box ...}}`` template, handling nested ``{{...}}`` inside
    parameter values (and optional whitespace after the opening braces,
    e.g. ``{{ Election box inline candidate ...}}``)."""
    out: List[Tuple[str, str, int]] = []
    n = len(text)
    for m in re.finditer(r"\{\{\s*[Ee]lection\s+box", text):
        idx = m.start()
        # walk forward with brace-depth counting
        depth = 0
        j = idx
        end = -1
        while j < n - 1:
            if text.startswith("{{", j):
                depth += 1
                j += 2
            elif text.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    end = j
                    break
            else:
                j += 1
        if end == -1:
            continue
        template = text[idx:end]
        inner = template[2:-2]
        kind = re.match(r"\s*[Ee]lection\s+box ([a-zA-Z ]+)", inner)
        out.append((kind.group(1).strip().lower() if kind else "", template, idx))
    return out


def _split_top_level(s: str) -> List[str]:
    """Split on '|' only at template top level (ignoring pipes inside
    ``{{...}}`` templates and ``[[...|...]]`` wikilinks)."""
    parts: List[str] = []
    cur: List[str] = []
    depth = 0     # {{ }} nesting
    bracket = 0   # [[ ]] nesting
    i, n = 0, len(s)
    while i < n:
        two = s[i:i + 2]
        if two == "{{":
            depth += 1
            cur.append("{{")
            i += 2
            continue
        if two == "}}":
            depth = max(0, depth - 1)
            cur.append("}}")
            i += 2
            continue
        if two == "[[":
            bracket += 1
            cur.append("[[")
            i += 2
            continue
        if two == "]]":
            bracket = max(0, bracket - 1)
            cur.append("]]")
            i += 2
            continue
        ch = s[i]
        if ch == "|" and depth == 0 and bracket == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _template_params(template: str) -> Dict[str, str]:
    """Split a template body into top-level named parameters."""
    inner = template.strip()
    if inner.startswith("{{"):
        inner = inner[2:]
    if inner.endswith("}}"):
        inner = inner[:-2]
    parts = _split_top_level(inner)
    params: Dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip().lower()] = v.strip()
    return params


# ────────────────────────────────────────────────────────────────────────────
# CHAMBER ARTICLE PARSER
# ────────────────────────────────────────────────────────────────────────────

def _state_from_title(title: str) -> Tuple[str, str]:
    """'2018 California State Assembly election' -> ('California', 'CA')."""
    t = title.strip()
    t = re.sub(r"^\d{4}\s+", "", t)
    t = re.sub(r"\s+elections?\s*$", "", t, flags=re.I)
    t = re.sub(rf"\s*{_CHAMBER_WORDS}\s*$", "", t, flags=re.I).strip()
    t = re.sub(r"\s+", " ", t)
    if t.lower().startswith("the "):
        t = t[4:]
    return t, _state_code(t)


def _race_from_title(title: str) -> str:
    t = (title or "").lower()
    special = "special" in t
    if "convention" in t:
        return "convention"
    if "primary" in t:
        return "special primary" if special else "primary"
    if "general" in t or special:
        return "special general" if special else "general"
    return "general"


def _district_from_box_title(title: str) -> str:
    """Extract the district from an election-box title.  Known styles:

    * ``[[California's 1st State Assembly district]] election, 2018`` -> '1'
    * ``Vermont House district Addison-1 election, 2018``            -> 'ADDISON-1'
    * ``2020 Kentucky Senate 38th district special election``         -> '38'
    * ``[[...District 19|District 19]] special election``             -> '19'
    * ``Vermont Senate Addison district general election``            -> 'ADDISON'
    """
    t = title or ""
    m = re.search(
        rf"\[\[[^|\]]*?\s(\d{{1,3}}(?:st|nd|rd|th|[A-Za-z])?)\s+(?:State\s+)?{_CHAMBER_WORDS}\s+district",
        t, re.I,
    )
    if m:
        return re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", m.group(1), flags=re.I)
    # <chamber> 38th district  (no wikilink)
    m = re.search(
        rf"(?:{_CHAMBER_WORDS})\s+(\d{{1,3}}(?:st|nd|rd|th)?)\s+district",
        t, re.I,
    )
    if m:
        return re.sub(r"(?<=\d)(?:st|nd|rd|th)$", "", m.group(1), flags=re.I)
    # named district AFTER the word 'district' (Vermont style)
    m = re.search(r"\bdistrict\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*?)\s+(?:special\s+)?(?:general\s+)?election", t)
    if m:
        return m.group(1).upper()
    # named district BEFORE the word 'district'
    m = re.search(r"\b([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*?)\s+district\b", t)
    if m:
        name = m.group(1).strip()
        if name.lower() not in ("state", "senate", "assembly", "house", "election"):
            return name.upper()
    m = re.search(r"\b(?:District)\s*(\d{1,3}[A-Za-z]?)\b", t, re.I)
    return m.group(1) if m else ""


def parse_chamber(text: str, year: int, state: str, chamber: str) -> pd.DataFrame:
    """Parse one chamber article into per-candidate result rows."""
    text = _strip_refs(text)

    # detailed-results / results-summary zones: named-district headings
    # (Massachusetts style) are only trusted inside these
    zones: List[Tuple[int, int]] = []
    for m in _ZONE_MARKER_RE.finditer(text):
        nxt = _ZONE_HEADING_RE.search(text, m.end())
        zones.append((m.start(), nxt.start() if nxt else len(text)))

    def _in_zone(pos: int) -> bool:
        return any(a <= pos < b for a, b in zones)

    # district section headers -> offset-sorted (pos, token) list
    numeric_sections: List[Tuple[int, str]] = [
        (m.start(), m.group(1).upper()) for m in _DISTRICT_HEADER_RE.finditer(text)
    ]
    numeric_sections += [
        (m.start(), f"{m.group(1)} {m.group(2)}".upper())
        for m in _COUNTY_DISTRICT_HEADER_RE.finditer(text)
    ]
    numeric_sections += [
        (m.start(), m.group(1).upper())
        for m in _HYPHEN_DISTRICT_HEADER_RE.finditer(text)
    ]
    # ordinal-first headers: '=== 140th legislative district ===' (PA)
    numeric_sections += [
        (m.start(), m.group(1)) for m in _ORDINAL_DISTRICT_HEADER_RE.finditer(text)
    ]
    sections = list(numeric_sections)
    # named-district headers (MA): only inside results zones, never the
    # generic section headings listed in _NAMED_HEADER_EXCLUDE
    for m in _NAMED_DISTRICT_HEADER_RE.finditer(text):
        name = m.group(1).strip()
        low = name.lower()
        if low in _NAMED_HEADER_EXCLUDE or re.search(
            r"\b(?:results|summary|primary|party|election)\b", low
        ):
            continue
        if not _in_zone(m.start()):
            continue
        sections.append((m.start(), name.upper()))
    sections.sort(key=lambda t: t[0])

    def section_district_at(pos: int) -> str:
        cur = ""
        for start, token in sections:
            if start < pos:
                cur = token
            else:
                break
        return cur

    stream = _iter_box_templates(text)
    rows: List[Dict] = []

    # articles with >= 2 numeric district headers: boxes outside them are
    # treated cautiously (named/ordinal sections do not trigger strictness)
    strict_sections = len(numeric_sections) >= 2

    i = 0
    while i < len(stream):
        kind, template, pos = stream[i]
        if "inline" in kind or "begin" not in kind:
            i += 1
            continue

        box_title = _template_params(template).get("title", "")
        # top-two 'open primary' boxes (WA/CA/NE): the box holds the August
        # primary rows first; the '{{Election box open primary general
        # election}}' divider switches it to its November general rows
        top_two = "open primary begin" in kind
        race = "primary" if top_two else _race_from_box(box_title, kind)

        # collect row templates until the box's {{Election box end}}
        j = i + 1
        box_rows: List[Tuple[str, Dict[str, str]]] = []
        gen_start: Optional[int] = None
        while j < len(stream):
            k2, t2, _p2 = stream[j]
            if "begin" in k2:
                break                      # malformed box (no end) — stop early
            if "end" in k2:
                break
            if top_two and "open primary general election" in k2:
                gen_start = len(box_rows)  # divider: general rows start here
                j += 1
                continue
            box_rows.append((k2, _template_params(t2)))
            j += 1

        if top_two:
            if gen_start is not None:      # WA/CA/NE: general rows after divider
                box_rows = box_rows[gen_start:]
                race = "general"
            elif any("winning" in k2 for k2, _t2 in box_rows):
                # LA-style jungle primary: no runoff section, but the box
                # declares a winner — the October primary WAS the decisive
                # (general) round; keep its rows
                race = "general"
            else:                          # primary inconclusive -> runoff held
                i = j + 1 if j < len(stream) and "end" in stream[j][0] else j
                continue

        district = section_district_at(pos)
        if not district:
            district = _district_from_box_title(box_title)
        if not district:
            # no section context AND no district in the title -> not a
            # legislative race (e.g. statewide execs bundled in the article)
            i = j + 1 if j < len(stream) and "end" in stream[j][0] else j
            continue

        rows.extend(
            _rows_from_box(box_rows, race, district, state, chamber, year, box_title)
        )
        i = j + 1 if j < len(stream) and "end" in stream[j][0] else j

    # Pennsylvania-style inline election boxes (one wrapper, per-district
    # incumbent headers followed by inline candidate rows)
    rows.extend(_rows_from_inline_boxes(stream, year, state, chamber))

    df = pd.DataFrame(rows, columns=_COLUMNS)
    if not len(df):
        return df

    # ── post-processing ────────────────────────────────────────────────────
    # 1) cross-party endorsement merge: same candidate on several party lines
    #    (New York style) -> one row, party from the highest-vote line
    agg: Dict[Tuple, Dict] = {}
    order: List[Tuple] = []
    for r in rows:
        key = (r["district"], r["race"], r["candidate"].lower())
        if key not in agg:
            agg[key] = dict(r)
            order.append(key)
        else:
            cur = agg[key]
            cur_votes = cur["votes"] or 0
            new_votes = r["votes"] or 0
            if new_votes > cur_votes:
                cur["party"] = r["party"] or cur["party"]
                cur["percentage"] = r["percentage"] or cur["percentage"]
            cur["votes"] = (cur["votes"] or 0) + (r["votes"] or 0) or None
            cur["winner"] = cur["winner"] or r["winner"]
            cur["incumbent"] = cur["incumbent"] or r["incumbent"]
    rows = [agg[k] for k in order]
    df = pd.DataFrame(rows, columns=_COLUMNS)

    # 2) winner fallback for general boxes with no 'winning' templates:
    #    flag the top vote-getter when a general race shows no winner at all
    grp_all = df.groupby(["district", "race"], dropna=False)["winner"].transform("max")
    needy = (df["race"] == "general") & (~grp_all.astype(bool))
    if needy.any():
        ranked = df[needy].sort_values("votes", ascending=False, na_position="last")
        top = ranked.groupby(["district", "race"], dropna=False, sort=False).head(1).index
        df.loc[top, "winner"] = True
        logger.info("    winner fallback applied to %d general race(s)", len(top))

    return df


def _district_from_inline_race(race_raw: str) -> str:
    """District from an inline-box ``|race=`` value:
    ``[[Pennsylvania House of Representatives, District 1|1]]<ref>...</ref>``
    -> '1' (display text preferred, then the target's District number)."""
    t = re.sub(r"<ref[^>]*/>", "", race_raw or "")
    t = re.sub(r"<ref[^>]*>.*?</ref>", "", t, flags=re.S | re.I)
    t = t.replace("'''", "")
    m = re.search(r"\|\s*(\d{1,3}[A-Za-z]?)\s*\]\]", t)
    if m:
        return m.group(1)
    m = re.search(r"district\s*(\d{1,3}[A-Za-z]?)", t, re.I)
    return m.group(1) if m else ""


def _rows_from_inline_boxes(
    stream: List[Tuple[str, str, int]],
    year: int,
    state: str,
    chamber: str,
) -> List[Dict]:
    """Pennsylvania-style 'inline' election-box family: a single
    ``{{Election box inline begin}}`` wrapper holds, per district, an
    ``{{Election box inline incumbent|race=[[... District 12|12]]|...}}``
    header followed by ``{{Election box inline candidate ...}}`` rows
    (winner = bolded candidate; totals and hold rows are absent)."""
    rows: List[Dict] = []
    district = ""
    for kind, template, _pos in stream:
        if "inline" not in kind:
            continue
        params = _template_params(template)
        if "incumbent" in kind:
            district = _district_from_inline_race(params.get("race", ""))
            continue
        if "candidate" not in kind or not district:
            continue
        if re.search(r"\bwrite.?in\b", kind, re.I):
            continue
        raw_cand = params.get("candidate", "")
        cand, inc = extract_incumbent_flag(_clean_name(raw_cand))
        if not cand or cand.lower().rstrip("s") in _TABLE_JUNK_CANDS:
            continue
        if _BOX_JUNK_CAND_RE.search(cand):
            continue
        party = normalize_party(params.get("party", ""))
        votes = _eval_number(params.get("votes", ""))
        pct = _eval_percentage(params.get("percentage", ""), votes)
        rows.append({
            "year": year, "state": state, "state_code": _state_code(state),
            "chamber": chamber, "district": district, "race": "general",
            "candidate": cand, "party": party, "votes": votes,
            "percentage": pct,
            "winner": "'''" in raw_cand, "incumbent": inc,
        })
    return rows


def _race_from_box(box_title: str, begin_kind: str) -> str:
    """Classify a box as general / primary / special / convention."""
    t = (box_title or "").lower()
    special = "special" in t
    if "convention" in t:
        return "convention"
    if "primary" in t or "open primary" in begin_kind:
        return "special primary" if special else "primary"
    return "special general" if special else "general"


def _rows_from_box(
    box_rows: List[Tuple[str, Dict[str, str]]],
    race: str,
    district: str,
    state: str,
    chamber: str,
    year: int,
    box_title: str,
) -> List[Dict]:
    # Pipeline scope is general-election results: skip primary/convention
    # boxes (common in older articles, e.g. Arizona chambers bundling the
    # R/D primaries ahead of the general for every district).
    if race not in ("general", "special general"):
        return []
    out: List[Dict] = []
    for kind, params in box_rows:
        if any(s in kind for s in _SKIP_ROW_KINDS):
            continue
        if "winning" in kind:
            winner = True
        elif "candidate" in kind:
            winner = False
        else:
            continue
        cand, inc = extract_incumbent_flag(_clean_name(params.get("candidate", "")))
        if not cand:
            continue
        if cand.lower().rstrip("s") in {
            "blank", "write-in", "overvote", "undecid", "scattering", "all other",
            "other", "total", "none of these", "no valid", "spoil",
        }:
            continue
        if _BOX_JUNK_CAND_RE.search(cand):
            continue
        party = normalize_party(params.get("party", ""))
        if party.lower() in ("total", "majority", "swing"):
            continue
        votes = _eval_number(params.get("votes", ""))
        pct = _eval_percentage(params.get("percentage", ""), votes)
        out.append({
            "year": year, "state": state, "state_code": _state_code(state),
            "chamber": chamber, "district": district, "race": race,
            "candidate": cand, "party": party, "votes": votes,
            "percentage": pct, "winner": winner, "incumbent": inc,
        })
    return out


# ────────────────────────────────────────────────────────────────────────────
# FALLBACK: DISTRICT RESULT TABLES (no election boxes)
# ────────────────────────────────────────────────────────────────────────────

def _wikitables(text: str) -> List[Tuple[str, int]]:
    """Split wikitext into outermost {| ... |} tables with offsets.
    Skips balanced ``{{...}}`` templates while scanning (so a ``|}}`` inside
    ``{{Refn|...|}}`` etc. is not mistaken for a table closer) and tolerates
    stray ``|}`` closers from broken upstream markup by clamping the nesting
    depth at 0 instead of going negative, which would swallow later tables."""
    tables, depth, start, i = [], 0, None, 0
    n = len(text)
    while i < n:
        if text.startswith("{|", i):
            if depth == 0:
                start = i
            depth += 1
            i += 2
        elif text.startswith("|}", i):
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    tables.append((text[start:i + 2], start))
                    start = None
            i += 2
        elif text.startswith("{{", i):
            # skip one balanced template
            t_depth, j = 0, i
            while j < n - 1:
                if text.startswith("{{", j):
                    t_depth += 1
                    j += 2
                elif text.startswith("}}", j):
                    t_depth -= 1
                    j += 2
                    if t_depth == 0:
                        break
                else:
                    j += 1
            i = j if t_depth == 0 else i + 2
        else:
            i += 1
    return tables


def _table_headers(table: str) -> List[str]:
    """Lower-cased header cell labels of the first chunk carrying '!' cells.
    Cells may be ``!!``-separated on a single line (MS-style two-row
    headers) or one per line."""
    for chunk in re.split(r"(?m)^\|-.*$", table):
        if re.search(r"(?m)^!", chunk):
            cells: List[str] = []
            for ln in re.findall(r"(?m)^!.*$", chunk):
                for piece in ln.split("!!"):
                    cells.append(
                        re.sub(r"<[^>]+>", " ", piece).strip(" !|\n").lower()
                    )
            return cells
    return []


def _is_party_text(text: str) -> bool:
    """True when a cell's value is a bare party label (e.g. bold '|Republican')."""
    t = re.sub(r"'''|''", "", (text or "")).strip().lower().rstrip("s")
    return t in {
        "republican", "democratic", "dfl", "independent", "libertarian",
        "green", "constitution", "socialist", "progressive", "nonpartisan",
        "independence", "farmer-labor", "democratic-farmer-labor",
    }


def _is_bold_cell(cell: str) -> bool:
    """True when a table cell renders a bolded *candidate* name - the
    winner marker in summary tables.  Covers ``'''Name'''`` wiki-bold and
    ``style="font-weight:bold"`` CSS-bold, while rejecting numeric cells
    (votes are often bold-styled too), bare party labels and seat-result
    cells (``'''Republican gain'''`` - bolded when the seat flipped)."""
    v = _cell_value(cell).strip()
    if not v or re.fullmatch(r"[\d,.]+%?", v) or _is_party_text(v):
        return False
    if re.search(r"\b(?:hold|gain|loss)\b", v, re.I):
        return False
    if "'''" in cell:
        return True
    return bool(re.search(r"font-weight\s*:\s*bold", cell, re.I))


def _numeric_cell_text(cell: str) -> str:
    """Pure integer-with-commas text of a cell (tolerating a trailing
    ``<ref>...</ref>``), or ``""`` when the cell is not a vote count."""
    v = _cell_value(cell).strip()
    v = re.sub(r"\s*<ref[^>]*>\s*.*?\s*</ref>\s*$", "", v, flags=re.S)
    v = re.sub(r"\s*<ref[^>]*/>\s*$", "", v)
    return v if re.fullmatch(r"[\d,]+", v) else ""


def _row_cells(chunk: str) -> List[str]:
    """Body-row cells: lines starting with '|' or '!', raw content kept
    (attributes like ``rowspan="2"`` are handled by the caller)."""
    cells = []
    for ln in chunk.split("\n"):
        s = ln.strip()
        if s.startswith("|") or s.startswith("!"):
            s = s[1:].strip()
            if s:
                cells.append(s)
    return cells


def _cell_value(cell: str) -> str:
    """Value of a table cell: strip leading attributes and/or leading
    template(s) (each may be followed by an inner pipe) while keeping
    template pipes intact.  Handles quoted (``rowspan="2"``), single-quoted
    and unquoted (``id=7A``, ``align=right``) attribute values, in any
    order, before the attribute-separator pipe:
    ``id=7A rowspan="2" data-sort-value="13" |7A`` -> ``7A``."""
    cell = (cell or "").strip()
    while True:
        m = re.match(r'^[A-Za-z][\w:-]*\s*=\s*"[^"]*"\s*', cell)
        if not m:
            # single-quoted / unquoted attribute value; an unquoted value is
            # only an attribute when a separator pipe remains further in
            # (otherwise "Others = 3.2%" style content would be eaten)
            m = re.match(
                r"^[A-Za-z][\w:-]*\s*=\s*(?:'[^']*'|[^\s|]+)\s*", cell
            )
            if m and "|" not in cell[m.end():]:
                m = None
        if m:
            cell = cell[m.end():]
            cell = re.sub(r"^\|\s*", "", cell)
            continue
        if cell.startswith("{{"):
            # strip one balanced {{...}} template, then an inner pipe
            depth, j = 0, 0
            while j < len(cell) - 1:
                if cell.startswith("{{", j):
                    depth += 1
                    j += 2
                elif cell.startswith("}}", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
                if depth == 0:
                    break
            cell = cell[j:].strip()
            cell = re.sub(r"^\|\s*", "", cell).strip()
            continue
        break
    return cell.strip()


def _section_heading_at(text: str, pos: int) -> str:
    """Nearest == / === heading at or before *pos* (lowercased)."""
    heads = list(re.finditer(r"(?m)^=+[^=].*?=+\s*$", text[:pos]))
    if not heads:
        return ""
    return heads[-1].group(0).lower()


_PLAINLIST_RE = re.compile(
    r"\{\{\s*(?:[Pp]lainlist|[Uu]nbulleted ?list|[Uu]bl)\s*(\||\})"
)
# {{plainlist}} with the items OUTSIDE the template, terminated by
# {{endplainlist}} (Mississippi / Nebraska summary tables)
_PLAINLIST_NOPARAM_RE = re.compile(r"\{\{\s*[Pp]lainlist\s*\}\}")
_ENDPLAINLIST_RE = re.compile(r"\{\{\s*end\s*plainlist\s*\}\}", re.I)

# inline trailing party label + share: '(Republican) 56.62%'
_INLINE_PARTY_PCT_RE = re.compile(
    r"\(([^()]{2,40}?)\)\s*:?\s*([\d,.]+)\s*%\s*$", re.I
)
_INLINE_PCT_RE = re.compile(r"([\d,.]+)\s*%\s*$")
_INLINE_PARTY_PAREN_RE = re.compile(
    r"\(\s*(republican|democratic|independent|libertarian|green|"
    r"nonpartisan|constitution|progressive|no\s+party|write.?in|dfl|"
    r"democratic.farmer.labor|democratic.npl|working\s+families|"
    r"legal\s+marijuana|independence)\s*\)\s*$",
    re.I,
)

# Non-candidate cell values that must never become result rows: blank/write-in
# pseudo-entries plus header labels that leak as body rows when a two-row
# header uses ``|-`` between its rows (``!Name !Party !Votes !%`` etc.)
_TABLE_JUNK_CANDS = frozenset({
    "blank", "write-in", "overvote", "undecid", "scattering",
    "all other", "other", "total", "spoil",
    "name", "party", "vote", "percent", "percentage", "%",
    "first elected", "winner", "candidate", "district",
    "incumbent", "source", "margin", "note", "turnout",
    "registered", "before", "after", "location", "member",
    "status", "seat", "strength", "change", "swing", "first",
    "elected", "total vote", "popular vote", "rank", "preferences",
    "first round", "second round", "runoff", "general election",
    "primary election",
})


def _plainlist_items(chunk: str) -> Tuple[bool, List[str]]:
    """All bullet items inside every plainlist template in *chunk*.

    Handles both ``{{Plainlist|* item * item}}`` (items inside the
    template's first parameter, Oklahoma style) and ``{{plainlist}}``
    followed by starred lines up to ``{{endplainlist}}`` (Mississippi /
    Nebraska summary-table style)."""
    items: List[str] = []
    pos = 0
    found = False
    while True:
        m = _PLAINLIST_RE.search(chunk, pos)
        if not m:
            break
        found = True
        # balanced scan for the matching }} of this plainlist template
        depth, j = 0, m.start()
        while j < len(chunk) - 1:
            if chunk.startswith("{{", j):
                depth += 1
                j += 2
            elif chunk.startswith("}}", j):
                depth -= 1
                j += 2
                if depth == 0:
                    break
            else:
                j += 1
        if depth != 0:            # malformed template — skip the rest
            break
        body = chunk[m.end():j - 2] if m.group(1) == "|" else ""
        if body.strip():
            items.extend(re.split(r"(?m)^\*+", body))
        else:
            # parameterless {{plainlist}}: items live between the template
            # and its {{endplainlist}} terminator
            end_m = _ENDPLAINLIST_RE.search(chunk, j)
            # NB: search() positions are absolute — do NOT re-add j
            stop = end_m.start() if end_m else len(chunk)
            items.extend(re.split(r"(?m)^\*+", chunk[j:stop]))
        pos = j
    return found, items


def _rows_from_plainlist_cell(
    chunk: str, year: int, state: str, chamber: str, district: str
) -> List[Dict]:
    """Candidate rows from a row chunk whose candidates cell bundles
    '*{{Party stripe|...}} [[Name]] - 53%' items inside a plainlist
    template.  The template may span several lines, so the whole chunk is
    scanned; every plainlist in the chunk is unpacked.

    Mississippi / Nebraska style items ('''[[Name]]''' (Party) 56.62%) are
    handled too, including bold winner marking, {{Aye}} checkmarks and
    inline '(Nonpartisan) 48.5%' labels.  Round markers inside the list
    ('''Primary election:''', '''General election:''', 'First round:',
    'Runoff:') split the items into rounds — when a final round (general
    election / runoff / second round) exists, only its rows are kept, so
    two-round nonpartisan specials do not double-count the same seat.
    Items after an 'Eliminated in primary' marker are primary-only and
    skipped."""
    found, items = _plainlist_items(chunk)
    if not found:
        return []

    _ROUND_PAT = re.compile(
        r"^(?:first|second|third|general|primary|runoff)\s+(?:round|election)"
        r"|^runoff$|^(?:general|primary)$",
        re.I,
    )
    _FINAL_ROUND_PREFIXES = ("general", "runoff", "second")

    groups: Dict[str, List[Dict]] = {}
    order: List[str] = []
    cur_round = ""            # '' = unlabeled single-round list
    eliminated = False

    def _emit(label: str, row: Dict) -> None:
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(row)

    for item in items:
        item = re.sub(r"<ref[^>]*/>", "", item)
        item = re.sub(r"<ref[^>]*>.*?</ref>", "", item, flags=re.S | re.I)
        item = item.strip()
        if not item:
            continue
        # cut table-cell spill (form-B lists whose {{endplainlist}} is
        # missing): body items never continue past a line starting with '|'
        item = re.split(r"(?m)^\|", item)[0]
        item = re.split(r"\|\}", item)[0]
        item = item.strip()
        if not item:
            continue
        probe = re.sub(r"\{\{[^{}]*\}\}", "", item)
        probe = re.sub(r"'''|''", "", probe)
        probe = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", probe)
        probe = re.sub(r"<[^>]+>", " ", probe)
        probe = re.sub(r"\s+", " ", probe).strip(" :*")
        low = probe.lower().rstrip(".")
        if _ROUND_PAT.match(low):
            cur_round = low
            eliminated = False          # rounds restart the elimination flag
            continue
        if "eliminated in primar" in low or "eliminated at convention" in low \
                or "withdrew" in low or "dropped out" in low or "lost primary" in low:
            eliminated = True
            continue
        if eliminated and not re.search(r"\d+(?:\.\d+)?\s*%", item):
            continue
        # party: {{Party stripe|Republican Party (US)}} / {{small|...}}
        pm = re.search(r"\{\{\s*[Pp]arty stripe\s*\|\s*([^|}]+)", item)
        party = normalize_party(pm.group(1)) if pm else ""
        # candidate: first wikilink (display text) or cleaned leading text
        lm = re.search(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", item)
        if lm:
            cand = lm.group(2) or lm.group(1)
        else:
            cand = probe
        cand = _clean_name(cand)
        cand, inc = extract_incumbent_flag(cand)
        if not cand:
            continue
        # votes / percentage: '(Republican) 56.62%' inline label+share,
        # plain trailing '56.62%', or legacy 'Name - 1234 / - 53%' items.
        # Legacy dash/percent values may sit outside the candidate wikilink,
        # so retry the search over the whole cleaned item (probe).
        pct = None
        votes = None
        m2 = _INLINE_PARTY_PCT_RE.search(cand)
        m2b = _INLINE_PARTY_PAREN_RE.search(cand)
        if m2:
            if not party:
                party = normalize_party(m2.group(1))
            pct = float(m2.group(2).replace(",", ""))
            cand = cand[:m2.start()].strip()
        elif m2b:
            if not party:
                party = normalize_party(m2b.group(1))
            cand = cand[:m2b.start()].strip()
        else:
            for target in (cand, probe):
                nm = re.search(
                    r"[-\u2013\u2014]\s*([\d,]+(?:\.\d+)?)\s*(%?)\s*$", target
                )
                m3 = _INLINE_PCT_RE.search(target) if not nm else None
                if m3:
                    pct = float(m3.group(1).replace(",", ""))
                    if not party:
                        pm2 = _INLINE_PARTY_PCT_RE.search(probe)
                        if pm2:
                            party = normalize_party(pm2.group(1))
                    break
                if nm:
                    val = float(nm.group(1).replace(",", ""))
                    if nm.group(2):
                        pct = val
                    else:
                        votes = val
                    break
        row = {
            "year": year, "state": state, "state_code": _state_code(state),
            "chamber": chamber, "district": district, "race": "general",
            "candidate": cand, "party": party, "votes": votes,
            "percentage": pct,
            # winner: bold candidate (MS/NE convention) or {{Aye}} checkmark
            "winner": bool(re.search(r"\{\{\s*(?:Aye|mby)\s*\}\}", item, re.I))
            or "'''" in item,
            "incumbent": inc,
        }
        _emit(cur_round, row)

    if not order:
        return []
    # prefer final rounds (general election / runoff / second round) when
    # the list carries round markers; otherwise keep everything
    final_keys = [lb for lb in order if lb.startswith(_FINAL_ROUND_PREFIXES)]
    keep_keys = final_keys if final_keys else order
    out: List[Dict] = []
    for lb in keep_keys:
        out.extend(groups[lb])
    return out


def _parse_results_tables(text: str, year: int, state: str, chamber: str) -> pd.DataFrame:
    """Fallback for articles without election boxes: parse
    ``District | Party | Candidates | Votes | %`` summary tables (e.g.
    Minnesota). Winner = bold candidate; rowspan'd district cells carry
    across continuation rows."""
    # refs carry wikilinks (|website=[[WTVA]]) that would otherwise leak
    # into candidate extraction
    text = _strip_refs(text)
    rows: List[Dict] = []
    for table, offset in _wikitables(text):
        # skip primary-election tables (their enclosing section says so)
        if "primary" in _section_heading_at(text, offset):
            continue
        headers = _table_headers(table)
        if not any("district" in h for h in headers) or not any(
            "candidate" in h for h in headers
        ):
            continue
        if any("poll" in h or "source" in h for h in headers):
            continue

        cur_district: Optional[str] = None
        cur_rows_left = 0
        cur_party = ""

        for chunk in re.split(r"(?m)^\|-.*$", table)[1:]:
            cells = _row_cells(chunk)
            if not cells:
                continue
            # two-row header spill ('! Member !! Party !! ...'): header cells
            # keep their '!!' separators and rowspan/colspan attributes;
            # body cells never do
            if any("!!" in c for c in cells):
                continue

            # leading rowspan'd district cell?  ``| rowspan="2" |6``
            m_rs = re.search(r'rowspan\s*=\s*"?(\d+)"?', cells[0])
            if m_rs and len(cells) >= 2:
                val = _cell_value(cells[0])
                if re.fullmatch(r"\d{1,3}[A-Za-z]?", val):
                    cur_district = val
                    cur_rows_left = int(m_rs.group(1))
                    cells = cells[1:]

            # non-rowspan district cell (header style):
            # ``![[...District 1|1]]`` / ``!1`` / ``|1``
            if cells and (not cur_rows_left or True):
                first_val = _cell_value(cells[0])
                dist_hit = None
                if re.fullmatch(r"\d{1,3}[A-Za-z]?", first_val):
                    dist_hit = first_val
                elif "district" in cells[0].lower():
                    dist_hit = _district_from_box_title(cells[0])
                if dist_hit:
                    cur_district = str(dist_hit)
                    cells = cells[1:]

            # rowspan'd party cell (winner's party, e.g. ``|DFL``) - only
            # when the value actually IS a party label (MN-style rows put a
            # rowspan'd incumbent/candidate cell in the same position)
            party = ""
            m_rs2 = re.search(r'rowspan\s*=\s*"?(\d+)"?', cells[0]) if cells else None
            if m_rs2 and len(cells) >= 4:
                popped = _clean_name(_cell_value(cells[0]))
                if popped.lower().rstrip("s") in {
                    "republican", "democratic", "dfl", "independent",
                    "libertarian", "green", "constitution", "socialist",
                    "progressive", "nonpartisan", "independence",
                    "farmer-labor", "democratic-farmer-labor",
                }:
                    party = popped
                    cells = cells[1:]
            if len(party) <= 1:
                party = ""

            # Plainlist candidate cells (Oklahoma-style summary tables):
            # the candidates column bundles '*[[Name]] - 53%' items inside a
            # {{Plainlist|...}} template that may span several lines - emit
            # one row per item and skip the incumbent/status context cells
            # of the same row entirely
            if cur_district and _PLAINLIST_RE.search(chunk):
                pl_rows = _rows_from_plainlist_cell(
                    chunk, year, state, chamber, cur_district
                )
                if pl_rows:
                    rows.extend(pl_rows)
                    if cur_rows_left > 0:
                        cur_rows_left -= 1
                    continue
                if _PLAINLIST_NOPARAM_RE.search(chunk):
                    # MS/NE-style {{plainlist}} ... {{endplainlist}} cell
                    # with no usable items — skip the chunk entirely (the
                    # generic cell scan would only emit incumbent/status
                    # junk)
                    continue

            # MN-style rows embed context cells (rowspan'd incumbent, party,
            # first-elected, <ref>s) AROUND the candidate cells - realign to
            # the bold candidate cell when one exists (winners are bolded
            # with '''...''' or style="font-weight:bold"; e.g. MN 2018 puts
            # the winner in a separate style-bold cell, so the rowspan'd
            # incumbent cell must NOT be taken as the candidate when they
            # differ - 6B 2018: retiring Metsa vs. winner Lislegard).
            bold_idx = next(
                (k for k, c in enumerate(cells) if _is_bold_cell(c)), None
            )
            cand_idx = bold_idx if bold_idx is not None else 0
            cand_cell = cells[cand_idx] if cells else ""
            winner = bold_idx is not None

            # votes/pct: first pure-number cell after the candidate, with the
            # following cell as its share - skips <ref>, party labels,
            # rowspan'd year links and other context cells wherever they sit
            votes_i = pct_i = -1
            for k in range(cand_idx + 1, len(cells)):
                if _numeric_cell_text(cells[k]):
                    votes_i = k
                    pct_i = k + 1 if k + 1 < len(cells) else -1
                    break
            votes_cell = cells[votes_i] if votes_i >= 0 else ""
            pct_cell = cells[pct_i] if pct_i >= 0 else ""

            # party: first bare party-label cell in the row (excluding the
            # candidate/votes/pct cells themselves)
            if not party:
                for k, c in enumerate(cells):
                    if k in (cand_idx, votes_i, pct_i):
                        continue
                    v = _cell_value(c)
                    if _is_party_text(v):
                        party = _clean_name(v)
                        break
            cand = _clean_name(_cell_value(cand_cell))
            cand, inc = extract_incumbent_flag(cand)
            if re.search(r"\((?:inc|incumbent)\)\s*$", cand, flags=re.I):
                inc = True
            cand = re.sub(r"\((?:inc|incumbent)\)\s*$", "", cand, flags=re.I).strip(" ,;")
            cand_lc = cand.lower()
            if not cand or cand_lc in _TABLE_JUNK_CANDS or cand_lc.rstrip("s") in _TABLE_JUNK_CANDS:
                continue
            # seat-result cells ('Republican hold' / 'Democratic gain') are
            # not candidates, however they are styled
            if re.search(r"\b(?:hold|gain|loss)\b", cand, re.I):
                continue
            # misparsed party-label cells (second table family) — drop
            if cand.lower() in {
                "dfl", "democratic", "republican", "independent", "libertarian",
                "green", "constitution", "socialist", "progressive", "nonpartisan",
            }:
                continue

            votes = _eval_number(_cell_value(votes_cell))
            pct = _eval_percentage(_cell_value(pct_cell), votes)
            if pct is not None and pct > 100.1:
                # misaligned cell row (junk) — never emit impossible shares
                continue

            rows.append({
                "year": year, "state": state, "state_code": _state_code(state),
                "chamber": chamber, "district": cur_district or "", "race": "general",
                "candidate": cand, "party": normalize_party(party) if party else "",
                "votes": votes, "percentage": pct,
                "winner": winner, "incumbent": inc,
            })
            if cur_rows_left > 0:
                cur_rows_left -= 1

    if not rows:
        return pd.DataFrame(columns=_COLUMNS)
    df = pd.DataFrame(rows, columns=_COLUMNS)
    if len(df):
        # single candidate in a district = unopposed winner; otherwise flag
        # the top vote-getter (or top share when only percentages exist, e.g.
        # Plainlist summary tables) when a district shows no winner at all
        grp = df.groupby("district", dropna=False)
        df.loc[grp["candidate"].transform("count") == 1, "winner"] = True
        no_win = ~grp["winner"].transform("max").astype(bool)
        if no_win.any():
            ranked = df[no_win].sort_values(
                ["votes", "percentage"], ascending=False, na_position="last"
            )
            top = ranked.groupby("district", dropna=False, sort=False).head(1).index
            df.loc[top, "winner"] = True
    return df


# ────────────────────────────────────────────────────────────────────────────
# DISCOVERY + BATCH DRIVER
# ────────────────────────────────────────────────────────────────────────────

def discover_chambers(overview: str, year: int) -> List[str]:
    """Extract chamber article titles from the overview's {{main|...}} links."""
    links = re.findall(
        r"\{\{\s*[Mm]ain(?:\s+article)?\s*\|\s*([^}|]+(?:\|[^}|]+)*)\s*\}\}", overview
    )
    titles: List[str] = []
    for group in links:
        for part in group.split("|"):
            part = part.strip()
            if f"{year} " in part and re.search(
                r"Senate|House|Assembly|Delegates|General Assembly|Legislature",
                part, re.I,
            ):
                titles.append(part)
    return list(dict.fromkeys(titles))


def run(
    start_year: int = 2018,
    end_year: int = 2024,
    output_dir: str = "data",
    client: Optional[WikiAPIClient] = None,
) -> pd.DataFrame:
    """CLI entry point: parse state legislature results for the given range.

    Every year in the range is processed; odd years yield results only for
    the odd-year legislature states (NJ/VA/LA/MS) because the other states
    hold no regular legislative cycles in odd years."""
    years = list(range(start_year, end_year + 1))
    if not years:
        logger.warning("No years in [%d, %d] — nothing to do.", start_year, end_year)
        return pd.DataFrame()
    logger.info("State legislature cycles to process: %s", years)

    senate_dir = os.path.join(output_dir, "state_senate")
    house_dir = os.path.join(output_dir, "state_house")
    os.makedirs(senate_dir, exist_ok=True)
    os.makedirs(house_dir, exist_ok=True)

    client = client or get_default_client()
    meta: Dict = {"years": {}}

    for year in years:
        overview_title = OVERVIEW_PAT.format(y=year)
        overview = client.fetch_single(overview_title) if client else None
        if not overview:
            logger.warning("FAIL %d — overview article not found (%s)", year, overview_title)
            meta["years"][year] = {"error": "overview missing"}
            continue

        titles = discover_chambers(overview, year)
        # dedicated special-election articles duplicate the boxes already
        # inside the chamber articles; skip them (their titles also break
        # state extraction, e.g. "2023 Virginia's 7th Senate district
        # special election")
        titles = [t for t in titles if "special" not in t.lower()]
        if year % 2:
            # odd cycles exist only in the odd-year legislature states
            titles = [
                t for t in titles
                if _state_from_title(t)[1] in ODD_YEAR_STATES
            ]
        upper = [t for t in titles if re.search(r"Senate|Legislature", t)]
        lower = [t for t in titles if t not in set(upper)]
        logger.info(
            "%s: %d chamber articles (%d senate / %d house)", year,
            len(titles), len(upper), len(lower),
        )

        content = client.fetch_wikitext(titles) if titles else {}

        year_meta = {"senate": {}, "house": {}, "missing": []}
        frames_by_chamber: Dict[str, List[pd.DataFrame]] = {"senate": [], "house": []}

        for group, chamber_label in ((upper, "State Senate"), (lower, "State House")):
            key = "senate" if chamber_label == "State Senate" else "house"
            for title in group:
                text = content.get(title)
                if not text:
                    logger.warning("  %s missing chamber article: %s", year, title)
                    year_meta["missing"].append(title)
                    continue
                state, _code = _state_from_title(title)
                if not state:
                    logger.warning("  %s cannot derive state from title: %s", year, title)
                    year_meta["missing"].append(title)
                    continue
                df = parse_chamber(text, year, state, chamber_label)
                if not len(df) or not (df["race"] == "general").any():
                    df_tbl = _parse_results_tables(text, year, state, chamber_label)
                    if len(df_tbl):
                        logger.info("    table fallback: %d rows", len(df_tbl))
                        df = df_tbl if not len(df) else pd.concat(
                            [df_tbl, df], ignore_index=True
                        )
                n_dist = df["district"].nunique() if len(df) else 0
                if len(df):
                    frames_by_chamber[key].append(df)
                    logger.info(
                        "  %s %-58s %5d rows / %3d districts", year, title,
                        len(df), n_dist,
                    )
                else:
                    logger.info("  %s %-58s (no results parsed)", year, title)
                year_meta[key][title] = {"rows": int(len(df)), "districts": int(n_dist)}

        for key, out_dir in (("senate", senate_dir), ("house", house_dir)):
            frames = frames_by_chamber[key]
            if not frames:
                continue
            df_year = pd.concat(frames, ignore_index=True)
            prefix = f"state_{key}"
            path = os.path.join(out_dir, f"{prefix}_results_{year}.csv")
            df_year.to_csv(path, index=False)
            logger.info("  saved -> %s", path)
        meta["years"][year] = year_meta

    # rebuild the combined _all.csv from every per-year file on disk (not
    # just the years processed by this invocation) so partial re-runs and
    # odd-year additions stay consistent
    combined: Optional[pd.DataFrame] = None
    for key, out_dir in (("senate", senate_dir), ("house", house_dir)):
        year_files = sorted(glob.glob(
            os.path.join(out_dir, f"state_{key}_results_[0-9]*.csv")
        ))
        if not year_files:
            continue
        frames = [
            pd.read_csv(p, dtype={"district": str, "state_code": str,
                                 "state": str, "chamber": str,
                                 "race": str, "candidate": str,
                                 "party": str})
            for p in year_files
        ]
        sub = pd.concat(frames, ignore_index=True)
        path = os.path.join(out_dir, f"state_{key}_results_all.csv")
        sub.to_csv(path, index=False)
        logger.info("Combined -> %s (%s rows)", path, f"{len(sub):,}")
        combined = sub if combined is None else pd.concat(
            [combined, sub], ignore_index=True
        )

    return combined if combined is not None else pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run()
