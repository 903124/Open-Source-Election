"""
district_lean.py — Predicted partisan lean for every U.S. congressional
district, computed by joining two data sources:

  (1) the district -> county crosswalk mapping
      ``resources/district_counties.json``, generated directly from public
      boundary geometry by ``district_counties.py`` (UCLA cdmaps district
      polygons x 2010 Census county polygons, Full/Partial classification;
      NO Excel workbook involved), and

  (2) the county-level presidential results produced by
      ``presidential_elections.py`` (``data/presidential/presidential_results_{year}.csv``).

Method
------
For each presidential election year Y in [start_year, end_year] the pipeline
uses the map vintage that was actually in force:

    2004, 2008, 2012  ->  2000s map (108th-112th Congress, 2003-2013)
    2016, 2020        ->  2010s map (113th-117th Congress, 2013-2023)
    2024              ->  2020s map (118th-119th Congress, 2023-present)

(Nov 2012 elected the 113th Congress but voted under the 2000s map; the
2000s snapshot shows the lines as finally used, including the Texas 2003 /
Georgia 2006-07 mid-decade redraws.)

Every listed county's two-party vote (Democratic vs Republican; all other
parties are summed as "other" and excluded from the lean) is allocated to
the districts that contain it:

  * whole counties   -> 100% of the county's vote to the district;
  * partial counties -> allocated in proportion to each claimant district's
    geometric share of the county (``county_share_pct`` in the mapping;
    whole claimants carry weight 1.0, weights normalised across claimants).
    The legacy equal-split rule amplified boundary slivers — a 2% geometric
    sliver would have carried 50% of a two-claimant county's vote;
  * at-large districts (AK, DE, ND, SD, VT, WY, DC) -> all counties of the
    jurisdiction, i.e. exact statewide aggregation.

Allocation is conservative: each county's votes are distributed across its
claimant districts without loss, so state totals are preserved.

lean_pct is a fixed-weight blend of four Cook-PVI-style leans, each
computed as (Democratic two-party share of the race - the same race's
national Democratic two-party share) * 100 and reported as ``D+x.x`` /
``R+x.x`` / ``EVEN``:

    pres      0.40  the county-allocated presidential vote described
                    below;
    house     0.20  the district's own U.S. House general result
                    (data/house/house_results_{year}.csv) — exact
                    district-level votes;
    senate    0.20  the state's U.S. Senate general vote (regular +
                    special races pooled, data/senate/) — statewide;
    statewide 0.20  the state's other statewide ballot pooled — governor,
                    attorney general, secretary of state, state treasurer
                    (data/statewide/) plus the state-legislature chamber
                    totals (data/state_senate/ + data/state_house/,
                    contested districts only).

These weights are the consensus optimum of an out-of-sample weight search
over 2004-2024 (mean MAE 7.46 -> 6.87 vs the previous house-heavy blend
across five forecasting targets; see README "Lean methodology"). Weights
are renormalised over the sources actually available to each district-year:
one-sided races (a major party polled no votes — top-two D-vs-D / R-vs-R
matchups, walkovers) and missing files drop out. Older
Wikipedia House boxes often omit the winner's raw vote count; those rows
are reconstructed as percentage x district turnout, and a district
reporting no turnout falls back to a percentage-only two-party share.
The presidential component keeps the county-allocation caveats: split
counties are allocated by geometric area shares (not population), so
leans for districts containing large shared urban counties are dampened
toward the state mean; ``votes_from_partial_pct`` measures how much of
each district's presidential vote came from that approximation. Alaska
has no county presidential tables before 2024, so AK-AL blends house +
senate for 2004-2020.

The PVI-style summary (``district_pvi_summary.csv``) averages the lean over
the presidential elections held under each vintage
(2000s: 2004+2008+2012 · 2010s: 2016+2020 · 2020s: 2024).

Midterm district lean (``--midterm``)
------------------------------------
The same scheme measured on the *midterm* ballot instead of the
presidential one. Midterm years have no presidential race, so the blend
is the unified weights minus the pres component — house 0.20 / senate
0.20 / statewide 0.20 before renormalisation (≈ 1/3 each when all three
are up; 50/50 house + statewide when the state has no Senate race) —
drawn from the district's own House general result plus the statewide
races the state actually ran: U.S. Senate regular + special races pooled
and the statewide executive offices governor, attorney general, secretary
of state, state treasurer, plus the state-legislature chamber totals
(contested state-leg districts only), each against its own national
baseline. The House component makes the midterm lean district-specific;
districts whose House race is missing or one-sided fall back to the
statewide blend and are flagged ``statewide_baseline``. Map vintages:
2006/2010 -> 2000s, 2014/2018 -> 2010s, 2022 -> 2020s. Fusion party lines are summed as reported
(Working Families / Conservative / Independence votes land in "other",
excluded from the two-party baseline); ``Total`` rows are excluded;
write-ins count as "other"; and races where one major party is absent
from the ballot entirely (top-two / top-four matchups such as CA-2016 or
AK-2022 senate) are dropped as uninformative.

Midterm outputs (under *output_dir*, default ``data/district_lean/``):
    district_lean_midterm_{year}.csv   one row per district in force that midterm
    district_lean_midterm_all.csv      combined across the requested range
    district_midterm_lean_summary.csv  per-district midterm lean per vintage
    district_lean_midterm_metadata_{ts}.json  method notes + coverage

Inputs are read-only: this module does NOT fetch anything unless
``fetch_missing=True``, in which case missing
``presidential_results_{year}.csv`` files are produced on the fly by
calling ``presidential_elections.run`` (rate-limited MediaWiki API).

Output columns (written under *output_dir*, default ``data/district_lean/``):
    year, map_vintage, state, state_code, district, district_note, at_large,
    counties_whole, counties_partial, counties_whole_list,
    counties_partial_list, matched_counties, d_votes, r_votes, other_votes,
    total_votes, d_share_two_party_pct, r_share_two_party_pct,
    national_d_share_two_party_pct, lean_pct, lean_label,
    votes_from_partial_pct,
    lean_pres_pct, lean_house_pct, lean_senate_pct, lean_statewide_pct,
    weight_pres, weight_house, weight_senate, weight_statewide, sources_used

(d_share / r_share / national_d_share keep their historical meaning — the
presidential component's numbers — while ``lean_pct`` is the blend.)

    district_lean_{year}.csv        one row per district in force that year
    district_lean_all.csv           combined across the requested range
    district_pvi_summary.csv        per-district lean averaged per vintage
    district_lean_metadata_{ts}.json  method notes + coverage + unmatched counties

Usage:
    python cli.py lean                                  # 2004-2024 from local CSVs
    python cli.py lean --start-year 2016 --end-year 2024
    python cli.py lean --fetch-missing                  # fetch missing presidential CSVs first
    python cli.py lean --mapping resources/district_counties.json --presidential-dir data/presidential
    python cli.py lean --house-dir data/house --senate-dir data/senate
    python cli.py lean --midterm                        # midterm lean: house + senate + statewide + state-leg votes
    python cli.py lean --midterm --components senate,statewide  # drop the state-leg chambers
    python cli.py crosswalk                             # regenerate the mapping JSON from geometry
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from typing import Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger("district_lean")

MAPPING_NAME = "district_counties.json"

#: map vintage in force at each presidential election (see module docstring)
VINTAGE_FOR_YEAR: Dict[int, str] = {
    2004: "2000s", 2008: "2000s", 2012: "2000s",
    2016: "2010s", 2020: "2010s",
    2024: "2020s",
}
LEAN_YEARS: Tuple[int, ...] = tuple(sorted(VINTAGE_FOR_YEAR))
MAP_VINTAGES: Tuple[str, ...] = ("2000s", "2010s", "2020s")

#: unified blend weights for the district lean (see the module docstring) —
#: the consensus optimum of an out-of-sample weight search over 2004-2024
#: (scripts/blend_investigation.py; README "Lean methodology"). "statewide"
#: pools governor / attorney general / secretary of state / state treasurer
#: with the state-legislature chamber totals. Renormalised over the sources
#: present for each district-year.
BLEND_WEIGHTS: Dict[str, float] = {
    "pres": 0.40, "house": 0.20, "senate": 0.20, "statewide": 0.20}
#: midterm years have no presidential race, so the midterm blend is the same
#: scheme minus the pres component (renormalised: 1/3 each when house +
#: senate + statewide are all present, 50/50 house + statewide when the
#: state has no Senate race on the ballot)
MIDTERM_BLEND_WEIGHTS: Dict[str, float] = {
    k: w for k, w in BLEND_WEIGHTS.items() if k != "pres"}

#: two-party classification (presidential_elections.py already normalised
#: DFL / Democratic-NPL into these labels; be tolerant of dash variants)
DEM_PARTIES = {"democratic", "democratic-farmer-labor", "democratic-npl"}
REP_PARTIES = {"republican"}

# ────────────────────────────────────────────────────────────────────────────
# COUNTY NAME NORMALISATION
# ────────────────────────────────────────────────────────────────────────────

_STRIP_SUFFIXES = ("county", "parish", "borough", "census area", "municipality")


def normalize_county(name: str) -> str:
    """Reduce a county / parish / borough name from either source to a match key.

    * case-folded; punctuation (St., DeKalb, De Witt ...) removed;
    * ``City of X`` (xlsx style) -> ``X City`` — the trailing "city" is KEPT
      so Virginia/Maryland/Missouri independent cities never collide with
      their same-named counties (Fairfax County vs Fairfax City, St. Louis
      County vs St. Louis City, Baltimore County vs Baltimore City);
    * every other county-type suffix ("County", "Parish", "Borough",
      "Census Area", "Municipality") is stripped, because the presidential
      CSVs use bare names for most subdivisions but keep "Borough"/"Census
      Area" (Alaska) and the disambiguating "County"/"City" (Virginia).

    >>> normalize_county("St. Joseph County") == normalize_county("St. Joseph")
    True
    >>> normalize_county("City of Fairfax") == normalize_county("Fairfax City")
    True
    """
    t = (name or "").strip().lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))  # ñ -> n
    if t.startswith("city of "):
        t = t[len("city of "):].strip() + " city"
    # strip the trailing county-type suffix (only one ever trails a name),
    # but never the "city" marker
    for suffix in sorted(_STRIP_SUFFIXES, key=len, reverse=True):
        pat = r"[\s\-]+" + re.escape(suffix) + r"$"
        stripped = re.sub(pat, "", t)
        if stripped != t:
            t = stripped
            break
    return re.sub(r"[^a-z0-9]", "", t)


def _key_variants(key: str) -> List[str]:
    """Ordered lookup variants for a county match key: the key itself, then
    with a trailing ``city`` / ``county`` marker stripped (one at a time).
    Used ONLY through the uniqueness-guarded *alias* index built by
    :func:`load_county_counts` — a raw variant hit could silently bind a
    city to its same-named county."""
    out = [key]
    for token in ("city", "county"):
        if key.endswith(token) and len(key) > len(token):
            stripped = key[:-len(token)]
            if stripped not in out:
                out.append(stripped)
    return out


def _party_class(party: str) -> str:
    p = re.sub(r"[–—−]", "-", str(party or "").strip().lower())
    if p in DEM_PARTIES:
        return "D"
    if p in REP_PARTIES:
        return "R"
    return "O"


def _round_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Round renormalised blend weights to 3 decimals while keeping their sum
    at exactly 1.000 (a largest-component nudge absorbs the rounding residue:
    three equal 1/3 shares would otherwise print as .333 x 3 = .999).
    Purely cosmetic — the unrounded weights already sum to 1."""
    rounded = {k: round(w, 3) for k, w in weights.items()}
    diff = round(1.0 - sum(rounded.values()), 3)
    if diff and rounded:
        kmax = max(rounded, key=lambda k: rounded[k])
        rounded[kmax] = round(rounded[kmax] + diff, 3)
    return rounded


