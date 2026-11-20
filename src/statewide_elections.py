"""
statewide_elections.py — Parse U.S. **statewide** executive election results
(Governor, Attorney General, Secretary of State, State Treasurer, State
Auditor and the state-dependent down-ballot offices) AND the accompanying
state-level opinion polling from Wikipedia, fetched via the MediaWiki Action
API.  For every parsed race the pipeline also derives the **vote margin**:
winner vs. runner-up in percentage points and — where the race article
publishes vote counts — in raw votes.

For each even year in [start_year, end_year] up to five overview articles are
fetched in a single batched request:

    {year} United States gubernatorial elections
    {year} United States attorney general elections
    {year} United States secretary of state elections
    {year} United States state treasurer elections
    {year} United States state auditor elections

(Secretary of State / State Treasurer overview articles do not exist for
2018 and State Auditor overviews exist only from 2022 — those cycles are
skipped and logged.)

STATE-DEPENDENT RACES: most down-ballot offices exist only in some states
and Wikipedia publishes **no** overview article for them (e.g. ``{y} United
States agriculture commissioner elections`` does not exist).  Their
per-state race articles are discovered FIRST from the **year election
page**: the ``Template:{y} United States elections`` navbox carries an
``Other statewide elections`` row (plus a ``Judicial`` row) that lists,
per state, exactly which down-ballot races ran that cycle — Alabama's
Board of Education + Public Service Commission, Arizona's Corporation
Commission, West Virginia's Agriculture Commissioner, North Carolina's
council of state, Washington's Land/Insurance/Superintendent races, the
judicial seats, and so on.  Each row link is either a dedicated race
article (``2024 Alabama Public Service Commission election``) or a
state-year umbrella anchor (``2024 Texas elections#Railroad
Commissioner``): dedicated articles are parsed whole, while umbrella
anchors are sliced to the anchored office section (election boxes, simple
result tables or an embedded ``{{Infobox election}}``) and parsed there.
The curated ``STATE_DEPENDENT_OFFICE_TITLES`` map (per-state office list
→ candidate titles fetched in batched article requests — a handful of API
requests per cycle, no search calls) runs as a second discovery source,
and an OPT-IN ``intitle:`` search fallback (``search_discovery=True``;
CLI ``--search-discovery``) covers gaps both miss.  Race titles that
redirect to a state-year umbrella article (``{y} {State} elections``) are
re-routed to the office's own umbrella section instead of being
mis-parsed, and duplicate article texts (redirect aliases) are parsed
only once.

VOTE MARGINS: each cycle also produces a per-race margin table (one row per
year × state × office × seat race) written to ``statewide_margins_{year}.csv``
and ``statewide_margins_all.csv``.  Margins are computed from the per-race
general-election results (vote counts — the authoritative source, incl.
fusion-ticket aggregation) and, where a race article is missing, from the
overview summary tables (percentage only); the ``source`` column records
which.  Multi-seat boards/commissions report the seat-cutoff margin (last
winner vs. first loser) with the number of seats in the ``seats`` column.

ODD (off-year) cycles in the range are processed by default
(``include_off_years=True``; CLI ``--include-off-years``/``--no-include-off-years``)
— New Jersey and Virginia gubernatorial elections, the
Kentucky/Louisiana/Mississippi statewide slate, and occasional special
elections.  Pass ``include_off_years=False`` (CLI:
``--no-include-off-years``) to process even (federal) years only.  Odd-year
overview articles exist for every cycle since 2001, so the same "Race
summary" parsing applies.

Each overview's ``== Race summary ==`` section carries one sortable table per
scope (``===States===`` and, for governor, ``=== Territories and federal
district ===``) with rows keyed by ``! [[#State|State]]`` and a Candidates
cell holding a ``{{Plainlist|* ...}}`` bullet per candidate:

    * {{Party stripe|Republican Party (US)}}{{aye}} '''[[Kay Ivey]]''' (Republican) 59.5%

Per-race primary/general results (vote counts included) are parsed from the
per-state race articles discovered via ``{{main|...}}`` links on each
overview — the same dynamic-discovery approach as the Senate pipeline.
Race articles carry ``{{Election box ...}}`` result templates under
level-2 headings (``== Democratic primary ==``, ``== Republican primary ==``,
``== Jungle primary ==``, ``== General election ==``, ...); boxes are
classified as primary or general by the heading they sit under, so titles
without the word "primary" (e.g. Mississippi's "2019 Republican") are
handled correctly.  A conservative fallback parses simple
Candidate/Votes/% wikitables in primary sections that have no boxes;
ranked-choice multi-round tables (Virginia 2021 GOP convention) and
by-county/by-district breakdowns are intentionally skipped.

STATE-LEVEL POLLING (``include_polling=True`` by default; CLI
``--include-polling``/``--no-include-polling``): the same race-article
fetches are reused, so polling extraction costs no extra API calls.  Two
sources are mined per race:

* the race article's own ``== Polling ==`` / ``=== Polls ===`` sections;
* a dedicated ``Opinion polling for the {year} {State} ... election``
  article when the race article links one via ``{{main|...}}`` — the bulk
  of gubernatorial polling lives there (e.g. Virginia/New Jersey 2021,
  Kentucky 2023).

Polling tables are parsed with the Senate pipeline's parsers
(``senate_elections.parse_polling_table_universal`` +
``wide_to_long_polls``), which support both the 2018-era table generation
and the 2020+ sortable generation.  Polls are classified primary vs.
general positionally from the level-2 headings (Democratic/Republican
primary, jungle primary, General election).  Long format: one row per
poll per candidate.

Result columns (written under *output_dir*, default ``data/statewide/``):
    year, state, state_code, office, candidate, party, percentage,
    winner, incumbent

Margin columns (one row per year × state × office × seat race):
    year, state, state_code, office, election, race, seats,
    winner_candidate, winner_party, winner_percentage, winner_votes,
    runner_up_candidate, runner_up_party, runner_up_percentage,
    runner_up_votes, total_votes, margin_pct, margin_votes,
    margin_pct_votes, source

``margin_pct`` is the published percentage-point margin (winner % −
runner-up %); ``margin_votes`` the raw vote difference and
``margin_pct_votes`` the vote-share margin ``100 × margin_votes /
total_votes`` (both only when the race article publishes vote counts).
Louisiana-style two-stage races contribute the FINAL stage (runoff) row.
Unopposed races list an empty runner-up and a ``margin_pct`` equal to the
winner's share.

Polling columns (same schema as the Senate polling families, plus
``State_Code`` and ``Office``):
    Year, State, State_Code, Office, Primary_Type, Poll_Source, Date,
    Date_Start, Date_End, Sample, MoE, Candidate, Party, Pct, Incumbent,
    Date_Original

    statewide_results_{year}.csv            per year, overview summaries
                                            (general election, % only)
    statewide_results_all.csv               combined across ALL runs on disk
    statewide_primary_results_{year}.csv    per-race primary results
                                            (votes + %, from race articles)
    statewide_primary_results_all.csv
    statewide_general_results_{year}.csv    per-race general results
                                            (votes + %, from race articles)
    statewide_general_results_all.csv
    statewide_primary_polling_{year}.csv    per-race primary polling
                                            (long format, race + polling
                                            articles)
    statewide_primary_polling_all.csv
    statewide_general_polling_{year}.csv    per-race general polling
                                            (long format, race + polling
                                            articles)
    statewide_general_polling_all.csv
    statewide_margins_{year}.csv            per-race vote margins (winner /
                                            runner-up, pp + vote margins,
                                            one row per office race)
    statewide_margins_all.csv

Usage:
    python cli.py statewide --start-year 2018 --end-year 2024
    python cli.py statewide --start-year 2001 --end-year 2025
    python cli.py statewide --start-year 2018 --end-year 2024 --no-include-off-years
    python cli.py statewide --start-year 2017 --end-year 2023 --no-include-polling
    python cli.py statewide --start-year 2024 --end-year 2024 --offices "state treasurer,agriculture commissioner"
    python cli.py statewide --start-year 2018 --end-year 2024 --no-search-discovery
    python statewide_elections.py
"""

from __future__ import annotations

import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import pandas as pd

# Polling-table machinery is shared with the Senate pipeline: Wikipedia uses
# the same polling-table generations (2018-era 'align=center|' cells and the
# 2020+ sortable generation) in gubernatorial and other statewide race
# articles, so the battle-tested Senate parsers are reused instead of a
# second, drifting implementation.  senate_elections has no statewide
# dependency, so this import cannot be circular.
from senate_elections import (
    extract_infobox_incumbent,
    iter_hypothetical_polling_spans,
    iter_polling_spans,
    normalize_polling_date_columns,
    parse_hypothetical_tables,
    parse_polling_table_universal,
    parse_polling_tables_classified,
    wide_to_long_polls,
)
from wiki_utils import (
    WikiAPIClient,
    clean_wikitext,
    even_years,
    extract_incumbent_flag,
    get_default_client,
    remove_wikilinks,
    set_default_client,
    unwrap_format_templates,
)

logger = logging.getLogger("statewide_elections")


def _odd_years(start_year: int, end_year: int) -> List[int]:
    """
    Odd (off-year) years within ``[start_year, end_year]``, inclusive.

    Defined locally — deliberately not imported from ``wiki_utils`` — so this
    module stays compatible with stock wiki_utils versions that only ship
    ``even_years``.  ``_odd_years(2019, 2024)`` → ``[2019, 2021, 2023]``.
    Returns an empty list when the range contains no odd year.
    """
    start = start_year if start_year % 2 == 1 else start_year + 1
    end = end_year if end_year % 2 == 1 else end_year - 1
    return list(range(start, end + 1, 2)) if start <= end else []


# ────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ────────────────────────────────────────────────────────────────────────────

OFFICES: Dict[str, str] = {
    "governor": "{y} United States gubernatorial elections",
    "attorney general": "{y} United States attorney general elections",
    "secretary of state": "{y} United States secretary of state elections",
    "state treasurer": "{y} United States state treasurer elections",
    # State Auditor overview articles exist only for the 2022+ cycles; the
    # missing earlier cycles (and every office below, which has NO overview
    # article at all) are recovered through search-based race discovery.
    "state auditor": "{y} United States state auditor elections",
}

#: Canonical office labels of the state-dependent families recovered through
#: search discovery (CLI ``--offices`` accepts any of the labels below plus
#: the OFFICES keys).
SEARCH_RACE_OFFICES: Dict[str, List[str]] = {
    "agriculture commissioner": [
        "agriculture commissioner", "commissioner of agriculture",
        "secretary of agriculture",
    ],
    "land commissioner": [
        "land commissioner", "public lands commissioner",
        "commissioner of public lands", "commissioner of state lands",
    ],
    "insurance commissioner": [
        "insurance commissioner", "commissioner of insurance",
    ],
    "labor commissioner": [
        "labor commissioner", "commissioner of labor",
        "commissioner of labor and industries",
    ],
    "comptroller": [
        "state comptroller", "comptroller of public accounts",
        "state controller", "comptroller", "controller",
    ],
    "state auditor": [
        "state auditor", "auditor of state", "auditor of accounts",
        "auditor of public accounts", "auditor",
    ],
    "superintendent of public instruction": [
        "superintendent of public instruction",
    ],
}

#: Curated per-state map of state-dependent offices (user-supplied).  This is
#: the PRIMARY discovery mechanism: it turns directly into candidate race
#: titles (``{year} {State} {piece} election``) that are fetched in one
#: batched API request per ~50 titles — a handful of requests per cycle —
#: instead of dozens of ``intitle:`` search calls that can trip Wikipedia's
#: rate limiter (403 "Too Many Reqs").  Each entry is
#: ``(canonical_office_label, [title pieces to try])``; pieces are ordered by
#: likelihood and wrong guesses are simply logged as missing articles.
#: Only some of these offices are single-seat — boards and commissions
#: (Public Service Commission, State Board of Education, Corporation
#: Commission, university boards, …) elect multiple members, and the margin
#: engine reports the seat-cutoff margin for them (last winner vs. first
#: loser) with a ``seats`` column recording how many were elected.
#: "(sp)" in the source list marks offices whose races appear as special
#: elections only.
STATE_DEPENDENT_OFFICE_TITLES: Dict[str, List[Tuple[str, List[str]]]] = {
    "Alabama": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture and Industries", "Commissioner of Agriculture",
          "Agriculture Commissioner"]),
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("public service commission", ["Public Service Commission"]),
    ],
    "Arizona": [
        ("corporation commission", ["Corporation Commission"]),
        ("state mine inspector", ["State Mine Inspector", "Mine Inspector"]),
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
    "Arkansas": [
        ("land commissioner",
         ["Commissioner of State Lands", "Land Commissioner"]),
    ],
    "Delaware": [
        ("insurance commissioner", ["Insurance Commissioner",
                                    "Commissioner of Insurance"]),
    ],
    "California": [
        ("board of equalization", ["Board of Equalization"]),
        ("insurance commissioner", ["Insurance Commissioner", "Commissioner of Insurance"]),
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
    "Colorado": [
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("board of regents", ["Board of Regents", "Regents"]),
    ],
    "Florida": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture", "Agriculture Commissioner"]),
    ],
    "Georgia": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture", "Agriculture Commissioner"]),
        ("insurance commissioner",
         ["Insurance Commissioner", "Commissioner of Insurance"]),
        ("labor commissioner", ["Labor Commissioner", "Commissioner of Labor"]),
        ("public service commission", ["Public Service Commission"]),
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "Superintendent of Schools",
          "State Superintendent"]),
        # state judicial slate — the navbox row links the plural
        # "{y} Georgia judicial elections" article
        ("judicial", ["Judicial"]),
    ],
    "Kentucky": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture", "Agriculture Commissioner"]),
        ("state auditor",
         ["Auditor of Public Accounts", "Auditor of State", "State Auditor",
          "Auditor"]),
    ],
    "Hawaii": [
        ("office of hawaiian affairs", ["Office of Hawaiian Affairs"]),
    ],
    "Idaho": [
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
    "Iowa": [
        ("agriculture commissioner",
         ["Secretary of Agriculture", "Agriculture Secretary"]),
    ],
    "Kansas": [
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("insurance commissioner", ["Insurance Commissioner", "Commissioner of Insurance"]),
    ],
    "Louisiana": [
        # "(sp)" — BESE races surface as special/standalone cycles
        ("state board of education",
         ["Board of Elementary and Secondary Education", "BESE", "Board of Education"]),
        ("public service commission", ["Public Service Commission"]),
    ],
    "Massachusetts": [
        ("governor's council", ["Governor's Council", "Governors Council"]),
    ],
    "Michigan": [
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("msu board of trustees",
         ["Michigan State University Board of Trustees", "MSU Board of Trustees",
          "State University Board of Trustees"]),
        ("um board of regents",
         ["University of Michigan Board of Regents", "UM Board of Regents"]),
        ("wsu board of governors",
         ["Wayne State University Board of Governors", "WSU Board of Governors"]),
    ],
    "Montana": [
        ("public service commission", ["Public Service Commission"]),
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
    "North Carolina": [
        # one multi-office article covers the whole council of state slate
        ("council of state", ["Council of State"]),
    ],
    "Nebraska": [
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("board of regents",
         ["Board of Regents", "University of Nebraska Board of Regents"]),
        ("public service commission", ["Public Service Commission"]),
    ],
    "New Hampshire": [
        ("executive council", ["Executive Council"]),
    ],
    "New Mexico": [
        ("land commissioner",
         ["Commissioner of Public Lands", "Land Commissioner"]),
        ("public education commission", ["Public Education Commission"]),
    ],
    "Nevada": [
        ("board of regents", ["Board of Regents"]),
    ],
    "North Dakota": [
        ("agriculture commissioner",
         ["Agriculture Commissioner", "Commissioner of Agriculture"]),
        ("insurance commissioner",
         ["Insurance Commissioner", "Commissioner of Insurance"]),
        ("public service commission", ["Public Service Commission"]),
        # "(sp)" — superintendent races surface as special/standalone cycles
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
        ("tax commissioner", ["Tax Commissioner"]),
    ],
    "Oklahoma": [
        ("corporation commission", ["Corporation Commission"]),
        ("insurance commissioner", ["Insurance Commissioner", "Commissioner of Insurance"]),
        ("labor commissioner", ["Labor Commissioner", "Commissioner of Labor"]),
        ("superintendent of public instruction",
         ["State Superintendent of Public Instruction", "Superintendent of Public Instruction"]),
    ],
    "Oregon": [
        ("labor commissioner",
         ["Commissioner of Labor", "Labor Commissioner",
          "Commissioner of Labor and Industries"]),
    ],
    "South Carolina": [
        ("agriculture commissioner",
         ["Agriculture Commissioner", "Commissioner of Agriculture"]),
        ("superintendent of education",
         ["Superintendent of Education", "State Superintendent of Education"]),
    ],
    "South Dakota": [
        ("commissioner of school and public lands",
         ["Commissioner of School and Public Lands", "School and Public Lands Commissioner"]),
        ("public utilities commission", ["Public Utilities Commission"]),
    ],
    "Texas": [
        ("agriculture commissioner",
         ["Agriculture Commissioner", "Commissioner of Agriculture"]),
        ("state board of education", ["State Board of Education", "Board of Education"]),
        ("land commissioner",
         ["Land Commissioner", "Commissioner of the General Land Office",
          "General Land Office Commissioner"]),
        ("railroad commissioner", ["Railroad Commissioner"]),
    ],
    "Louisiana": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture and Forestry", "Commissioner of Agriculture",
          "Agriculture Commissioner"]),
        ("insurance commissioner",
         ["Insurance Commissioner", "Commissioner of Insurance"]),
    ],
    "Mississippi": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture and Commerce", "Agriculture Commissioner",
          "Commissioner of Agriculture"]),
        ("state auditor",
         ["State Auditor", "Auditor of Public Accounts", "Auditor"]),
        ("insurance commissioner",
         ["Insurance Commissioner", "Commissioner of Insurance"]),
    ],
    "Pennsylvania": [
        # state judicial slate — "... judicial elections" plural article
        ("judicial", ["Judicial"]),
    ],
    "Washington": [
        ("land commissioner",
         ["Public Lands Commissioner", "Commissioner of Public Lands",
          "Land Commissioner"]),
        ("insurance commissioner",
         ["Insurance Commissioner", "Commissioner of Insurance"]),
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
    "West Virginia": [
        ("agriculture commissioner",
         ["Commissioner of Agriculture", "Agriculture Commissioner"]),
    ],
    "Wisconsin": [
        # state judicial slate — "... judicial elections" plural article
        ("judicial", ["Judicial"]),
    ],
    "Utah": [
        ("state board of education", ["State Board of Education", "Board of Education"]),
    ],
    "Wyoming": [
        ("superintendent of public instruction",
         ["Superintendent of Public Instruction", "State Superintendent"]),
    ],
}

