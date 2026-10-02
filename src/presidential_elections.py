"""
presidential_elections.py — Parse **county-level** U.S. presidential election
results from the Wikipedia article series

    "{year} United States presidential election in {State}"

fetched via the MediaWiki Action API (batched, rate-limited: a full
51-jurisdiction cycle is only ~2 HTTP requests thanks to 50-title batches).

For each presidential year (divisible by 4) in [start_year, end_year] the
pipeline fetches one article per state plus Washington, D.C., and parses the
subdivision-level results table. All 51 jurisdictions share the same
two-row-header table layout, only the heading and first column differ:

    Alabama..Wyoming   "By county"                -> County
    Louisiana          "By parish"                -> Parish
    Virginia           "By county and independent
                        city"                     -> County/Independent city
    Alaska             "By borough and census
                        area (estimates)"         -> Borough/Census area
    Washington, D.C.   "Results by ward"          -> Ward

Table shape (Indiana 2024 shown; cells may carry {{party shading/...}},
style attributes in any order, <ref>s, links, negative margins):

    {|width="60%" class="wikitable sortable"
    ! rowspan="2" |[[List of counties in Indiana|County]]
    ! colspan="2" |Donald Trump<br />Republican
    ! colspan="2" |Kamala Harris<br />Democratic
    ! colspan="2" |Various candidates<br />Other parties
    ! colspan="2" |Margin
    ! rowspan="2" |Total
    |-
    ! data-sort-type="number" |#
    ! data-sort-type="number" |%  ... (one pair per candidate group)
    |-
    | {{party shading/Republican}} |[[Adams County, Indiana|Adams]]
    | {{party shading/Republican}} |10,528
    | {{party shading/Republican}} |75.28%
    ...
    |-
    !Totals!!1,720,347!!58.43%!!1,163,603!!39.52%!!60,386!!2.05%!!556,744!!18.91%!!2,944,336
    |}

The parser mats every table through an HTML-style rowspan/colspan grid, so
header variants (linked labels, <ref>s, attribute order, stray empty row
separators, {{Update}} banners before the table) are tolerated, and the
trailing "Totals" row is captured for a per-candidate cross-check against
the sum of county rows (recorded in the metadata JSON).

Output columns (written under *output_dir*, default ``data/presidential/``):
    year, state, state_code, county, subdivision_type, candidate, party,
    votes, percentage, total_votes, winning_party

    presidential_results_{year}.csv   per year (all 50 states + DC)
    presidential_results_all.csv      combined across the requested range
    presidential_metadata_{ts}.json   per-state coverage + totals cross-check

PRESIDENTIAL OPINION POLLING (``include_polling=True`` by default; CLI
``--include-polling``/``--no-include-polling``): the same run also mines the
Wikipedia polling articles for the cycle, reusing the Senate pipeline's
polling-table parsers (same table generations as senate/statewide races):

* ``Nationwide opinion polling for the {year} United States presidential
  election`` — national polls, classified by the matchup section they sit
  under (``Kamala Harris vs. Donald Trump``, ...); hypothetical matchups are
  kept and flagged via ``Poll_Type``.
* ``Statewide opinion polling for the {year} United States presidential
  election`` — state-level polls when the article carries real tables
  (2008–2016 vintages); the 2020+ vintages only transclude
  (``{{#section-h:...}}``) from the state articles, so they parse to zero
  rows here.
* The per-state ``{year} ... election in {State}`` articles' own
  ``== Polling ==`` / ``== Polls ==`` sections — already fetched for the
  county results, so this source costs no extra API request and covers the
  transclusion-only 2020+ cycles.

Rows from the overlapping sources are de-duplicated on
(Year, State, Poll_Source, Date, Candidate, Pct).

Polling columns (long format, one row per poll x candidate; same schema as
the Senate/statewide polling families plus ``Scope`` / ``Matchup`` /
``Poll_Type``):
    Year, Scope, State, State_Code, Matchup, Poll_Type, Poll_Source, Date,
    Date_Start, Date_End, Sample, MoE, Candidate, Party, Pct, Incumbent,
    Date_Original

    presidential_national_polling_{year}.csv   national polls
    presidential_national_polling_all.csv      combined across runs on disk
    presidential_state_polling_{year}.csv      state-level polls
    presidential_state_polling_all.csv         combined across runs on disk

Usage:
    python cli.py presidential --start-year 2018 --end-year 2024
    python cli.py presidential --start-year 2018 --end-year 2024 --no-include-polling
    python presidential_elections.py
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

from wiki_utils import (
    WikiAPIClient,
    even_years,
    get_default_client,
    remove_wikilinks,
    unwrap_format_templates,
)

# Polling-table machinery is shared with the Senate pipeline (statewide does
# the same): Wikipedia uses the same polling-table generations in the
# presidential polling articles, so the battle-tested Senate parsers are
# reused instead of a second, drifting implementation.  senate_elections has
# no presidential dependency, so this import cannot be circular.
from senate_elections import (  # noqa: E402  (module import order is fine)
    iter_polling_spans,
    normalize_polling_date_columns,
    parse_polling_table_universal,
    wide_to_long_polls,
)

logger = logging.getLogger("presidential_elections")

# ────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ────────────────────────────────────────────────────────────────────────────

#: (title suffix, display name, state code) for all 50 states + D.C.
#: Washington needs the "(state)" disambiguator, D.C. the comma form.
PLACES: List[Tuple[str, str, str]] = [
    ("Alabama", "Alabama", "AL"),
    ("Alaska", "Alaska", "AK"),
    ("Arizona", "Arizona", "AZ"),
    ("Arkansas", "Arkansas", "AR"),
    ("California", "California", "CA"),
    ("Colorado", "Colorado", "CO"),
    ("Connecticut", "Connecticut", "CT"),
    ("Delaware", "Delaware", "DE"),
    ("Florida", "Florida", "FL"),
    ("Georgia", "Georgia", "GA"),
    ("Hawaii", "Hawaii", "HI"),
    ("Idaho", "Idaho", "ID"),
    ("Illinois", "Illinois", "IL"),
    ("Indiana", "Indiana", "IN"),
    ("Iowa", "Iowa", "IA"),
    ("Kansas", "Kansas", "KS"),
    ("Kentucky", "Kentucky", "KY"),
    ("Louisiana", "Louisiana", "LA"),
    ("Maine", "Maine", "ME"),
    ("Maryland", "Maryland", "MD"),
    ("Massachusetts", "Massachusetts", "MA"),
    ("Michigan", "Michigan", "MI"),
    ("Minnesota", "Minnesota", "MN"),
    ("Mississippi", "Mississippi", "MS"),
    ("Missouri", "Missouri", "MO"),
    ("Montana", "Montana", "MT"),
    ("Nebraska", "Nebraska", "NE"),
    ("Nevada", "Nevada", "NV"),
    ("New Hampshire", "New Hampshire", "NH"),
    ("New Jersey", "New Jersey", "NJ"),
    ("New Mexico", "New Mexico", "NM"),
    ("New York", "New York", "NY"),
    ("North Carolina", "North Carolina", "NC"),
    ("North Dakota", "North Dakota", "ND"),
    ("Ohio", "Ohio", "OH"),
    ("Oklahoma", "Oklahoma", "OK"),
    ("Oregon", "Oregon", "OR"),
    ("Pennsylvania", "Pennsylvania", "PA"),
    ("Rhode Island", "Rhode Island", "RI"),
    ("South Carolina", "South Carolina", "SC"),
    ("South Dakota", "South Dakota", "SD"),
    ("Tennessee", "Tennessee", "TN"),
    ("Texas", "Texas", "TX"),
    ("Utah", "Utah", "UT"),
    ("Vermont", "Vermont", "VT"),
    ("Virginia", "Virginia", "VA"),
    ("Washington (state)", "Washington", "WA"),
    ("West Virginia", "West Virginia", "WV"),
    ("Wisconsin", "Wisconsin", "WI"),
    ("Wyoming", "Wyoming", "WY"),
    ("Washington, D.C.", "District of Columbia", "DC"),
]

#: Subdivision-level results headings across the article series (level 2-4).
#: Note: primary-election tables often share these headings (e.g. West
#: Virginia 2012 "Results by county" under the Republican primary) —
#: parse_state_page therefore tries EVERY matching heading and prefers the
#: first table that looks like a general-election results table.
_COUNTY_HEADING_RE = re.compile(
    r"(?im)^={2,4}\s*("
    r"by county(?: and independent city)?"
    r"|by parish"
    r"|by borough[^=\n]*"
    r"|results by ward"
    r"|by ward"
    r"|results by county[^=\n]*"
    r"|county results"
    r"|by city and county"
    r")\s*={2,4}\s*$"
)

_ANY_HEADING_RE = re.compile(r"(?m)^={2,6}[^=].*?={2,6}\s*$")

_COLUMNS = [
    "year", "state", "state_code", "county", "subdivision_type",
    "candidate", "party", "votes", "percentage", "total_votes", "winning_party",
]

_PARTY_ALIASES: Dict[str, str] = {
    "republican": "Republican",
    "republican party": "Republican",
    "democratic": "Democratic",
    "democrat": "Democratic",
    "democratic party": "Democratic",
    "democratic-farmer-labor": "Democratic-Farmer-Labor",
    "dfl": "Democratic-Farmer-Labor",
    "democratic-npl": "Democratic-NPL",
    "democratic-nonpartisan league": "Democratic-NPL",
    "libertarian": "Libertarian",
    "libertarian party": "Libertarian",
    "green": "Green",
    "green party": "Green",
    "independent": "Independent",
    "independent politician": "Independent",
    "constitution": "Constitution",
    "constitution party": "Constitution",
    "reform": "Reform",
    "reform party": "Reform",
    "write-in": "Write-in",
    "other parties": "Other",
    "all others": "Other",
    "others": "Other",
    "various candidates": "Other",
    "no party preference": "No party preference",
}

_SHADING_RE = re.compile(r"\{\{\s*party shading/([^|}]+)", re.I)
_PCT_VALUE_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: first-column labels that mark the subdivision column
_SUBDIV_RE = re.compile(
    r"county|parish|borough|census area|ward|independent city|municipal|locality",
    re.I,
)


def presidential_years(start_year: int, end_year: int) -> List[int]:
    """Presidential cycles (divisible by 4) within the inclusive even-year
    range: ``presidential_years(2018, 2024)`` -> ``[2020, 2024]``."""
    return [y for y in even_years(start_year, end_year) if y % 4 == 0]


def page_title(year: int, suffix: str) -> str:
    return f"{year} United States presidential election in {suffix}"


# ────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL WIKITEXT HELPERS
# ────────────────────────────────────────────────────────────────────────────

def _strip_refs(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    return text


def _clean_text(text: str) -> str:
    """Normalise a table cell / header label to plain readable text."""
    t = text or ""
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = _strip_refs(t)
    t = re.sub(r"\{\{(?:Party|party) shading/[^}|]*\|?", "", t)
    t = re.sub(r"\{\{(?:nowrap|nobr)\|([^}]*)\}\}", r"\1", t, flags=re.I)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)          # leftover simple templates
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)          # second pass for nesting
    t = re.sub(r"<[^>]+>", " ", t)                # tags (br, sup, span...)
    t = t.replace("'''", "").replace("''", "")
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", t)
    t = html.unescape(t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" |\t\n")


def _shading_party(raw_cell: str) -> str:
    """Winning party hinted by {{party shading/X}} in a cell ('Others'→Other)."""
    m = _SHADING_RE.search(raw_cell or "")
    if not m:
        return ""
    return _normalize_party(m.group(1))


def _normalize_party(raw: str) -> str:
    p = _clean_text(raw or "").strip().lower().rstrip(".")
    p = re.sub(r"[\u2010\u2013\u2014]", "-", p)  # hyphen/en-dash/em-dash -> '-'
    if not p:
        return ""
    if p in _PARTY_ALIASES:
        return _PARTY_ALIASES[p]
    p2 = re.sub(r"\s+party(?: of the)?(?: \(united states\))?$", "", p).strip()
    return _PARTY_ALIASES.get(p2, p.title())


def _parse_number(text: str, kind: str):
    """Parse a votes (#) or percentage (%) cell to int / float, or None."""
    t = _clean_text(text)
    t = t.replace(",", "").replace("−", "-")
    m = _PCT_VALUE_RE.search(t)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except ValueError:
        return None
    return int(round(val)) if kind == "votes" else val


def _eval_percentage_template(raw: str) -> Optional[float]:
    """'{{percentage|123|1000}}' -> 12.3 (rare in these tables, safety net)."""
    m = re.search(r"\{\{\s*percentage\s*\|\s*(-?[\d.,]+)\s*\|\s*(-?[\d.,]+)", raw, re.I)
    if not m:
        return None
    try:
        num = float(m.group(1).replace(",", ""))
        den = float(m.group(2).replace(",", ""))
        return round(num / den * 100, 2) if den else None
    except ValueError:
        return None


# ────────────────────────────────────────────────────────────────────────────
# TABLE GRID (rowspan / colspan aware, HTML-table algorithm)
# ────────────────────────────────────────────────────────────────────────────

def _depth0_split(text: str, separator: str) -> List[str]:
    """Split *text* at *separator* ('||' or '!!') occurrences that sit outside
    [[..]] links and {{..}} templates."""
    parts, buf = [], []
    link_depth = tpl_depth = 0
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "[[":
            link_depth += 1
            buf.append(two)
            i += 2
            continue
        if two == "]]":
            link_depth = max(0, link_depth - 1)
            buf.append(two)
            i += 2
            continue
        if two == "{{":
            tpl_depth += 1
            buf.append(two)
            i += 2
            continue
        if two == "}}":
            tpl_depth = max(0, tpl_depth - 1)
            buf.append(two)
            i += 2
            continue
        if (link_depth == 0 and tpl_depth == 0
                and text.startswith(separator, i)):
            parts.append("".join(buf))
            buf = []
            i += 2
            continue
        buf.append(text[i])
        i += 1
    parts.append("".join(buf))
    return parts


def _split_attrs_content(raw: str) -> Tuple[str, str]:
    """Split a raw cell body into (attribute string, content).

    Wikitext cells look like ``| attrs | content`` — the content follows the
    LAST top-level pipe that is not inside [[..]] / {{..}} (this also handles
    the unquoted-attribute form ``id=7A rowspan="2" data-sort-value="13" |7A``
    known from the 2018 Minnesota House tables).
    """
    link_depth = tpl_depth = 0
    last = -1
    i, n = 0, len(raw)
    while i < n:
        two = raw[i:i + 2]
        if two == "[[":
            link_depth += 1
            i += 2
            continue
        if two == "]]":
            link_depth = max(0, link_depth - 1)
            i += 2
            continue
        if two == "{{":
            tpl_depth += 1
            i += 2
            continue
        if two == "}}":
            tpl_depth = max(0, tpl_depth - 1)
            i += 2
            continue
        if raw[i] == "|" and link_depth == 0 and tpl_depth == 0:
            last = i
        i += 1
    if last < 0:
        return "", raw
    return raw[:last], raw[last + 1:]


def _attrs_dict(attrs: str) -> Dict[str, int]:
    """Extract rowspan / colspan (default 1) from a cell attribute string."""
    out = {"rowspan": 1, "colspan": 1}
    for m in re.finditer(
        r'([A-Za-z][\w:-]*)\s*=\s*("([^"]*)"|\'([^\']*)\'|([^\s|]+))', attrs
    ):
        key = m.group(1).lower()
        if key in out:
            try:
                out[key] = max(1, int(m.group(3) or m.group(4) or m.group(5)))
            except (TypeError, ValueError):
                pass
    return out


def _row_cells(chunk: str) -> List[Tuple[bool, str]]:
    """Tokenize one row chunk (text between ``|-`` lines) into cells.

    Returns a list of ``(is_header_cell, raw_body)`` tuples; ``raw_body``
    excludes the leading ``|``/``!`` marker. Chained ``||`` / ``!!`` cells on
    one line are split; caption (``|+``) lines and nested ``|-`` are skipped;
    continuation lines are appended to the current cell.
    """
    cells: List[Tuple[bool, str]] = []
    cur: Optional[str] = None
    cur_header = False
    for line in chunk.split("\n"):
        s = line.strip()
        if not s:
            if cur is not None:
                cur += "\n"
            continue
        if s.startswith("|}"):
            break
        if s.startswith("|+") or s.startswith("|-"):
            continue
        if s[0] in "!|":
            header = s[0] == "!"
            parts = _depth0_split(s[1:], "!!" if header else "||")
            for part in parts:
                if cur is not None:
                    cells.append((cur_header, cur))
                cur = part
                cur_header = header
        elif cur is not None:
            cur += "\n" + s
    if cur is not None:
        cells.append((cur_header, cur))
    return cells


def _grid(table: str) -> List[Dict]:
    """Mat an entire wikitable into a list of row dicts ``{col: raw_content}``
    with rowspan/colspan expansion, like an HTML table renderer."""
    rows: List[Dict] = []
    pending: Dict[int, Tuple[str, int]] = {}
    for chunk in re.split(r"(?m)^\|-.*$", table):
        cells = _row_cells(chunk)
        if not cells:
            continue
        occupied = {c: v for c, (v, rem) in pending.items() if rem > 0}
        cur: Dict[int, str] = dict(occupied)
        col = 0
        for is_header, raw in cells:
            attrs, content = _split_attrs_content(raw)
            ad = _attrs_dict(attrs)
            rs, cs = ad["rowspan"], ad["colspan"]
            while col in cur:
                col += 1
            for k in range(cs):
                cur[col + k] = content
                if rs > 1:
                    # occupies *rs* rows counting forward (decremented below)
                    pending[col + k] = (content, rs)
            col += cs
        pending = {c: (v, r - 1) for c, (v, r) in pending.items() if r - 1 > 0}
        rows.append({"cells": cur, "header": all(h for h, _ in cells)})
    return rows


# ────────────────────────────────────────────────────────────────────────────
# COUNTY TABLE PARSER
# ────────────────────────────────────────────────────────────────────────────

def _county_section(text: str) -> str:
    """Wikitext chunk after the subdivision-results heading (any level 2-4)."""
    m = _COUNTY_HEADING_RE.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _ANY_HEADING_RE.search(rest)
    return rest[: nxt.start()] if nxt else rest


def _wikitables(section_text: str) -> List[str]:
    """Outermost ``{| ... |}`` tables inside *section_text*."""
    tables, depth, start, i = [], 0, None, 0
    while i < len(section_text):
        if section_text.startswith("{|", i):
            if depth == 0:
                start = i
            depth += 1
            i += 2
        elif section_text.startswith("|}", i):
            depth -= 1
            if depth == 0 and start is not None:
                tables.append(section_text[start:i + 2])
                start = None
            i += 2
        else:
            i += 1
    return tables


def _subdivision_type(label: str) -> str:
    low = (label or "").lower()
    if "borough" in low or "census area" in low:
        return "Borough/Census area"
    if "parish" in low:
        return "Parish"
    if "ward" in low:
        return "Ward"
    if "independent city" in low:
        return "County/Independent city"
    if "county" in low:
        return "County"
    return label.strip().title() if label.strip() else "County"


def _plan_columns(header_rows: List[Dict]) -> Optional[Dict]:
    """Classify columns from the (1- or 2-row) header grid.

    Returns ``{"county_col", "total_col", "subdivision", "candidates": [...]}``
    where each candidate is ``{"name", "party", "votes_col", "pct_col"}``
    (votes_col / pct_col may be None when only one metric exists).
    """
    r0 = header_rows[0]["cells"]
    r1 = header_rows[1]["cells"] if len(header_rows) > 1 else {}
    if not r0:
        return None
    ncol = max(r0) + 1
    county_col = total_col = None
    subdivision_label = ""
    groups: List[Dict] = []
    skip_cols = set()
    c = 0
    while c < ncol:
        raw_cell = r0.get(c, "")          # keep raw: <br /> splits name/party
        raw0 = _clean_text(raw_cell)
        low0 = raw0.lower()
        raw1 = _clean_text(r1.get(c, "")) if c in r1 else ""
        # sub-column of a colspan pair: distinct label on the second row
        if low0 and raw1 and raw1.lower() != low0:
            gcols = [c]
            while (c + 1 < ncol
                   and _clean_text(r0.get(c + 1, "")).lower() == low0
                   and (c + 1) not in skip_cols):
                c += 1
                gcols.append(c)
            if "margin" in low0 or low0.startswith("total"):
                skip_cols.update(gcols)          # derived column, not a candidate
            else:
                groups.append({"raw_label": raw_cell, "cols": gcols})
        else:
            # singleton (rowspan=2 through both header rows)
            if _SUBDIV_RE.search(low0) and county_col is None:
                county_col = c
                subdivision_label = raw0
            elif low0.startswith("total") and total_col is None:
                total_col = c
            # 'margin' / other singletons are ignored
        c += 1

    if county_col is None or not groups:
        return None

    body_rows = []  # filled by caller for calibration; use header first
    candidates = []
    for g in groups:
        name, party = _candidate_from_label(g["raw_label"])
        vc = pc = None
        if len(g["cols"]) >= 2:
            for col in g["cols"]:
                sub = _clean_text(r1.get(col, "")).lower().strip(".")
                if sub in ("#", "votes", "vote", "votes cast", "count", "number", "n"):
                    vc = col
                elif sub in ("%", "percent", "pct", "pct."):
                    pc = col
        candidates.append({
            "name": name, "party": party,
            "cols": g["cols"], "votes_col": vc, "pct_col": pc,
        })
    return {
        "county_col": county_col,
        "total_col": total_col,
        "subdivision": _subdivision_type(subdivision_label),
        "candidates": candidates,
        "n_header_rows": len(header_rows),
    }


def _candidate_from_label(label: str) -> Tuple[str, str]:
    """'Donald Trump<br />Republican' -> ('Donald Trump', 'Republican').

    Splits on <br /> or a raw NEWLINE before cleaning — several article
    generations put the party on a second line inside the header cell, and
    cleaning would fuse name and party into one token."""
    parts = re.split(r"<br\s*/?>|\n", label or "", maxsplit=1)
    name = _clean_text(parts[0])
    party = _normalize_party(parts[1]) if len(parts) > 1 else ""
    if name.lower() in ("various candidates", "all others", "others",
                        "other candidates", "other"):
        name = "Other"
    return name, party


def _calibrate_vote_pct_columns(
    plan: Dict, rows: List[Dict], first_body: int
) -> None:
    """Fill missing votes_col / pct_col assignments from the body data:
    the column whose values mostly carry '%' is the percentage column."""
    for cand in plan["candidates"]:
        if cand["votes_col"] is not None and cand["pct_col"] is not None:
            continue
        stats = {col: [0, 0] for col in cand["cols"]}  # [pct_hits, numeric]
        for row in rows[first_body:first_body + 400]:
            if row["header"]:
                continue
            for col in cand["cols"]:
                raw = row["cells"].get(col, "")
                txt = _clean_text(raw)
                if not txt:
                    continue
                if "%" in txt:
                    stats[col][0] += 1
                elif _PCT_VALUE_RE.search(txt):
                    stats[col][1] += 1
        if cand["votes_col"] is None and cand["pct_col"] is None:
            if len(cand["cols"]) == 2:
                a, b = cand["cols"]
                if stats[a][0] >= stats[b][0]:
                    cand["pct_col"], cand["votes_col"] = a, b
                else:
                    cand["pct_col"], cand["votes_col"] = b, a
            elif len(cand["cols"]) == 1:
                col = cand["cols"][0]
                if stats[col][0] > stats[col][1]:
                    cand["pct_col"] = col
                else:
                    cand["votes_col"] = col
        elif cand["votes_col"] is None:
            others = [c for c in cand["cols"] if c != cand["pct_col"]]
            cand["votes_col"] = others[0] if others else None
        elif cand["pct_col"] is None:
            others = [c for c in cand["cols"] if c != cand["votes_col"]]
            cand["pct_col"] = others[0] if others else None


_TOTALS_LABEL_RE = re.compile(
    r"^(?:state\s+)?(?:totals?|total votes?|statewide)$", re.I
)


def parse_county_table(
    table: str, year: int, state: str, state_code: str
) -> Tuple[pd.DataFrame, Dict]:
    """Parse one subdivision-results wikitable.

    Returns ``(records_df, info)`` where *info* carries ``totals_row``
    (per-candidate votes from the trailing Totals row, when present),
    ``county_sums`` and ``counties``.
    """
    rows = _grid(table)

    # leading header rows (stop at the first body row)
    header_rows: List[Dict] = []
    first_body = 0
    for idx, row in enumerate(rows):
        if not row["cells"]:
            continue
        if row["header"] and not any(
            r["header"] is False for r in rows[:idx]
        ):
            header_rows.append(row)
            first_body = idx + 1
        else:
            break

    plan = _plan_columns(header_rows)
    if plan is None:
        return pd.DataFrame(columns=_COLUMNS), {}

    _calibrate_vote_pct_columns(plan, rows, first_body)
    county_col, total_col = plan["county_col"], plan["total_col"]
    subdivision = plan["subdivision"]

    records: List[Dict] = []
    totals_row: Dict[str, int] = {}
    counties_seen = set()

    for row in rows[first_body:]:
        cells = row["cells"]
        first_label = _clean_text(cells.get(county_col, ""))
        if row["header"]:
            if _TOTALS_LABEL_RE.match(first_label):
                for cand in plan["candidates"]:
                    if cand["votes_col"] is not None:
                        v = _parse_number(cells.get(cand["votes_col"], ""), "votes")
                        if v is not None:
                            totals_row[cand["name"]] = v
            continue
        county = _clean_text(cells.get(county_col, ""))
        if not county:
            continue

        row_records: List[Dict] = []
        group_votes: List[Tuple[Dict, Optional[int]]] = []
        for cand in plan["candidates"]:
            votes = pct = None
            if cand["votes_col"] is not None:
                votes = _parse_number(cells.get(cand["votes_col"], ""), "votes")
            if cand["pct_col"] is not None:
                raw_pct = cells.get(cand["pct_col"], "")
                pct = _parse_number(raw_pct, "pct")
                if pct is None:
                    pct = _eval_percentage_template(raw_pct)
            group_votes.append((cand, votes))
            if votes is None and pct is None:
                continue
            total_votes = (
                _parse_number(cells.get(total_col, ""), "votes")
                if total_col is not None else None
            )
            rec = {
                "year": year,
                "state": state,
                "state_code": state_code,
                "county": county,
                "subdivision_type": subdivision,
                "candidate": cand["name"],
                "party": cand["party"],
                "votes": votes,
                "percentage": pct,
                "total_votes": total_votes,
                "winning_party": "",  # filled below
            }
            records.append(rec)
            row_records.append(rec)
        counties_seen.add(county)

        # winning party: top votes among the named candidates in this row
        scored = [(v, cand) for cand, v in group_votes if v is not None]
        if scored:
            best = max(scored, key=lambda t: (t[0],))[1]
            win = best["party"] or ("Other" if best["name"] == "Other" else best["name"])
        else:
            win = _shading_party(cells.get(county_col, ""))
        for rec in row_records:
            rec["winning_party"] = win

    df = pd.DataFrame(records, columns=_COLUMNS)
    if len(df):
        df = df.drop_duplicates(
            subset=["county", "candidate"], keep="first"
        ).reset_index(drop=True)
        df["votes"] = df["votes"].astype("Int64")
        df["total_votes"] = df["total_votes"].astype("Int64")

    county_sums: Dict[str, int] = {}
    if len(df) and totals_row:
        for cand_name, tot in totals_row.items():
            s = df.loc[df["candidate"] == cand_name, "votes"].dropna()
            if len(s):
                county_sums[cand_name] = int(s.sum())

    info = {
        "counties": len(counties_seen),
        "rows": int(len(df)),
        "totals_row": totals_row,
        "county_sums": county_sums,
        "has_total": total_col is not None,
        "complete_groups": bool(plan["candidates"]) and all(
            c["votes_col"] is not None and c["pct_col"] is not None
            for c in plan["candidates"]
        ),
    }
    return df, info


# ────────────────────────────────────────────────────────────────────────────
# STATE PAGE PARSER
# ────────────────────────────────────────────────────────────────────────────

def parse_state_page(
    text: str, year: int, state: str, state_code: str
) -> Tuple[pd.DataFrame, Dict]:
    """Parse one "{year} United States presidential election in {State}"
    article into county-level records.

    Heading sections are tried in document order and the first table that
    yields rows AND looks like a general-election table (Total column or
    complete #/% candidate pairs) wins — this skips primary-election tables
    that share the same heading text (WV 2012, DC 2012). When no heading
    matches at all, the whole page is scanned as a fallback (DC 2004).
    """
    candidate_tables: List[str] = []
    for m in _COUNTY_HEADING_RE.finditer(text):
        rest = text[m.end():]
        nxt = _ANY_HEADING_RE.search(rest)
        section = rest[: nxt.start()] if nxt else rest
        candidate_tables.extend(_wikitables(section))
    if not candidate_tables:
        candidate_tables = _wikitables(text)

    best_df, best_info = pd.DataFrame(columns=_COLUMNS), {}
    for table in candidate_tables:
        df, info = parse_county_table(table, year, state, state_code)
        if not len(df):
            continue
        if info.get("has_total") or info.get("complete_groups"):
            return df, info
        if not len(best_df):       # last resort: incomplete table
            best_df, best_info = df, info
    return best_df, best_info


# ────────────────────────────────────────────────────────────────────────────
# PRESIDENTIAL OPINION POLLING
# ────────────────────────────────────────────────────────────────────────────

NATIONWIDE_POLLING_TITLE = (
    "Nationwide opinion polling for the {year} United States presidential election"
)
STATEWIDE_POLLING_TITLE = (
    "Statewide opinion polling for the {year} United States presidential election"
)

#: Long-format polling schema (superset of the Senate polling families).
_POLLING_COLUMNS = [
    "Year", "Scope", "State", "State_Code", "Matchup", "Poll_Type",
    "Poll_Source", "Date", "Date_Start", "Date_End", "Sample", "MoE",
    "Candidate", "Party", "Pct", "Incumbent", "Date_Original",
]

_POLLING_FAMILIES = ("national_polling", "state_polling")

_STATE_CODES: Dict[str, str] = {name: code for _, name, code in PLACES}
_STATE_CODES["Washington"] = "WA"        # polling articles drop the disambiguator
_STATE_CODES["Washington, D.C."] = "DC"
_STATE_CODES["Washington D.C."] = "DC"
_STATE_CODES["United States"] = "US"
_STATE_CODES["National"] = "US"
_CODE_TO_NAME: Dict[str, str] = {code: name for _, name, code in PLACES}

#: Sections of the dedicated polling articles that never hold attributable
#: poll tables.  Generic sections ('Most recent polling', 'Polling
#: aggregation in swing states', ...) are excluded by the state-name check in
#: the statewide-article parser; this set keeps the nationwide article walker
#: out of boilerplate.
_POLLING_SKIP_SECTIONS = {
    "see also", "notes", "references", "external links", "further reading",
    "bibliography", "navigation", "citations", "sources", "limitations",
    "forecasts",
}

#: Full party labels used in presidential polling-table headers
#: ('Kamala Harris {{nobold|Democratic}}') → single-letter abbreviations, the
#: format the shared Senate ``parse_candidate_header`` understands.
_POLL_PARTY_ABBR: Dict[str, str] = {
    "democratic": "D", "democrat": "D", "democratic party": "D",
    "republican": "R", "republican party": "R",
    "independent": "I", "independent politician": "I",
    "libertarian": "L", "libertarian party": "L",
    "green": "G", "green party": "G",
    "constitution": "C", "constitution party": "C",
}

_HEADING_RE = re.compile(r"(?m)^(=+)\s*(.*?)\s*\1\s*$")
_BOLD_VS_RE = re.compile(r"'''([^']{3,160}? vs\.?\s?[^']{0,160}?)'''")
_ROWSPAN_RE = re.compile(r'rowspan\s*=\s*["\']?([2-9]\d?)', re.I)

#: a bare percentage sub-column header of the 2016-generation tables
_PCT_HEADER_LABELS = {"%", "pct", "pct.", "percent", "percentage"}
#: a cell value that is a usable polling percentage (candidate names, poll
#: labels and markup leftovers are not)
_PCT_VALUE_OK_RE = re.compile(r"^\s*[≈~<>±+]?\s*\d[\d.,]*\s*%?\s*$")


def polling_article_titles(year: int) -> List[str]:
    """The cycle's two dedicated polling articles (either may not exist)."""
    return [
        NATIONWIDE_POLLING_TITLE.format(year=year),
        STATEWIDE_POLLING_TITLE.format(year=year),
    ]


def _prepare_polling_wikitext(text: str) -> str:
    """Pre-process a polling article for the shared Senate parsers.

    Presidential polling tables carry the candidate's party in a
    ``{{nobold|Democratic}}`` wrapper under the name; resolving those wrappers
    to the '(D)'-style marker that ``parse_candidate_header`` understands
    yields proper party values (the raw label would otherwise be deleted with
    the rest of the templates, leaving party unknown).  Other formatting
    wrappers are unwrapped to their content.
    """
    def _nobold(m: "re.Match[str]") -> str:
        inner = remove_wikilinks(m.group(1))
        key = re.sub(r"\s+", " ", inner).strip().lower()
        abbr = _POLL_PARTY_ABBR.get(key)
        return f"({abbr})" if abbr else inner.strip()

    text = re.sub(r"\{\{\s*nobold\s*\|([^{}]*)\}\}", _nobold, text, flags=re.I)
    return unwrap_format_templates(text)


def _clean_heading_title(title: str) -> str:
    """'[[Washington, D.C.|District of Columbia]]' → 'District of Columbia'."""
    t = re.sub(r"\{\{[^}]*\}\}", "", title or "")
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", remove_wikilinks(t)).strip()


def _heading_segments(text: str) -> List[Tuple[int, str, str]]:
    """``(level, title, body)`` for every heading-delimited segment in
    document order; text before the first heading comes first as
    ``(0, "", preamble)``."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [(0, "", text)]
    segments: List[Tuple[int, str, str]] = []
    if matches[0].start() > 0:
        segments.append((0, "", text[: matches[0].start()]))
    for i, m in enumerate(matches):
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append(
            (len(m.group(1)), _clean_heading_title(m.group(2)),
             text[m.end(): body_end])
        )
    return segments


def _wikitables_with_pos(segment: str) -> List[Tuple[int, str]]:
    """``(offset, table)`` for every outermost ``{| ... |}`` table inside
    *segment* (nesting-aware, same depth logic as :func:`_wikitables`)."""
    found: List[Tuple[int, str]] = []
    depth, start, i = 0, None, 0
    while i < len(segment):
        if segment.startswith("{|", i):
            if depth == 0:
                start = i
            depth += 1
            i += 2
        elif segment.startswith("|}", i):
            depth -= 1
            if depth == 0 and start is not None:
                found.append((start, segment[start:i + 2]))
                start = None
            i += 2
        else:
            i += 1
    return found


def _iter_poll_tables(text: str) -> List[Tuple[List[str], str, str]]:
    """Every wikitable in *text* with its heading context.

    Returns ``(context_titles, table, text_before_table)`` triples where
    *context_titles* is the cleaned heading path containing the table
    (nearest heading last) and *text_before_table* is the wikitext between
    the current section's start and the table (used for bold matchup lines).
    Tables under boilerplate sections (See also / References / ...) are
    skipped.
    """
    out: List[Tuple[List[str], str, str]] = []
    stack: List[Tuple[int, str]] = []
    for level, title, body in _heading_segments(text):
        while stack and stack[-1][0] >= level:
            stack.pop()
        if level > 0:
            stack.append((level, title))
        titles = [t for _, t in stack]
        if any(t.lower() in _POLLING_SKIP_SECTIONS for t in titles):
            continue
        for pos, table in _wikitables_with_pos(body):
            out.append((titles, table, body[:pos]))
    return out


def _matchup_of(context_titles: List[str], pre_text: str) -> str:
    """Best-effort matchup label for a polling table.

    The last bold 'A vs. B' line before the table wins (the state articles
    label their tables that way); otherwise the nearest heading that itself
    names a matchup (the nationwide article's level-3 matchup sections) or a
    race-shape heading ('Two-way race', ... as in the 2016 nationwide
    article).
    """
    cleaned = _strip_refs(pre_text or "")
    m = None
    for m in _BOLD_VS_RE.finditer(cleaned):
        pass
    if m is not None:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    for title in reversed(context_titles):
        low = title.lower()
        if " vs" in low or "race" in low:
            return title
    return ""


def _classify_poll(table: str, context_titles: List[str], matchup: str) -> str:
    """'Hypothetical' | 'Aggregate' | 'General' for one polling table."""
    joined = " ".join(context_titles + [matchup]).lower()
    head = table.split("|-", 1)[0].lower()
    if "hypothetical" in joined:
        return "Hypothetical"
    if "aggregate" in joined or "aggregation" in head:
        return "Aggregate"
    return "General"


def _materialize_body_rowspans(table: str) -> str:
    """Rebuild a wikitable with body-row ``rowspan`` inheritance made explicit.

    The 2016-era statewide polling tables vertically merge a candidate's
    percentage across consecutive polls (``rowspan="2"`` '48%' covering two
    pollster rows); the shared Senate parser aligns rows positionally against
    the header, so a row missing the inherited cell would shift every later
    value one column left.  Header rows ('!' rows) are copied verbatim so
    colspan group headers keep working; only body rows are rebuilt, one cell
    per occupied column.
    """
    out_lines: List[str] = []
    pending: Dict[int, Tuple[str, int]] = {}
    ncols = 0
    for chunk in re.split(r"(?m)^\|-.*$", table):
        cells = _row_cells(chunk)
        if not cells:
            continue
        if all(h for h, _ in cells):                     # header row: verbatim
            for line in chunk.split("\n"):
                s = line.strip()
                if (not s or s.startswith("{|") or s.startswith(("|+", "|-"))
                        or s.startswith("|}")):
                    continue
                out_lines.append(s)
            continue
        occupied = {c: v for c, (v, rem) in pending.items() if rem > 0}
        cur: Dict[int, str] = dict(occupied)
        col = 0
        for _h, raw in cells:
            attrs, content = _split_attrs_content(raw)
            ad = _attrs_dict(attrs)
            rs, cs = ad["rowspan"], ad["colspan"]
            while col in cur:
                col += 1
            for k in range(cs):
                cur[col + k] = content
                if rs > 1:
                    pending[col + k] = (content, rs)
            col += cs
        if cur:
            ncols = max(ncols, max(cur) + 1)
        # explicit row separator: the shared parser splits body rows on '|-'
        out_lines.append("|-")
        for c in range(ncols):
            out_lines.append("| " + cur.get(c, ""))
        pending = {c: (v, r - 1) for c, (v, r) in pending.items() if r - 1 > 0}
    return "\n".join(["{|"] + out_lines + ["|}"])


def _collapse_pct_pair_columns(table: str) -> str:
    """Collapse 'Label | %' column pairs of the 2016-generation tables.

    The 2016-era statewide presidential polling tables label their candidate
    columns generically and put the percentage in a bare '%' sub-column
    (``!Democrat !!% !!Republican !!%``) with the candidate *name* in the
    value cell.  The shared Senate parser models one column per candidate, so
    each pair is collapsed to a single ``Label`` column holding the poll's
    percentage (party is inferred from the label later); tables without such
    pairs pass through unchanged.
    """
    grid: List[Tuple[bool, List[Tuple[str, str]]]] = []   # (is_header, [(attrs, content)])
    for chunk in re.split(r"(?m)^\|-.*$", table):
        cells = _row_cells(chunk)
        if not cells:
            continue
        grid.append((
            all(h for h, _ in cells),
            [_split_attrs_content(raw) for _h, raw in cells],
        ))
    if not grid:
        return table

    # locate the first all-header row and its pair map
    header_idx = next((i for i, (is_hdr, _c) in enumerate(grid) if is_hdr), None)
    if header_idx is None:
        return table
    header_cells = grid[header_idx][1]
    cleaned = [_clean_text(content) for _a, content in header_cells]
    pairs: Dict[int, str] = {}            # label col -> label text
    for i, label in enumerate(cleaned[:-1]):
        if (label and cleaned[i + 1].lower() in _PCT_HEADER_LABELS
                and i not in pairs):
            pairs[i] = label
    if not pairs:
        return table

    out_lines: List[str] = []
    for i, (is_hdr, cells) in enumerate(grid):
        out_lines.append("|-")
        for j, (attrs, content) in enumerate(cells):
            prefix = "! " if is_hdr else "| "
            if j in pairs:
                label = pairs[j]
                abbr = _POLL_PARTY_ABBR.get(
                    re.sub(r"[^a-z ]", "", label.lower()).strip())
                # the pair value: the percentage-looking cell wins
                a_clean, b_clean = _clean_text(content), ""
                if j + 1 < len(cells):
                    b_clean = _clean_text(cells[j + 1][1])
                if is_hdr:
                    out_lines.append(
                        f"! {label} ({abbr})" if abbr else f"! {label}")
                else:
                    value = (b_clean if _PCT_VALUE_OK_RE.match(b_clean)
                             else a_clean if _PCT_VALUE_OK_RE.match(a_clean)
                             else b_clean or a_clean)
                    out_lines.append("| " + value)
            elif j - 1 in pairs:
                continue                                  # consumed '%' column
            elif attrs:
                out_lines.append(f"{prefix}{attrs}|{content}")
            else:
                out_lines.append(prefix + content)
    return "\n".join(["{|"] + out_lines + ["|}"])


def _parse_one_poll(table: str, state_name: str, context_titles: List[str],
                    pre_text: str) -> Optional[pd.DataFrame]:
    """Parse one polling table into a long-format frame with ``Matchup`` and
    ``Poll_Type`` columns attached (``Primary_Type`` placeholder dropped)."""
    if _ROWSPAN_RE.search(table):
        try:
            table = _materialize_body_rowspans(table)
        except Exception:                # never lose the row over a rebuild bug
            logger.debug("rowspan materialisation failed for a %s table",
                         state_name, exc_info=True)
    table = _collapse_pct_pair_columns(table)
    wide = parse_polling_table_universal(
        "\n== Polling ==\n" + table, state_name
    )
    if wide.empty:
        return None
    long_df = wide_to_long_polls(wide, state_name)
    if long_df.empty:
        return None
    matchup = _matchup_of(context_titles, pre_text)
    long_df["Matchup"] = matchup
    long_df["Poll_Type"] = _classify_poll(table, context_titles, matchup)
    return long_df.drop(columns=["Primary_Type"], errors="ignore")


def _split_trailing_party(candidate: str, party: str) -> Tuple[str, str]:
    """Split a trailing full party label ('Kamala Harris Democratic',
    'Kamala Harris (Democrat)') off a candidate header, and map bare party
    headers ('Democrat') to their party.  Header generations without
    ``{{nobold}}`` wrappers never match the shared parser's '(R)' pattern."""
    if party and party != "Unknown":
        return candidate, party
    if not isinstance(candidate, str) or not candidate.strip():
        return candidate, party
    c = re.sub(r"\s+", " ", candidate).strip()
    low = c.lower().strip(".,()")
    if low in _POLL_PARTY_ABBR:                      # whole header IS a party
        return c, _POLL_PARTY_ABBR[low]
    m = re.search(r"\s+(\S+)$", c)
    if m:
        word = m.group(1).lower().strip(".,()")
        abbr = _POLL_PARTY_ABBR.get(word)
        if abbr:
            name = c[: m.start()].strip().rstrip("()-–— ")
            if name:
                return name, abbr
    return c, party or "Unknown"


def _finalize_polling(frames: List[pd.DataFrame], year: int,
                      scope: str) -> pd.DataFrame:
    """Concat raw long frames, stamp ``Year`` / ``Scope``, enrich parties,
    de-duplicate overlapping sources and normalise dates."""
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "Year", int(year))
    df.insert(1, "Scope", scope)
    pairs = [
        _split_trailing_party(c, p)
        for c, p in zip(df.get("Candidate", ""), df.get("Party", ""))
    ]
    df["Candidate"] = [p[0] for p in pairs]
    df["Party"] = [p[1] for p in pairs]
    # a polling percentage is numeric; candidate names or markup leftovers
    # leaking into the Pct column (mis-aligned source cells) are dropped
    if len(df):
        df = df[df["Pct"].astype(str).str.match(_PCT_VALUE_OK_RE, na=False)]
    df = df.drop_duplicates(
        subset=["Year", "State", "Poll_Source", "Date", "Candidate", "Pct"],
        keep="first",
    ).reset_index(drop=True)
    df = normalize_polling_date_columns(df)
    return df.reindex(columns=_POLLING_COLUMNS)


def _nationwide_frames(text: str) -> List[pd.DataFrame]:
    """Raw long frames from the cycle's nationwide polling article."""
    if not text:
        return []
    frames: List[pd.DataFrame] = []
    for titles, table, pre in _iter_poll_tables(_prepare_polling_wikitext(text)):
        frame = _parse_one_poll(table, "National", titles, pre)
        if frame is not None:
            frame["State"] = "National"
            frame.insert(1, "State_Code", "US")
            frames.append(frame)
    return frames


def _statewide_article_frames(text: str) -> List[pd.DataFrame]:
    """Raw long frames from the cycle's statewide polling article.

    Only sections whose heading names a jurisdiction are mined — tables under
    generic sections ('Most recent polling', ...) cannot be attributed to a
    single state.  2020+ vintages hold no real tables (they transclude the
    state articles via ``{{#section-h:...}}``) and naturally yield nothing.
    """
    if not text:
        return []
    frames: List[pd.DataFrame] = []
    for _level, title, body in _heading_segments(_prepare_polling_wikitext(text)):
        code = _STATE_CODES.get(title)
        if code is None or code == "US":
            continue
        state_name = _CODE_TO_NAME[code]
        for titles, table, pre in _iter_poll_tables(body):
            frame = _parse_one_poll(table, state_name, titles, pre)
            if frame is not None:
                frame["State"] = state_name
                frame.insert(1, "State_Code", code)
                frames.append(frame)
    return frames


def _state_article_frames(text: str, state: str, code: str) -> List[pd.DataFrame]:
    """Raw long frames from one state presidential article's polling sections
    (``== Polling ==`` / ``== Polls ==`` at any level, e.g. under Campaign)."""
    if not text:
        return []
    frames: List[pd.DataFrame] = []
    prepared = _prepare_polling_wikitext(text)
    for head_start, _head_end, scope_end, _lvl in iter_polling_spans(prepared):
        scope = prepared[head_start:scope_end]
        for titles, table, pre in _iter_poll_tables(scope):
            frame = _parse_one_poll(table, state, titles, pre)
            if frame is not None:
                frame["State"] = state
                frame.insert(1, "State_Code", code)
                frames.append(frame)
    return frames


def collect_presidential_polling(
    year: int,
    state_articles: List[Tuple[str, str, Optional[str]]],
    national_text: Optional[str],
    statewide_text: Optional[str],
) -> "Tuple[pd.DataFrame, pd.DataFrame]":
    """Polling for one presidential cycle from every available source.

    *state_articles* carries ``(state, code, wikitext)`` per jurisdiction (the
    texts already fetched for the county results).  Returns
    ``(national_polling, state_polling)`` in :data:`_POLLING_COLUMNS` layout
    (empty frames when nothing parsed).
    """
    national = _finalize_polling(_nationwide_frames(national_text), year, "National")

    state_frames: List[pd.DataFrame] = []
    state_frames.extend(_statewide_article_frames(statewide_text))
    for state, code, text in state_articles:
        state_frames.extend(_state_article_frames(text, state, code))
    state = _finalize_polling(state_frames, year, "State")
    return national, state


def _rebuild_polling_combined(pres_dir: str, family: str) -> Optional[pd.DataFrame]:
    """Rebuild ``presidential_{family}_all.csv`` from every per-year file on
    disk (mirrors the Senate pipeline: per-year files are the source of truth,
    so a single-cycle run can never clobber multi-decade history)."""
    import glob

    parts: List[pd.DataFrame] = []
    for path in sorted(glob.glob(os.path.join(pres_dir, f"presidential_{family}_*.csv"))):
        name = os.path.basename(path)
        if not re.fullmatch(rf"presidential_{family}_\d{{4}}\.csv", name):
            continue
        try:
            df_year = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        except Exception:
            logger.warning("Could not read %s for the combined file — skipping", path)
            continue
        if len(df_year):
            parts.append(df_year)
    if not parts:
        return None
    combined = pd.concat(parts, ignore_index=True)
    if "Year" in combined.columns:
        combined = combined.sort_values(
            ["Year"] + [c for c in ("State", "Date", "Poll_Source", "Candidate")
                        if c in combined.columns],
            kind="stable",
        ).reset_index(drop=True)
    return combined


# ────────────────────────────────────────────────────────────────────────────
# BATCH DRIVER
# ────────────────────────────────────────────────────────────────────────────

def run(
    start_year: int = 2018,
    end_year: int = 2024,
    output_dir: str = "data",
    client: Optional[WikiAPIClient] = None,
    include_polling: bool = True,
) -> pd.DataFrame:
    """CLI entry point: process presidential cycles from *start_year* to
    *end_year*, writing CSVs under ``<output_dir>/presidential/``.

    When *include_polling* is set (default) the cycle's opinion-polling
    articles are fetched alongside the per-state result articles (two extra
    batched titles per cycle) and
    ``presidential_{national,state}_polling_{year}.csv`` are written next to
    the results; pass ``include_polling=False`` (CLI
    ``--no-include-polling``) for the results-only behaviour.
    """
    years = presidential_years(start_year, end_year)
    if not years:
        logger.warning(
            "No presidential election year (divisible by 4) in [%d, %d] — "
            "nothing to do.", start_year, end_year,
        )
        return pd.DataFrame()
    logger.info("Presidential cycles to process: %s", years)

    pres_dir = os.path.join(output_dir, "presidential")
    os.makedirs(pres_dir, exist_ok=True)

    client = client or get_default_client()

    all_frames: List[pd.DataFrame] = []
    meta: Dict = {"years": {}, "missing_articles": [], "polling": {}}

    for year in years:
        titles = [page_title(year, suffix) for suffix, _, _ in PLACES]
        nat_title = sw_title = None
        if include_polling:
            nat_title, sw_title = polling_article_titles(year)
            titles = titles + [nat_title, sw_title]
        content = client.fetch_wikitext(titles)

        frames: List[pd.DataFrame] = []
        state_articles: List[Tuple[str, str, Optional[str]]] = []
        year_meta: Dict = {}
        for suffix, state, code in PLACES:
            title = page_title(year, suffix)
            text = content.get(title)
            state_articles.append((state, code, text))
            if not text:
                logger.warning("  %s %-22s — article missing, skipped", year, state)
                meta["missing_articles"].append(title)
                year_meta[code] = None
                continue
            df, info = parse_state_page(text, year, state, code)
            if not len(df):
                logger.warning(
                    "  %s %-22s — no county rows parsed (article lacks the "
                    "standard results table)", year, state,
                )
                year_meta[code] = {"rows": 0, "counties": 0}
                continue

            # totals cross-check (trailing 'Totals' row vs county sums)
            diffs = {}
            if info.get("totals_row") and info.get("county_sums"):
                for cand, tot in info["totals_row"].items():
                    s = info["county_sums"].get(cand)
                    if s:
                        diffs[cand] = round((s - tot) / tot * 100, 3) if tot else None

            tv = df["total_votes"].dropna()
            year_meta[code] = {
                "state": state,
                "rows": info.get("rows", int(len(df))),
                "counties": info.get("counties", 0),
                "candidates": sorted(df["candidate"].unique().tolist()),
                "total_votes": int(tv.max()) if len(tv) else None,
                "totals_row": info.get("totals_row") or None,
                "county_sums_vs_totals_pct": diffs or None,
            }
            logger.info(
                "  %s %-22s %4d rows / %3d %s", year, state, len(df),
                info.get("counties", 0),
                df["subdivision_type"].iloc[0].lower(),
            )
            frames.append(df)

        if frames:
            df_year = pd.concat(frames, ignore_index=True)
            path = os.path.join(pres_dir, f"presidential_results_{year}.csv")
            df_year.to_csv(path, index=False)
            logger.info("  saved -> %s", path)
            all_frames.append(df_year)

        if include_polling:
            national_text = content.get(nat_title) if nat_title else None
            statewide_text = content.get(sw_title) if sw_title else None
            if not national_text:
                meta["missing_articles"].append(nat_title)
            if not statewide_text:
                meta["missing_articles"].append(sw_title)
            nat_df, st_df = collect_presidential_polling(
                year, state_articles, national_text, statewide_text,
            )
            if len(nat_df):
                path = os.path.join(pres_dir, f"presidential_national_polling_{year}.csv")
                nat_df.to_csv(path, index=False)
                logger.info("  saved -> %s (%d rows)", path, len(nat_df))
            if len(st_df):
                path = os.path.join(pres_dir, f"presidential_state_polling_{year}.csv")
                st_df.to_csv(path, index=False)
                logger.info("  saved -> %s (%d rows)", path, len(st_df))
            meta["polling"][year] = {
                "nationwide_article": bool(national_text),
                "statewide_article": bool(statewide_text),
                "national_rows": int(len(nat_df)),
                "state_rows": int(len(st_df)),
            }
            logger.info(
                "  %s polling — national: %d rows, state: %d rows",
                year, len(nat_df), len(st_df),
            )
        meta["years"][year] = year_meta

    combined: Optional[pd.DataFrame] = None
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        path = os.path.join(pres_dir, "presidential_results_all.csv")
        combined.to_csv(path, index=False)
        logger.info("Combined -> %s (%s rows)", path, f"{len(combined):,}")

    wrote_polling = False
    if include_polling:
        # rebuild the combined polling files from ALL per-year files on disk,
        # so single-cycle runs never clobber multi-cycle history
        for family in _POLLING_FAMILIES:
            poll_combined = _rebuild_polling_combined(pres_dir, family)
            if poll_combined is not None and len(poll_combined):
                path = os.path.join(pres_dir, f"presidential_{family}_all.csv")
                poll_combined.to_csv(path, index=False)
                logger.info("Polling combined -> %s (%s rows)",
                            path, f"{len(poll_combined):,}")
                wrote_polling = True

    if combined is not None or wrote_polling:
        ts = time.strftime("%Y%m%d_%H%M%S")
        with open(os.path.join(pres_dir, f"presidential_metadata_{ts}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2, default=str)
    return combined if combined is not None else pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run()