def lean_label(lean_pct: Optional[float]) -> str:
    """Format a lean in percentage points as ``D+x.x`` / ``R+x.x`` / ``EVEN``."""
    if lean_pct is None or pd.isna(lean_pct):
        return ""
    if abs(lean_pct) < 0.05:
        return "EVEN"
    side = "D" if lean_pct > 0 else "R"
    return f"{side}+{abs(lean_pct):.1f}"


# ────────────────────────────────────────────────────────────────────────────
# DISTRICT -> COUNTY MAPPING (geometric crosswalk JSON)
# ────────────────────────────────────────────────────────────────────────────

#: safety net for mapping files that carry no jurisdiction names
ABBR_FALLBACK_NAMES: Dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana",
    "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}

def load_district_map(mapping_path: str) -> Tuple[List[dict], dict]:
    """Load ``resources/district_counties.json`` (from district_counties.py)
    into per-district records.

    Returns (districts, meta). Each district record::

        {"state": "Indiana", "state_code": "IN", "district": "IN-02",
         "district_num": "02", "district_note": "",
         "vintages": {"2000s": {"exists": True, "at_large": False,
                                "counties": [("Fulton County", "fulton", False), ...]},
                      "2010s": {...}, "2020s": {...}}}

    ``counties`` entries are (raw_display_name, match_key, is_partial);
    jurisdictions with a single seat in a vintage are flagged ``at_large``
    and carry an empty county list (the lean consumes the statewide sum).
    """
    with open(mapping_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    vintages_raw: Dict[str, Dict[str, dict]] = raw["vintages"]
    src_meta: dict = raw.get("meta", {}) or {}

    meta: dict = {
        "path": str(mapping_path),
        "generated": src_meta.get("generated", ""),
        "source": src_meta.get("source", {}),
        "snapshot_congress": src_meta.get("snapshot_congress", {}),
        "snapshot_note": src_meta.get("snapshot_note", ""),
        "classification_note": src_meta.get("classification_note", ""),
        "thresholds": src_meta.get("thresholds", {}),
        "jurisdictions": src_meta.get("jurisdictions", {}),
        "seats": src_meta.get("seats", {}),
        "event_notes": src_meta.get("event_notes", {}),
        "validation": src_meta.get("validation", {}),
        "states_total": len(vintages_raw.get(MAP_VINTAGES[0], {})),
        "duplicate_labels": [],
        "unrecognised_labels": [],
    }

    districts_by_label: Dict[Tuple[str, str], dict] = {}
    states = sorted({abbr for cyc in vintages_raw.values() for abbr in cyc})
    for abbr in states:
        state_name = (meta["jurisdictions"].get(abbr, {})
                      .get("name") or ABBR_FALLBACK_NAMES.get(abbr, abbr))
        for vintage in MAP_VINTAGES:
            state_map = vintages_raw.get(vintage, {}).get(abbr, {})
            # a jurisdiction with a single seat in a vintage is at-large in
            # that vintage (labelled "XX-AL", consuming the statewide sum);
            # MT is the real case: at-large 2000s/2010s, MT-01/MT-02 in 2020s
            solo = len(state_map) == 1
            for dnum in state_map:
                label = f"{abbr}-AL" if solo else f"{abbr}-{int(dnum):02d}"
                rec = districts_by_label.setdefault(
                    (abbr, label),
                    {"state": state_name, "state_code": abbr,
                     "district": label, "label_full": label,
                     "district_num": "00" if solo else f"{int(dnum):02d}",
                     "district_note": "", "vintages": {}})
                entry = state_map[dnum]
                counties: List[Tuple[str, str, bool, float]] = []
                if not solo:
                    for name in entry.get("full", []):
                        key = normalize_county(name)
                        if key:
                            counties.append((name, key, False, 1.0))
                    for part in entry.get("partial", []):
                        key = normalize_county(part["county"])
                        if key:
                            # geometric share of the county's area inside this
                            # district; used to weight vote allocation
                            counties.append(
                                (part["county"], key, True,
                                 float(part.get("county_share_pct", 100.0)) / 100.0))
                rec["vintages"][vintage] = {
                    "exists": True,
                    "at_large": solo,
                    "counties": counties,
                }

    # vintages where a district did not exist
    for rec in districts_by_label.values():
        for vintage in MAP_VINTAGES:
            rec["vintages"].setdefault(
                vintage, {"exists": False, "at_large": False, "counties": []})

    districts = sorted(districts_by_label.values(),
                       key=lambda d: (d["state_code"], int(d["district_num"])))
    meta["districts_total"] = len(districts)
    if not districts:
        raise ValueError(f"No district rows found in {mapping_path!r}")
    return districts, meta


# ────────────────────────────────────────────────────────────────────────────
# PRESIDENTIAL COUNTY VOTES
# ────────────────────────────────────────────────────────────────────────────

def load_county_counts(pres_dir: str, year: int):
    """Aggregate one year's county-level presidential CSV into two-party totals.

    Returns (counts, national, n_rows, alias) where

    * ``counts`` maps ``(state_code, county_key)`` ->
      ``{"D": v, "R": v, "O": v}`` (votes; all third parties in "O"),
    * ``national`` = the same aggregation over the whole file,
    * ``alias`` maps ``(state_code, variant_key)`` -> ``county_key`` for
      UNAMBIGUOUS shortened forms. The article series writes independent
      cities bare in some eras ("Portsmouth") while the crosswalk always
      says "Portsmouth City"; the alias lets ``Portsmouth City`` fall back
      to ``Portsmouth`` — but only when exactly one CSV subdivision shares
      that shortened key, so "Fairfax City" can never be bound to Fairfax
      County (or vice versa) in years where the suffixes are missing.
    """
    path = os.path.join(pres_dir, f"presidential_results_{year}.csv")
    if not os.path.exists(path):
        return None, None, 0, {}
    df = pd.read_csv(path, dtype={"state_code": str, "county": str, "party": str})
    df["votes"] = pd.to_numeric(df["votes"], errors="coerce").fillna(0)
    df["pc"] = df["party"].map(_party_class)
    df["ckey"] = df["county"].map(normalize_county)

    def _agg(group_frame: pd.DataFrame) -> Dict:
        out: Dict = {}
        for (sc, ckey, pc), v in group_frame.groupby(
                ["state_code", "ckey", "pc"], observed=True)["votes"].sum().items():
            out.setdefault((sc, ckey), {"D": 0.0, "R": 0.0, "O": 0.0})[pc] = float(v)
        return out

    counts = _agg(df)
    national = {"D": 0.0, "R": 0.0, "O": 0.0}
    for pc, v in df.groupby("pc")["votes"].sum().items():
        national[pc] = float(v)

    # uniqueness-guarded bare-form alias (see docstring)
    variants_of: Dict[Tuple[str, str], set] = {}
    for (sc, ckey) in counts:
        for v_key in _key_variants(ckey):
            variants_of.setdefault((sc, v_key), set()).add(ckey)
    alias: Dict[Tuple[str, str], str] = {
        k: next(iter(v)) for k, v in variants_of.items() if len(v) == 1
    }
    return counts, national, int(len(df)), alias


# ────────────────────────────────────────────────────────────────────────────
# HOUSE DISTRICT VOTES (district-level component of the blend)
# ────────────────────────────────────────────────────────────────────────────

def _house_label(state_code: str, district) -> Optional[str]:
    """House CSV district code -> lean district label ("AL-01", "AK-AL")."""
    d = str(district).strip()
    if not d:
        return None
    if d.upper() == "AL":
        return f"{state_code}-AL"
    try:
        return f"{state_code}-{int(float(d)):02d}"
    except ValueError:
        return None


def load_house_year_races(house_dir: str, year: int, keep_codes, info: dict):
    """U.S. House general results for *year* -> per-district two-party records.

    Returns ``(counts, national)`` where ``counts`` maps the lean district
    label to ``{"state", "D", "R", "O", "share_pct", "votes_exact"}``
    (``share_pct`` = Democratic two-party share of the race) and
    ``national`` is the D/R vote sum over the informative districts — the
    votes-exact ones only. One-sided races (a major party polled no votes:
    top-two D-vs-D / R-vs-R matchups, walkovers) are dropped as
    uninformative; the same rule the midterm path applies.

    Wikipedia's older election boxes often omit the winner's raw vote
    count; those rows are reconstructed as ``percentage x district
    turnout`` — and raw counts contradicting that product by more than 2%
    of turnout are replaced by it, because wiki vote cells are occasionally
    corrupted in the source tables while the percentage is stable. A
    district reporting no turnout at all falls back to a percentage-only
    two-party share (``votes_exact=False``, excluded from the national
    sum).
    """
    path = os.path.join(house_dir, f"house_results_{year}.csv")
    if not os.path.exists(path):
        info["missing_files"].append(path)
        return {}, None
    df = pd.read_csv(path, dtype={"state_code": str, "district": str})
    df = df[df["state_code"].isin(keep_codes)]
    if df.empty:
        return {}, None
    df["_v"] = pd.to_numeric(df["votes"], errors="coerce")
    df["_pct"] = pd.to_numeric(df["percentage"], errors="coerce")
    df["_tv"] = pd.to_numeric(df["total_votes"], errors="coerce")
    # district turnout from any row that reports it (constant per district)
    df["_turnout"] = df.groupby(["state_code", "district"])["_tv"].transform("max")
    est = df["_pct"] / 100.0 * df["_turnout"]
    suspect = (df["_v"].notna() & est.notna()
               & ((df["_v"] - est).abs() > 0.02 * df["_turnout"]))
    info["house_votes_fixed"] = int(suspect.sum())
    df.loc[suspect, "_v"] = est[suspect]
    need = df["_v"].isna() & df["_pct"].notna() & df["_turnout"].notna()
    df.loc[need, "_v"] = est[need]
    df["_pc"] = df["party"].map(classify_party)

    counts: Dict[str, dict] = {}
    for (code, dist), sub in df.groupby(["state_code", "district"],
                                        observed=True):
        label = _house_label(code, dist)
        if label is None:
            continue
        rec = {"state": code, "D": 0.0, "R": 0.0, "O": 0.0}
        for cls in ("D", "R", "O"):
            rec[cls] = float(sub.loc[sub["_pc"] == cls, "_v"].sum())
        pct = sub.groupby(sub["_pc"])["_pct"].sum()   # party share of turnout
        if rec["D"] > 0 and rec["R"] > 0:
            rec["share_pct"] = rec["D"] / (rec["D"] + rec["R"]) * 100.0
            rec["votes_exact"] = True
        elif pct.get("D", 0.0) > 0 and pct.get("R", 0.0) > 0:
            # no usable vote counts anywhere — percentage-only two-party share
            rec["share_pct"] = 100.0 * pct["D"] / (pct["D"] + pct["R"])
            rec["votes_exact"] = False
        else:
            info["house_excluded"].append(
                {"state": code, "district": label,
                 "d_votes": round(rec["D"]), "r_votes": round(rec["R"])})
            continue
        counts[label] = rec

    national = {"D": 0.0, "R": 0.0}
    for rec in counts.values():
        if rec["votes_exact"]:
            national["D"] += rec["D"]
            national["R"] += rec["R"]
    return counts, national


# ────────────────────────────────────────────────────────────────────────────
# LEAN COMPUTATION
# ────────────────────────────────────────────────────────────────────────────

_ROW_FIELDS = [
    "year", "map_vintage", "state", "state_code", "district", "district_note",
    "at_large", "counties_whole", "counties_partial", "counties_whole_list",
    "counties_partial_list", "matched_counties", "d_votes", "r_votes",
    "other_votes", "total_votes", "d_share_two_party_pct",
    "r_share_two_party_pct", "national_d_share_two_party_pct", "lean_pct",
    "lean_label", "votes_from_partial_pct",
    "lean_pres_pct", "lean_house_pct", "lean_senate_pct", "lean_statewide_pct",
    "weight_pres", "weight_house", "weight_senate", "weight_statewide",
    "sources_used",
]


def compute_year_lean(
    districts: List[dict],
    counts: Dict[Tuple[str, str], Dict[str, float]],
    national: Dict[str, float],
    year: int,
    alias: Optional[Dict[Tuple[str, str], str]] = None,
    house_counts: Optional[Dict[str, dict]] = None,
    nat_house_share: Optional[float] = None,
    senate_state_votes: Optional[Dict[str, Dict[str, float]]] = None,
    nat_senate_share: Optional[float] = None,
    statewide_state_votes: Optional[Dict[str, Dict[str, float]]] = None,
    nat_statewide_share: Optional[float] = None,
) -> Tuple[pd.DataFrame, dict]:
    """Allocate county votes to districts for one presidential year and blend
    the four component leans (pres 0.40 / house 0.20 / senate 0.20 /
    statewide 0.20 — the statewide pool being governor + down-ballot
    executive + state-leg chamber totals, renormalised over the sources
    present — see the module docstring).

    Returns (rows_frame, info) — info carries unmatched county names and
    per-state allocation coverage for the metadata JSON.
    """
    alias = alias or {}

    def _resolve(state_code: str, key: str):
        """Exact match first, then uniqueness-guarded bare-form fallback.
        Returns the CSV-side county key, or None when unmatched."""
        if (state_code, key) in counts:
            return key
        for v_key in _key_variants(key):
            mapped = alias.get((state_code, v_key))
            if mapped is not None and (state_code, mapped) in counts:
                return mapped
        return None

    vintage = VINTAGE_FOR_YEAR[year]
    nat_d = national.get("D", 0.0)
    nat_r = national.get("R", 0.0)
    nat_tp = nat_d + nat_r
    nat_d_share = (nat_d / nat_tp * 100.0) if nat_tp else float("nan")

    by_state: Dict[str, List[dict]] = {}
    for d in districts:
        if d["vintages"][vintage]["exists"]:
            by_state.setdefault(d["state_code"], []).append(d)

    rows: List[dict] = []
    info: dict = {"year": year, "vintage": vintage, "unmatched": {},
                  "unclaimed": {}, "multi_whole": [], "states": {}}

    for state_code, dists in sorted(by_state.items()):
        # ── resolve every mapping county entry to a CSV-side key ─────────
        # (the same real-world county can be written "Bedford City" in one
        # row and "Bedford County" in another; both must collapse onto the
        # single CSV subdivision so votes are neither split nor doubled)
        resolved: Dict[str, Optional[str]] = {}
        for d in dists:
            for _raw, ckey, _partial, _w in d["vintages"][vintage]["counties"]:
                if ckey not in resolved:
                    resolved[ckey] = _resolve(state_code, ckey)

        # ── county -> claimant index for this state/vintage (CSV-key space) ──
        # whole claimants carry weight 1.0 (the full county), partial
        # claimants the geometric county-area share recorded in the mapping
        claim: Dict[str, Dict[str, list]] = {}
        for d in dists:
            vinfo = d["vintages"][vintage]
            if vinfo["at_large"]:
                continue
            for _raw, ckey, partial, area_w in vinfo["counties"]:
                rkey = resolved[ckey]
                if rkey is None:
                    continue
                slot = claim.setdefault(rkey, {"whole": [], "partial": []})
                side = "partial" if partial else "whole"
                if all(x[0]["district"] != d["district"] for x in slot[side]):
                    slot[side].append((d, area_w))

        # counties present in the vote data but claimed by nobody (at-large
        # states consume every county via the statewide sum, so they can
        # never have leftovers)
        state_keys = {ck for (sc, ck) in counts if sc == state_code}
        if any(d["vintages"][vintage]["at_large"] for d in dists):
            unclaimed: List[str] = []
        else:
            unclaimed = sorted(state_keys - set(claim))
        if unclaimed:
            info["unclaimed"][state_code] = unclaimed

        # ── per-district allocation ───────────────────────────────────────
        state_available = sum(
            sum(v.values()) for (sc, _ck), v in counts.items() if sc == state_code)
        state_allocated = 0.0
        unmatched_state: List[str] = []

        def _county_weight(rkey: str, district_label: str) -> float:
            """This district's share of the county's votes: its geometric
            area weight normalised across all claimants (whole = 1.0).
            Falls back to an equal split if weights are unavailable."""
            slot = claim[rkey]
            entries = slot["whole"] + slot["partial"]
            if not entries:
                return 0.0
            sum_w = sum(w for _d, w in entries)
            if sum_w <= 0:
                return 1.0 / len(entries)
            for d_rec, w in entries:
                if d_rec["district"] == district_label:
                    return w / sum_w
            return 0.0

        for d in dists:
            vinfo = d["vintages"][vintage]
            counties = vinfo["counties"]
            whole_names, partial_names = [], []
            d_votes = r_votes = o_votes = partial_votes = 0.0
            matched = 0

            if vinfo["at_large"]:
                for (sc, ckey), v in counts.items():
                    if sc != state_code:
                        continue
                    d_votes += v["D"]; r_votes += v["R"]; o_votes += v["O"]
                    matched += 1
                counties_whole_n = counties_partial_n = 0
                whole_list = partial_list = ""
            else:
                used: set = set()          # CSV keys already consumed by THIS district
                for raw, ckey, is_partial, area_w in counties:
                    rkey = resolved[ckey]
                    if rkey is None:
                        unmatched_state.append(raw)
                        continue
                    if rkey in used:       # city+county spellings of one unit
                        continue
                    used.add(rkey)
                    v = counts[(state_code, rkey)]
                    tv = v["D"] + v["R"] + v["O"]
                    slot = claim[rkey]
                    weight = _county_weight(rkey, d["district"])
                    if is_partial:
                        partial_names.append(raw)
                        partial_votes += weight * tv
                    else:
                        if len(slot["whole"]) > 1:
                            info["multi_whole"].append(
                                {"year": year, "state": state_code, "county": raw,
                                 "districts": [x[0]["district"] for x in slot["whole"]]})
                        whole_names.append(raw)
                    d_votes += weight * v["D"]
                    r_votes += weight * v["R"]
                    o_votes += weight * v["O"]
                    matched += 1
                counties_whole_n = len(whole_names)
                counties_partial_n = len(partial_names)
                whole_list = "; ".join(whole_names)
                partial_list = "; ".join(partial_names)

            total = d_votes + r_votes + o_votes
            state_allocated += total
            tp = d_votes + r_votes
            d_share = (d_votes / tp * 100.0) if tp else float("nan")
            r_share = (r_votes / tp * 100.0) if tp else float("nan")
            lean_pres = d_share - nat_d_share if tp else None

            # ── blend: pres .40 / house .20 / senate .20 / statewide .20,
            # renormalised over the sources present ───────────────────────
            lean_house = lean_senate = lean_statewide = None
            hh = (house_counts or {}).get(d["district"])
            if hh is not None and nat_house_share is not None \
                    and not pd.isna(nat_house_share):
                lean_house = hh["share_pct"] - nat_house_share
            sv = (senate_state_votes or {}).get(state_code)
            if sv and nat_senate_share is not None \
                    and not pd.isna(nat_senate_share):
                stp = sv["D"] + sv["R"]
                if stp > 0:
                    lean_senate = (sv["D"] / stp * 100.0) - nat_senate_share
            wv = (statewide_state_votes or {}).get(state_code)
            if wv and nat_statewide_share is not None \
                    and not pd.isna(nat_statewide_share):
                wtp = wv["D"] + wv["R"]
                if wtp > 0:
                    lean_statewide = (wv["D"] / wtp * 100.0) \
                        - nat_statewide_share
            parts = {
                "pres": (lean_pres, BLEND_WEIGHTS["pres"]),
                "house": (lean_house, BLEND_WEIGHTS["house"]),
                "senate": (lean_senate, BLEND_WEIGHTS["senate"]),
                "statewide": (lean_statewide, BLEND_WEIGHTS["statewide"]),
            }
            usable = {k: v for k, v in parts.items()
                      if v[0] is not None and not pd.isna(v[0])}
            wsum = sum(w for _l, w in usable.values())
            blend = (sum(l * w for l, w in usable.values()) / wsum
                     if wsum > 0 else float("nan"))
            if wsum > 0:
                weights = _round_weights(
                    {k: (w / wsum if k in usable else 0.0)
                     for k, (_l, w) in parts.items()})
            else:
                weights = {k: 0.0 for k in parts}

            rows.append({
                "year": year,
                "map_vintage": vintage,
                "state": d["state"],
                "state_code": state_code,
                "district": d["district"],
                "district_note": d["district_note"],
                "at_large": vinfo["at_large"],
                "counties_whole": counties_whole_n,
                "counties_partial": counties_partial_n,
                "counties_whole_list": whole_list,
                "counties_partial_list": partial_list,
                "matched_counties": matched,
                "d_votes": round(d_votes),
                "r_votes": round(r_votes),
                "other_votes": round(o_votes),
                "total_votes": round(total),
                "d_share_two_party_pct": round(d_share, 2) if tp else None,
                "r_share_two_party_pct": round(r_share, 2) if tp else None,
                "national_d_share_two_party_pct": round(nat_d_share, 2),
                "lean_pct": round(blend, 2) if wsum > 0 else None,
                "lean_label": lean_label(blend) if wsum > 0 else "",
                "votes_from_partial_pct": round(partial_votes / total * 100.0, 1) if total else None,
                "lean_pres_pct": round(lean_pres, 2) if lean_pres is not None else None,
                "lean_house_pct": round(lean_house, 2) if lean_house is not None else None,
                "lean_senate_pct": round(lean_senate, 2) if lean_senate is not None else None,
                "lean_statewide_pct": round(lean_statewide, 2) if lean_statewide is not None else None,
                "weight_pres": round(weights["pres"], 3),
                "weight_house": round(weights["house"], 3),
                "weight_senate": round(weights["senate"], 3),
                "weight_statewide": round(weights["statewide"], 3),
                "sources_used": "+".join(
                    k for k in ("pres", "house", "senate", "statewide")
                    if k in usable),
            })

        if unmatched_state:
            info["unmatched"][state_code] = sorted(set(unmatched_state))
        if state_available:
            info["states"][state_code] = {
                "allocated_vs_available_pct": round(
                    state_allocated / state_available * 100.0, 2),
                "districts": len(dists),
            }

    frame = pd.DataFrame(rows, columns=_ROW_FIELDS)
    info["rows"] = int(len(frame))
    info["house_districts"] = int(sum(1 for r in rows
                                      if r["lean_house_pct"] is not None))
    info["senate_states"] = int(len(senate_state_votes or {}))
    info["statewide_states"] = int(len(statewide_state_votes or {}))
    info["national"] = {
        "d_votes": round(nat_d), "r_votes": round(nat_r),
        "d_share_two_party_pct": round(nat_d_share, 2),
    }
    return frame, info


def summarize_pvi(all_frame: pd.DataFrame) -> pd.DataFrame:
    """Average the per-year lean over the elections held under each map
    vintage (PVI-style: mean district two-party margin minus mean national
    margin — identical to averaging the yearly leans)."""
    usable = all_frame.dropna(subset=["lean_pct"])
    if not len(usable):
        return pd.DataFrame()
    grp = usable.groupby(
        ["state", "state_code", "district", "district_note", "at_large",
         "map_vintage"], as_index=False).agg(
        years_used=("year", lambda s: "+".join(str(y) for y in sorted(s))),
        n_years=("year", "nunique"),
        lean_pct_mean=("lean_pct", "mean"),
        d_share_mean=("d_share_two_party_pct", "mean"),
        national_d_share_mean=("national_d_share_two_party_pct", "mean"),
        votes_from_partial_pct_max=("votes_from_partial_pct", "max"),
        counties_whole=("counties_whole", "first"),
        counties_partial=("counties_partial", "first"),
        total_votes_mean=("total_votes", "mean"),
    )
    grp["lean_pct_mean"] = grp["lean_pct_mean"].round(2)
    grp["d_share_mean"] = grp["d_share_mean"].round(2)
    grp["national_d_share_mean"] = grp["national_d_share_mean"].round(2)
    grp["total_votes_mean"] = grp["total_votes_mean"].round(0).astype("Int64")
    grp["pvi_label"] = grp["lean_pct_mean"].map(lean_label)
    grp["is_current_map"] = grp["map_vintage"] == "2020s"
    grp = grp.rename(columns={"lean_pct_mean": "pvi_pct"})
    grp = grp.sort_values(["state_code", "district", "map_vintage"])
    cols = ["state", "state_code", "district", "district_note", "at_large",
            "map_vintage", "is_current_map", "years_used", "n_years",
            "d_share_mean", "national_d_share_mean", "pvi_pct", "pvi_label",
            "total_votes_mean", "counties_whole", "counties_partial",
            "votes_from_partial_pct_max"]
    return grp[cols].reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# MIDTERM DISTRICT LEAN (senate + state election votes)
# ────────────────────────────────────────────────────────────────────────────

#: map vintage in force at each midterm election. The 113th Congress was the
#: first elected under the 2010s lines (Nov 2012 still voted under the 2000s
#: map), and the 118th was the first elected under the 2020s lines (2022).
MIDTERM_VINTAGE_FOR_YEAR: Dict[int, str] = {
    2006: "2000s", 2010: "2000s",
    2014: "2010s", 2018: "2010s",
    2022: "2020s",
}
MIDTERM_YEARS: Tuple[int, ...] = tuple(sorted(MIDTERM_VINTAGE_FOR_YEAR))

#: statewide-level vote sources that can enter the midterm blend
MIDTERM_COMPONENTS: Tuple[str, ...] = ("senate", "statewide", "state-leg")

#: canonical order used to render the ``offices_used`` column
_OFFICE_ORDER: Tuple[str, ...] = (
    "senate", "governor", "attorney general", "secretary of state",
    "state treasurer", "state senate", "state house",
)

#: full jurisdiction name -> USPS code (the senate CSVs carry names only);
#: the inverse of the ABBR_FALLBACK_NAMES safety net above
NAME_TO_ABBR: Dict[str, str] = {v: k for k, v in ABBR_FALLBACK_NAMES.items()}


def _state_code_from_name(name) -> Optional[str]:
    """Map a state/jurisdiction name to its USPS code, tolerating the
    article-derived suffixes the senate pipeline records verbatim
    (``"New York (special)"`` -> ``NY``)."""
    n = re.sub(r"\s*\([^)]*\)\s*$", "", str(name or "").strip())
    return NAME_TO_ABBR.get(n)


def classify_party(label: str) -> str:
    """Classify a long-form Wikipedia party label as ``D`` / ``R`` / ``O``.

    Unlike :func:`_party_class` (which normalised presidential-CSV labels),
    the senate / statewide / state-leg families carry verbatim article
    labels such as ``"Democratic Party (United States)"``,
    ``"Minnesota Democratic–Farmer–Labor Party"`` or
    ``"North Dakota Democratic-NPL Party"``. Classification is
    parenthetical-stripped substring matching, so state branches and DFL /
    NPL variants classify correctly; labels naming both major parties
    (ambiguous fusion lines such as ``"Democratic/Republican"``) resolve to
    ``O`` — excluded from the two-party baseline like any third party.
    """
    p = str(label or "").strip().lower()
    p = unicodedata.normalize("NFKD", p)
    p = "".join(ch for ch in p if not unicodedata.combining(ch))
    p = re.sub(r"[–—−]", "-", p)
    p = re.sub(r"\([^)]*\)", " ", p)          # "(United States)", "(New York)"
    p = re.sub(r"\b(party|politician|us)\b", " ", p)
    p = re.sub(r"[^a-z \-]", " ", p)
    p = re.sub(r"\s+", " ", p).strip()
    if not p or "write-in" in p or "write in" in p:
        return "O"
    has_d = "democrat" in p
    has_r = "republican" in p
    if has_d and has_r:
        return "O"
    if has_r:
        return "R"
    if has_d:
        return "D"
    return "O"


def _votes_numeric(series: pd.Series) -> pd.Series:
    """Vote strings ("1,676,335", floats with stray spaces) -> numeric."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce")


def load_senate_midterm_races(senate_dir: str, year: int, keep_codes, info: dict):
    """U.S. Senate general races for *year* -> per-race vote records.

    One record per race article (regular and special elections both kept):
    ``{"state", "office", "component", "D", "R", "O"}``. ``Total`` turnout
    rows are excluded; fusion party lines are summed as reported (they land
    in "O" unless the line itself is a major-party line).
    """
    path = os.path.join(senate_dir, f"senate_general_results_{year}.csv")
    if not os.path.exists(path):
        info["missing_files"].append(path)
        return []
    df = pd.read_csv(path)
    df = df[df["Row_Type"].fillna("").str.strip().str.lower() != "total"]
    df = df.assign(state_code=df["State"].map(_state_code_from_name))
    unknown = sorted(set(df.loc[df["state_code"].isna(), "State"].dropna()))
    if unknown:
        info["unknown_states"][f"senate_{year}"] = unknown
    df = df[df["state_code"].isin(keep_codes)]
    df = df.assign(_v=_votes_numeric(df["votes"]))
    df = df[df["_v"] > 0]

    races: List[dict] = []
    for (code, _election), sub in df.groupby(["state_code", "Election"],
                                             observed=True):
        pc = sub["party"].map(classify_party)
        rec = {"state": code, "office": "senate", "component": "senate"}
        for cls in ("D", "R", "O"):
            rec[cls] = float(sub.loc[pc == cls, "_v"].sum())
        races.append(rec)
    return races


def load_statewide_midterm_races(statewide_dir: str, year: int, keep_codes,
                                 info: dict):
    """Statewide executive races for *year* -> per-race vote records.

    One record per office race (governor / attorney general / secretary of
    state / state treasurer — whichever appear in that cycle's tables).
    The winner's ``Total`` row (which aggregates a candidate's votes across
    fusion party lines) is excluded so fusion votes are neither lost nor
    doubled.
    """
    path = os.path.join(statewide_dir, f"statewide_general_results_{year}.csv")
    if not os.path.exists(path):
        info["missing_files"].append(path)
        return []
    df = pd.read_csv(path)
    df = df[df["row_type"].fillna("").str.strip().str.lower() != "total"]
    df = df[df["state_code"].fillna("").isin(keep_codes)]
    df = df.assign(_v=_votes_numeric(df["votes"]))
    df = df[df["_v"] > 0]

    races: List[dict] = []
    for (code, office, _election), sub in df.groupby(
            ["state_code", "office", "election"], observed=True):
        pc = sub["party"].map(classify_party)
        rec = {"state": code, "office": str(office).strip().lower(),
               "component": "statewide"}
        for cls in ("D", "R", "O"):
            rec[cls] = float(sub.loc[pc == cls, "_v"].sum())
        races.append(rec)
    return races


def load_state_leg_midterm_races(state_leg_dir: str, year: int, keep_codes,
                                 info: dict):
    """State-legislature results for *year* -> per-chamber vote records.

    The state-leg CSVs carry votes per state-legislative district. Those
    districts cannot be mapped to congressional districts (no crosswalk
    geometry), so they enter the midterm blend aggregated to statewide
    chamber totals — one record per state per chamber (state senate, state
    house), summed over every *contested* district of that chamber. A
    state-leg district is contested when both major parties polled votes
    there; walkovers (a major party absent / polling zero) are dropped
    before the aggregation so one-sided seats cannot skew the state
    two-party totals.
    """
    races: List[dict] = []
    dropped: List[dict] = []
    senate_path = os.path.join(state_leg_dir, f"state_senate_results_{year}.csv")
    # the shipped layout keeps the two chambers in sibling directories
    house_dir = os.path.join(
        os.path.dirname(os.path.abspath(state_leg_dir).rstrip(os.sep)),
        "state_house")
    house_path = os.path.join(house_dir, f"state_house_results_{year}.csv")
    for chamber, path in (("state senate", senate_path),
                          ("state house", house_path)):
        if not os.path.exists(path):
            info["missing_files"].append(path)
            continue
        df = pd.read_csv(path, dtype={"district": str})
        df = df[df["state_code"].fillna("").isin(keep_codes)]
        df = df.assign(_v=_votes_numeric(df["votes"]))
        df = df[df["_v"] > 0]
        df = df.assign(_pc=df["party"].map(classify_party))
        # per state-leg district: keep only contested races (both major
        # parties polled votes) before the statewide chamber aggregation
        for (code, _ch, district), sub in df.groupby(
                ["state_code", "chamber", "district"], observed=True):
            pcs = set(sub["_pc"])
            if not ({"D", "R"} <= pcs):
                dropped.append({"state": code, "chamber": chamber,
                                "district": district})
                continue
            rec = {"state": code, "office": chamber, "component": "state-leg"}
            for cls in ("D", "R", "O"):
                rec[cls] = float(sub.loc[sub["_pc"] == cls, "_v"].sum())
            races.append(rec)
    if dropped:
        info["stateleg_walkover_districts"] = \
            info.get("stateleg_walkover_districts", 0) + len(dropped)
    return races


def compute_midterm_state_votes(
    races: List[dict], info: dict,
) -> Tuple[Dict[str, Dict[str, float]], Dict[str, dict], Dict[str, float]]:
    """Blend informative races into per-state two-party tallies.

    A race is *informative* when both major parties polled votes on the
    ballot; top-two / top-four matchups without one of them (CA-2016-senate
    D-vs-D, AK-2022-senate R-vs-R, a wholly uncontested write-in race) carry
    no two-party signal and are dropped. Returns
    ``(state_votes, state_meta, national)`` where ``state_meta`` carries the
    per-state race counts and the ``offices_used`` descriptor.
    """
    kept: List[dict] = []
    for r in races:
        if r["D"] <= 0 or r["R"] <= 0:
            info["excluded_races"].append({
                "state": r["state"], "office": r["office"],
                "d_votes": round(r["D"]), "r_votes": round(r["R"]),
            })
            continue
        kept.append(r)

    state_votes: Dict[str, Dict[str, float]] = {}
    state_races: Dict[str, Dict[str, int]] = {}
    state_offices: Dict[str, set] = {}
    for r in kept:
        sv = state_votes.setdefault(r["state"], {"D": 0.0, "R": 0.0, "O": 0.0})
        for cls in ("D", "R", "O"):
            sv[cls] += r[cls]
        state_races.setdefault(
            r["state"], {"senate": 0, "statewide": 0, "state-leg": 0})
        state_races[r["state"]][r["component"]] += 1
        state_offices.setdefault(r["state"], set()).add(r["office"])

    state_meta: Dict[str, dict] = {}
    for code, sv in state_votes.items():
        state_meta[code] = {
            "races_used": sum(state_races[code].values()),
            "senate_races": state_races[code]["senate"],
            "statewide_races": state_races[code]["statewide"],
            "stateleg_races": state_races[code]["state-leg"],
            "offices_used": "; ".join(
                o for o in _OFFICE_ORDER if o in state_offices[code]),
        }

    national = {"D": 0.0, "R": 0.0, "O": 0.0}
    for sv in state_votes.values():
        for cls in ("D", "R", "O"):
            national[cls] += sv[cls]
    return state_votes, state_meta, national


_MIDTERM_ROW_FIELDS = [
    "year", "map_vintage", "state", "state_code", "district", "district_note",
    "at_large", "statewide_baseline", "races_used", "offices_used",
    "senate_races", "statewide_races", "stateleg_races", "d_votes", "r_votes",
    "other_votes", "total_votes", "d_share_two_party_pct",
    "r_share_two_party_pct", "national_d_share_two_party_pct", "lean_pct",
    "lean_label",
    "lean_house_pct", "lean_statewide_pct", "lean_senate_pct",
    "weight_house", "weight_statewide", "weight_senate", "sources_used",
]


def compute_midterm_year_lean(
    districts: List[dict],
    statewide_votes: Dict[str, Dict[str, float]],
    state_meta: Dict[str, dict],
    national: Dict[str, float],
    year: int,
    house_counts: Optional[Dict[str, dict]] = None,
    nat_house_share: Optional[float] = None,
    senate_votes: Optional[Dict[str, Dict[str, float]]] = None,
    nat_senate_share: Optional[float] = None,
) -> Tuple[pd.DataFrame, dict]:
    """Blend the midterm ballot into a per-district lean.

    Components (the unified scheme minus pres, renormalised over what is
    present): the district's own House general result (0.20), the state's
    pooled statewide ballot (0.20 — governor / attorney general / secretary
    of state / state treasurer plus the state-leg chamber totals, contested
    state-leg districts only) and its pooled Senate races (0.20), each
    against its own national baseline — ≈ 1/3 each when all three are up.
    Districts without a usable House race fall back to the statewide
    blend and are flagged ``statewide_baseline``. The d/r/other vote
    columns keep reporting the statewide tallies (senate + statewide
    pools); House votes live only in the lean.
    """
    vintage = MIDTERM_VINTAGE_FOR_YEAR[year]
    nat_d = national.get("D", 0.0)
    nat_r = national.get("R", 0.0)
    nat_tp = nat_d + nat_r
    nat_d_share = (nat_d / nat_tp * 100.0) if nat_tp else float("nan")
    nat_sen = nat_senate_share if nat_senate_share is not None \
        and not pd.isna(nat_senate_share) else None
    nat_house = nat_house_share if nat_house_share is not None \
        and not pd.isna(nat_house_share) else None

    by_state: Dict[str, List[dict]] = {}
    for d in districts:
        if d["vintages"][vintage]["exists"]:
            by_state.setdefault(d["state_code"], []).append(d)

    def _state_lean(state_code, pool, nat_share):
        sv = (pool or {}).get(state_code)
        if not sv or nat_share is None:
            return None, {"D": 0.0, "R": 0.0, "O": 0.0}
        tp = sv["D"] + sv["R"]
        if tp <= 0:
            return None, {"D": 0.0, "R": 0.0, "O": 0.0}
        return sv["D"] / tp * 100.0 - nat_share, sv

    rows: List[dict] = []
    states_without_data: List[str] = []
    house_rows = 0
    for state_code, dists in sorted(by_state.items()):
        lean_stw, svw = _state_lean(state_code, statewide_votes, nat_d_share)
        lean_sen, svn = _state_lean(state_code, senate_votes, nat_sen)
        pooled = {c: svw[c] + svn[c] for c in ("D", "R", "O")}
        sm = state_meta.get(state_code, {})
        if pooled["D"] + pooled["R"] > 0:
            common = {
                "races_used": sm.get("races_used", 0),
                "offices_used": sm.get("offices_used", ""),
                "senate_races": sm.get("senate_races", 0),
                "statewide_races": sm.get("statewide_races", 0),
                "stateleg_races": sm.get("stateleg_races", 0),
                "d_votes": round(pooled["D"]),
                "r_votes": round(pooled["R"]),
                "other_votes": round(pooled["O"]),
                "total_votes": round(sum(pooled.values())),
                "d_share_two_party_pct": round(
                    pooled["D"] / (pooled["D"] + pooled["R"]) * 100.0, 2),
                "r_share_two_party_pct": round(
                    pooled["R"] / (pooled["D"] + pooled["R"]) * 100.0, 2),
            }
        else:
            states_without_data.append(state_code)
            common = {
                "races_used": 0, "offices_used": "", "senate_races": 0,
                "statewide_races": 0, "stateleg_races": 0,
                "d_votes": 0, "r_votes": 0, "other_votes": 0, "total_votes": 0,
                "d_share_two_party_pct": None, "r_share_two_party_pct": None,
            }
        for d in dists:
            vinfo = d["vintages"][vintage]
            lean_house = None
            hh = (house_counts or {}).get(d["district"])
            if hh is not None and nat_house is not None:
                lean_house = hh["share_pct"] - nat_house
            parts = {
                "house": (lean_house, MIDTERM_BLEND_WEIGHTS["house"]),
                "statewide": (lean_stw, MIDTERM_BLEND_WEIGHTS["statewide"]),
                "senate": (lean_sen, MIDTERM_BLEND_WEIGHTS["senate"]),
            }
            usable = {k: v for k, v in parts.items()
                      if v[0] is not None and not pd.isna(v[0])}
            wsum = sum(w for _l, w in usable.values())
            blend = (sum(l * w for l, w in usable.values()) / wsum
                     if wsum > 0 else float("nan"))
            if wsum > 0:
                weights = _round_weights(
                    {k: (w / wsum if k in usable else 0.0)
                     for k, (_l, w) in parts.items()})
            else:
                weights = {k: 0.0 for k in parts}
            if lean_house is not None:
                house_rows += 1
            rows.append({
                "year": year,
                "map_vintage": vintage,
                "state": d["state"],
                "state_code": state_code,
                "district": d["district"],
                "district_note": d["district_note"],
                "at_large": vinfo["at_large"],
                "statewide_baseline": lean_house is None,
                **common,
                "national_d_share_two_party_pct": round(nat_d_share, 2),
                "lean_pct": round(blend, 2) if wsum > 0 else None,
                "lean_label": lean_label(blend) if wsum > 0 else "",
                "lean_house_pct": round(lean_house, 2) if lean_house is not None else None,
                "lean_statewide_pct": round(lean_stw, 2) if lean_stw is not None else None,
                "lean_senate_pct": round(lean_sen, 2) if lean_sen is not None else None,
                "weight_house": round(weights["house"], 3),
                "weight_statewide": round(weights["statewide"], 3),
                "weight_senate": round(weights["senate"], 3),
                "sources_used": "+".join(
                    k for k in ("house", "statewide", "senate") if k in usable),
            })

    frame = pd.DataFrame(rows, columns=_MIDTERM_ROW_FIELDS)
    info = {
        "rows": int(len(frame)),
        "house_districts": house_rows,
        "national": {
            "d_votes": round(nat_d), "r_votes": round(nat_r),
            "d_share_two_party_pct": round(nat_d_share, 2) if nat_tp else None,
        },
        "states_present": len(statewide_votes),
        "states_without_data": states_without_data or None,
    }
    return frame, info


def summarize_midterm_lean(all_frame: pd.DataFrame) -> pd.DataFrame:
    """Average the midterm lean over the midterms held under each map
    vintage (PVI-style: mean district margin minus mean national margin —
    identical to averaging the yearly leans). ``statewide_baseline`` is
    True only for district-vintage combos that never had a House result."""
    usable = all_frame.dropna(subset=["lean_pct"])
    if not len(usable):
        return pd.DataFrame()
    grp = usable.groupby(
        ["state", "state_code", "district", "district_note", "at_large",
         "map_vintage"], as_index=False).agg(
        years_used=("year", lambda s: "+".join(str(y) for y in sorted(s))),
        n_years=("year", "nunique"),
        midterm_lean_pct=("lean_pct", "mean"),
        d_share_mean=("d_share_two_party_pct", "mean"),
        national_d_share_mean=("national_d_share_two_party_pct", "mean"),
        total_votes_mean=("total_votes", "mean"),
        races_used_mean=("races_used", "mean"),
        statewide_baseline=("statewide_baseline", "all"),
    )
    grp["midterm_lean_pct"] = grp["midterm_lean_pct"].round(2)
    grp["d_share_mean"] = grp["d_share_mean"].round(2)
    grp["national_d_share_mean"] = grp["national_d_share_mean"].round(2)
    grp["total_votes_mean"] = grp["total_votes_mean"].round(0).astype("Int64")
    grp["races_used_mean"] = grp["races_used_mean"].round(1)
    grp["midterm_lean_label"] = grp["midterm_lean_pct"].map(lean_label)
    grp["is_current_map"] = grp["map_vintage"] == "2020s"
    grp = grp.sort_values(["state_code", "district", "map_vintage"])
    cols = ["state", "state_code", "district", "district_note", "at_large",
            "map_vintage", "is_current_map", "years_used", "n_years",
            "d_share_mean", "national_d_share_mean", "midterm_lean_pct",
            "midterm_lean_label", "total_votes_mean", "races_used_mean",
            "statewide_baseline"]
    return grp[cols].reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# DRIVER
# ────────────────────────────────────────────────────────────────────────────

def _resolve_mapping_path(mapping_path: Optional[str]) -> str:
    if mapping_path:
        if not os.path.exists(mapping_path):
            raise FileNotFoundError(f"district->county mapping not found: {mapping_path}")
        return mapping_path
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join("resources", MAPPING_NAME),
                 os.path.join(here, "resources", MAPPING_NAME),
                 os.path.join(here, MAPPING_NAME)):
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError(
        f"district->county mapping not found (looked for resources/{MAPPING_NAME!r} "
        f"relative to CWD and to the module) — pass --mapping explicitly or run "
        f"`cli.py crosswalk` to build it from boundary geometry")


def _run_midterm(
    start_year: int,
    end_year: int,
    output_dir: str,
    mapping_path: Optional[str],
    components: Tuple[str, ...],
    senate_dir: Optional[str] = None,
    statewide_dir: Optional[str] = None,
    state_leg_dir: Optional[str] = None,
    house_dir: Optional[str] = None,
) -> pd.DataFrame:
    """Compute the midterm district lean for the midterm years in
    [start_year, end_year] (see the module docstring for the method).

    Reads ``house_results_{year}.csv``, ``senate_general_results_{year}.csv``,
    ``statewide_general_results_{year}.csv`` and (opt-in) the two state-leg
    chamber CSVs; never fetches. Writes ``district_lean_midterm_{year}.csv``,
    ``district_lean_midterm_all.csv``, ``district_midterm_lean_summary.csv``
    and a metadata JSON under ``<output_dir>/district_lean/``.
    """
    components = tuple(
        c.strip().lower() for c in components if str(c).strip())
    bad = [c for c in components if c not in MIDTERM_COMPONENTS]
    if bad:
        raise ValueError(
            f"Unknown midterm component(s) {bad} — choose from "
            f"{list(MIDTERM_COMPONENTS)}")
    if not components:
        raise ValueError(
            "No midterm components requested — pass at least one of "
            f"{list(MIDTERM_COMPONENTS)}")

    years = [y for y in MIDTERM_YEARS if start_year <= y <= end_year]
    if not years:
        logger.warning(
            "No midterm year with a known map vintage in [%d, %d] — "
            "supported years: %s", start_year, end_year, list(MIDTERM_YEARS))
        return pd.DataFrame()

    mapping_file = _resolve_mapping_path(mapping_path)
    lean_dir = os.path.join(output_dir, "district_lean")
    os.makedirs(lean_dir, exist_ok=True)
    senate_dir = senate_dir or os.path.join(output_dir, "senate")
    statewide_dir = statewide_dir or os.path.join(output_dir, "statewide")
    state_leg_dir = state_leg_dir or os.path.join(output_dir, "state_senate")
    house_dir = house_dir or os.path.join(output_dir, "house")

    # the mapping supplies the district universe per map vintage (no county
    # allocation happens in the midterm path — votes are statewide)
    logger.info("District universe from mapping: %s", mapping_file)
    districts, _map_meta = load_district_map(mapping_file)
    keep_codes = sorted({d["state_code"] for d in districts})
    logger.info(
        "Midterm lean %d-%d from %s (districts across vintages %s)",
        years[0], years[-1], " + ".join(components), ", ".join(MAP_VINTAGES))

    meta: dict = {
        "method": {
            "lean": "fixed-weight blend of the district's own House general "
                    "result (0.20), the state's midterm statewide ballot "
                    "(0.20: governor / attorney general / secretary of "
                    "state / state treasurer, plus state-leg chamber totals "
                    "over contested state-leg districts) and its pooled "
                    "U.S. Senate races (0.20) — the unified lean scheme "
                    "minus the presidential component — each as Democratic "
                    "two-party share minus the same race's national share; "
                    "weights renormalised over the sources present "
                    "(≈ 1/3 each when all three are up, 50/50 house + "
                    "statewide when no Senate race is on the ballot) — "
                    "districts without a usable House race fall back to the "
                    "statewide blend (statewide_baseline)",
            "components": {
                "house": "the district's own U.S. House general result from "
                        "data/house/house_results_{year}.csv; winner votes "
                        "missing in the article are reconstructed from "
                        "percentage x district turnout; one-sided races "
                        "dropped as uninformative",
                "senate": "U.S. Senate general races (regular + special) "
                          "from data/senate/senate_general_results_{year}.csv",
                "statewide": "governor / attorney general / secretary of "
                             "state / state treasurer races from "
                             "data/statewide/statewide_general_results_{year}.csv",
                "state-leg": "state senate + state house votes aggregated to "
                             "statewide chamber totals from data/state_senate/ "
                             "and data/state_house/ (state-leg districts "
                             "cannot be mapped to congressional districts, so "
                             "they enter only as chamber sums); walkover "
                             "state-leg districts (a major party polled no "
                             "votes) are dropped before the aggregation",
            },
            "components_used": list(components) + ["house"],
            "two_party": "party lines summed as reported: fusion lines "
                         "(Working Families, Conservative, Independence, ...) "
                         "count on their own line party and land in "
                         "other_votes, excluded from the two-party baseline; "
                         "Total rows excluded; write-ins = other",
            "race_exclusion": "races where a major party polled zero votes "
                              "(top-two / top-four matchups such as CA 2016 "
                              "or AK 2022 senate) are dropped as "
                              "uninformative before blending",
            "national_baseline": "each component's national baseline is that "
                                 "component summed over all district-map "
                                 "states with data; territories (PR, GU, MP, "
                                 "...) excluded",
            "map_vintage_for_year": {str(k): v for k, v
                                     in MIDTERM_VINTAGE_FOR_YEAR.items()},
            "caveats": [
                "the statewide components (senate / statewide executive / "
                "state-leg) carry no district detail, so districts without "
                "a usable House race share the state's blended baseline",
                "the statewide blend follows each state's actual ballot (not "
                "every state has a senate race up; AG appears from 2018, "
                "SoS/treasurer from 2022), so national baselines are not "
                "directly comparable across years",
                "state-leg chamber totals cover the states whose legislative "
                "races the source parses (11-24 states before 2016, 39-41 "
                "from 2020) and count contested districts only",
                "the 2020s crosswalk snapshot includes the 2024 AL/GA/LA/NY/"
                "NC court redraws, while the 2022 midterms voted on the "
                "pre-redraw lines",
                "DC has no senate race and no statewide-executive rows in "
                "the source tables, so DC-AL leans on its House delegate "
                "race alone where one is parsed",
            ],
        },
        "years": {},
    }

    all_frames: List[pd.DataFrame] = []
    for year in years:
        info: dict = {"missing_files": [], "unknown_states": {},
                      "excluded_races": []}
        races: List[dict] = []
        if "senate" in components:
            races += load_senate_midterm_races(senate_dir, year, keep_codes,
                                               info)
        if "statewide" in components:
            races += load_statewide_midterm_races(statewide_dir, year,
                                                  keep_codes, info)
        if "state-leg" in components:
            races += load_state_leg_midterm_races(state_leg_dir, year,
                                                  keep_codes, info)
        house_info: dict = {"missing_files": [], "house_excluded": []}
        house_counts, nat_house = load_house_year_races(
            house_dir, year, keep_codes, house_info)
        info["missing_files"] += house_info["missing_files"]
        if not races and not house_counts:
            logger.warning(
                "%d — no midterm vote rows found (components %s + house); "
                "skipping (run `cli.py house` / `senate` / `statewide` / "
                "`state-leg` first)", year, list(components))
            meta["years"][year] = {
                "skipped": "no input rows",
                "missing_files": info["missing_files"] or None,
            }
            continue

        # split the statewide pools: senate races weigh 0.20, the statewide
        # ballot (executive offices + state-leg chamber totals) 0.20, the
        # district's House race 0.20 — renormalised over what is present
        sen_info: dict = {"excluded_races": []}
        stw_info: dict = {"excluded_races": []}
        sen_races = [r for r in races if r["component"] == "senate"]
        stw_races = [r for r in races if r["component"] != "senate"]
        sen_votes, sen_meta, sen_national = compute_midterm_state_votes(
            sen_races, sen_info)
        stw_votes, stw_meta, stw_national = compute_midterm_state_votes(
            stw_races, stw_info)
        info["excluded_races"] += sen_info["excluded_races"] \
            + stw_info["excluded_races"]
        if not sen_votes and not stw_votes and not house_counts:
            logger.warning(
                "%d — every race was excluded as uninformative; skipped", year)
            meta["years"][year] = {
                "skipped": "all races excluded",
                "excluded_races": info["excluded_races"] or None,
            }
            continue

        nat_house_share = (
            nat_house["D"] / (nat_house["D"] + nat_house["R"]) * 100.0
            if nat_house and (nat_house["D"] + nat_house["R"]) > 0 else None)
        nat_sen_share = (
            sen_national["D"] / (sen_national["D"] + sen_national["R"])
            * 100.0
            if sen_national and (sen_national["D"] + sen_national["R"]) > 0
            else None)
        nat_stw_share = (
            stw_national["D"] / (stw_national["D"] + stw_national["R"])
            * 100.0
            if stw_national and (stw_national["D"] + stw_national["R"]) > 0
            else None)

        # merged per-state race meta (senate pool + statewide pool)
        def _merge_offices(a: str, b: str) -> str:
            have = ({o for o in a.split("; ") if o}
                    | {o for o in b.split("; ") if o})
            return "; ".join(o for o in _OFFICE_ORDER if o in have)

        state_meta: Dict[str, dict] = {}
        for code in set(sen_meta) | set(stw_meta):
            s, t = sen_meta.get(code, {}), stw_meta.get(code, {})
            state_meta[code] = {
                "races_used": s.get("races_used", 0) + t.get("races_used", 0),
                "senate_races": s.get("senate_races", 0),
                "statewide_races": t.get("statewide_races", 0),
                "stateleg_races": t.get("stateleg_races", 0),
                "offices_used": _merge_offices(s.get("offices_used", ""),
                                               t.get("offices_used", "")),
            }

        frame, year_info = compute_midterm_year_lean(
            districts, stw_votes, state_meta, stw_national, year,
            house_counts=house_counts, nat_house_share=nat_house_share,
            senate_votes=sen_votes, nat_senate_share=nat_sen_share)
        path = os.path.join(lean_dir, f"district_lean_midterm_{year}.csv")
        frame.to_csv(path, index=False)
        logger.info(
            "  %d [%s map] %d districts (house %d, senate states %d, "
            "statewide states %d) -> %s",
            year, MIDTERM_VINTAGE_FOR_YEAR[year], len(frame),
            year_info["house_districts"], len(sen_votes), len(stw_votes), path)
        meta["years"][year] = {
            "vintage": MIDTERM_VINTAGE_FOR_YEAR[year],
            **year_info,
            "house": {
                "districts": year_info["house_districts"],
                "votes_fixed": house_info["house_votes_fixed"],
                "national_d_share": round(nat_house_share, 2)
                                    if nat_house_share is not None else None,
                "excluded_races": house_info["house_excluded"] or None,
            },
            "senate": {
                "states": len(sen_votes),
                "national_d_share": round(nat_sen_share, 2)
                                    if nat_sen_share is not None else None,
            },
            "statewide": {
                "states": len(stw_votes),
                "national_d_share": round(nat_stw_share, 2)
                                    if nat_stw_share is not None else None,
                "stateleg_walkover_districts":
                    info.get("stateleg_walkover_districts", 0) or None,
            },
            "excluded_races": info["excluded_races"] or None,
            "unknown_states": info["unknown_states"] or None,
            "missing_files": info["missing_files"] or None,
            "state_race_counts": {
                code: state_meta[code]["races_used"] for code in state_meta},
            "state_offices": {
                code: state_meta[code]["offices_used"] for code in state_meta},
        }
        all_frames.append(frame)

    if not all_frames:
        logger.warning("No midterm lean rows computed — nothing written.")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    path = os.path.join(lean_dir, "district_lean_midterm_all.csv")
    combined.to_csv(path, index=False)
    logger.info("Combined -> %s (%s rows)", path, f"{len(combined):,}")

    summary = summarize_midterm_lean(combined)
    if len(summary):
        path = os.path.join(lean_dir, "district_midterm_lean_summary.csv")
        summary.to_csv(path, index=False)
        logger.info("Midterm summary -> %s (%s rows)", path,
                    f"{len(summary):,}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(lean_dir,
                           f"district_lean_midterm_metadata_{ts}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, default=str)
    return combined


def run(
    start_year: int = 2004,
    end_year: int = 2024,
    output_dir: str = "data",
    mapping_path: Optional[str] = None,
    presidential_dir: Optional[str] = None,
    house_dir: Optional[str] = None,
    senate_dir: Optional[str] = None,
    client=None,
    fetch_missing: bool = False,
    midterm: bool = False,
    midterm_components: Tuple[str, ...] = ("senate", "statewide", "state-leg"),
    statewide_dir: Optional[str] = None,
    state_leg_dir: Optional[str] = None,
) -> pd.DataFrame:
    """Compute predicted district partisan leans, writing CSVs under
    ``<output_dir>/district_lean/``.

    Default (``midterm=False``): the blended presidential-year lean for
    every presidential year in [start_year, end_year] — pres 0.40 /
    house 0.20 / senate 0.20 / statewide 0.20 (the statewide pool being
    governor + down-ballot executive + state-leg chamber totals),
    renormalised over the sources present (see the module docstring).
    Presidential years without a local ``presidential_results_{year}.csv``
    still compute from house + senate + statewide (with a warning) unless
    *fetch_missing* is True, in which case ``presidential_elections.run``
    produces them first via the API.

    ``midterm=True``: the midterm district lean for every midterm year in
    [start_year, end_year], blending house + senate + statewide + state-leg
    votes (statewide sources selectable via *midterm_components*; see
    the module docstring). Never fetches — run the house/senate/statewide/
    state-leg pipelines first.
    """
    if midterm:
        return _run_midterm(
            start_year=start_year, end_year=end_year, output_dir=output_dir,
            mapping_path=mapping_path, components=midterm_components,
            senate_dir=senate_dir, statewide_dir=statewide_dir,
            state_leg_dir=state_leg_dir)

    years = [y for y in LEAN_YEARS if start_year <= y <= end_year]
    if not years:
        logger.warning(
            "No presidential year with a known map vintage in [%d, %d] — "
            "supported years: %s", start_year, end_year, list(LEAN_YEARS))
        return pd.DataFrame()

    mapping_file = _resolve_mapping_path(mapping_path)
    pres_dir = presidential_dir or os.path.join(output_dir, "presidential")
    house_dir = house_dir or os.path.join(output_dir, "house")
    senate_dir = senate_dir or os.path.join(output_dir, "senate")
    statewide_dir = statewide_dir or os.path.join(output_dir, "statewide")
    state_leg_dir = state_leg_dir or os.path.join(output_dir, "state_senate")
    lean_dir = os.path.join(output_dir, "district_lean")
    os.makedirs(lean_dir, exist_ok=True)

    logger.info("District->county mapping: %s", mapping_file)
    districts, map_meta = load_district_map(mapping_file)
    keep_codes = sorted({d["state_code"] for d in districts})
    n_at_large = sum(1 for d in districts
                     if all(v["at_large"] or not v["exists"]
                            for v in d["vintages"].values()))
    logger.info(
        "Loaded %d districts (%d states incl. DC; %d at-large) across vintages %s",
        len(districts), map_meta["states_total"], n_at_large, ", ".join(MAP_VINTAGES))

    all_frames: List[pd.DataFrame] = []
    meta: dict = {
        "method": {
            "lean": "fixed-weight blend of four two-party leans — pres "
                    "0.40 (county-allocated presidential vote), house 0.20 "
                    "(the district's own U.S. House general result), senate "
                    "0.20 (state's pooled Senate general races), statewide "
                    "0.20 (state's pooled governor / attorney general / "
                    "secretary of state / state treasurer vote plus its "
                    "state-legislature chamber totals over contested "
                    "state-leg districts) — each measured as the Democratic "
                    "two-party share of that race minus the same race's "
                    "national share; weights renormalised over the sources "
                    "present for each district-year; consensus optimum of "
                    "an out-of-sample weight search (see README)",
            "components": {
                "house": "the district's own U.S. House general result from "
                        "data/house/house_results_{year}.csv; winner votes "
                        "missing in the article are reconstructed from "
                        "percentage x district turnout, districts with no "
                        "turnout fall back to a percentage-only share; "
                        "one-sided races (top-two D-vs-D / R-vs-R, "
                        "walkovers) dropped as uninformative",
                "pres": "county-allocated presidential vote (allocation "
                        "described below)",
                "senate": "the state's U.S. Senate general races (regular + "
                          "special, pooled) from data/senate/; statewide, "
                          "so uniform within a state",
                "statewide": "governor / attorney general / secretary of "
                             "state / state treasurer races from data/statewide/ "
                             "plus state senate + state house chamber totals "
                             "from data/state_senate/ and data/state_house/ "
                             "(contested state-leg districts only; no "
                             "state-leg -> congressional-district crosswalk "
                             "exists, so they enter as statewide sums); "
                             "presidential-year coverage is partial — 10-12 "
                             "gov states, exec offices parsed from 2016, "
                             "state-leg 11-24 states before 2016 and 39-41 "
                             "from 2020",
            },
            "allocation": "whole counties: full weight; partial counties: "
                          "allocated by geometric county-area shares "
                          "(county_share_pct) normalised across claimant "
                          "districts; at-large: entire jurisdiction",
            "map_vintage_for_year": {str(k): v for k, v in VINTAGE_FOR_YEAR.items()},
            "note_2012": "Nov 2012 voted under the 2000s map (108th-112th "
                         "Congress lines, as finally used incl. TX 2003 / GA 2006)",
            "caveat": "partial counties are area-weighted, not population-"
                      "weighted; presidential leans for districts containing "
                      "large shared counties are dampened toward the state "
                      "mean — see votes_from_partial_pct; the 2010s "
                      "crosswalk snapshot shows the map as first enacted "
                      "(FL 2015 / NC 2016 & 2019 / PA 2018 court redraws are "
                      "not reflected); Alaska has no borough-level "
                      "presidential table before 2024, so AK-AL blends "
                      "house + senate + statewide for 2004-2020; house "
                      "leans carry candidate/incumbency effects the "
                      "presidential ticket does not; statewide-office "
                      "coverage is partial in presidential years (most "
                      "states elect them in midterms), so the statewide "
                      "component earns its 0.20 weight only where a race "
                      "was actually on the ballot",
        },
        "mapping": {k: v for k, v in map_meta.items() if k != "path"},
        "years": {},
    }

    for year in years:
        counts, national, n_rows, alias = load_county_counts(pres_dir, year)
        if counts is None:
            if fetch_missing and client is not None:
                import presidential_elections
                logger.info("%d — presidential CSV missing, fetching…", year)
                presidential_elections.run(start_year=year, end_year=year,
                                           output_dir=output_dir, client=client)
                counts, national, n_rows, alias = load_county_counts(pres_dir, year)
            else:
                logger.warning(
                    "%d — %s not found; blending house + senate + statewide "
                    "(run `cli.py presidential` first or pass --fetch-missing)",
                    year, os.path.join(pres_dir, f"presidential_results_{year}.csv"))
                counts, national, n_rows, alias = {}, {}, 0, {}
        pres_skipped = not counts
        if pres_skipped:
            logger.warning(
                "%d — presidential CSV present but empty; blending house + "
                "senate + statewide only", year)

        house_info: dict = {"missing_files": [], "house_excluded": []}
        house_counts, nat_house = load_house_year_races(
            house_dir, year, keep_codes, house_info)
        sen_info: dict = {"missing_files": [], "unknown_states": {},
                          "excluded_races": []}
        sen_races = load_senate_midterm_races(senate_dir, year, keep_codes,
                                              sen_info)
        sen_votes, _sen_meta, sen_national = compute_midterm_state_votes(
            sen_races, sen_info)
        # statewide pool: executive offices + state-leg chamber totals
        # (same loaders as the midterm path; partial coverage in
        # presidential years is expected and handled by renormalisation)
        stw_info: dict = {"missing_files": [], "unknown_states": {},
                          "excluded_races": []}
        stw_races = load_statewide_midterm_races(statewide_dir, year,
                                                 keep_codes, stw_info)
        stw_races += load_state_leg_midterm_races(state_leg_dir, year,
                                                  keep_codes, stw_info)
        stw_votes, _stw_meta, stw_national = compute_midterm_state_votes(
            stw_races, stw_info)
        nat_house_share = (
            nat_house["D"] / (nat_house["D"] + nat_house["R"]) * 100.0
            if nat_house and (nat_house["D"] + nat_house["R"]) > 0 else None)
        nat_sen_share = (
            sen_national["D"] / (sen_national["D"] + sen_national["R"]) * 100.0
            if sen_national and (sen_national["D"] + sen_national["R"]) > 0
            else None)
        nat_stw_share = (
            stw_national["D"] / (stw_national["D"] + stw_national["R"]) * 100.0
            if stw_national and (stw_national["D"] + stw_national["R"]) > 0
            else None)

        frame, info = compute_year_lean(
            districts, counts, national, year, alias,
            house_counts=house_counts, nat_house_share=nat_house_share,
            senate_state_votes=sen_votes, nat_senate_share=nat_sen_share,
            statewide_state_votes=stw_votes,
            nat_statewide_share=nat_stw_share)
        path = os.path.join(lean_dir, f"district_lean_{year}.csv")
        frame.to_csv(path, index=False)
        logger.info(
            "  %d [%s map] %d districts (house %d, senate states %d, "
            "statewide states %d), national D two-party pres %s / house %s "
            "/ senate %s / statewide %s -> %s",
            year, VINTAGE_FOR_YEAR[year], len(frame), info["house_districts"],
            info["senate_states"], info["statewide_states"],
            info["national"]["d_share_two_party_pct"],
            f"{nat_house_share:.2f}" if nat_house_share is not None else "—",
            f"{nat_sen_share:.2f}" if nat_sen_share is not None else "—",
            f"{nat_stw_share:.2f}" if nat_stw_share is not None else "—", path)
        meta["years"][year] = {
            "vintage": VINTAGE_FOR_YEAR[year],
            "presidential_rows": n_rows,
            "presidential_csv": "missing" if pres_skipped else "ok",
            "states_present": len({sc for (sc, _ck) in counts}),
            "rows": info["rows"],
            "national": info["national"],
            "house": {
                "districts": info["house_districts"],
                "votes_fixed": house_info["house_votes_fixed"],
                "national_d_share": round(nat_house_share, 2)
                                    if nat_house_share is not None else None,
                "excluded_races": house_info["house_excluded"] or None,
                "missing_files": house_info["missing_files"] or None,
            },
            "senate": {
                "states": info["senate_states"],
                "national_d_share": round(nat_sen_share, 2)
                                    if nat_sen_share is not None else None,
                "excluded_races": sen_info["excluded_races"] or None,
                "unknown_states": sen_info["unknown_states"] or None,
            },
            "statewide": {
                "states": info["statewide_states"],
                "national_d_share": round(nat_stw_share, 2)
                                    if nat_stw_share is not None else None,
                "excluded_races": stw_info["excluded_races"] or None,
                "stateleg_walkover_districts":
                    stw_info.get("stateleg_walkover_districts", 0) or None,
                "missing_files": stw_info["missing_files"] or None,
            },
            "allocation_coverage": info["states"],
            "unmatched_counties": info["unmatched"] or None,
            "unclaimed_counties": info["unclaimed"] or None,
            "multi_whole_counties": info["multi_whole"] or None,
        }
        all_frames.append(frame)

    if not all_frames:
        logger.warning("No lean rows computed — nothing written.")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    path = os.path.join(lean_dir, "district_lean_all.csv")
    combined.to_csv(path, index=False)
    logger.info("Combined -> %s (%s rows)", path, f"{len(combined):,}")

    pvi = summarize_pvi(combined)
    if len(pvi):
        path = os.path.join(lean_dir, "district_pvi_summary.csv")
        pvi.to_csv(path, index=False)
        logger.info("PVI summary -> %s (%s rows)", path, f"{len(pvi):,}")

    ts = time.strftime("%Y%m%d_%H%M%S")
    with open(os.path.join(lean_dir, f"district_lean_metadata_{ts}.json"), "w",
              encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, default=str)
    return combined


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    run()