#: Canonical office labels that ONLY the year-page navbox introduces (no
#: overview article, no curated-map title guess): the multi-office state
#: judicial slates, the North Carolina council of state slate and the
#: North Carolina lieutenant governor seat carried by that article.
YEAR_PAGE_ONLY_OFFICE_LABELS: tuple = (
    "judicial", "council of state", "lieutenant governor",
)

#: Office labels introduced by the curated map (boards, commissions and other
#: state-specific offices) — appended to the search families for ``--offices``
#: validation and margin-schema documentation.
MAP_ONLY_OFFICE_LABELS: tuple = tuple(dict.fromkeys(
    label
    for entries in STATE_DEPENDENT_OFFICE_TITLES.values()
    for label, _pieces in entries
    if label not in OFFICES and label not in SEARCH_RACE_OFFICES
))

#: Every canonical office label this pipeline understands (overview offices +
#: search families + curated-map families).
ALL_OFFICE_LABELS: tuple = tuple(OFFICES) + tuple(
    k for k in SEARCH_RACE_OFFICES if k not in OFFICES
) + MAP_ONLY_OFFICE_LABELS + tuple(
    label for label in YEAR_PAGE_ONLY_OFFICE_LABELS
    if label not in MAP_ONLY_OFFICE_LABELS
)

# canonical two-letter codes for states + territories that elect executives
STATE_CODES: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    # territories / federal district with elected executives
    "district of columbia": "DC", "guam": "GU", "guamanian": "GU",
    "northern mariana islands": "MP", "american samoa": "AS",
    "u.s. virgin islands": "VI", "united states virgin islands": "VI",
    "puerto rico": "PR",
    # longer forms appearing in race-article titles
    "new york state": "NY",
}

PARTY_NORMALIZE: Dict[str, str] = {
    "democratic party (us)": "Democratic",
    "republican party (us)": "Republican",
    "libertarian party (us)": "Libertarian",
    "green party (us)": "Green",
    "constitution party (us)": "Constitution",
    "independent american party": "Independent American",
    "progressive party (us)": "Progressive",
    "independent": "Independent",
    "independent politician": "Independent",
    "independent (politician)": "Independent",
    "no party preference (united states)": "No party preference",
    "no party preference": "No party preference",
    "nonpartisan": "Nonpartisan",
    "write-in": "Write-in",
}

_COLUMNS = [
    "year", "state", "state_code", "office", "candidate", "party",
    "percentage", "winner", "incumbent",
]

# ─── per-race primary/general outputs (from race articles) ────────────────

_RACE_COLUMNS = [
    "year", "state", "state_code", "office", "election", "row_type",
    "candidate", "party", "votes", "percentage", "winner", "incumbent",
    "race_article",
]

# Level-2 headings that hold nominating contests.  "convention" covers the
# Virginia GOP canvass-style nominating conventions; "jungle" covers the
# Louisiana nonpartisan blanket primary.  "Lieutenant gubernatorial
# nomination" sections match "nomination" but hold no vote tables, so they
# are filtered out naturally by the box/table parsers.
_PRIMARY_HEADING_RE = re.compile(r"primary|jungle|convention|nomination", re.I)
_GENERAL_HEADING_RE = re.compile(r"general election|runoff|^results$", re.I)

# Guards against overview-level {{main}} links (no state in the title).
_NON_STATE_TITLES_RE = re.compile(
    r"united states (?:gubernatorial|attorney general|secretary of state|"
    r"state treasurer|state auditor|agriculture commissioner|land commissioner|"
    r"insurance commissioner|labor commissioner|comptroller|controller|"
    r"superintendent of public instruction|auditor|treasurer|elections?$)",
    re.I,
)

#: State-year umbrella articles ("2023 Kentucky elections") that some race
#: titles redirect to.  Such pages carry one section per office, so parsing
#: them as a single race article would mis-attribute every office's results;
#: they are detected and skipped instead.  Signal 1: the infobox
#: ``election_name`` ends with the plural "elections".  Signal 2: three or
#: more distinct level-2 headings naming offices/chambers.
_UMBRELLA_NAME_RE = re.compile(r"^\s*\|\s*election_name\s*=\s*(.+?)\s*$", re.M)
_UMBRELLA_HEADING_RE = re.compile(
    r"(?im)^==\s*(governor|lieutenant governor|united states senate|"
    r"united states house of representatives|attorney general|"
    r"secretary of state|state treasurer|state auditor|auditor of public "
    r"accounts|auditor of accounts|commissioner of agriculture|agriculture "
    r"commissioner|land commissioner|commissioner of public lands|"
    r"commissioner of state lands|insurance commissioner|labor commissioner|"
    r"comptroller|state controller|superintendent of public instruction|"
    r"state legislature|ballot measures?|referenda?)\s*==\s*$")


def looks_like_umbrella_article(text: str, race_title: str = "") -> bool:
    """True when *text* is a state-year umbrella (or disambiguation) page
    rather than a single-race article.

    An infobox ``election_name`` ending in the plural "elections" is the
    primary signal — but multi-race articles such as ``2024 Georgia judicial
    elections`` or ``2024 North Carolina Council of State elections`` carry
    their own title there too.  When *race_title* is given and matches the
    infobox name, the page is the article itself, not an umbrella, and the
    election_name signal does not fire.
    """
    if not text:
        return False
    name = _UMBRELLA_NAME_RE.search(text)
    if name:
        val = re.sub(r"\s+", " ", name.group(1).strip().lower())
        if race_title:
            # the article's own infobox name may differ from the fetched
            # title only in the singular/plural of "election(s)" (the
            # 2024 North Carolina Council of State article carries
            # election_name '... Council of State election' while its
            # title is plural) — compare election-normalized forms and
            # accept the page as a single (multi-section) race article
            own = re.sub(r"\s+", " ", race_title.strip().lower())
            own = re.sub(r"elections?$", "election", own)
            if val == own or re.sub(r"elections?$", "election", val) == own:
                return False
        if val.endswith("elections"):
            return True
    if re.search(r"\{\{\s*disambiguation", text[:3000], re.I):
        return True
    heads = {h.strip().lower() for h in _UMBRELLA_HEADING_RE.findall(text)}
    return len(heads) >= 3


#: District/seat sub-headings inside a race article ("=== District 1 ===",
#: "=== Place 2 ===", "=== Seat 3 ===", "=== Position 5 ===", and the
#: possessive seat headings the judicial slates use — "=== Dillard's seat ===")
#: — multi-district articles put one results box per district under them.
#: The heading-marker level is captured (group 1) because umbrella slices and
#: multi-office articles carry these sub-headings at levels 3–6.
_DISTRICT_SUBHEADING_RE = re.compile(
    r"(?m)^(={3,6})\s*((?:district|place|seat|position)\s*\d+[A-Za-z]?"
    r"|[A-Za-z][A-Za-z .'’\-]*?'s seat)\s*\1\s*$", re.I)

#: A section heading that itself names a district/seat.
_DISTRICT_SECTION_RE = re.compile(
    r"^(?:district|place|seat|position)\s*\d+[A-Za-z]?$"
    r"|^[A-Za-z][A-Za-z .'’\-]*?'s seat$", re.I)

#: The "District N – " / "Seat name's seat – " prefix the parser stamps into
#: election labels.
_DISTRICT_ELECTION_PREFIX_RE = re.compile(
    r"^((?:district|place|seat|position)\s*\d+[A-Za-z]?"
    r"|[A-Za-z][A-Za-z .'’\-]*?'s seat)\s*[–—-]\s*", re.I)


def _state_code(name: str) -> str:
    return STATE_CODES.get(name.strip().lower(), "")


def normalize_party(raw: str) -> str:
    """'Republican Party (US)' / 'Arizona Democratic Party' -> short label."""
    p = clean_wikitext(raw or "").strip()
    if not p:
        return ""
    key = p.lower().strip()
    if key in PARTY_NORMALIZE:
        return PARTY_NORMALIZE[key]
    # strip trailing "(US)" style qualifiers
    key = re.sub(r"\s*\((?:us|united states)\)\s*$", "", key).strip()
    if key in PARTY_NORMALIZE:
        return PARTY_NORMALIZE[key]
    # 'Conservative Party of New York State' / 'Utah Constitution Party'
    key = re.sub(r"\s+of\s+[a-z ]+$", "", key)
    key = re.sub(r"^(?:[a-z ]+?)\s+(?=[a-z]+ party$)", "", key)  # state prefix
    key = re.sub(r"\s+party$", "", key).strip()
    if not key:
        return ""
    return " ".join(w.capitalize() if w not in ("of", "the") else w for w in key.split())


# ────────────────────────────────────────────────────────────────────────────
# LOW-LEVEL TEXT HELPERS
# ────────────────────────────────────────────────────────────────────────────

def _strip_refs(text: str) -> str:
    text = re.sub(r"<ref[^>]*/>", "", text)
    text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S | re.I)
    return text


def _clean_cell(text: str) -> str:
    """Normalise a table cell to plain text (links, templates, entities)."""
    t = _strip_refs(text)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"\{\{sort(?:name)?\|([^|}]+)\|([^|}]+)[^}]*\}\}",
               lambda m: f"{m.group(1).strip()} {m.group(2).strip()}", t, flags=re.I)
    t = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"'''?", "", t)
    t = re.sub(r"\{\{(?:Party|party) shading/[^}|]*\|?", "", t)
    t = re.sub(r"\{\{[^{}]*\}\}", "", t)  # any leftover simple template
    t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return t.strip(" |\n\t")


def _section(text: str, heading: str, level: int = 2) -> str:
    """Return the content after *heading* (== level) up to the next same-level heading."""
    pat = re.compile(r"(?m)^={%d}\s*%s\s*={%d}\s*$" % (level, re.escape(heading), level), re.I)
    m = pat.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"(?m)^={%d}[^=].*?={%d}\s*$" % (level, level), rest)
    return rest[: nxt.start()] if nxt else rest


# ────────────────────────────────────────────────────────────────────────────
# TABLE EXTRACTION
# ────────────────────────────────────────────────────────────────────────────

def _wikitables(section_text: str) -> List[str]:
    """Split a wikitext chunk into individual {| ... |} tables (outermost)."""
    return [t for _s, t in _wikitables_with_pos(section_text)]


def _wikitables_with_pos(section_text: str) -> List[Tuple[int, str]]:
    """:func:`_wikitables` with offsets — ``(start, table)`` pairs, so callers
    can tell which tables sit inside hypothetical-polling spans."""
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
                tables.append((start, section_text[start:i + 2]))
                start = None
            i += 2
        else:
            i += 1
    return tables


def _table_headers(table: str) -> List[str]:
    """
    Header cell labels, in order, across ALL header rows.

    Some overview tables (e.g. 2015 gubernatorial) use a two-row header:
    ``! State | ! Incumbent | ! Results`` followed by ``! State | ! Governor
    | ! Party | ... | ! Candidates``.  Chunks that carry only '!' lines are
    header rows and are concatenated; the first chunk mixing '!' and '|'
    lines ends the header (its '!' cells are still collected).
    """
    chunks = re.split(r"(?m)^\|-.*$", table)
    headers: List[str] = []
    for chunk in chunks:
        if not re.search(r"(?m)^!", chunk):
            continue
        headers.extend(_clean_cell(c) for c in re.findall(r"(?m)^!(?:[^!\n]*)", chunk))
        if re.search(r"(?m)^\|", chunk):
            break  # header row shares its chunk with data rows — headers end
    return headers


def _table_rows(table: str) -> List[str]:
    """Row chunks of a table (split on any '|-' separator line)."""
    parts = re.split(r"(?m)^\|-.*$", table)
    return [p for p in parts[1:] if p.strip()]


# ────────────────────────────────────────────────────────────────────────────
# CANDIDATE BULLET PARSING
# ────────────────────────────────────────────────────────────────────────────

_PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


def _parse_candidate_bullet(bullet: str) -> Optional[Dict]:
    """Parse one '* {{Party stripe|...}}{{aye}} '''[[Name]]''' (Party) 59.5%' bullet."""
    b = _strip_refs(bullet)
    b = re.sub(r"<!--.*?-->", "", b, flags=re.S)
    if not b.strip():
        return None

    stripe = re.search(r"\{\{\s*[Pp]arty (?:stripe|shade)\s*\|\s*([^|}]+)", b)
    party = normalize_party(stripe.group(1)) if stripe else ""
    b_wo_stripe = re.sub(r"\{\{\s*[Pp]arty (?:stripe|shade)\s*\|[^}]*\}\}", "", b)

    winner = bool(re.search(r"\{\{\s*[Aa]ye\s*\}\}", b_wo_stripe))

    # percentage (before entity/template cleanup so '59.5%' survives)
    pct = None
    pm = _PCT_RE.search(b_wo_stripe)
    if pm:
        pct = float(pm.group(1))
        b_wo_stripe = b_wo_stripe[: pm.start()]

    # candidate name
    name = ""
    m = re.search(r"'''\s*(\[\[[^]]+\]\]|[^']+?)\s*'''", b_wo_stripe)
    if m:
        name = m.group(1)
        winner = winner or True  # bolded candidate == winner marker in these tables
    else:
        # unbolded leading link or plain text
        m = re.match(r"\s*(?:\[\[([^]]+)\]\]|[^*(\n]+)", b_wo_stripe.strip())
        if m:
            name = m.group(1) or m.group(0)

    name = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", r"\1", name or "")
    name = re.sub(r"\{\{sortname\|([^|}]+)\|([^|}]+)[^}]*\}\}",
                  lambda mm: f"{mm.group(1).strip()} {mm.group(2).strip()}", name, flags=re.I)
    name = re.sub(r"\(\s*(?:incumbent|lying in state|deceased|withdrew|resigned)[^)]*\)", "",
                  name, flags=re.I)
    name = re.sub(r"\{\{[^{}]*\}\}", "", name)
    name = re.sub(r"<[^>]+>", "", name)
    # the unbolded-link branch above captures the raw link interior
    # ('Jack Conway (politician)|Jack Conway') — keep the display part.  This
    # must run AFTER template resolution: sortname params contain '|' too.
    if "|" in name:
        name = name.rsplit("|", 1)[1].strip()
    name = name.replace("&nbsp;", " ")
    name = re.sub(r"\s+", " ", name).strip(" ,;")
    # trailing parenthetical party label '(Republican)' — already captured via stripe
    if not stripe:
        pm2 = re.search(r"\(([^)]+)\)\s*$", name)
        if pm2 and pm2.group(1).lower() not in ("incumbent", "write-in",
                                                "write-in candidate", "deceased",
                                                "withdrew", "lying in state"):
            party = party or normalize_party(pm2.group(1))
            name = name[: pm2.start()].strip()
        if not party:
            # 2002-era bullets place the party label between the bold name and
            # the percentage: "'''[[Bob R. Riley]]''' (Republican) 49.2%"
            for pm3 in re.finditer(r"\(([^)]{2,40})\)", b_wo_stripe):
                label = pm3.group(1).strip()
                if label.lower() in ("incumbent", "write-in", "write-in candidate",
                                     "deceased", "withdrew", "lying in state",
                                     "running for other office", "appointed"):
                    continue
                norm = normalize_party(label)
                if norm and not re.match(r"^(election|runoff|special|term)", norm.lower()):
                    party = norm
                    break
    name = name.strip(" ,;")

    if not name:
        return None
    # Two-round territory tables use pseudo-bullets ('First round:', 'Runoff:')
    if re.match(r"^(first|second|third)\s+round\s*:?\s*$|^runoff\s*:?\s*$", name, re.I):
        return None
    if party.lower() == "write-in" or "write-in" in name.lower():
        name = re.sub(r"\(write-in[^)]*\)", "", name, flags=re.I).strip(" ,;")
    return {"candidate": name, "party": party, "percentage": pct, "winner": winner}


