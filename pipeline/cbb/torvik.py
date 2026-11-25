"""
Your CBB Daily Edges model (CBB_Daily_Edges.ipynb), ported for GitHub Actions.

Torvik ratings + four factors -> z-scores -> your weighted matchup edge -> points via a
calibrated slope (anchored to last season's) + 3 pts home court. Cached in docs/data/cbb/cache
because each GitHub run starts fresh. Sources (in order): Torvik T-Rank table, Torvik
fffinal.csv, Sports-Reference (computed four factors); team_results.csv for ratings.
"""
import difflib
import math
import re
import time
import unicodedata
from datetime import datetime
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..stats import ET

# ---- season / blending (your settings) ----
BLEND_DAYS = 14
DAYS_BACK = 60
HALF_LIFE_DAYS = 14
FF_FULL_GAMES = 6
SLOPE_PRIOR_GAMES = 100
HCA_POINTS = 3.0
DEFAULT_SLOPE_FULL = 6.09
DEFAULT_SLOPE_BASE = 10.15
D1_EFG, D1_TOV, D1_ORB, D1_FTR, D1_DRB = 51.3, 17.1, 30.9, 35.5, 69.1
W_FG, W_ORB, W_EM, W_TO, W_FTR = 0.35, 0.25, 0.20, 0.15, 0.10
STRONG_EDGE, GOOD_EDGE, LEAN_EDGE = 6.0, 3.0, 1.5
MAX_TRUSTED_EDGE = 12.0
FUZZY_CUTOFF = 0.90
INCLUDE_NON_D1_OPPONENTS = True
NON_D1_QUANTILE = 0.02
NON_D1_PENALTY = 5.0

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
})
RUN_LOG = []
CACHE_DIR = Path("docs/data/cbb/cache")
LEGACY_4F_FILE = CACHE_DIR / "torvik_teamstats_clean_4f.csv"


def set_cache_dir(p):
    global CACHE_DIR
    CACHE_DIR = Path(p)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def log(msg=""):
    print(msg)
    RUN_LOG.append(str(msg))


def et_today():
    return datetime.now(ET).date()