def _candidates_from_cell(cell_text: str) -> List[Dict]:
    """Extract candidate bullets from a Candidates table cell.

    Only ``*`` bullet lines are considered — bold text in other cells
    (e.g. 'Result: \'\'\'Republican hold\'\'\''.) must never leak in.
    """
    raw = _strip_refs(cell_text)
    m = re.search(r"\{\{\s*[Pp]lainlist\s*\|(.*?)\}\}\s*$", raw, re.S)
    bullets_text = m.group(1) if m else raw
    lines = re.findall(r"(?m)^\s*\*\s*(.+)$", bullets_text)
    out = []
    for b in lines:
        parsed = _parse_candidate_bullet(b)
        if parsed:
            out.append(parsed)
    return out


# ────────────────────────────────────────────────────────────────────────────
# OVERVIEW ARTICLE PARSER
# ────────────────────────────────────────────────────────────────────────────

def parse_overview(text: str, year: int, office: str) -> pd.DataFrame:
    """Parse the 'Race summary' (or 'Summary') tables of a statewide overview."""
    race = _section(text, "Race summary", level=2)
    if not race:
        race = _section(text, "Summary", level=2)
    if not race:
        logger.warning("  no 'Race summary'/'Summary' section found")
        return pd.DataFrame(columns=_COLUMNS)

    rows: List[Dict] = []
    for table in _wikitables(race):
        headers = [h.lower() for h in _table_headers(table)]
        if not any("state" in h or "territory" in h for h in headers) or not any(
            "candidate" in h for h in headers
        ):
            continue  # rating / prediction / composition tables

        for chunk in _table_rows(table):
            sm = re.search(r"(?m)^!\s*\[\[#[^|\]]*\|([^\]]+)\]\]", chunk)
            if not sm:
                # 2022-style: '! [[2022 Alabama gubernatorial election|Alabama]]'
                sm = re.search(r"(?m)^!\s*\[\[(?:[^|\]]*\|)?([^\]]+)\]\]", chunk)
            if not sm:
                sm = re.search(r"(?m)^!\s*([A-Za-z .]+)", chunk)
            state_raw = (sm.group(1) or "").strip() if sm else ""
            state = re.sub(r"\s*\(.*$", "", state_raw).strip()
            state = re.sub(r"<br\s*/?>", " ", state)   # 'Northern Mariana<br />Islands'
            state = re.sub(r"\s+", " ", state).strip()
            if not state:
                continue
            # header chunks can masquerade as rows ('! State' ...)
            if state.lower().rstrip("s") in {"state", "territory", "district", "candidate"}:
                continue

            lines = [ln for ln in chunk.split("\n") if ln.strip()]
            incumbent = ""
            for ln in lines[1:]:
                s = ln.strip()
                if s.startswith("|") or s.startswith("!"):
                    val = _clean_cell(s.lstrip("|!"))
                    if val and not re.match(r"^(r|d|indep|\+|-)\s*\+?\d+", val.lower()):
                        incumbent = val
                    break

            cands = _candidates_from_cell(chunk)
            if not cands:
                continue

            inc_key = incumbent.lower()
            for c in cands:
                rows.append({
                    "year": year,
                    "state": state,
                    "state_code": _state_code(state),
                    "office": office,
                    "candidate": c["candidate"],
                    "party": c["party"],
                    "percentage": c["percentage"],
                    "winner": bool(c["winner"]),
                    "incumbent": bool(inc_key and inc_key in c["candidate"].lower()),
                })

    df = pd.DataFrame(rows, columns=_COLUMNS)
    if len(df):
        # de-dup identical rows (tables can repeat across subsections)
        df = df.drop_duplicates(
            subset=["year", "state", "office", "candidate"], keep="first"
        ).reset_index(drop=True)
        # single-seat offices: at most one winner per state — keep the top
        # percentage when the wikitext marks several candidates with {{aye}}
        grp = df.groupby(["year", "state", "office"])
        win_count = grp["winner"].transform("sum")
        max_pct = grp["percentage"].transform("max")
        stray = (
            df["winner"]
            & (win_count > 1)
            & df["percentage"].notna()
            & (df["percentage"] != max_pct)
        )
        if stray.any():
            logger.info(
                "  demoting %d stray winner flag(s), e.g. %s",
                int(stray.sum()), df.loc[stray, "state"].unique()[:4],
            )
            df.loc[stray, "winner"] = False
    return df


# ────────────────────────────────────────────────────────────────────────────
# PER-RACE PRIMARY / GENERAL PARSING (race articles)
# ────────────────────────────────────────────────────────────────────────────

def discover_race_titles(text: str, year: int) -> List[str]:
    """
    Extract per-state race article titles from an overview article.

    Race sections link to their dedicated article via ``{{main|...}}``;
    only titles carrying the cycle year and ending in the singular
    "... election" are kept, so overview-level links (e.g. to the next
    cycle's plural overview) are excluded automatically.
    """
    titles: List[str] = []
    for raw in re.findall(r"\{\{\s*[Mm]ain(?:\s+list)?\s*\|([^}]+?)\s*(?:\|[^}]*)?\}\}", text):
        for part in raw.split("|"):
            t = part.strip()
            if str(year) not in t or _NON_STATE_TITLES_RE.search(t):
                continue
            if not t.endswith("election"):
                continue
            titles.append(t)
    return list(dict.fromkeys(titles))


#: Race-title grammar, piece 1: governor races.  "2021 New Jersey
#: gubernatorial election", "2021 California gubernatorial recall election",
#: "2010 New York gubernatorial special election" (hypothetical shapes).
_GOVERNOR_TITLE_RE = re.compile(
    r"^(?P<y>\d{4})\s+(?P<state>.+?)\s+gubernatorial\s+"
    r"(?P<qual>special\s+|recall\s+|runoff\s+)?elections?$", re.I)

#: Race-title grammar, piece 2: every other statewide executive office,
#: longest variant FIRST so "State Comptroller" wins over "Comptroller",
#: "Commissioner of Labor and Industries" over "Commissioner of Labor", etc.
#: Each piece maps to a canonical office label (``_PIECE_TO_OFFICE``), so
#: title spellings like "Auditor of Accounts" (Vermont) or "Public Lands
#: Commissioner" (Washington) collapse into one office family.
_RACE_TITLE_OFFICES: "tuple[tuple[str, str], ...]" = (
    ("State Attorney General", "attorney general"),
    ("Attorney General", "attorney general"),
    ("Secretary of State", "secretary of state"),
    ("General Treasurer", "state treasurer"),
    ("State Treasurer", "state treasurer"),
    ("Treasurer", "state treasurer"),
    ("State Auditor", "state auditor"),
    ("Auditor of State", "state auditor"),
    ("Auditor of Accounts", "state auditor"),
    ("Auditor of Public Accounts", "state auditor"),
    ("Auditor", "state auditor"),
    ("Commissioner of Agriculture and Industries", "agriculture commissioner"),
    ("Commissioner of Agriculture and Commerce", "agriculture commissioner"),
    ("Commissioner of Agriculture and Forestry", "agriculture commissioner"),
    ("Commissioner of Agriculture", "agriculture commissioner"),
    ("Agriculture Commissioner", "agriculture commissioner"),
    ("Secretary of Agriculture", "agriculture commissioner"),
    ("Commissioner of Public Lands", "land commissioner"),
    ("Commissioner of State Lands", "land commissioner"),
    ("Public Lands Commissioner", "land commissioner"),
    ("Land Commissioner", "land commissioner"),
    ("Commissioner of Insurance", "insurance commissioner"),
    ("Insurance Commissioner", "insurance commissioner"),
    ("Commissioner of Labor and Industries", "labor commissioner"),
    ("Commissioner of Labor", "labor commissioner"),
    ("Labor Commissioner", "labor commissioner"),
    ("Comptroller of Public Accounts", "comptroller"),
    ("State Comptroller", "comptroller"),
    ("State Controller", "comptroller"),
    ("Comptroller", "comptroller"),
    ("Controller", "comptroller"),
    ("Superintendent of Public Instruction", "superintendent of public instruction"),
    ("Superintendent of Education", "superintendent of education"),
    ("Public Service Commission", "public service commission"),
    ("Public Utilities Commission", "public utilities commission"),
    ("Public Education Commission", "public education commission"),
    ("Corporation Commission", "corporation commission"),
    ("Board of Elementary and Secondary Education", "state board of education"),
    ("State Board of Education", "state board of education"),
    ("Board of Education", "state board of education"),
    ("Board of Equalization", "board of equalization"),
    ("Board of Regents", "board of regents"),
    ("Executive Council", "executive council"),
    ("Governor's Council", "governor's council"),
    ("Office of Hawaiian Affairs", "office of hawaiian affairs"),
    ("State Mine Inspector", "state mine inspector"),
    ("Tax Commissioner", "tax commissioner"),
    ("Railroad Commissioner", "railroad commissioner"),
    ("Commissioner of School and Public Lands", "commissioner of school and public lands"),
    ("School and Public Lands Commissioner", "commissioner of school and public lands"),
)
_OFFICE_TITLE_ALTERNATION = "|".join(
    re.escape(piece) for piece, _label in _RACE_TITLE_OFFICES
)
_PIECE_TO_OFFICE: Dict[str, str] = dict(_RACE_TITLE_OFFICES)

#: Display labels used by the ``Template:{y} United States elections`` navbox
#: ("Other statewide elections" + "Judicial" rows) that are NOT already
#: covered by the race-title pieces above — mapped to canonical office labels.
#: Used to canonicalize navbox entries and to build umbrella-section anchor
#: candidates.  Keys are lowercase.
_EXTRA_LABEL_ALIASES: Dict[str, str] = {
    "public service commissioner": "public service commission",
    "corporation commissioner": "corporation commission",
    "judicial": "judicial",
    "state supreme court": "judicial",
    "supreme court": "judicial",
    "court of appeals": "judicial",
    "court of criminal appeals": "judicial",
    "supreme court of appeals": "judicial",
    "superior court": "judicial",
    "council of state": "council of state",
    "superintendent": "superintendent of public instruction",
    "state superintendent": "superintendent of public instruction",
    "state superintendent of schools": "superintendent of public instruction",
    "superintendent of schools": "superintendent of public instruction",
    "insurance and safety fire commissioner": "insurance commissioner",
    "commissioner of insurance and safety fire": "insurance commissioner",
    "agriculture secretary": "agriculture commissioner",
    "university regents": "board of regents",
    "regents": "board of regents",
    "land commissioner": "land commissioner",
    "commissioner of the general land office": "land commissioner",
    "lieutenant governor": "lieutenant governor",
    "governor": "governor",
    "attorney general": "attorney general",
    "secretary of state": "secretary of state",
    "state treasurer": "state treasurer",
    "treasurer": "state treasurer",
    "auditor": "state auditor",
}

#: Lowercased display label → canonical office label (title pieces + aliases).
_LABEL_TO_OFFICE: Dict[str, str] = {
    piece.strip().lower(): label for piece, label in _RACE_TITLE_OFFICES
}
_LABEL_TO_OFFICE.update(_EXTRA_LABEL_ALIASES)


def canonical_office_from_label(label: str) -> Optional[str]:
    """
    Navbox / heading display label → canonical office label, or None.

    'Public Service Commissioner' → 'public service commission';
    'Board of Education' → 'state board of education';  'Judicial' →
    'judicial';  'State Superintendent' → 'superintendent of public
    instruction'.  A trailing 'election'/'elections' (and the navbox's
    ' (sp)' special-election marker) is stripped before the lookup.
    """
    raw = re.sub(r"\s*\(sp\)\s*$", "", (label or "").strip(), flags=re.I)
    key = re.sub(r"\s+", " ", raw).strip().lower()
    if not key:
        return None
    if key in _LABEL_TO_OFFICE:
        return _LABEL_TO_OFFICE[key]
    key = re.sub(r"\s+elections?$", "", key).strip()
    return _LABEL_TO_OFFICE.get(key)


#: "2023 Kentucky Attorney General election",
#: "2022 New York State Attorney General election",
#: "2022 Rhode Island General Treasurer election",
#: "2024 West Virginia Commissioner of Agriculture election",
#: "2024 Washington Public Lands Commissioner election",
#: "2022 Texas Comptroller of Public Accounts election"
_OTHER_OFFICE_TITLE_RE = re.compile(
    r"^(?P<y>\d{4})\s+(?P<state>.+?)\s+(?:special\s+|recall\s+|runoff\s+)?"
    r"(?P<office>" + _OFFICE_TITLE_ALTERNATION + r")\s+elections?$", re.I)


def _anchor_candidates(office: str, title: str = "") -> List[str]:
    """
    Candidate umbrella-section headings for *office*, best first.

    The office piece of a race title (when the title matches the grammar)
    comes first — e.g. 'Railroad Commissioner' for '2024 Texas Railroad
    Commissioner election' redirecting into '2024 Texas elections' — then
    every known display spelling of the office (title pieces, navbox label
    aliases and curated-map title pieces), longest first so that
    'Superintendent of Public Instruction' is tried before 'Superintendent'.
    """
    cands: List[str] = []
    m = _OTHER_OFFICE_TITLE_RE.match(title or "")
    if m and m.group("office"):
        cands.append(m.group("office").strip())
    for piece, label in _RACE_TITLE_OFFICES:
        if label == office and piece not in cands:
            cands.append(piece)
    for alias, label in _EXTRA_LABEL_ALIASES.items():
        if label == office:
            display = " ".join(
                w if w in ("of", "the", "and") else w.capitalize()
                for w in alias.split()
            )
            if display not in cands:
                cands.append(display)
    for entries in STATE_DEPENDENT_OFFICE_TITLES.values():
        for label, pieces in entries:
            if label == office:
                for piece in pieces:
                    if piece not in cands:
                        cands.append(piece)
    # longest first: exact matches are tried in order, and the startswith
    # fallback benefits from the most specific anchor first
    return sorted(cands, key=len, reverse=True)

_RACE_TITLE_PATTERNS: "tuple[tuple[re.Pattern, Optional[str]], ...]" = (
    (_GOVERNOR_TITLE_RE, "governor"),
    (_OTHER_OFFICE_TITLE_RE, None),  # office from the matched title piece
)


def _match_race_title(title: str, year: int) -> "tuple[Optional[str], Optional[str]]":
    """
    Match a race article title against the grammar.

    Returns ``(state, office)`` when *title* is a race title for *year*
    (e.g. ``("West Virginia", "agriculture commissioner")``), else
    ``(None, None)``.
    """
    t = title.strip()
    for pat, fixed_office in _RACE_TITLE_PATTERNS:
        m = pat.match(t)
        if m and int(m.group("y")) == year:
            if fixed_office:
                return m.group("state").strip(), fixed_office
            office = _PIECE_TO_OFFICE.get((m.group("office") or "").strip(), "")
            if office:
                return m.group("state").strip(), office
            break
    return None, None


def state_from_race_title(title: str, year: int) -> Optional[str]:
    """
    Derive the state label from a race article title, or None.

    '2021 New Jersey gubernatorial election'   → 'New Jersey'
    '2021 California gubernatorial recall election' → 'California'
    '2023 Kentucky Attorney General election'  → 'Kentucky'
    '2024 Washington Public Lands Commissioner election' → 'Washington'
    """
    return _match_race_title(title, year)[0]


def office_from_race_title(title: str, year: int) -> Optional[str]:
    """
    Derive the canonical office label from a race article title, or None.

    '2024 West Virginia Commissioner of Agriculture election' →
    'agriculture commissioner';  '2022 California State Controller election'
    → 'comptroller';  '2024 Vermont Auditor of Accounts election' →
    'state auditor'.
    """
    return _match_race_title(title, year)[1]


def _race_sections(text: str, level: int = 2) -> List:
    """Sections at heading *level* as (name, start, end) triples, in order."""
    marks_ = "=" * level
    marks = [
        (m.start(), m.end(), m.group(1).strip())
        for m in re.finditer(
            rf"(?m)^{marks_}\s*([^=].*?)\s*{marks_}\s*$", text)
    ]
    sections = []
    for i, (start, hdr_end, name) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections.append((name, start, end))
    return sections


def _race_election_type(section_name: str) -> str:
    """Classify a race-article section as Primary / General / '' (ignored)."""
    if _PRIMARY_HEADING_RE.search(section_name):
        return "Primary"
    if _GENERAL_HEADING_RE.search(section_name):
        return "General"
    return ""


#: Row templates of the {{Election box}} family.  Row templates may embed
#: nested templates inside parameters (e.g. ticket cells like
#'{{ubl|{{nowrap|[[A]] (incumbent)}}|[[B]] (incumbent)}}' on New Mexico 2022),
#: so the parameter body must tolerate two levels of balanced '{{...}}' spans.
_EB_PARAM_BODY = r"((?:[^{}]|\{\{(?:[^{}]|\{\{[^{}]*\}\})*\}\})+?)"
_EB_ROW_PATTERNS = [
    (r"\{\{Election box winning candidate(?: with party link| without party|"
     r" for a political party)?(?: no change)?[\s\|]" + _EB_PARAM_BODY + r"\}\}", "Winning"),
    (r"\{\{Election box candidate(?: with party link| without party|"
     r" for a political party)?(?: no change)?[\s\|]" + _EB_PARAM_BODY + r"\}\}", "Candidate"),
    (r"\{\{Election box write-in(?: with party link)?(?: no change)?[\s\|]"
     + _EB_PARAM_BODY + r"\}\}", "Write-in"),
    (r"\{\{Election box total(?: no change)?[\s\|]" + _EB_PARAM_BODY + r"\}\}", "Total"),
]


def _parse_election_box_rows(box_text: str) -> List[Dict]:
    """Rows of one {{Election box begin ... end}} chunk as raw param dicts."""
    # Value must tolerate '|' inside [[link|display]] and template spans —
    # a naive [^|\n]+ would truncate '[[John Buckley (Virginia politician)|John
    # Buckley]]'.  One level of template nesting is allowed so ticket cells
    # like '{{ubl|{{nowrap|[[A]] (incumbent)}}|[[B]] (incumbent)}}' survive
    # intact (New Mexico 2022 general box).
    _value = r"((?:\{\{(?:[^{}]|\{\{[^{}]*\}\})*\}\}|\[\[[^\]]*\]\]|[^\|\n{])+)"
    rows: List[Dict] = []
    for regex, row_type in _EB_ROW_PATTERNS:
        for params in re.findall(regex, box_text, re.DOTALL):
            row: Dict = {"row_type": row_type}
            for key, val in re.findall(r"(?:^|\|)\s*(\w+)\s*=\s*" + _value, params):
                row[key.lower()] = val.strip()
            rows.append(row)
    return rows


def _fallback_cell(cell: str) -> str:
    """Wikitable cell → plain text (attrs, links, templates stripped)."""
    t = _clean_cell(cell)
    # Cell attributes: the quoted value must not contain '|', '<' or '>'
    # (impossible in valid MediaWiki cell markup).  Excluding them stops an
    # unbalanced quote from swallowing the cell content up to the next '"'
    # (e.g. 'style="text-align:left;|Name (R)<ref name="x"/>' → 'x"/>').
    t = re.sub(r'^\s*(?:[a-zA-Z-]+\s*=\s*"[^"<>|]*"\s*)+', "", t)  # cell attributes
    return t.replace("|", " ").strip()


def _parse_simple_votes_table(table: str, section_name: str) -> List[Dict]:
    """
    Conservative fallback for primary sections without election boxes.

    Only simple ``Candidate | Votes | %`` tables are parsed: the header must
    mention Candidate and (Votes or %), must NOT be a ranked-choice
    multi-round table (Round 1..n column groups), and must not be a
    by-county / by-district breakdown.  Returns raw row dicts with
    candidate / votes / percentage keys.
    """
    headers = _table_headers(table)
    joined = " | ".join(headers).lower()
    if "candidate" not in joined and "nominee" not in joined:
        return []
    if not ("vote" in joined or "%" in joined or "percent" in joined):
        return []
    if re.search(r"round\s*\d|county|district|parish|precinct", joined):
        return []

    out: List[Dict] = []
    for chunk in _table_rows(table):
        # One chunk == one data row.  Cells are '|' lines; '||' separates
        # cells packed onto a single line.  Header ('!') lines are skipped.
        cells: List[str] = []
        for ln in chunk.split("\n"):
            s = ln.strip()
            if s.startswith("||"):
                cells.extend(s[2:].split("||"))
            elif s.startswith("|"):
                cells.append(s[1:])
        cleaned = [c for c in (_fallback_cell(c) for c in cells) if c]
        if len(cleaned) < 2:
            continue
        name = cleaned[0]
        if not name or len(name) > 60:
            continue
        if re.match(r"^(total|valid|rejected|turned away|majority|swing|electorate|"
                    r"registered|turnout|blank|spoilt|source)", name, re.I):
            continue
        votes, pct = None, None
        for cell in cleaned[1:]:
            vm = re.search(r"\d{1,3}(?:,\d{3})+|\d{3,}\b", cell)
            pm = _PCT_RE.search(cell)
            if pm and pct is None:
                pct = float(pm.group(1))
            elif vm and votes is None:
                votes = int(vm.group(0).replace(",", ""))
        if votes is None and pct is None:
            continue
        out.append({
            "row_type": "Candidate",
            "candidate": name,
            "votes": votes,
            "percentage": pct,
            "party": "",
            "election": section_name,
        })
    return out