def season_year(d):
    return d.year + 1 if d.month >= 8 else d.year


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def http_get(url, params=None, method="GET", data=None, tries=3, timeout=30):
    last = None
    for i in range(tries):
        try:
            r = SESSION.request(method, url, params=params, data=data, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {r.status_code} from {url}")
            return r
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def first_number(x):
    if x is None:
        return np.nan
    if isinstance(x, (int, float, np.number)):
        return float(x) if not pd.isna(x) else np.nan
    m = re.search(r"[-+]?\d*\.?\d+", str(x).replace(",", ""))
    return float(m.group(0)) if m else np.nan


def cache_fresh(path, max_age_hours):
    if not path.exists():
        return False
    if max_age_hours is None:
        return True
    meta = path.with_suffix(".at")          # git checkouts reset mtimes, so keep our own stamp
    t = float(meta.read_text()) if meta.exists() else 0
    return (time.time() - t) < max_age_hours * 3600


def stamp(path):
    path.with_suffix(".at").write_text(str(time.time()))


# ============================================================ team names (your cell 3)
_APOS = "'’‘`ʻʼ´"
def canon(name):
    if name is None or (isinstance(name, float) and math.isnan(name)):
        return ""
    s = unicodedata.normalize("NFKD", str(name))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower().strip()
    for ch in _APOS:
        s = s.replace(ch, "")
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    toks = [{"saint": "st", "state": "st"}.get(t, t) for t in s.split()]
    return " ".join(toks)
# group key  ->  every spelling seen across sources
TEAM_GROUPS = {
    "nc st": ["NC State", "N.C. State", "North Carolina State", "N.C. St."],
    "connecticut": ["UConn", "Connecticut"],
    "mississippi": ["Ole Miss", "Mississippi"],
    "miami fl": ["Miami (FL)", "Miami FL", "Miami", "Miami Florida", "Miami-Florida"],
    "miami oh": ["Miami (OH)", "Miami OH", "Miami Ohio", "Miami (Ohio)", "Miami-Ohio"],
    "usc": ["USC", "Southern California", "Southern Cal"],
    "penn": ["Penn", "Pennsylvania"],
    "pittsburgh": ["Pitt", "Pittsburgh"],
    "unlv": ["UNLV", "Nevada-Las Vegas", "Nevada Las Vegas"],
    "lsu": ["LSU", "Louisiana State"],
    "ucf": ["UCF", "Central Florida"],
    "byu": ["BYU", "Brigham Young"],
    "vcu": ["VCU", "Virginia Commonwealth"],
    "smu": ["SMU", "Southern Methodist"],
    "tcu": ["TCU", "Texas Christian"],
    "uab": ["UAB", "Alabama-Birmingham", "Alabama Birmingham"],
    "utsa": ["UTSA", "Texas-San Antonio", "UT San Antonio"],
    "utep": ["UTEP", "Texas-El Paso", "UT El Paso"],
    "umbc": ["UMBC", "Maryland-Baltimore County"],
    "kansas city": ["Kansas City", "UMKC", "Missouri-Kansas City"],
    "iu indy": ["IU Indy", "IUPUI", "IU Indianapolis"],
    "purdue fort wayne": ["Purdue Fort Wayne", "Fort Wayne", "PFW", "IPFW"],
    "louisiana monroe": ["UL Monroe", "ULM", "Louisiana-Monroe", "Louisiana Monroe"],
    "louisiana": ["Louisiana", "Louisiana-Lafayette", "UL Lafayette", "Louisiana Lafayette"],
    "little rock": ["Little Rock", "Arkansas-Little Rock", "UALR", "Arkansas Little Rock"],
    "texas a and m corpus christi": ["Texas A&M-Corpus Christi", "Texas A&M Corpus Christi", "Texas A&M Corpus Chris",
                                     "A&M-Corpus Christi", "A&M Corpus Christi", "Texas A&M-CC", "TAMU-CC", "Corpus Christi"],
    "east texas a and m": ["East Texas A&M", "Texas A&M-Commerce", "Texas A&M Commerce"],
    "ut rio grande valley": ["UTRGV", "UT Rio Grande Valley", "Texas-Rio Grande Valley", "Texas Rio Grande Valley"],
    "tennessee martin": ["UT Martin", "Tennessee-Martin", "Tennessee Martin", "Tenn-Martin"],
    "siu edwardsville": ["SIUE", "SIU Edwardsville", "SIU-Edwardsville", "Southern Illinois Edwardsville"],
    "illinois chicago": ["UIC", "Illinois-Chicago", "Illinois Chicago"],
    "nebraska omaha": ["Omaha", "Nebraska Omaha", "Nebraska-Omaha", "UNO"],
    "cal baptist": ["California Baptist", "Cal Baptist", "CBU"],
    "st thomas": ["St. Thomas (MN)", "St. Thomas", "St. Thomas-Minnesota", "St. Thomas Minnesota"],
    "cal st bakersfield": ["CSU Bakersfield", "Cal State Bakersfield", "Cal St. Bakersfield", "Bakersfield"],
    "cal st fullerton": ["Cal State Fullerton", "Cal St. Fullerton", "CSU Fullerton", "CSUF", "Fullerton"],
    "cal st northridge": ["CSUN", "Cal State Northridge", "Cal St. Northridge", "CSU Northridge", "Northridge"],
    "long beach st": ["Long Beach State", "Long Beach St.", "LBSU", "Long Beach"],
    "sacramento st": ["Sacramento State", "Sac State", "Sacramento"],
    "sam houston st": ["Sam Houston", "Sam Houston State", "Sam Houston St."],
    "the citadel": ["The Citadel", "Citadel"],
    "appalachian st": ["App State", "Appalachian State", "Appalachian St."],
    "southern miss": ["Southern Miss", "Southern Mississippi", "Southern Miss."],
    "fiu": ["FIU", "Florida International", "Florida Intl", "Florida Int'l"],
    "florida gulf coast": ["FGCU", "Florida Gulf Coast"],
    "florida atlantic": ["FAU", "Florida Atlantic", "Fla. Atlantic"],
    "loyola chicago": ["Loyola Chicago", "Loyola-Chicago", "Loyola (IL)", "Loyola (Chi)", "Loyola Chi"],
    "loyola md": ["Loyola (MD)", "Loyola Maryland", "Loyola MD", "Loyola-Maryland"],
    "loyola marymount": ["LMU", "LMU (CA)", "Loyola Marymount"],
    "st marys": ["Saint Mary's", "Saint Mary's (CA)", "St. Mary's", "St. Mary's (CA)"],
    "st johns": ["St. John's", "St. John's (NY)", "Saint John's"],
    "st francis": ["Saint Francis", "St. Francis (PA)", "Saint Francis (PA)", "St. Francis PA", "Saint Francis U", "St. Francis U"],
    "mount st marys": ["Mount St. Mary's", "Mt. St. Mary's", "Mount Saint Mary's"],
    "unc wilmington": ["UNCW", "UNC Wilmington", "UNC-Wilmington", "NC Wilmington"],
    "unc greensboro": ["UNCG", "UNC Greensboro", "UNC-Greensboro"],
    "unc asheville": ["UNCA", "UNC Asheville", "UNC-Asheville"],
    "north carolina a and t": ["N.C. A&T", "NC A&T", "North Carolina A&T"],
    "north carolina central": ["NC Central", "N.C. Central", "North Carolina Central"],
    "northern illinois": ["NIU", "Northern Illinois", "Northern Ill."],
    "northern iowa": ["UNI", "Northern Iowa"],
    "stephen f austin": ["SFA", "Stephen F. Austin", "SF Austin"],
    "fairleigh dickinson": ["FDU", "Fairleigh Dickinson"],
    "east tennessee st": ["ETSU", "East Tennessee State", "East Tennessee St.", "East Tenn. St."],
    "vmi": ["VMI", "Virginia Military", "Virginia Military Institute"],
    "western kentucky": ["WKU", "Western Ky.", "Western Kentucky"],
    "northern kentucky": ["NKU", "Northern Ky.", "Northern Kentucky"],
    "eastern kentucky": ["EKU", "Eastern Ky.", "Eastern Kentucky"],
    "georgia southern": ["Ga. Southern", "GA Southern", "Georgia Southern"],
    "georgia st": ["Ga. State", "Georgia State", "Georgia St."],
    "eastern michigan": ["Eastern Mich.", "Eastern Michigan"],
    "western michigan": ["Western Mich.", "Western Michigan"],
    "central michigan": ["Central Mich.", "Central Michigan"],
    "mcneese st": ["McNeese", "McNeese State", "McNeese St."],
    "nicholls st": ["Nicholls", "Nicholls State", "Nicholls St."],
    "southeastern louisiana": ["Southeastern La.", "SE Louisiana", "Southeastern Louisiana"],
    "southeast missouri st": ["SEMO", "Southeast Missouri", "Southeast Mo. St.", "SE Missouri St.", "Southeast Missouri State"],
    "boston university": ["Boston U.", "Boston Univ.", "Boston University", "BU"],
    "albany": ["UAlbany", "Albany (NY)", "Albany"],
    "north alabama": ["North Ala.", "UNA", "North Alabama"],
    "south alabama": ["South Ala.", "South Alabama"],
    "middle tennessee": ["Middle Tenn.", "MTSU", "Middle Tennessee State", "Middle Tennessee"],
    "western carolina": ["Western Caro.", "Western Carolina"],
    "charleston southern": ["Charleston So.", "Charleston Southern"],
    "charleston": ["College of Charleston", "Charleston", "Col. of Charleston", "Col of Charleston"],
    "eastern washington": ["Eastern Wash.", "Eastern Washington"],
    "northern arizona": ["Northern Ariz.", "Northern Arizona"],
    "northern colorado": ["Northern Colo.", "Northern Colorado"],
    "western illinois": ["Western Ill.", "Western Illinois"],
    "eastern illinois": ["Eastern Ill.", "Eastern Illinois"],
    "southern indiana": ["Southern Ind.", "Southern Indiana"],
    "southern illinois": ["Southern Ill.", "Southern Illinois"],
    "central arkansas": ["Central Ark.", "Central Arkansas"],
    "south florida": ["South Fla.", "USF", "South Florida"],
    "central connecticut": ["Central Conn. St.", "Central Connecticut State", "Central Connecticut", "CCSU"],
    "arkansas pine bluff": ["Ark.-Pine Bluff", "UAPB", "Arkansas-Pine Bluff", "Arkansas Pine Bluff"],
    "mississippi valley st": ["Mississippi Val.", "Miss. Valley St.", "Miss Valley St", "MVSU", "Mississippi Valley State"],
    "grambling st": ["Grambling", "Grambling State", "Grambling St."],
    "prairie view a and m": ["Prairie View", "Prairie View A&M", "PVAMU"],
    "maryland eastern shore": ["UMES", "Md.-East. Shore", "Maryland-Eastern Shore", "MD Eastern Shore", "Md Eastern Shore"],
    "southern": ["Southern U.", "Southern University", "Southern"],
    "incarnate word": ["UIW", "Incarnate Word"],
    "houston christian": ["Houston Christian", "Houston Baptist", "HCU"],
    "ut arlington": ["UT Arlington", "UTA", "Texas-Arlington", "UT-Arlington"],
    "seattle": ["Seattle U", "Seattle", "Seattle University"],
    "queens": ["Queens (NC)", "Queens", "Queens University"],
    "army": ["Army West Point", "Army"],
    "liu": ["LIU", "LIU Brooklyn", "Long Island University", "Long Island"],
    "massachusetts": ["UMass", "Massachusetts"],
    "green bay": ["Green Bay", "Wisconsin-Green Bay", "UW-Green Bay"],
    "milwaukee": ["Milwaukee", "Wisconsin-Milwaukee", "UW-Milwaukee"],
    "detroit mercy": ["Detroit", "Detroit Mercy"],
    "usc upstate": ["USC Upstate", "South Carolina Upstate", "SC Upstate"],
    "utah tech": ["Utah Tech", "Dixie State"],
    "tarleton st": ["Tarleton", "Tarleton State", "Tarleton St."],
    "grand canyon": ["Grand Canyon", "GCU"],
    "uc santa barbara": ["UCSB", "UC Santa Barbara"],
    "uc san diego": ["UCSD", "UC San Diego"],
}
ALIAS = {}
for _key, _variants in TEAM_GROUPS.items():
    _k = canon(_key)
    for _v in _variants + [_key]:
        ALIAS[canon(_v)] = _k
# Optional: your own extra aliases in team_aliases.json  {"spelling seen": "Torvik spelling"}

def group_key(name):
    c = canon(name)
    return ALIAS.get(c, c)
class TeamResolver:
    """Maps any source's team name to a Torvik team key."""
    def __init__(self, names):
        self.display = {}
        for n in names:
            if isinstance(n, str) and n.strip():
                self.display.setdefault(group_key(n), n.strip())
        self.keys = set(self.display)
        self._keys_list = sorted(self.keys)
        self._cache = {}
        self.fuzzy_hits = {}
        self.unmatched = set()
    def _try(self, name, allow_prefix):
        c = canon(name)
        if not c:
            return None
        g = ALIAS.get(c, c)
        if g in self.keys:
            return g
        toks = c.split()
        if allow_prefix:   # odds names carry mascots: "Duke Blue Devils" -> "duke"
            for k in range(len(toks) - 1, 0, -1):
                p = " ".join(toks[:k])
                p = ALIAS.get(p, p)
                if p in self.keys:
                    return p
            return None
        cands = difflib.get_close_matches(g, self._keys_list, n=3, cutoff=FUZZY_CUTOFF)
        cands = [m for m in cands
                 if (set(m.split()) ^ set(g.split())) != {"st"}       # never "X" <-> "X State"
                 and m.split()[0] == g.split()[0]]                     # must share first word
        if not cands:
            return None
        if len(cands) > 1:
            r0 = difflib.SequenceMatcher(None, g, cands[0]).ratio()
            r1 = difflib.SequenceMatcher(None, g, cands[1]).ratio()
            if r0 - r1 < 0.03:
                return None
        self.fuzzy_hits[name] = self.display[cands[0]]
        return cands[0]
    def resolve(self, *names, allow_prefix=False, record=True):
        names = [n for n in names if isinstance(n, str) and n.strip()]
        ck = (tuple(names), allow_prefix)
        if ck in self._cache:
            return self._cache[ck]
        out = None
        for n in names:
            out = self._try(n, allow_prefix)
            if out:
                break
        if out is None and record and names:
            self.unmatched.add(names[0])
        self._cache[ck] = out
        return out

# ============================================================ stats (your cell 4)
FF_COLS = ["efg_o", "efg_d", "tov_o", "tov_d", "orb_o", "drb_d", "ftr_o", "ftr_d"]
def _flatten_cols(df):
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        cols = []
        for tup in df.columns:
            parts = [str(p) for p in tup if not str(p).startswith("Unnamed")]
            cols.append(parts[-1] if parts else "")
        df.columns = cols
    else:
        df.columns = [str(c) for c in df.columns]
    return df
def classify_ff(header):
    """Header text -> (factor, is_defense). Works for Torvik (EFG%, TORD...) and CSV styles (eFG% Def...)."""
    h = re.sub(r"\s+", " ", str(header).lower()).strip()
    if not h or "rank" in h or h.endswith(" rk") or h == "rk":
        return None
    h2 = h.replace(" ", "")
    is_def = any(x in h for x in ("def", "allow", "opp"))
    if re.match(r"^efg", h2):               return ("efg", is_def or h2.startswith("efgd"))
    if re.match(r"^(tord|tovd|tor|tov|to%|to$)", h2): return ("to", is_def or h2.startswith(("tord", "tovd")))
    if re.match(r"^(orb|oreb|or%)", h2):     return ("orb", is_def or h2.startswith("orbd"))
    if re.match(r"^(drb|dreb|dr%)", h2):     return ("drb", False)
    if re.match(r"^ftr", h2):               return ("ftr", is_def or h2.startswith("ftrd"))
    return None
_FF_TARGET = {("efg", False): "efg_o", ("efg", True): "efg_d", ("to", False): "tov_o", ("to", True): "tov_d",
              ("orb", False): "orb_o", ("orb", True): "opp_orb", ("drb", False): "drb_d",
              ("ftr", False): "ftr_o", ("ftr", True): "ftr_d"}
def clean_team_cell(s, torvik_style=True):
    s = str(s).replace("\xa0", " ").strip()
    if torvik_style:   # strip seed/next-game annotations Torvik adds after the name
        s = re.split(r"\s{2,}|\(|\d|\s+(?:vs\.?|at|@)\s+", s)[0]
    s = re.sub(r"\s*NCAA$", "", s)
    return s.strip()
FF_RANGE = {"efg_o": (25, 80), "efg_d": (25, 80), "tov_o": (5, 40), "tov_d": (5, 40),
            "orb_o": (5, 65), "drb_d": (35, 95), "ftr_o": (5, 100), "ftr_d": (5, 100)}
def sanitize_ff(df):
    """Torvik shows placeholder values (200, 100, 0) for teams with no D1 possessions yet.
    Any impossible value wipes that team's row, so the team falls back to last season."""
    df = df.copy()
    bad = pd.Series(False, index=df.index)
    for c, (lo, hi) in FF_RANGE.items():
        v = pd.to_numeric(df[c], errors="coerce")
        bad |= v.isna() | (v < lo) | (v > hi)
    df.loc[bad, list(FF_RANGE)] = np.nan
    return df
def finalize_ff(out, source, min_rows):
    if out is None or any(c not in out for c in FF_COLS):
        return None
    out = out[["team"] + FF_COLS].copy()
    for c in FF_COLS:
        out[c] = pd.to_numeric(out[c], errors="coerce")
        out.loc[out[c] <= 0, c] = np.nan
    # Some exports list "DRB" as opponent OR% (~30). Your formula expects DRB% (~70, higher = better).
    if out["drb_d"].median() < 50:
        out["drb_d"] = 100 - out["drb_d"]
    out = sanitize_ff(out)
    ok = out.dropna()
    if len(ok) < min_rows:
        return None
    m = ok[FF_COLS].median()
    sane = (40 < m.efg_o < 62 and 40 < m.efg_d < 62 and 10 < m.tov_o < 26 and 10 < m.tov_d < 26
            and 18 < m.orb_o < 42 and 55 < m.drb_d < 82 and 15 < m.ftr_o < 60 and 15 < m.ftr_d < 60)
    if not sane:
        log(f"    {source}: numbers out of range, ignored")
        return None
    out = out.drop_duplicates("team")
    out["source"] = source
    return out
def normalize_ff(raw, source, min_rows, torvik_style=True):
    if raw is None or len(raw) < min_rows:
        return None
    df = _flatten_cols(raw)
    team_col = next((c for c in df.columns if re.sub(r"[^a-z]", "", c.lower()) in ("team", "teamname", "school")), None)
    if team_col is None:
        return None
    picks = {}
    for c in df.columns:
        if c == team_col:
            continue
        cl = classify_ff(c)
        tgt = _FF_TARGET.get(cl) if cl else None
        if tgt and tgt not in picks:
            picks[tgt] = c
    out = pd.DataFrame({"team": df[team_col].astype(str).map(lambda s: clean_team_cell(s, torvik_style))})
    for tgt, c in picks.items():
        col = df[c]
        if isinstance(col, pd.DataFrame):
            col = col.iloc[:, 0]
        out[tgt] = col.map(first_number)
    out = out[(out["team"].str.len() > 1) & (~out["team"].str.lower().isin(["team", "school", "teamname"]))]
    if "drb_d" not in out and "opp_orb" in out:
        out["drb_d"] = 100 - out["opp_orb"]
    return finalize_ff(out, source, min_rows)
# --- source A: Torvik main table (same columns as your old Excel export) ---
def ff_from_torvik_trank(year, min_rows):
    url = "https://barttorvik.com/trank.php"
    params = {"year": year, "conlimit": "All", "type": "All"}
    for mode in ("GET", "POST"):     # POST answers Torvik's "verifying browser" page
        r = http_get(url, params=params, method=mode, data={"js_test_submitted": "on"} if mode == "POST" else None)
        if r.status_code != 200 or "efg" not in r.text.lower():
            continue
        try:
            tables = pd.read_html(StringIO(r.text))
        except ValueError:
            continue
        for t in tables:
            out = normalize_ff(t, "Torvik T-Rank table", min_rows)
            if out is not None:
                return out
    return None
# --- source B: Torvik static CSV ---
def ff_from_torvik_fffinal(year, min_rows):
    r = http_get(f"https://barttorvik.com/{year}_fffinal.csv")
    if r.status_code != 200 or len(r.text) < 2000:
        return None
    return normalize_ff(pd.read_csv(StringIO(r.text)), "Torvik fffinal.csv", min_rows)
# --- source C: Sports-Reference team + opponent totals (four factors computed) ---
def ff_from_sports_reference(year, min_rows):
    base = f"https://www.sports-reference.com/cbb/seasons/men/{year}"
    need = ["FG", "FGA", "3P", "FTA", "ORB", "TRB", "TOV"]
    def grab(suffix):
        r = http_get(f"{base}-{suffix}.html")
        if r.status_code != 200:
            return None
        t = _flatten_cols(pd.read_html(StringIO(r.text.replace("<!--", "").replace("-->", "")))[0])
        t = t.loc[:, ~pd.Index(t.columns).duplicated()]
        if "School" not in t or any(c not in t for c in need):
            return None
        t = t[t["School"].astype(str).ne("School")].copy()
        t["team"] = t["School"].astype(str).str.replace(r"\s*NCAA$", "", regex=True).str.strip()
        for c in need:
            t[c] = pd.to_numeric(t[c], errors="coerce")
        return t.set_index("team")[need]
    tm, op = grab("school-stats"), grab("opponent-stats")
    if tm is None or op is None:
        return None
    j = tm.join(op, rsuffix="_o", how="inner")
    poss_o = j.FGA - j.ORB + j.TOV + 0.475 * j.FTA
    poss_d = j.FGA_o - j.ORB_o + j.TOV_o + 0.475 * j.FTA_o
    out = pd.DataFrame({
        "team": j.index,
        "efg_o": 100 * (j.FG + 0.5 * j["3P"]) / j.FGA,
        "efg_d": 100 * (j.FG_o + 0.5 * j["3P_o"]) / j.FGA_o,
        "tov_o": 100 * j.TOV / poss_o,
        "tov_d": 100 * j.TOV_o / poss_d,
        "orb_o": 100 * j.ORB / (j.ORB + (j.TRB_o - j.ORB_o)),
        "drb_d": 100 * (j.TRB - j.ORB) / ((j.TRB - j.ORB) + j.ORB_o),
        "ftr_o": 100 * j.FTA / j.FGA,
        "ftr_d": 100 * j.FTA_o / j.FGA_o,
    }).reset_index(drop=True)
    return finalize_ff(out, "Sports-Reference (computed)", min_rows)
def load_four_factors(year, max_age_hours, min_rows):
    path = CACHE_DIR / f"fourfactors_{year}.csv"
    if cache_fresh(path, max_age_hours):
        df = sanitize_ff(pd.read_csv(path))
        log(f"  four factors {year}: {len(df)} teams (cache, {df['source'].iloc[0]})")
        return df
    for fn in (ff_from_torvik_trank, ff_from_torvik_fffinal, ff_from_sports_reference):
        try:
            df = fn(year, min_rows)
            if df is not None:
                df.to_csv(path, index=False)
                stamp(path)
                log(f"  four factors {year}: {len(df)} teams from {df['source'].iloc[0]}")
                return df
        except Exception as e:
            log(f"    {fn.__name__} failed: {type(e).__name__}: {str(e)[:120]}")
    if path.exists():
        log(f"  ⚠️ four factors {year}: all sources failed — using older cache")
        return sanitize_ff(pd.read_csv(path))
    if LEGACY_4F_FILE.exists() and year == season_year(et_today()) - 1:
        df = finalize_ff(pd.read_csv(LEGACY_4F_FILE).rename(columns={"team_norm": "team"}), "your old 4F file", 200)
        if df is not None:
            log(f"  four factors {year}: using {LEGACY_4F_FILE.name}")
            return df
    log(f"  four factors {year}: not available yet")
    return None
def parse_team_results(raw):
    norm = {re.sub(r"[^a-z]", "", str(c).lower()): c for c in raw.columns}
    def pick(*names, contains=None):
        for n in names:
            if n in norm:
                return norm[n]
        if contains:
            for k, c in norm.items():
                if any(t in k for t in contains) and "rank" not in k and not k.endswith("rk"):
                    return c
        return None
    c_team = pick("team")
    c_oe, c_de = pick("adjoe", contains=["adjoe"]), pick("adjde", contains=["adjde"])
    c_t = pick("adjt", "adjtempo", contains=["adjt", "tempo", "pace"])
    c_rec = pick("record", "rec")
    if not (c_team and c_oe and c_de):
        return None
    out = pd.DataFrame({
        "team": raw[c_team].astype(str).str.strip(),
        "adjoe": pd.to_numeric(raw[c_oe], errors="coerce"),
        "adjde": pd.to_numeric(raw[c_de], errors="coerce"),
        "adjt": pd.to_numeric(raw[c_t], errors="coerce") if c_t else np.nan,
    })
    if c_rec:
        out["gp"] = raw[c_rec].astype(str).map(
            lambda s: sum(int(x) for x in re.findall(r"\d+", s)[:2]) if re.search(r"\d+\s*[-–]\s*\d+", s) else np.nan)
    else:
        out["gp"] = np.nan
    return out.dropna(subset=["adjoe", "adjde"]).drop_duplicates("team")
def load_team_results(year, max_age_hours):
    path = CACHE_DIR / f"team_results_{year}.csv"
    if cache_fresh(path, max_age_hours):
        df = pd.read_csv(path)
        log(f"  ratings {year}: {len(df)} teams (cache)")
        return df
    try:
        r = http_get(f"https://barttorvik.com/{year}_team_results.csv")
        if r.status_code == 200 and r.text.count("\n") > 100:
            df = parse_team_results(pd.read_csv(StringIO(r.text)))
            if df is not None and len(df) > 100:
                df.to_csv(path, index=False)
                stamp(path)
                log(f"  ratings {year}: {len(df)} teams from Torvik")
                return df
        log(f"  ratings {year}: Torvik returned HTTP {r.status_code}")
    except Exception as e:
        log(f"  ratings {year}: fetch failed ({type(e).__name__})")
    if path.exists():
        log(f"  ⚠️ ratings {year}: using older cache")
        return pd.read_csv(path)
    return None
def load_all_tables(cur_y):
    log("Loading stats…")
    return {
        "cur_res":  load_team_results(cur_y, 6),
        "cur_ff":   load_four_factors(cur_y, 6, min_rows=40),
        "prev_res": load_team_results(cur_y - 1, None),      # final season: cache forever
        "prev_ff":  load_four_factors(cur_y - 1, None, min_rows=250),
    }

# ============================================================ blend + z-scores (your cell 6)
NON_D1_KEY = "__non_d1__"
def _blend(c, p, w):
    res = w * c + (1 - w) * p
    res = res.where(~c.isna(), p)
    res = res.where(~p.isna(), c)
    return res
def blend_ratings(tables, R, today, season_start):
    def keyed(df):
        if df is None or len(df) == 0:
            return pd.DataFrame()
        d = df.copy()
        d["key"] = d["team"].map(lambda n: R.resolve(n, record=False))
        return d.dropna(subset=["key"]).drop_duplicates("key").set_index("key")
    cur_r, cur_f = keyed(tables["cur_res"]), keyed(tables["cur_ff"])
    pr_r, pr_f = keyed(tables["prev_res"]), keyed(tables["prev_ff"])
    days_in = max(0, (today - season_start).days)
    w_day = clamp(days_in / BLEND_DAYS, 0.0, 1.0) if BLEND_DAYS > 0 else 1.0
    keys = sorted(set(cur_r.index) | set(pr_r.index))
    def ser(df, col):
        return df[col].reindex(keys).astype(float) if (len(df) and col in df) else pd.Series(np.nan, index=keys)
    out = pd.DataFrame(index=keys)
    out["team"] = [R.display.get(k, k) for k in keys]
    for col in ["adjoe", "adjde", "adjt"]:
        out[col] = _blend(ser(cur_r, col), ser(pr_r, col), w_day)
    # Four factors after 2-3 games are mostly noise (one hot shooting night = 65% eFG),
    # so a team's own numbers only get full weight once it has played FF_FULL_GAMES games.
    gp = ser(cur_r, "gp")
    w4 = np.minimum(w_day, (gp / FF_FULL_GAMES).clip(0, 1)).fillna(w_day)
    for col in FF_COLS:
        out[col] = _blend(ser(cur_f, col), ser(pr_f, col), w4)
    out["adjem"] = out["adjoe"] - out["adjde"]
    out = out.dropna(subset=["adjem"])
    out.index.name = "key"
    # one stand-in row for any non-D1 opponent: bottom-of-D1 in every stat, then a little worse
    q = NON_D1_QUANTILE
    worse_low = {"adjoe": True, "adjde": False, "efg_o": True, "efg_d": False, "tov_o": False, "tov_d": True,
                 "orb_o": True, "drb_d": True, "ftr_o": True, "ftr_d": False}
    nd = {"team": "Non-D1 opponent", "adjt": out["adjt"].median()}
    for col, low in worse_low.items():
        s_ = out[col].dropna()
        nd[col] = s_.quantile(q if low else 1 - q) if len(s_) else np.nan
    nd["adjoe"] -= NON_D1_PENALTY / 2
    nd["adjde"] += NON_D1_PENALTY / 2
    nd["adjem"] = nd["adjoe"] - nd["adjde"]
    out.loc[NON_D1_KEY] = pd.Series(nd)
    d1 = out.drop(index=NON_D1_KEY)
    info = {"w_cur": w_day, "days_in": days_in, "season_start": season_start,
            "teams": len(d1), "full_4f": int(d1[FF_COLS].notna().all(axis=1).sum())}
    info["w_ff"] = float(w4.reindex(out.index).mean())
    if w_day < 1:
        log(f"Blend (ratings): day {days_in} of {BLEND_DAYS} → {1 - w_day:.0%} last season / {w_day:.0%} this season")
    else:
        log("Blend (ratings): 100% this season")
    log(f"Blend (four factors): avg {info['w_ff']:.0%} this season (full weight after {FF_FULL_GAMES} games)")
    log(f"Teams rated: {info['teams']}  ·  with complete four factors: {info['full_4f']}")
    return out, info
Z_COLS = ["adjem", "off_efg_plus", "def_efg_plus", "to_off_plus", "to_def_plus",
          "orb_plus", "drb_plus", "ftr_off_plus", "ftr_def_plus"]
NEEDED_FULL = [f"z_{c}" for c in Z_COLS]
def attach_zscores(df):
    df = df.copy()
    df["off_efg_plus"] = df["efg_o"] - D1_EFG
    df["def_efg_plus"] = D1_EFG - df["efg_d"]
    df["to_off_plus"]  = D1_TOV - df["tov_o"]
    df["to_def_plus"]  = df["tov_d"] - D1_TOV
    df["orb_plus"]     = df["orb_o"] - D1_ORB
    df["drb_plus"]     = df["drb_d"] - D1_DRB
    df["ftr_off_plus"] = df["ftr_o"] - D1_FTR
    df["ftr_def_plus"] = D1_FTR - df["ftr_d"]
    is_d1 = df.index != NON_D1_KEY
    for c in Z_COLS:
        s = pd.to_numeric(df[c], errors="coerce")
        mu, sd = s[is_d1].mean(), s[is_d1].std(ddof=0)
        df[f"z_{c}"] = (s - mu) / (sd if sd and sd > 1e-9 else 1.0)
    return df

# ============================================================ model (your cell 7)
def matchup_edge_full(h, a):
    fg_edge  = (h["z_off_efg_plus"] - a["z_def_efg_plus"]) + (h["z_def_efg_plus"] - a["z_off_efg_plus"])
    orb_edge = (h["z_orb_plus"] - a["z_drb_plus"]) - (a["z_orb_plus"] - h["z_drb_plus"])
    to_edge  = (h["z_to_off_plus"] - a["z_to_def_plus"]) - (a["z_to_off_plus"] - h["z_to_def_plus"])
    ftr_edge = (h["z_ftr_off_plus"] - a["z_ftr_def_plus"]) - (a["z_ftr_off_plus"] - h["z_ftr_def_plus"])
    em_edge  = h["z_adjem"] - a["z_adjem"]
    return float(W_FG * fg_edge + W_ORB * orb_edge + W_EM * em_edge + W_TO * to_edge + W_FTR * ftr_edge)
def matchup_edge_base(h, a):
    return float(h["z_adjem"] - a["z_adjem"])
def can_full(h, a):
    return not (h[NEEDED_FULL].isna().any() or a[NEEDED_FULL].isna().any())
SLOPE_CACHE = CACHE_DIR / "slopes.json"
def calibrate_slopes(Z, finals, today):
    """Fit points-per-unit from recent finals, pulled toward last season's slope until there are
    enough games. Early-season fits are unreliable: the stats being scored were built from the very
    games being fit, and November is full of 40-point mismatches."""
    result = {}
    for mode, lo, hi, default in (("FULL", 4.0, 10.0, DEFAULT_SLOPE_FULL), ("BASE", 6.0, 15.0, DEFAULT_SLOPE_BASE)):
        xs, ys, ws = [], [], []
        for g in finals:
            if g["home_key"] not in Z.index or g["away_key"] not in Z.index:
                continue
            h, a = Z.loc[g["home_key"]], Z.loc[g["away_key"]]
            if mode == "FULL":
                if not can_full(h, a):
                    continue
                x = matchup_edge_full(h, a)
            else:
                if pd.isna(h["z_adjem"]) or pd.isna(a["z_adjem"]):
                    continue
                x = matchup_edge_base(h, a)
            y = (g["home_score"] - g["away_score"]) - (0.0 if g.get("neutral") else HCA_POINTS)
            if x == 0 or pd.isna(x):
                continue
            xs.append(x); ys.append(y); ws.append(0.5 ** ((today - g["date"]).days / HALF_LIFE_DAYS))
        xs, ys, ws = map(np.array, (xs, ys, ws))
        n = len(xs)
        if n >= 30 and (ws * xs * xs).sum() > 1e-9:
            raw = float((ws * xs * ys).sum() / (ws * xs * xs).sum())
            slope = clamp((n * raw + SLOPE_PRIOR_GAMES * default) / (n + SLOPE_PRIOR_GAMES), lo, hi)
            result[mode] = {"slope": slope, "raw": raw, "n": n}
        else:
            result[mode] = {"slope": default, "raw": float("nan"), "n": n}
    for k, v in result.items():
        log(f"Slope {k}: {v['slope']:.2f}  (raw fit {v['raw']:.2f} on {v['n']} games, anchored to last season)")
    return result