def _param_number(raw) -> Optional[float]:
    """Numeric {{Election box}} param value (strip refs/tags/commas/%/nbsp)."""
    if raw in (None, ""):
        return None
    s = re.sub(r"<[^>]+>", "", str(raw))
    s = s.replace("&nbsp;", " ").replace(",", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


#: Ticket cells list the governor candidate first, then the running mate:
#'{{ubl|[[Phil Murphy]] (incumbent)|[[Sheila Oliver]] (incumbent)}}'
_TICKET_LIST_RE = re.compile(
    r"\{\{\s*(?:ubl|unbulleted\s*list|plainlist|flatlist|hlist|"
    r"bulleted\s*list|pagelist)\s*\|(.*?)\}\}", re.S | re.I,
)


def _top_of_ticket(val: str) -> str:
    """
    Keep the top-of-ticket candidate from a list-template cell.

    Governor general-election boxes name the full governor/lieutenant-governor
    ticket via ``{{ubl|...}}``; keeping only the first item matches the grain
    of the overview summaries (one row per gubernatorial candidate).
    """
    m = _TICKET_LIST_RE.search(val)
    if not m:
        return val
    for item in m.group(1).split("|"):
        item = item.strip()
        if item and not re.match(r"^\s*\w+\s*=", item):  # skip named params
            return item
    return val


def _clean_candidate_param(raw: str) -> "tuple[str, bool]":
    """{{Election box}} candidate param → (plain top-of-ticket name, incumbent)."""
    val = unwrap_format_templates(raw or "")   # {{nowrap|x}} → x
    val = remove_wikilinks(val)                 # [[A|B]] → B (before pipe splits)
    val = _top_of_ticket(val)                   # {{ubl|A|B}} → A
    val, is_inc = extract_incumbent_flag(clean_wikitext(val))
    return re.sub(r"\s+", " ", val).strip(" ,;"), is_inc


def parse_race_article(
    text: str,
    year: int,
    state: str,
    state_code: str,
    office: str,
    source_title: str = "",
    heading_level: int = 2,
    default_general: bool = False,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """
    Parse one per-state race article into (primary, general) DataFrames.

    Election boxes are classified by the level-2 heading they sit under, so
    box titles without the word "primary" (e.g. Mississippi's
    "2019 Republican", New Jersey's long official-certification titles) are
    attributed correctly.  Primary sections without boxes fall back to
    simple Candidate/Votes/% wikitables.

    *heading_level* selects which heading depth carries the primary/general
    sections — multi-office articles (e.g. the North Carolina council of
    state slate) are parsed one ``##`` office section at a time, so their
    ``###`` election sections are passed with ``heading_level=3``.

    *default_general* relaxes the conservative section classification: boxes
    under headings that name neither a primary nor a general election (the
    judicial slates use "=== Dillard's seat ===" / "=== Court of Appeals ===")
    are classified by their own box title and otherwise treated as general
    election results — nonpartisan judicial races have no primaries.

    *source_title* (the race article's Wikipedia title) is stamped into the
    ``race_article`` column of every emitted row — multi-seat offices whose
    seats have one article each (e.g. Georgia Public Service Commission
    districts) then keep distinguishable races even though every box is
    titled "General election", which the margin engine relies on.
    """
    pairs = [(name, text[start:end])
             for name, start, end in _race_sections(text, heading_level)]
    return _parse_race_section_pairs(
        pairs, year, state, state_code, office,
        source_title=source_title, default_general=default_general,
    )


def _parse_race_section_pairs(
    section_pairs: "List[tuple[str, str]]",
    year: int,
    state: str,
    state_code: str,
    office: str,
    source_title: str = "",
    default_general: bool = False,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """Core of :func:`parse_race_article` over pre-cut (name, text) sections."""
    primary_rows: List[Dict] = []
    general_rows: List[Dict] = []

    for section_name, section_text in section_pairs:
        election_type = _race_election_type(section_name)

        # ── district/seat sub-headings inside the section ────────────
        # Multi-district race articles (e.g. New Hampshire Executive
        # Council: five "=== District N ===" subsections under
        # "== General election ==") put one results box per district;
        # the nearest preceding sub-heading is stamped into the election
        # label so per-district rows and margins stay separable.
        district_marks = [
            (m.start(), m.group(2))
            for m in _DISTRICT_SUBHEADING_RE.finditer(section_text)
        ]
        section_is_district = bool(_DISTRICT_SECTION_RE.match(section_name))

        # enter classified sections, district-named sections whose
        # boxes/table titles self-classify (primary / general), and — under
        # *default_general* — unclassified sections whose boxes fall back to
        # box-title classification and then to general-election results
        if (not election_type and not section_is_district and not district_marks
                and not default_general):
            continue

        # heading positions inside the section — used to classify boxes in
        # district-named sections, where the nearest preceding heading (e.g.
        # "==== Results ====" inside "=== General election ===") may sit at
        # an intermediate level
        sec_headings = [
            (m.start(), m.group(1).strip())
            for m in re.finditer(r"(?m)^={2,6}\s*([^=].*?)\s*={2,6}\s*$",
                                 section_text)
        ]

        # ── election boxes (dominant format, 2001–2026) ──────────────
        box_rows: List[Dict] = []
        for bm in re.finditer(
            r"\{\{Election box begin(?: no change)?\s*\|?\s*"
            r"((?:[^{}]|\{\{[^}]*\}\})*?)\}\}"
            r"(.*?)\{\{Election box end\}\}",
            section_text, re.DOTALL,
        ):
            header, box_content = bm.group(1), bm.group(2)
            title_match = re.search(r"title\s*=\s*([^\n<]+)", header)
            box_title = clean_wikitext(title_match.group(1)) if title_match else section_name
            box_title = box_title.strip() or section_name

            # district label = nearest preceding sub-heading, else the
            # section's own name when that names a district/seat
            district = None
            for pos, lab in district_marks:
                if pos < bm.start():
                    district = lab
                else:
                    break
            if district is None and section_is_district:
                district = section_name

            # a district-named section without its own primary/general
            # heading is classified per the nearest preceding heading
            # ("==== Results ====" -> "=== General election ==="), then per
            # box title ("Republican primary", …); under *default_general*
            # unclassifiable boxes are treated as general-election results;
            # otherwise they are skipped
            if section_is_district or district_marks:
                effective = ""
                for h_pos, h_name in reversed(sec_headings):
                    if h_pos < bm.start():
                        # "Results" is a wrapper heading inside a real
                        # primary/general subsection — it must not stop the
                        # outward walk ("Results" → "Republican primary")
                        if re.fullmatch(r"results", h_name.strip(), re.I):
                            continue
                        effective = _race_election_type(h_name)
                        if effective:
                            break
                effective = effective or _race_election_type(box_title)
            else:
                effective = election_type
            if not effective:
                effective = _race_election_type(box_title)
            if not effective:
                effective = _race_election_type(section_name)
            if not effective and default_general:
                effective = "General"
            if not effective:
                continue

            # jungle-primary / runoff boxes sit under a primary heading but
            # the box title may say "general" — the heading wins.
            for row in _parse_election_box_rows(box_content):
                row["election"] = f"{district} – {box_title}" if district else box_title
                row["_type"] = effective
                box_rows.append(row)

        # ── simple wikitable fallback for sections w/o boxes ──────────
        if (election_type or default_general) and not box_rows:
            for table in _wikitables(section_text):
                for row in _parse_simple_votes_table(table, section_name):
                    row["_type"] = election_type or "General"
                    box_rows.append(row)

        for row in box_rows:
            candidate, is_inc = _clean_candidate_param(row.get("candidate", ""))

            # nameless write-in / scattering rows ('|party=Write-ins' with no
            # candidate param): keep the votes under a Write-in row instead of
            # a nameless Candidate row
            if not candidate and row.get("row_type") in ("Winning", "Candidate"):
                party_lbl = (row.get("party") or "").strip()
                if re.search(r"write.?in|scattering|blank", party_lbl, re.I):
                    row["row_type"] = "Write-in"
                    candidate = party_lbl.title().replace("Write-In", "Write-in")

            votes_raw = _param_number(row.get("votes"))
            pct_raw = _param_number(row.get("percentage"))
            votes = int(votes_raw) if votes_raw is not None else None
            pct = round(pct_raw, 2) if pct_raw is not None else None

            # placeholder rows with no name and no numbers (empty box stubs,
            # withdrawn-candidate slots) carry no information — drop them
            if (
                not candidate
                and votes is None
                and pct is None
                and row.get("row_type") in ("Winning", "Candidate", "Write-in")
            ):
                continue

            record = {
                "year": year,
                "state": state,
                "state_code": state_code,
                "office": office,
                "election": row.get("election", section_name),
                "row_type": row.get("row_type", "Candidate"),
                "candidate": candidate,
                "party": normalize_party(row.get("party", "")),
                "votes": votes,
                "percentage": pct,
                "winner": row.get("row_type") == "Winning",
                "incumbent": is_inc,
                "race_article": source_title or row.get("election", section_name),
            }
            if row.get("_type", election_type) == "Primary":
                primary_rows.append(record)
            else:
                general_rows.append(record)

    def _frame(rows: List[Dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=_RACE_COLUMNS)
        if len(df):
            df = df.drop_duplicates(
                subset=["year", "state", "office", "election", "row_type", "candidate"],
                keep="first",
            ).reset_index(drop=True)
        return df

    return _frame(primary_rows), _frame(general_rows)


# ────────────────────────────────────────────────────────────────────────────
# YEAR-PAGE UMBRELLA SECTIONS (state-year articles sliced per office)
# ────────────────────────────────────────────────────────────────────────────

def _strip_first_heading(text: str) -> str:
    """Remove the first heading line (levels 2–6) from *text*."""
    return re.sub(r"(?m)^={2,6}\s*[^=].*?\s*={2,6}\s*$", "", text, count=1)


def _slice_by_anchor(text: str, anchor: str) -> "tuple[Optional[str], int]":
    """
    Slice *text* from the heading that matches *anchor* (case-insensitive,
    whitespace-normalized; a startswith fallback covers anchor/heading
    spellings that drift slightly) until the next heading of the same or a
    shallower level.  Returns ``(slice_text, heading_level)`` or ``(None, 0)``.
    """
    if not text or not anchor:
        return None, 0

    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()

    target = norm(anchor)
    headings = [
        (m.start(), len(m.group(1)), m.group(2))
        for m in re.finditer(r"(?m)^(={2,6})\s*([^=].*?)\s*\1\s*$", text)
    ]
    match = None
    for start, lvl, name in headings:                     # exact match first
        if norm(name) == target:
            match = (start, lvl)
            break
    if match is None:                                     # prefix fallback
        for start, lvl, name in headings:
            n = norm(name)
            if len(n) >= 4 and (n.startswith(target) or target.startswith(n)):
                match = (start, lvl)
                break
    if match is None:
        return None, 0
    start, level = match
    end = len(text)
    for h_start, lvl, _name in headings:
        if h_start > start and lvl <= level:
            end = h_start
            break
    return text[start:end], level


def _parse_infobox_election_rows(text: str, election_label: str) -> List[Dict]:
    """
    Candidate rows from ``{{Infobox election}}`` templates embedded in a
    state-year umbrella section (e.g. the North Dakota Public Service
    Commissioner section, which publishes nominee/party/popular_vote/
    percentage params instead of an election box or results table).
    The first listed nominee is the winner (Wikipedia bolds them).
    """
    rows: List[Dict] = []
    for m in re.finditer(
            r"\{\{\s*Infobox\s+(?:election|office election|officeholder "
            r"election)\b", text, re.I):
        end_m = re.search(r"(?m)^\}\}", text[m.start():])
        if not end_m:
            continue
        body = text[m.start(): m.start() + end_m.start()]
        params: Dict[str, str] = {}
        for pm in re.finditer(r"(?m)^\|\s*([A-Za-z0-9_]+)\s*=[ \t]*(.*)$", body):
            params[pm.group(1).lower()] = pm.group(2).strip()
        n = 1
        while True:
            cand = params.get(f"nominee{n}") or params.get(f"candidate{n}")
            if cand is None:
                break
            votes = _param_number(
                params.get(f"popular_vote{n}") or params.get(f"votes{n}"))
            pct = _param_number(params.get(f"percentage{n}"))
            if votes is None and pct is None:
                n += 1
                continue
            name, is_inc = _clean_candidate_param(cand)
            if not name:
                n += 1
                continue
            rows.append({
                "row_type": "Winning" if n == 1 else "Candidate",
                "candidate": name,
                "votes": int(votes) if votes is not None else None,
                "percentage": round(pct, 2) if pct is not None else None,
                "party": normalize_party(params.get(f"party{n}", "")),
                "election": election_label,
                "incumbent": is_inc,
            })
            n += 1
    return rows


def parse_umbrella_office_section(
    text: str,
    year: int,
    state: str,
    state_code: str,
    office: str,
    anchors: List[str],
    source_title: str = "",
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """
    Parse ONE office's results out of a state-year umbrella article
    (``2024 Texas elections#Railroad Commissioner``, ``2024 Montana
    elections#Superintendent of Public Instruction``, …).

    The page is sliced from the office's heading (first *anchors* candidate
    that matches, most specific first) to the next heading of the same or a
    shallower level.  Inside the slice, deeper headings carry the
    primary/general sections (parsed like a race article with the
    conservative classifier relaxed to *default_general* — umbrella sections
    title their subsections inconsistently); a slice without subsections is
    parsed flat, and an embedded ``{{Infobox election}}`` is the last resort.
    """
    empty = (pd.DataFrame(columns=_RACE_COLUMNS),
             pd.DataFrame(columns=_RACE_COLUMNS))
    slice_text = None
    matched = ""
    for anchor in anchors or []:
        slice_text, level = _slice_by_anchor(text, anchor)
        if slice_text:
            matched = anchor
            break
    if not slice_text:
        logger.debug("  umbrella slice not found: %s (anchors: %s)",
                     office, anchors)
        return empty

    sub_marks = [
        (m.start(), len(m.group(1)), m.group(2).strip())
        for m in re.finditer(r"(?m)^(={2,6})\s*([^=].*?)\s*\1\s*$", slice_text)
        if len(m.group(1)) > level
    ]
    if sub_marks:
        sub_level = min(lvl for _s, lvl, _n in sub_marks)
        tops = [(s, n) for s, lvl, n in sub_marks if lvl == sub_level]
        pairs = []
        for i, (s, n) in enumerate(tops):
            e = tops[i + 1][0] if i + 1 < len(tops) else len(slice_text)
            pairs.append((n, slice_text[s:e]))
        return _parse_race_section_pairs(
            pairs, year, state, state_code, office,
            source_title=source_title or matched, default_general=True,
        )

    # flat section: everything directly under the anchor heading
    body = _strip_first_heading(slice_text)
    primary_df, general_df = _parse_race_section_pairs(
        [(matched, body)], year, state, state_code, office,
        source_title=source_title or matched, default_general=True,
    )
    if len(primary_df) or len(general_df):
        return primary_df, general_df
    # last resort: an embedded {{Infobox election}} in the section body
    rows = _parse_infobox_election_rows(body, election_label=matched)
    if not rows:
        return empty
    for r in rows:
        r.update({
            "year": year, "state": state, "state_code": state_code,
            "office": office, "winner": r.get("row_type") == "Winning",
            "race_article": source_title or matched,
        })
    general_df = pd.DataFrame(rows, columns=_RACE_COLUMNS)
    general_df = general_df.drop_duplicates(
        subset=["year", "state", "office", "election", "row_type", "candidate"],
        keep="first",
    ).reset_index(drop=True)
    return pd.DataFrame(columns=_RACE_COLUMNS), general_df


# ────────────────────────────────────────────────────────────────────────────
# MULTI-OFFICE ARTICLES (North Carolina council of state slate)
# ────────────────────────────────────────────────────────────────────────────

#: ``== Section headings ==`` of the North Carolina "Council of State
#: elections" article → canonical office labels.
_COS_HEADING_TO_OFFICE: Dict[str, str] = {
    "governor": "governor",
    "lieutenant governor": "lieutenant governor",
    "attorney general": "attorney general",
    "secretary of state": "secretary of state",
    "state auditor": "state auditor",
    "auditor": "state auditor",
    "state treasurer": "state treasurer",
    "treasurer": "state treasurer",
    "superintendent of public instruction": "superintendent of public instruction",
    "commissioner of agriculture": "agriculture commissioner",
    "agriculture commissioner": "agriculture commissioner",
    "commissioner of labor": "labor commissioner",
    "labor commissioner": "labor commissioner",
    "commissioner of insurance": "insurance commissioner",
    "insurance commissioner": "insurance commissioner",
}

#: Offices whose council-of-state sections are SKIPPED — those races are
#: already parsed from their dedicated per-office race articles (discovered
#: from the cycle overviews), and a second parse would produce duplicate
#: margin rows for the same race.
_COS_SKIP_OFFICES = {"governor", "attorney general", "secretary of state",
                     "state treasurer", "state auditor"}


def parse_council_of_state_article(
    text: str,
    year: int,
    state: str,
    state_code: str,
    source_title: str = "",
) -> "List[tuple[str, pd.DataFrame, pd.DataFrame]]":
    """
    Parse a multi-office council-of-state slate article (e.g. ``2024 North
    Carolina Council of State elections``) into one (office, primary,
    general) triple per ``== office ==`` section.  Sections naming offices
    already covered by the overview pipeline (governor, AG, SoS, treasurer,
    auditor) are skipped to avoid duplicate race margins.
    """
    out: "List[tuple[str, pd.DataFrame, pd.DataFrame]]" = []
    for name, start, end in _race_sections(text, 2):
        office = _COS_HEADING_TO_OFFICE.get(
            re.sub(r"\s+", " ", name).strip().lower())
        if not office or office in _COS_SKIP_OFFICES:
            continue
        section = text[start:end]
        subs = _race_sections(section, 3)
        if subs:
            pairs = [(n, section[s:e]) for n, s, e in subs]
        else:
            pairs = [(name, _strip_first_heading(section))]
        p, g = _parse_race_section_pairs(
            pairs, year, state, state_code, office,
            source_title=source_title or name, default_general=True,
        )
        out.append((office, p, g))
    return out


# ────────────────────────────────────────────────────────────────────────────
# STATE-LEVEL POLLING (race articles + dedicated "Opinion polling for ..." articles)
# ────────────────────────────────────────────────────────────────────────────

#: Level-2 section headings, used for positional primary/general classification.
_LEVEL2_HEADING_RE = re.compile(r"(?m)^==\s*([^=\n].*?)\s*==\s*$")

#: Dedicated polling-article links race articles carry next to their
#: (often summary-only) polling tables:
#:     {{main|Opinion polling for the 2021 Virginia gubernatorial election}}
#:     {{further|Opinion polling for the 2023 Kentucky gubernatorial election}}
_POLLING_ARTICLE_RE = re.compile(
    r"\{\{\s*(?:[Mm]ain|[Ff]urther)\s*\|\s*(Opinion polling [^}|]+?)\s*(?:\|[^}]*)?\}\}"
)


def _find_heading(text: str, name: str) -> int:
    """Offset of the first level-2 heading exactly matching *name*, or -1.

    Mirrors ``senate_elections._find_heading`` — kept local so the statewide
    module stays runnable even if the Senate module is refactored.
    """
    m = re.search(rf"^==\s*{re.escape(name)}\s*==\s*$", text, re.MULTILINE | re.IGNORECASE)
    return m.start() if m else -1


def _section_end(text: str, start_pos: int) -> int:
    """End of the level-2 section that begins at *start_pos* (next level-2
    heading, or end of text)."""
    m = _LEVEL2_HEADING_RE.search(text, start_pos + 1)
    return m.start() if m else len(text)


def discover_polling_article_title(race_text: str) -> Optional[str]:
    """
    Return the dedicated polling-article title linked from a race article,
    or None.

    Gubernatorial race articles frequently delegate the full polling tables
    to a companion article — ``Opinion polling for the 2021 Virginia
    gubernatorial election`` and friends — linking it from their own
    (summary) polling section via ``{{main|...}}``.  Only titles that carry
    a four-digit year are accepted, so generic links are ignored.
    """
    for raw in _POLLING_ARTICLE_RE.findall(race_text or ""):
        title = raw.strip()
        if re.search(r"\b(19|20)\d{2}\b", title):
            return title
    return None


#: Poll-source cells in some polling-table generations (the 2021 California
#: recall article, notably) carry raw external links — '[https://… Label]' —
#: which the shared parser keeps verbatim.  Strip the URL fragment and keep
#: the label ('… Real Clear Politics]' → 'Real Clear Politics').
_POLL_SRC_BRACKETS_RE = re.compile(r"[\[\]]")


def _clean_poll_source(raw) -> str:
    """Normalise a leaked external-link poll-source cell to its label."""
    if not isinstance(raw, str):
        return raw
    v = _POLL_SRC_BRACKETS_RE.sub("", raw).strip()
    if "/" not in v and ":" not in v:
        return v
    # drop leading URL-ish tokens ('https://…', 'ttps://…', '…/report.pdf')
    while True:
        first, _sep, rest = v.partition(" ")
        if rest and (":" in first or "/" in first
                     or re.search(r"\.(?:pdf|html?|aspx|php)$", first, re.I)):
            v = rest
            continue
        break
    return v.strip()


def parse_race_polling(
    text: str,
    year: int,
    state: str,
    state_code: str,
    office: str,
    whole_article: bool = False,
) -> "tuple[pd.DataFrame, pd.DataFrame]":
    """
    Extract (primary, general) polling rows from one state-level race or
    polling article.

    Works on BOTH article kinds — the per-race article and the dedicated
    ``Opinion polling for ...`` companion:

    * **Race-article mode** (``whole_article=False``): polling headings
      (``Polling`` / ``Polls`` / ``Opinion polling``, any level) are located
      with the Senate pipeline's :func:`iter_polling_spans` and classified
      positionally — inside ``== Democratic primary ==`` → primary polls,
      inside ``== Republican primary ==`` → Republican Primary, inside a
      jungle/blanket-primary heading → ``jungle``, everything else →
      ``General``.
    * **Dedicated-polling-article mode** (``whole_article=True``): every
      wikitable in the article is a polling table by definition, so each
      table is parsed and classified by the level-2 section it sits in.
      This also captures the articles that put tables directly under
      ``== General election ==`` without a nested ``Polling`` heading.
      Results-style tables (no date column) fail the parser's date check
      and are skipped automatically.

    Unlike the Senate classifier, a top-level polling section that sits
    between the primary sections and ``== General election ==`` (or after
    ``== Results ==``) is treated as GENERAL, not primary — statewide
    articles put their main polling section at the end, and the Senate
    fallback would mislabel those rows.

    Every row also carries a ``Poll_Type`` label ('Primary' / 'General' /
    'Hypothetical'): tables inside ``{{hidden begin|title=Hypothetical
    polling}}`` blocks or under a 'Hypothetical polling' heading are
    labelled instead of melting into the surrounding bucket, and standalone
    hypothetical sections no ``Polling`` heading covers are parsed too.

    Returns ``(primary_polling, general_polling)`` in the Senate long
    format, augmented with ``Year`` / ``State_Code`` / ``Office`` columns
    and ISO-normalised dates (``Date_Start`` / ``Date_End`` /
    ``Date_Original``).
    """
    if not text:
        return pd.DataFrame(), pd.DataFrame()

    # ── locate the section anchors ─────────────────────────────────
    general_start = _find_heading(text, "General election")
    if general_start == -1:
        general_start = len(text)
    dem_start = _find_heading(text, "Democratic primary")
    rep_start = _find_heading(text, "Republican primary")
    # statewide spellings of a nonpartisan/jungle round (Louisiana-style)
    jungle_start = -1
    for name in ("Jungle primary", "Primary election", "Nonpartisan blanket primary"):
        jungle_start = _find_heading(text, name)
        if jungle_start != -1:
            break

    has_dem = dem_start != -1 and dem_start < general_start
    has_rep = rep_start != -1 and rep_start < general_start
    has_jungle = jungle_start != -1 and jungle_start < general_start
    if has_dem and has_rep:
        primary_type = "two_party"
    elif has_dem or has_rep:
        primary_type = "single_party"
    elif has_jungle:
        primary_type = "jungle"
    else:
        primary_type = "no_primary"

    # section bounds — an absent second party lets the present one run to
    # the general-election heading
    dem_end = rep_start if (dem_start != -1 and rep_start > dem_start) else general_start
    rep_end = dem_start if (rep_start != -1 and dem_start > rep_start) else general_start
    jungle_end = _section_end(text, jungle_start) if jungle_start != -1 else -1

    def _primary_context(pos: int) -> Optional["tuple[str, Optional[str]]"]:
        """(Primary_Type, party) when a heading at *pos* sits inside a
        primary section, else None (→ general polling)."""
        if pos >= general_start:
            return None
        if dem_start != -1 and dem_start <= pos < dem_end:
            return ("Democratic Primary", "D")
        if rep_start != -1 and rep_start <= pos < rep_end:
            return ("Republican Primary", "R")
        if jungle_start != -1 and jungle_start <= pos < jungle_end:
            return ("jungle", None)
        return None

    incumbent_name = extract_infobox_incumbent(text)
    primary_frames: List[pd.DataFrame] = []
    general_frames: List[pd.DataFrame] = []

    def _emit(wide: pd.DataFrame, ctx, poll_type: Optional[str]) -> None:
        """Melt one wide frame into long rows and file it under its bucket."""
        if wide.empty:
            return
        long_df = wide_to_long_polls(
            wide, state,
            ctx[0] if ctx else "General",
            is_primary=ctx is not None,
            primary_party=ctx[1] if ctx else None,
            incumbent_name=incumbent_name,
            poll_type=poll_type,
        )
        if len(long_df):
            (primary_frames if ctx else general_frames).append(long_df)

    def _parse_scope(scope_text: str, ctx,
                     force_poll_type: Optional[str] = None) -> None:
        """Parse one chunk of wikitext as polling tables.  Tables inside
        hypothetical blocks/headings are labelled 'Hypothetical'; the rest
        follow *ctx* ('Primary'/'General') unless *force_poll_type*
        overrides it (per-table classification in whole-article mode)."""
        regular_wide, hypo_wide = parse_polling_tables_classified(
            scope_text,
            state,
            is_primary=ctx is not None,
            primary_party=ctx[1] if ctx else None,
        )
        _emit(regular_wide, ctx,
              force_poll_type or ("Primary" if ctx else "General"))
        _emit(hypo_wide, ctx, "Hypothetical")

    if whole_article:
        # ── dedicated polling article: parse EVERY table, classified by
        # the level-2 section it sits in.  A synthetic '== Polling =='
        # heading is prepended so the shared parser can find a scope.
        marks = [(m.start(), m.group(1).strip())
                 for m in _LEVEL2_HEADING_RE.finditer(text)]
        intervals: List["tuple[int, int, Optional[tuple]"] = []
        for idx, (pos, _name) in enumerate(marks):
            sec_end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
            intervals.append((pos, sec_end, _primary_context(pos)))
        if marks and marks[0][0] > 0:          # preamble before the first heading
            intervals.insert(0, (0, marks[0][0], None))
        for sec_start, sec_end, ctx in intervals:
            chunk = text[sec_start:sec_end]
            # per-table hypothetical classification: the wrapper's
            # '{{hidden end}}' sits outside the table itself, so the spans
            # of the enclosing chunk decide
            hypo_spans = iter_hypothetical_polling_spans(chunk)
            for tbl_start, table in _wikitables_with_pos(chunk):
                is_hypo = any(s <= tbl_start < e for s, e in hypo_spans)
                _parse_scope(
                    "\n== Polling ==\n" + table, ctx,
                    force_poll_type="Hypothetical" if is_hypo else None,
                )
    else:
        poll_spans = iter_polling_spans(text)
        for head_start, _head_end, scope_end, _level in poll_spans:
            ctx = _primary_context(head_start)
            if ctx is not None:
                # a primary-classified polling section must never swallow
                # general-election content
                scope_end = min(scope_end, general_start)
                if head_start >= scope_end:
                    continue
            _parse_scope(text[head_start:scope_end], ctx)
        # standalone hypothetical sections no Polling heading covers
        covered = [(hs, se) for hs, _he, se, _lvl in poll_spans]
        for h_start, h_end in iter_hypothetical_polling_spans(text):
            if any(s <= h_start < e for s, e in covered):
                continue
            hypo_wide = parse_hypothetical_tables(text[h_start:h_end], state)
            _emit(hypo_wide, _primary_context(h_start), "Hypothetical")

    def _finalize(frames: List[pd.DataFrame]) -> pd.DataFrame:
        if not frames:
            return pd.DataFrame()
        df = pd.concat(frames, ignore_index=True)
        df.insert(0, "Year", int(year))
        df.insert(2, "State_Code", state_code)
        df.insert(3, "Office", office)
        # normalise leaked external-link source cells BEFORE dedup so the
        # cleaned labels collapse duplicate rows
        df["Poll_Source"] = df["Poll_Source"].map(_clean_poll_source)
        # a state's governor and AG articles can reprint the same poll — the
        # office column keeps both copies distinguishable, so dedup on it
        df = df.drop_duplicates(
            subset=["Year", "State", "Office", "Poll_Type", "Poll_Source",
                    "Date", "Candidate", "Pct"],
            keep="first",
        ).reset_index(drop=True)
        return normalize_polling_date_columns(df)

    return _finalize(primary_frames), _finalize(general_frames)


# ────────────────────────────────────────────────────────────────────────────
# SEARCH-BASED DISCOVERY OF STATE-DEPENDENT RACE ARTICLES
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# HARDCODED (MAP-BASED) DISCOVERY OF STATE-DEPENDENT RACE ARTICLES
# ────────────────────────────────────────────────────────────────────────────

def hardcoded_state_dependent_titles(
    years: List[int],
    offices: Optional[List[str]] = None,
) -> Dict[str, tuple]:
    """
    Turn the curated ``STATE_DEPENDENT_OFFICE_TITLES`` map into candidate
    race titles — the PRIMARY discovery path for state-dependent offices.

    ``{year} {State} {title piece} election`` (and the plural
    ``... elections`` variant — the multi-race judicial and council-of-state
    slate articles use the plural form) per (state × office × piece ×
    cycle).  The titles are fetched through the normal batched article fetch
    (~50 titles per request), so a full map costs only a handful of API
    requests per cycle instead of dozens of ``intitle:`` search calls.
    Titles that don't exist on Wikipedia are logged as missing and skipped.
    Honours the canonical ``--offices`` filter.
    """
    wanted = set(offices) if offices else None
    out: Dict[str, tuple] = {}
    for year in years:
        for state, entries in STATE_DEPENDENT_OFFICE_TITLES.items():
            if not _state_code(state):
                continue
            for label, pieces in entries:
                if wanted is not None and label not in wanted:
                    continue
                for piece in pieces:
                    base = f"{year} {state} {piece} "
                    for suffix in ("election", "elections"):
                        out.setdefault(base + suffix, (year, state, label))
    return out


def search_state_dependent_race_titles(
    client: WikiAPIClient,
    years: List[int],
    offices: Optional[List[str]] = None,
) -> "tuple[Dict[str, tuple], int]":
    """
    Discover per-state race articles for offices that have no cycle overview
    article on Wikipedia (agriculture commissioner, comptroller, …).

    One rate-limited ``intitle:`` search per (cycle × title variant); every
    hit must survive the race-title grammar (``_match_race_title``), carry a
    resolvable state code, and end in "election"/"elections" before it is
    accepted.  Returns ``(title → (year, state, office), queries_made)``.
    """
    found: Dict[str, tuple] = {}
    queries = 0
    wanted = {o for o in (offices or SEARCH_RACE_OFFICES) if o in SEARCH_RACE_OFFICES}
    for year in years:
        for office in sorted(wanted):
            for variant in SEARCH_RACE_OFFICES[office]:
                query = f'intitle:"{year}" intitle:"{variant}"'
                queries += 1
                try:
                    hits = client.search_titles(query)
                except Exception:
                    logger.warning("  search failed: %s — skipped", query)
                    continue
                for title in hits:
                    if not re.search(r"elections?$", title.strip(), re.I):
                        continue
                    state, hit_office = _match_race_title(title, year)
                    if not state or hit_office != office:
                        continue
                    if not _state_code(state):
                        continue
                    found.setdefault(title, (year, state, office))
    return found, queries


# ────────────────────────────────────────────────────────────────────────────
# YEAR ELECTION PAGE DISCOVERY ("Other statewide elections" navbox row)
# ────────────────────────────────────────────────────────────────────────────

#: The navbox template embedded at the bottom of every ``{y} United States
#: elections`` overview article.  Its ``Other statewide elections`` group
#: lists, per state, the down-ballot races that ran that cycle (state boards,
#: commissions, single-state executive offices, judicial slates); its
#: ``Judicial`` group lists the state supreme / appellate court seats.
_YEAR_TEMPLATE_TITLE = "Template:{y} United States elections"

#: Navbox top-level params: ``|groupN =`` row headings, ``|listN =`` row
#: bodies.  The value may run onto the following lines until the next param.
_YEAR_NAVBOX_PARAM_RE = re.compile(r"(?m)^\|\s*([a-z][a-z0-9_ ]*?)\s*=[ \t]*(.*)$")

_NAVBOX_ITEM_RE = re.compile(r"^(\*+)\s*(.*)$")
_NAVBOX_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def _navbox_split_wikilink(content: str) -> "tuple[Optional[str], str]":
    """First wikilink of a navbox item → (target, display); (None, text) if bare."""
    m = _NAVBOX_WIKILINK_RE.search(content)
    if not m:
        return None, clean_wikitext(content)
    target = m.group(1).strip()
    display = (m.group(2) or target).strip()
    return target, clean_wikitext(display)


def _navbox_resolve_state(display: str, target: str) -> Optional[str]:
    """State label from a navbox row ('* Alabama', '* [[2024 X elections|Alabama]]')."""
    for candidate in (display, target):
        text = re.sub(r"\s+", " ", candidate or "").strip()
        text = re.sub(rf"^\d{{4}}\s+", "", text, flags=re.I)
        text = re.sub(r"\s+elections?$", "", text, flags=re.I).strip()
        if _state_code(text):
            return text.title() if text.islower() else text
    return None


def _parse_navbox_list_block(
    block: str,
    year: int,
    default_office: Optional[str],
    dedicated: Dict[str, tuple],
    umbrellas: Dict[str, Dict[tuple, List[str]]],
    unparsed: List[tuple],
) -> None:
    """
    Parse one navbox row body: ``* State`` headers with ``** Office`` links.

    ``**`` entries link either a dedicated race article
    (``2024 Alabama Public Service Commission election``) or a state-year
    umbrella anchor (``2024 North Dakota elections#Insurance
    Commissioner``); bare ``**`` labels (no link) mean the page publishes no
    race article at all and are reported as unparsed.  ``*`` entries whose
    link target is itself a race article for *year* (the Judicial row's
    ``* [[2024 Kentucky Supreme Court election|Kentucky]]``) are office
    entries too.
    """
    state: Optional[str] = None
    for line in block.splitlines():
        m = _NAVBOX_ITEM_RE.match(line.strip())
        if not m:
            continue
        depth, content = len(m.group(1)), m.group(2).strip()
        if not content:
            continue
        target, display = _navbox_split_wikilink(content)

        if depth == 1:
            state = _navbox_resolve_state(display, target or "")
            if not state:
                continue
            if target and "#" in target:
                # single-line umbrella anchor (the Judicial row's
                # '* [[2024 Michigan elections#Supreme Court|Michigan]]')
                page, anchor = target.split("#", 1)
                page = page.strip()
                anchor = anchor.strip()
                office = (default_office
                          or canonical_office_from_label(display)
                          or canonical_office_from_label(anchor))
                if office:
                    cands = umbrellas.setdefault(page, {}).setdefault(
                        (year, state, office), [])
                    for cand in [anchor] + [
                            a for a in _anchor_candidates(office, "") if a]:
                        if cand not in cands:
                            cands.append(cand)
                else:
                    unparsed.append((year, state, display or anchor))
                continue
            # single-line race links (judicial rows): the state IS the race
            if (target and str(year) in target
                    and re.search(r"elections?$", target, re.I)):
                office = (default_office
                          or canonical_office_from_label(display)
                          or office_from_race_title(target, year))
                race_state = state_from_race_title(target, year) or state
                if office and race_state:
                    dedicated.setdefault(target, (year, race_state, office))
                else:
                    unparsed.append((year, state, display))
            continue

        if depth != 2 or not state:
            continue
        if not target:
            # '** Insurance Commissioner' — no race article exists on the page
            unparsed.append((year, state, display))
            continue
        if "#" in target:
            page, anchor = target.split("#", 1)
            page = page.strip()
            anchor = anchor.strip()
            office = (canonical_office_from_label(display)
                      or default_office
                      or canonical_office_from_label(anchor))
            if not office or not _state_code(state):
                unparsed.append((year, state, display or anchor))
                continue
            jobs = umbrellas.setdefault(page, {})
            key = (year, state, office)
            cands = jobs.setdefault(key, [])
            for cand in [anchor] + [
                    a for a in _anchor_candidates(office, "") if a]:
                if cand not in cands:
                    cands.append(cand)
        else:
            office = (canonical_office_from_label(display)
                      or default_office
                      or office_from_race_title(target, year))
            # the navbox groups entries under an authoritative state header;
            # the title grammar is only a fallback (a slate title like
            # '2022 California State Board of Equalization elections' would
            # otherwise derive the bogus state 'California State')
            race_state = state or state_from_race_title(target, year)
            if office and race_state:
                dedicated.setdefault(target, (year, race_state, office))
            else:
                unparsed.append((year, state, display))


def parse_year_page_navbox(text: str, year: int):
    """
    Extract the state-dependent race targets from ``Template:{y} United
    States elections``.  Returns ``(dedicated, umbrellas, unparsed)``:

    * ``dedicated`` — race-article title → ``(year, state, office)``;
    * ``umbrellas`` — state-year page title →
      ``{(year, state, office): [anchor candidates]}``;
    * ``unparsed`` — ``(year, state, label)`` rows the navbox carries
      without a usable link (e.g. Delaware's 2024 Insurance Commissioner
      entry, which has no race article) — reported, never parsed.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    params = [
        (m.start(), m.end(), m.group(1).strip(), m.group(2))
        for m in _YEAR_NAVBOX_PARAM_RE.finditer(text)
    ]

    def _param_block(prefix: str) -> Optional[str]:
        for i, (pos, eol, name, val) in enumerate(params):
            if name != prefix:
                continue
            end = params[i + 1][0] if i + 1 < len(params) else len(text)
            rest = text[eol:end]
            return (val.strip() + "\n" + rest) if val.strip() else rest
        return None

    dedicated: Dict[str, tuple] = {}
    umbrellas: Dict[str, Dict[tuple, List[str]]] = {}
    unparsed: List[tuple] = []
    for pos, eol, name, val in params:
        gm = re.fullmatch(r"group(\d+)", name)
        if not gm:
            continue
        plain = re.sub(r"<[^>]+>", " ", val)
        if re.search(r"other\s+statewide\s+elections", plain, re.I):
            default_office = None            # office from each row's label
        elif re.search(r"judicial", plain, re.I):
            default_office = "judicial"
        else:
            continue
        block = _param_block(f"list{gm.group(1)}")
        if block is None:
            logger.warning("  %s navbox: group %s has no matching list%s",
                           year, gm.group(1), gm.group(1))
            continue
        _parse_navbox_list_block(
            block, year, default_office, dedicated, umbrellas, unparsed)
    return dedicated, umbrellas, unparsed


def discover_race_titles_from_year_pages(
    client: WikiAPIClient,
    years: List[int],
    offices: Optional[List[str]] = None,
) -> "tuple[Dict[str, tuple], Dict[str, Dict[tuple, List[str]]], Dict]":
    """
    FIRST discovery source for state-dependent races: read the
    ``Other statewide elections`` (and ``Judicial``) navbox rows of
    ``Template:{y} United States elections`` — one batched fetch for all
    cycles — and go through the linked pages: dedicated race articles are
    queued into the normal batched race fetch, state-year umbrella anchors
    are queued for section-level parsing.

    Returns ``(dedicated, umbrellas, meta)``; *offices* filters both by
    canonical office label.
    """
    template_titles = [_YEAR_TEMPLATE_TITLE.format(y=y) for y in years]
    content = client.fetch_wikitext(template_titles) if client else {}
    dedicated: Dict[str, tuple] = {}
    umbrellas: Dict[str, Dict[tuple, List[str]]] = {}
    unparsed: List[tuple] = []
    for y in years:
        text = content.get(_YEAR_TEMPLATE_TITLE.format(y=y))
        if not text:
            logger.warning(
                "  %s United States elections navbox missing — year-page "
                "discovery skipped for this cycle", y)
            continue
        d, u, un = parse_year_page_navbox(text, y)
        for t, v in d.items():
            dedicated.setdefault(t, v)
        for page, jobs in u.items():
            tgt = umbrellas.setdefault(page, {})
            for key, anchors in jobs.items():
                cands = tgt.setdefault(key, [])
                for a in anchors:
                    if a not in cands:
                        cands.append(a)
        unparsed.extend(un)

    wanted = set(offices) if offices is not None else None
    if wanted is not None:
        dedicated = {t: v for t, v in dedicated.items() if v[2] in wanted}
        umbrellas = {
            p: {k: a for k, a in jobs.items() if k[2] in wanted}
            for p, jobs in umbrellas.items()
        }
        umbrellas = {p: jobs for p, jobs in umbrellas.items() if jobs}

    meta = {
        "templates": template_titles,
        "dedicated": sorted(dedicated),
        "umbrella_pages": sorted(umbrellas),
        "umbrella_jobs": sum(len(j) for j in umbrellas.values()),
        "unparsed_labels": [
            f"{y} {s}: {label}" for (y, s, label) in unparsed
        ],
    }
    return dedicated, umbrellas, meta


# ────────────────────────────────────────────────────────────────────────────
# VOTE MARGINS (winner vs. runner-up per race)
# ────────────────────────────────────────────────────────────────────────────

_MARGIN_COLUMNS = [
    "year", "state", "state_code", "office", "election", "race",
    "seats",
    "winner_candidate", "winner_party", "winner_percentage", "winner_votes",
    "runner_up_candidate", "runner_up_party", "runner_up_percentage",
    "runner_up_votes", "total_votes", "margin_pct", "margin_votes",
    "margin_pct_votes", "source",
]

#: Row types that carry candidate votes.  The ``Total`` row_type is the
#: box's table total (no candidate name in modern data) and must never
#: enter the aggregation; on fusion-ticket states (NY/CT/VT pre-2000) the
#: WINNING row carries the candidate's cross-party aggregate and the
#: per-party Candidate rows are the ballot-line splits.
_CANDIDATE_ROW_TYPES = ("Winning", "Candidate", "Write-in")


def _aggregate_candidates(gdf: pd.DataFrame) -> pd.DataFrame:
    """
    Collapse candidate-level rows to one row per candidate.

    Vote/percentage aggregation rules:
    * a candidate with a ``Winning`` row keeps that row's figures (on fusion
      states it is the cross-party aggregate; per-party ``Candidate`` rows
      for the same candidate are ballot-line splits and must NOT be summed
      on top of it);
    * a candidate with several per-party ``Candidate`` rows (fusion tickets
      without an aggregate row) gets their sum;
    * a candidate's party label prefers the first real (non-``Total``)
      party value across their rows.
    """
    records: List[Dict] = []
    for cand, sub in gdf.groupby("candidate", sort=False):
        is_win = (sub["row_type"] == "Winning").any()
        votes_num = pd.to_numeric(sub["votes"], errors="coerce")
        pct_num = pd.to_numeric(sub["percentage"], errors="coerce")
        if is_win:
            votes = votes_num.max()
            if pd.isna(votes):
                votes = votes_num.sum(min_count=1)
            pct = pct_num.max()
            if pd.isna(pct):
                pct = pct_num.sum(min_count=1)
        else:
            votes = votes_num.sum(min_count=1)
            pct = pct_num.sum(min_count=1)
        party = ""
        for _, r in sub.iterrows():
            p = str(r.get("party") or "").strip()
            if p and p.lower() != "total":
                party = p
                break
        records.append({
            "candidate": cand, "party": party,
            "votes": float(votes) if pd.notna(votes) else None,
            "percentage": float(pct) if pd.notna(pct) else None,
            "winner": bool(is_win),
        })
    return pd.DataFrame(records, columns=["candidate", "party", "votes",
                                          "percentage", "winner"])


def _margin_from_general_rows(gdf: pd.DataFrame) -> Optional[Dict]:
    """
    One margin record from one race's general-election candidate rows
    (vote counts authoritative).  Two-stage Louisiana races are narrowed to
    the FINAL stage (a runoff-titled box when present, else the last box in
    document order) before aggregation, so jungle-primary and runoff votes
    are never mixed.

    Single-seat races report winner vs. runner-up.  Multi-seat races
    (boards/commissions electing *n* members — every winner carries a
    ``Winning`` row) report the SEAT-CUTOFF margin: the last-elected
    candidate (lowest-vote winner) vs. the first loser (highest-vote
    non-winner); ``seats`` records how many candidates were elected.
    """
    gdf = gdf[gdf["row_type"].fillna("").isin(_CANDIDATE_ROW_TYPES)].copy()
    _has_name = gdf["candidate"].notna() & (gdf["candidate"].astype(str).str.strip() != "")
    # nameless write-in rows ("|row_type=Write-in|votes=10" with no candidate)
    # carry real votes for the race total but no candidate to aggregate
    _raw = gdf.assign(_has_name=_has_name)
    gdf = gdf[_has_name]
    if gdf.empty:
        return None

    # ── narrow multi-stage races to the final general-election stage ──
    stages = [e for e in dict.fromkeys(gdf["election"].dropna().tolist())]
    if len(stages) > 1:
        runoffs = [e for e in stages if "runoff" in str(e).lower()]
        chosen = runoffs[-1] if runoffs else stages[-1]
        gdf = gdf[gdf["election"] == chosen]
    election_label = str(gdf["election"].iloc[0]) if len(gdf) else ""
    race_label = str(gdf["race_article"].iloc[0]) if "race_article" in gdf.columns \
        else ""

    agg = _aggregate_candidates(gdf)
    if agg.empty:
        return None
    # no Winning row parsed (fallback tables) → the vote leader is the winner
    if not agg["winner"].any():
        if agg["votes"].notna().any():
            leader = agg["votes"].idxmax()
        elif agg["percentage"].notna().any():
            leader = agg["percentage"].idxmax()
        else:
            # candidates listed but no results published yet — e.g. an
            # upcoming race whose table carries neither vote counts nor
            # percentages (CI crash: pandas "Encountered all NA values").
            # Nothing to rank: skip the race; the overview-summary fill may
            # still cover this (state, office) later.
            logger.info("  %s — no results published yet, margin skipped",
                        race_label or election_label or "unnamed race")
            return None
        agg.loc[leader, "winner"] = True

    win_pool = agg[agg["winner"]]
    seats = int(len(win_pool))
    if seats > 1:
        # seat-cutoff: the last seat won is the tightest — compare the
        # lowest-vote winner against the highest-vote non-winner
        win = win_pool.loc[win_pool["votes"].idxmin()
                           if win_pool["votes"].notna().any()
                           else win_pool.index[-1]]
        rest = agg[~agg["winner"]]
    else:
        win = win_pool.loc[win_pool["votes"].idxmax()
                           if win_pool["votes"].notna().any()
                           else win_pool.index[0]]
        rest = agg[agg["candidate"] != win["candidate"]]
    total_votes = agg["votes"].dropna().sum()
    # add back votes scattered to nameless write-in rows of the chosen stage
    nameless_writein_votes = pd.to_numeric(
        _raw.loc[~_raw["_has_name"] & (_raw["row_type"] == "Write-in")
                 & (_raw["election"] == election_label), "votes"],
        errors="coerce",
    ).sum(min_count=1)
    if pd.notna(nameless_writein_votes):
        total_votes = total_votes + nameless_writein_votes
    total_votes = float(total_votes) if total_votes else None

    def _share(row) -> Optional[float]:
        """Published %, falling back to the computed vote share."""
        if row["percentage"] is not None and not pd.isna(row["percentage"]):
            return float(row["percentage"])
        if pd.notna(row["votes"]) and total_votes:
            return round(100.0 * row["votes"] / total_votes, 2)
        return None

    win_pct = _share(win)
    if not rest.empty and rest["votes"].notna().any():
        run = rest.loc[rest["votes"].idxmax()]
    elif not rest.empty and rest["percentage"].notna().any():
        run = rest.loc[rest["percentage"].idxmax()]
    else:
        run = None

    # NaN-safe: an all-numeric column turns stored Nones into NaN, so a
    # plain ``is not None`` test lets NaN slip into int()/arithmetic below
    win_votes = None if pd.isna(win["votes"]) else float(win["votes"])
    run_votes = (None if run is None or pd.isna(run["votes"])
                 else float(run["votes"]))
    margin_votes = (round(win_votes - run_votes)
                    if win_votes is not None and run_votes is not None else None)
    margin_pct_votes = (round(100.0 * margin_votes / total_votes, 2)
                        if margin_votes is not None and total_votes else None)
    run_pct = _share(run) if run is not None else None
    # unopposed race: no runner-up to subtract — the margin is the winner's
    # own share (the runner-up line stays empty)
    margin_pct = (
        round(win_pct - run_pct, 2)
        if win_pct is not None and run_pct is not None
        else (round(win_pct, 2) if run is None and win_pct is not None else None)
    )

    return {
        "election": election_label,
        "race": race_label,
        "seats": seats,
        "winner_candidate": win["candidate"],
        "winner_party": win["party"],
        "winner_percentage": win_pct,
        "winner_votes": int(win_votes) if win_votes is not None else None,
        "runner_up_candidate": run["candidate"] if run is not None else "",
        "runner_up_party": run["party"] if run is not None else "",
        "runner_up_percentage": run_pct,
        "runner_up_votes": int(run_votes) if run_votes is not None else None,
        "total_votes": int(total_votes) if total_votes else None,
        "margin_pct": margin_pct,
        "margin_votes": margin_votes,
        "margin_pct_votes": margin_pct_votes,
        "source": "race_article",
    }


def _margin_from_overview_rows(odf: pd.DataFrame) -> Optional[Dict]:
    """
    One margin record from one race's overview summary rows (percentage
    only).  The overview bullets are per candidate and already aggregated,
    so no fusion handling is needed here.
    """
    odf = odf[odf["candidate"].notna()
              & (odf["candidate"].astype(str).str.strip() != "")]
    odf = odf.copy()
    odf["percentage"] = pd.to_numeric(odf["percentage"], errors="coerce")
    odf = odf[odf["percentage"].notna()]
    if odf.empty:
        return None

    winners = odf[odf["winner"].astype(bool)]
    if winners.empty:
        winners = odf
    win = winners.loc[winners["percentage"].idxmax()]
    rest = odf[odf["candidate"] != win["candidate"]]
    run = rest.loc[rest["percentage"].idxmax()] if not rest.empty else None

    margin_pct = (round(float(win["percentage"]) - float(run["percentage"]), 2)
                  if run is not None else round(float(win["percentage"]), 2))
    return {
        "election": "",
        "race": "",
        "seats": 1,
        "winner_candidate": win["candidate"],
        "winner_party": str(win.get("party") or ""),
        "winner_percentage": float(win["percentage"]),
        "winner_votes": None,
        "runner_up_candidate": run["candidate"] if run is not None else "",
        "runner_up_party": str(run.get("party") or "") if run is not None else "",
        "runner_up_percentage": (float(run["percentage"])
                                 if run is not None else None),
        "runner_up_votes": None,
        "total_votes": None,
        "margin_pct": margin_pct,
        "margin_votes": None,
        "margin_pct_votes": None,
        "source": "overview_summary",
    }


def build_margin_frames(
    general_by_year: Dict[int, pd.DataFrame],
    overview_by_year: Dict[int, pd.DataFrame],
) -> Dict[int, pd.DataFrame]:
    """
    Build the per-year margin tables.

    Race-article margins (vote counts) are authoritative; overview-summary
    margins fill in every (state, office) race whose race article yielded no
    general results, so the margin table covers the full parsed slate.
    Race grouping includes the ``race_article`` column so multi-seat
    offices with one article per seat (Georgia PSC districts, …) get one
    margin row per seat race; frames lacking that column (older CSVs) are
    treated as single-race groups.
    """
    margins: Dict[int, pd.DataFrame] = {}
    for year in sorted(set(general_by_year) | set(overview_by_year)):
        records: List[Dict] = []
        seen_keys: set = set()

        gdf = general_by_year.get(year)
        if gdf is not None and len(gdf):
            if "race_article" not in gdf.columns:
                gdf = gdf.assign(race_article="")
            gdf = gdf.copy()
            gdf["race_article"] = gdf["race_article"].fillna("")
            # multi-district articles stamp "District N – " into the election
            # label; one margin row per district race
            gdf["_district"] = (
                gdf["election"].astype(str)
                .str.extract(_DISTRICT_ELECTION_PREFIX_RE.pattern, flags=re.I)[0]
                .fillna("")
            )
            for (state, state_code, office, race, district), sub in gdf.groupby(
                    ["state", "state_code", "office", "race_article", "_district"],
                    sort=False, dropna=False):
                rec = _margin_from_general_rows(sub)
                if rec is None:
                    continue
                records.append({
                    "year": year, "state": state,
                    "state_code": state_code or "", "office": office,
                    **rec,
                })
                seen_keys.add((str(state).lower(), str(office).lower(),
                               str(race).lower()))

        odf = overview_by_year.get(year)
        if odf is not None and len(odf):
            for (state, state_code, office), sub in odf.groupby(
                    ["state", "state_code", "office"], sort=False,
                    dropna=False):
                # an overview fill is only needed when NO race margin for
                # this (state, office) exists at all
                if any(k[0] == str(state).lower() and k[1] == str(office).lower()
                       for k in seen_keys):
                    continue
                rec = _margin_from_overview_rows(sub)
                if rec is None:
                    continue
                records.append({
                    "year": year, "state": state,
                    "state_code": state_code or "", "office": office,
                    **rec,
                })

        if records:
            df = pd.DataFrame(records, columns=_MARGIN_COLUMNS)
            df = df.sort_values(["office", "state"], kind="stable").reset_index(drop=True)
            margins[year] = df
    return margins


def _rebuild_combined(statewide_dir: str, family: str = "results_") -> Optional[pd.DataFrame]:
    """
    Rebuild ``statewide_{family}all.csv`` from every per-year file on disk.

    A run that processes only one cycle (or only off-years) must NOT
    overwrite the combined CSV with just its own rows — that would silently
    wipe history from the _all files.  Per-year files are the source of
    truth: every year ever processed by any run leaves a file behind, so
    concatenating them (sorted by year) always yields the full-history
    combined file.  *family* is "results_" (overview summaries),
    "primary_results_", "general_results_", "primary_polling_",
    "general_polling_" or "margins_".
    """
    import glob

    parts: List[pd.DataFrame] = []
    for path in sorted(glob.glob(os.path.join(statewide_dir, f"statewide_{family}*.csv"))):
        name = os.path.basename(path)
        m = re.fullmatch(rf"statewide_{re.escape(family)}(\d{{4}})\.csv", name)
        if not m:
            continue  # skip the _all file itself
        try:
            df_year = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
        except Exception:
            logger.warning("Could not read %s for the combined file — skipping", path)
            continue
        df_year.insert(0, "_year", int(m.group(1)))
        parts.append(df_year)

    if not parts:
        return None
    combined = pd.concat(parts, ignore_index=True)
    combined = combined.sort_values("_year", kind="stable")
    combined = combined.drop(columns=["_year"])
    return combined


# ────────────────────────────────────────────────────────────────────────────
# BATCH DRIVER
# ────────────────────────────────────────────────────────────────────────────

def _parse_offices_arg(offices: Optional[List[str]]) -> Optional[List[str]]:
    """Validate a canonical office-label list (None → all offices)."""
    if not offices:
        return None
    valid = set(ALL_OFFICE_LABELS)
    unknown = [o for o in offices if o.strip().lower() not in valid]
    if unknown:
        raise ValueError(
            "Unknown office label(s): %s — valid labels: %s"
            % (", ".join(unknown), ", ".join(ALL_OFFICE_LABELS))
        )
    return [o.strip().lower() for o in offices]


def run(
    start_year: int = 2018,
    end_year: int = 2024,
    output_dir: str = "data",
    client: Optional[WikiAPIClient] = None,
    include_off_years: bool = True,
    fetch_races: bool = True,
    include_polling: bool = True,
    offices: Optional[List[str]] = None,
    search_discovery: bool = False,
    year_page_discovery: bool = True,
) -> pd.DataFrame:
    """CLI entry point: process statewide executive cycles from *start_year*
    to *end_year*, writing CSVs under ``<output_dir>/statewide/``.

    Even (federal) years are always processed, and the odd (off-year) cycles
    in the range are included by default (NJ/VA gubernatorial, the KY/LA/MS
    statewide slate, occasional specials) — pass ``include_off_years=False``
    to restrict the run to even years.  When *fetch_races* is true the
    per-state race articles are fetched and parsed for primary and general
    results (vote counts), written to
    ``statewide_primary_results_{year}.csv`` /
    ``statewide_general_results_{year}.csv``.

    When *include_polling* is also true (default), the same race-article
    texts — plus any dedicated ``Opinion polling for the {year} {State} ...
    election`` article the race articles link — are mined for opinion
    polling and the results are written to
    ``statewide_primary_polling_{year}.csv`` /
    ``statewide_general_polling_{year}.csv`` (Senate long schema +
    ``State_Code`` / ``Office`` columns).  Polling extraction reuses the
    race-article fetches; only the companion polling articles trigger
    extra API requests.

    State-dependent offices (agriculture commissioner, land commissioner,
    insurance commissioner, labor commissioner, comptroller, state auditor
    outside its 2022+ overview cycles, superintendent of public
    instruction, plus the boards, commissions and judicial slates) are
    recovered from per-state race articles.  Discovery is YEAR-PAGE FIRST:
    the ``Other statewide elections`` / ``Judicial`` navbox rows of
    ``Template:{y} United States elections`` (one batched fetch per cycle)
    list exactly which down-ballot races ran in each state; their links are
    followed — dedicated race articles parsed whole, state-year umbrella
    anchors sliced to the office's own section.  The curated
    ``STATE_DEPENDENT_OFFICE_TITLES`` map runs as a second source
    (pass *year_page_discovery* false to use the map only), and setting
    *search_discovery* true additionally runs per-variant ``intitle:``
    searches for states/offices both miss — useful for exhaustive coverage,
    but it costs one API request per (cycle × variant) and can trip
    Wikipedia's rate limiter (403) on long ranges.

    *offices* (CLI ``--offices "state treasurer,agriculture commissioner"``)
    restricts the run to a subset of canonical office labels — overview
    articles, map titles, search queries, race parses, and margin rows all
    honour it.

    Every parsed cycle additionally yields a per-race vote-margin table
    (winner vs. runner-up, percentage points + votes; seat-cutoff margin
    for multi-seat boards) written to ``statewide_margins_{year}.csv`` and
    combined into ``statewide_margins_all.csv``.
    """
    office_filter = _parse_offices_arg(offices)
    years = even_years(start_year, end_year)
    if include_off_years:
        off = _odd_years(start_year, end_year)
        if off:
            logger.info("Including off-year (odd) cycles: %s", off)
        years = sorted(set(years) | set(off))
    if not years:
        logger.warning("No election years in [%d, %d] — nothing to do.", start_year, end_year)
        return pd.DataFrame()
    if not include_off_years and (start_year, end_year) != (years[0], years[-1]):
        logger.info("Odd bounds clamped to even years: %d–%d", years[0], years[-1])
    if include_polling and not fetch_races:
        logger.warning("include_polling needs the race-article fetch — disabling polling")
        include_polling = False
    if office_filter:
        logger.info("Office filter: %s", ", ".join(office_filter))
    logger.info("Statewide cycles to process: %s", years)

    statewide_dir = os.path.join(output_dir, "statewide")
    os.makedirs(statewide_dir, exist_ok=True)

    client = client or get_default_client()

    meta = {"years": {}, "races": {}, "polling_articles": {}, "missing_articles": [],
            "hardcoded_titles": {}, "search_discovery": {},
            "year_page_discovery": {}}
    # title -> (year, state, office) for every discovered race article
    race_map: Dict[str, tuple] = {}
    # title -> (year, state, office) for dedicated 'Opinion polling for ...'
    # companion articles (discovered from the race articles in STEP 2)
    polling_map: Dict[str, tuple] = {}
    # year -> overview summary rows (margins input, percentage-based)
    overview_results_by_year: Dict[int, pd.DataFrame] = {}

    # ── STEP 1: overview articles → general summary tables ─────────────
    for year in years:
        titles = {
            office: pat.format(y=year)
            for office, pat in OFFICES.items()
            if not office_filter or office in office_filter
        }
        content = client.fetch_wikitext(list(titles.values())) if client else {}

        frames = []
        year_meta = {}
        for office, title in titles.items():
            text = content.get(title)
            if not text:
                logger.warning("  %s %s — overview article missing, skipped", year, office)
                meta["missing_articles"].append(title)
                year_meta[office] = None
                continue
            df = parse_overview(text, year, office)
            n_states = df["state"].nunique() if len(df) else 0
            logger.info("  %s %-35s %3d candidate rows / %2d states", year, office,
                        len(df), n_states)
            year_meta[office] = {"rows": int(len(df)), "states": int(n_states)}
            if len(df):
                frames.append(df)

            # discover per-state race articles for primary/general parsing
            if fetch_races:
                for race_title in discover_race_titles(text, year):
                    state = state_from_race_title(race_title, year)
                    if not state:
                        logger.debug("  skipping non-race link: %s", race_title)
                        continue
                    race_map.setdefault(race_title, (year, state, office))

        if frames:
            df_year = pd.concat(frames, ignore_index=True)
            overview_results_by_year[year] = df_year
            path = os.path.join(statewide_dir, f"statewide_results_{year}.csv")
            df_year.to_csv(path, index=False)
            logger.info("  saved -> %s", path)
        meta["years"][year] = year_meta

    # ── STEP 1b: year election page → "Other statewide elections" links ─
    umbrella_map: Dict[str, Dict[tuple, List[str]]] = {}
    if fetch_races and year_page_discovery and client:
        navbox_dedicated, umbrella_map, navbox_meta = \
            discover_race_titles_from_year_pages(client, years, offices=office_filter)
        for race_title, (year, state, office) in navbox_dedicated.items():
            race_map.setdefault(race_title, (year, state, office))
        meta["year_page_discovery"] = navbox_meta
        if navbox_dedicated or umbrella_map:
            logger.info(
                "Year election page navbox: %d dedicated race article(s) + "
                "%d umbrella section job(s) discovered",
                len(navbox_dedicated), navbox_meta["umbrella_jobs"],
            )
            if navbox_meta["unparsed_labels"]:
                logger.info(
                    "  navbox entries without a race article (reported, not "
                    "parsed): %s", "; ".join(navbox_meta["unparsed_labels"]),
                )

    # ── STEP 1c: curated map → state-dependent race titles (SECONDARY) ─
    map_titles: Dict[str, tuple] = {}
    if fetch_races:
        map_titles = hardcoded_state_dependent_titles(years, offices=office_filter)
        for race_title, (year, state, office) in map_titles.items():
            race_map.setdefault(race_title, (year, state, office))
        if map_titles:
            meta["hardcoded_titles"] = {
                "candidates": len(map_titles),
                "titles": sorted(map_titles),
            }
            logger.info(
                "Curated state-dependent map: %d candidate race title(s) queued "
                "(fetched in batched article requests, not searches)",
                len(map_titles),
            )

    # ── STEP 1d: opt-in search fallback for gaps navbox + map miss ─────
    if fetch_races and search_discovery and client:
        wanted = [o for o in (office_filter or list(SEARCH_RACE_OFFICES))
                  if o in SEARCH_RACE_OFFICES]
        if wanted:
            logger.info("Searching race articles for state-dependent offices: %s",
                        ", ".join(sorted(wanted)))
            found, n_queries = search_state_dependent_race_titles(
                client, years, offices=office_filter,
            )
            for race_title, (year, state, office) in found.items():
                race_map.setdefault(race_title, (year, state, office))
            meta["search_discovery"] = {
                "queries": int(n_queries),
                "titles": sorted(found),
            }
            logger.info("  %d race article(s) found via search in %d queries",
                        len(found), n_queries)

    # drop race titles outside the office filter
    if office_filter:
        race_map = {
            t: v for t, v in race_map.items()
            if v[2] in office_filter
        }

    # ── STEP 2: race articles → primary / general results + polling ────
    primary_frames: Dict[int, List[pd.DataFrame]] = {}
    general_frames: Dict[int, List[pd.DataFrame]] = {}
    primary_poll_frames: Dict[int, List[pd.DataFrame]] = {}
    general_poll_frames: Dict[int, List[pd.DataFrame]] = {}
    # every fetched race-article text, kept so umbrella pages discovered by
    # the navbox and already fetched via a redirect need no second request
    fetched_texts: Dict[str, str] = {}
    # hash of already-parsed texts: two discovery sources can queue two
    # spellings of the same race title (a redirect alias) — parse once
    parsed_text_hashes: Dict[int, str] = {}
    # (page-text hash, year, state, office) umbrella sections already parsed
    # from a redirecting race title — STEP 2c must not parse them again
    parsed_umbrella_keys: set = set()
    if fetch_races and race_map:
        logger.info("Fetching %d statewide race articles ...", len(race_map))
        content = client.fetch_wikitext(list(race_map))
        for title, (year, state, office) in sorted(race_map.items()):
            text = content.get(title)
            if not text:
                # curated-map candidates are best-effort title guesses — a
                # miss is normal and only logged at debug level
                if title in map_titles:
                    logger.debug("  race article missing (map candidate): %s", title)
                else:
                    logger.warning("  race article missing: %s", title)
                    meta["missing_articles"].append(title)
                continue
            fetched_texts[title] = text

            # duplicate article text = a redirect alias of an already-parsed
            # page (e.g. a curated-map guess and a navbox row naming the
            # same race differently) — parsing it twice would double rows
            text_hash = hash(text)
            if text_hash in parsed_text_hashes:
                logger.debug("  duplicate article text (redirect alias): %s", title)
                meta["races"][title] = {"year": year, "state": state,
                                        "office": office, "skipped": "duplicate"}
                continue
            parsed_text_hashes[text_hash] = title

            # state-year umbrella / disambiguation pages (some race titles
            # redirect to "{y} {State} elections") carry every office's
            # results — parsing them as one race would mis-attribute rows.
            # When the office's own section can be located inside the
            # umbrella (anchor candidates from the race title), parse just
            # that section instead of skipping.
            if looks_like_umbrella_article(text, race_title=title):
                umb_primary: pd.DataFrame = pd.DataFrame()
                umb_general: pd.DataFrame = pd.DataFrame()
                anchors = _anchor_candidates(office, title)
                if anchors:
                    try:
                        umb_primary, umb_general = parse_umbrella_office_section(
                            text, year, state, _state_code(state), office,
                            anchors, source_title=title,
                        )
                    except Exception:
                        logger.exception(
                            "  umbrella section parse failed: %s", title)
                if len(umb_primary) or len(umb_general):
                    parsed_umbrella_keys.add((text_hash, year, state, office))
                    logger.info(
                        "  %s [%s] %s — parsed from the office's umbrella "
                        "section (title redirects to a state-year article): "
                        "primary rows: %d, general rows: %d",
                        year, office, title, len(umb_primary), len(umb_general),
                    )
                    meta["races"][title] = {
                        "year": year, "state": state, "office": office,
                        "primary_rows": int(len(umb_primary)),
                        "general_rows": int(len(umb_general)),
                        "parsed": "umbrella-section",
                    }
                    if len(umb_primary):
                        primary_frames.setdefault(year, []).append(umb_primary)
                    if len(umb_general):
                        general_frames.setdefault(year, []).append(umb_general)
                    continue
                logger.warning(
                    "  race title redirects to a state-year umbrella article "
                    "— skipped: %s", title,
                )
                meta["races"][title] = {"year": year, "state": state,
                                        "office": office, "skipped": "umbrella"}
                continue

            # queue the dedicated polling companion article, if linked
            if include_polling:
                poll_title = discover_polling_article_title(text)
                if poll_title and poll_title not in polling_map:
                    polling_map[poll_title] = (year, state, office)

            try:
                if office == "council of state":
                    # multi-office slate article: one parse per office section
                    # (governor/AG/SoS/treasurer/auditor sections are skipped —
                    # their dedicated race articles are parsed from the
                    # overview {{main}} links)
                    slate = parse_council_of_state_article(
                        text, year, state, _state_code(state),
                        source_title=title,
                    )
                    meta["races"][title] = {
                        "year": year, "state": state, "office": office,
                        "sections": int(len(slate)),
                    }
                    for sub_office, sub_p, sub_g in slate:
                        if len(sub_p):
                            primary_frames.setdefault(year, []).append(sub_p)
                        if len(sub_g):
                            general_frames.setdefault(year, []).append(sub_g)
                    logger.info(
                        "  %s [%s] %s — slate office sections parsed: %d",
                        year, office, title, len(slate),
                    )
                    continue
                if office == "judicial":
                    # multi-seat nonpartisan judicial slates: seat subsections
                    # ("=== Dillard's seat ===") are not primary/general
                    # headings — boxes classify by their own title and
                    # otherwise count as general-election results
                    primary_df, general_df = parse_race_article(
                        text, year, state, _state_code(state), office,
                        source_title=title, default_general=True,
                    )
                else:
                    primary_df, general_df = parse_race_article(
                        text, year, state, _state_code(state), office,
                        source_title=title,
                    )
            except Exception:
                logger.exception("  parse failed: %s", title)
                meta["races"][title] = {"year": year, "state": state,
                                        "office": office, "error": True}
                continue
            meta["races"][title] = {
                "year": year, "state": state, "office": office,
                "primary_rows": int(len(primary_df)),
                "general_rows": int(len(general_df)),
            }
            if len(primary_df):
                primary_frames.setdefault(year, []).append(primary_df)
            if len(general_df):
                general_frames.setdefault(year, []).append(general_df)

            # polling mined from the SAME article text — no extra fetches
            poll_counts = (0, 0)
            if include_polling:
                try:
                    p_polls, g_polls = parse_race_polling(
                        text, year, state, _state_code(state), office,
                    )
                except Exception:
                    # polling must never break the results pipeline
                    logger.exception("  polling parse failed: %s", title)
                    p_polls = g_polls = pd.DataFrame()
                if len(p_polls):
                    primary_poll_frames.setdefault(year, []).append(p_polls)
                if len(g_polls):
                    general_poll_frames.setdefault(year, []).append(g_polls)
                poll_counts = (len(p_polls), len(g_polls))
            logger.info(
                "  %s [%s] %s — primary rows: %d, general rows: %d%s",
                year, office, title, len(primary_df), len(general_df),
                (", primary polls: %d, general polls: %d" % poll_counts)
                if include_polling else "",
            )

        # ── STEP 2b: dedicated 'Opinion polling for ...' articles ───────
        if include_polling and polling_map:
            fresh = [t for t in polling_map if t not in race_map]
            logger.info("Fetching %d dedicated polling articles ...", len(fresh))
            poll_content = client.fetch_wikitext(fresh) if fresh else {}
            for title in sorted(fresh):
                year, state, office = polling_map[title]
                text = poll_content.get(title)
                if not text:
                    logger.warning("  polling article missing: %s", title)
                    meta["missing_articles"].append(title)
                    continue
                try:
                    p_polls, g_polls = parse_race_polling(
                        text, year, state, _state_code(state), office,
                        whole_article=True,
                    )
                except Exception:
                    logger.exception("  polling article parse failed: %s", title)
                    p_polls = g_polls = pd.DataFrame()
                meta["polling_articles"][title] = {
                    "year": year, "state": state, "office": office,
                    "primary_poll_rows": int(len(p_polls)),
                    "general_poll_rows": int(len(g_polls)),
                }
                if len(p_polls):
                    primary_poll_frames.setdefault(year, []).append(p_polls)
                if len(g_polls):
                    general_poll_frames.setdefault(year, []).append(g_polls)
                logger.info(
                    "  %s [%s] %s — primary polls: %d, general polls: %d",
                    year, office, title, len(p_polls), len(g_polls),
                )

    # ── STEP 2c: year-page umbrella sections (per-office slices) ────────
    # The navbox anchors some down-ballot races at "{y} {State}
    # elections#Office" — the results live in one section of the state-year
    # umbrella article.  Fetch each page once, slice it per office.
    if fetch_races and umbrella_map:
        fresh_pages = [p for p in umbrella_map if p not in fetched_texts]
        page_texts: Dict[str, Optional[str]] = {
            p: fetched_texts[p] for p in umbrella_map if p in fetched_texts
        }
        if fresh_pages:
            logger.info(
                "Fetching %d state-year umbrella page(s) for anchored "
                "office sections ...", len(fresh_pages))
            page_texts.update(client.fetch_wikitext(fresh_pages))
        for page in sorted(umbrella_map):
            text = page_texts.get(page)
            if not text:
                logger.warning("  umbrella page missing: %s", page)
                meta["missing_articles"].append(page)
                continue
            for (year, state, office), anchors in sorted(
                    umbrella_map[page].items(),
                    key=lambda kv: (kv[0][0], kv[0][1], kv[0][2])):
                if (hash(text), year, state, office) in parsed_umbrella_keys:
                    # already parsed from a redirecting race title pointing
                    # at this very page — the section would be parsed twice
                    logger.debug(
                        "  %s [%s] %s — umbrella section already parsed via "
                        "redirect", year, office, page)
                    continue
                try:
                    umb_p, umb_g = parse_umbrella_office_section(
                        text, year, state, _state_code(state), office,
                        anchors, source_title=page,
                    )
                except Exception:
                    logger.exception(
                        "  umbrella section parse failed: %s (%s %s %s)",
                        page, year, state, office)
                    continue
                meta.setdefault("umbrella_sections", {})[
                    f"{page}#{anchors[0]} ({year} {state} {office})"
                ] = {
                    "primary_rows": int(len(umb_p)),
                    "general_rows": int(len(umb_g)),
                    "anchor_tried": anchors[0],
                }
                if not len(umb_p) and not len(umb_g):
                    logger.info(
                        "  %s [%s] %s — umbrella section carries no result "
                        "tables (anchor: %s)", year, office, page, anchors[0])
                else:
                    logger.info(
                        "  %s [%s] %s#%s — primary rows: %d, general rows: %d",
                        year, office, page, anchors[0], len(umb_p), len(umb_g))
                if len(umb_p):
                    primary_frames.setdefault(year, []).append(umb_p)
                if len(umb_g):
                    general_frames.setdefault(year, []).append(umb_g)

    def _write_family(frames_by_year: Dict[int, List[pd.DataFrame]], family: str) -> None:
        for year in sorted(frames_by_year):
            df_year = pd.concat(frames_by_year[year], ignore_index=True)
            path = os.path.join(statewide_dir, f"statewide_{family}{year}.csv")
            df_year.to_csv(path, index=False)
            logger.info("  saved -> %s", path)

    _write_family(primary_frames, "primary_results_")
    _write_family(general_frames, "general_results_")
    _write_family(primary_poll_frames, "primary_polling_")
    _write_family(general_poll_frames, "general_polling_")

    # ── STEP 2c: vote margins (winner vs. runner-up per race) ───────────
    general_results_by_year: Dict[int, pd.DataFrame] = {
        year: pd.concat(frames, ignore_index=True)
        for year, frames in general_frames.items() if frames
    }
    margin_frames = build_margin_frames(
        general_results_by_year, overview_results_by_year,
    )
    for year in sorted(margin_frames):
        path = os.path.join(statewide_dir, f"statewide_margins_{year}.csv")
        margin_frames[year].to_csv(path, index=False)
        logger.info("  saved -> %s (%d races)", path, len(margin_frames[year]))

    # ── STEP 3: combined _all files rebuilt from ALL per-year files ────
    combined_summary = None
    for family, label in (("results_", "results"),
                          ("primary_results_", "primary results"),
                          ("general_results_", "general results"),
                          ("primary_polling_", "primary polling"),
                          ("general_polling_", "general polling"),
                          ("margins_", "margins")):
        combined = _rebuild_combined(statewide_dir, family)
        if combined is not None:
            path = os.path.join(statewide_dir, f"statewide_{family}all.csv")
            combined.to_csv(path, index=False)
            logger.info("Combined -> %s (%s rows)", path, f"{len(combined):,}")
            if family == "results_":
                combined_summary = combined
        else:
            logger.info("No per-year %s files on disk yet — combined file skipped", label)

    return combined_summary if combined_summary is not None else pd.DataFrame()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run()
