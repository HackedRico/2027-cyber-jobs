#!/usr/bin/env python3
"""Pure classification + location logic for the cyber-jobs scraper.

Deliberately dependency-free (stdlib only): no `requests`, no `yaml`. That
keeps test_classification.py runnable with a bare interpreter and lets the
scraper, the community-submission scripts, and the tests share one source of
truth for the taxonomy.

Pipeline per job (see evaluate_job):
  1. Hard rejects (seniority, non-cyber functions, physical security)
  2. Cyber relevance (title keywords; generic engineering titles allowed at
     pure-play security companies flagged `security_company: true`)
  3. Level classification -> intern | newgrad | earlycareer
  4. US-only location filter
  5. Clearance/citizenship detection -> 🇺🇸 marker
  6. Category inference (SOC, AppSec, Offensive Security, GRC, ...)
"""

import html
import re
import unicodedata
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Title classification keywords
# ---------------------------------------------------------------------------

# Roles too senior for an early-career board. The leveled numerals (III/IV/3/4)
# are NOT bare-matched here — a bare `\b4\b` rejected "PCI DSS 4.0 Analyst I"
# and "Layer 3 Security"; LEVELED_SENIOR_RE below matches them only in a
# role/level-noun context.
SENIORITY_REJECT = [
    r'\bsenior\b', r'\bsr\b\.?', r'\bstaff\b', r'\bprincipal\b', r'\blead\b',
    r'\bmanager\b', r'\bdirector\b', r'\bvp\b', r'\bvice president\b',
    r'\bhead of\b', r'\bchief\b', r'\bdistinguished\b', r'\bfellow\b',
    r'\barchitect\b', r'\bexecutive\b', r'\bexpert\b',
    r'\bsme\b', r'\bsubject matter expert\b',
]

# Senior levels are rejected only when III/IV/3/4 qualifies a role/level noun,
# so "SOC Analyst III" and "Tier 3 Responder" are rejected but "Layer 3 Network
# Analyst I", "PCI DSS 4.0 Compliance Analyst", and "Cyber IV&V Engineer" pass.
LEVELED_SENIOR_RE = re.compile(
    r'\b(?:analyst|engineer|consultant|specialist|administrator|technician|'
    r'developer|tester|responder|investigator|scientist|researcher|'
    r'tier|level)\s+(?:iii|iv|3|4)\b'
)

# Internships and co-ops get their own level. Title signals only — job
# descriptions mention "our internship program" as boilerplate far too often
# to be trusted. Word boundaries keep 'intern' from matching 'internal'.
INTERN_TITLE_RES = [re.compile(p) for p in (
    r'\bintern\b', r'\binternship\b', r'\bco-?op\b',
    # Bank-style ("Cybersecurity Summer Analyst") and USAJOBS Pathways
    # ("Student Trainee") internship titles.
    r'\bsummer analyst\b', r'\bstudent trainee\b',
)]

# "Security Engineer - Summer 2027" is an internship req even without the
# word "intern" — but only when paired with a hiring-cycle year (COHORT_YEAR_RE
# bounds the window, so a stale "Summer 2019" repost is not resurrected), and
# only after the explicit new-grad signals lose ("New Grad ... Summer 2026
# Start" is a full-time cohort with a season, not an internship).
SUMMER_RE = re.compile(r'\bsummer\b')

# Physical security and non-cyber business functions. Short terms that live
# inside legitimate words ('sales' in 'salesforce', 'finance' in 'financial')
# are word-bounded via FUNCTION_REJECT_RE below.
FUNCTION_REJECT = [
    'security guard', 'physical security', 'loss prevention', 'public safety',
    'executive protection', 'transportation security', 'safety and security',
    'security screener', 'campus safety', 'alarm technician',
    'nuclear safeguards',  # 'safeguards' alone is an AI-safety signal
    'sales', 'account executive', 'account manager', 'marketing',
    'recruiter', 'recruiting', 'talent acquisition', 'human resources',
    'people technology', 'people operations', 'channel systems',
    'customer success', 'business development', 'partner manager',
    'payroll', 'accountant', 'accounting', 'finance', 'financial analyst',
    'fp&a', 'revenue', 'billing', 'procurement', 'supply chain',
    'attorney', 'counsel', 'paralegal', 'executive assistant',
    'administrative assistant', 'workplace', 'facilities',
    'copywriter', 'community manager', 'social media',
    'hackathon', 'general interest', 'talent community', 'talent network',
    # Hardware/manufacturing — "SoC" (system-on-chip) titles are not SOC roles.
    'asic', 'rtl design', 'soc design', 'soc verification', 'soc architect',
    'silicon', 'chip design', 'tapeout', 'manufacturing engineer',
    'process engineer', 'mechanical engineer', 'electrical engineer',
    'chemical engineer', 'industrial engineer', 'civil engineer',
    'photolithography', 'metrology',
]


def _term_regex(term):
    # Word-bound each term so 'india' doesn't match 'Indianapolis' and 'sales'
    # doesn't match 'salesforce'.
    pat = re.escape(term)
    if term[0].isalnum():
        pat = r'\b' + pat
    if term[-1].isalnum():
        pat += r'\b'
    return pat


FUNCTION_REJECT_RE = re.compile('|'.join(_term_regex(t) for t in FUNCTION_REJECT))

# "Security Officer" is usually a guard; keep it only when clearly infosec.
SECURITY_OFFICER_RE = re.compile(r'\bsecurity officer\b')
INFOSEC_OFFICER_HINTS = ['information security', 'cyber', 'ciso', 'iso ']

# A title containing any of these is a cybersecurity role.
CYBER_KEYWORDS = [
    'security', 'cyber', 'infosec', 'information assurance',
    'penetration test', 'pentest', 'red team', 'blue team', 'purple team',
    'threat', 'incident response', 'forensic', 'malware', 'vulnerability',
    'appsec', 'exploit', 'reverse engineer', 'cryptograph', 'grc',
    'siem', 'detection engineer', 'detection and response',
    'devsecops', 'identity and access', 'zero trust', 'privacy engineer',
    'iam engineer', 'iam analyst', 'cyber risk', 'security risk',
    'technology risk',
    # AI security & AI safety — model/LLM security, adversarial ML, and
    # safety/alignment work at AI labs.
    'ai security', 'ml security', 'llm security', 'model security',
    'genai security', 'ai safety', 'ai risk', 'ai governance',
    'responsible ai', 'trustworthy ai', 'ai red team',
    'adversarial machine learning', 'adversarial ml', 'adversarial robustness',
    'ai alignment', 'alignment science', 'alignment research', 'safeguards',
]

# Short acronyms need word boundaries ('soc' is inside 'associate'), and
# 'SoC' must not match system-on-chip hardware titles.
CYBER_REGEXES = [re.compile(p) for p in
                 (r'\bsoc\b(?!\s+(asic|design|verification|rtl|silicon|power|hardware))',
                  r'\bcnd\b', r'\bcno\b', r'\bdfir\b', r'\bir analyst\b')]

# Bare 'safeguards' is an AI-safety signal here, but IAEA/nuclear
# non-proliferation "Safeguards Analyst" titles (that omit the word 'nuclear'
# so FUNCTION_REJECT misses them) must not be pulled in as AI security.
NUCLEAR_SAFEGUARDS_RE = re.compile(r'\b(nuclear|radiological|iaea|'
                                   r'non-?proliferation)\b')

# At `security_company: true` employers, any engineering role is a
# security-industry job even without a cyber keyword in the title.
TECH_KEYWORDS = [
    'engineer', 'developer', 'software', 'devops', 'sre', 'researcher',
    'scientist', 'data analyst', 'solutions engineer', 'support engineer',
    'infrastructure', 'platform', 'backend', 'frontend', 'full stack',
    'full-stack', 'machine learning', 'detection', 'analyst',
]

NEWGRAD_SIGNALS = [
    'new grad', 'new-grad', 'university grad', 'college grad', 'campus hire',
    'graduate program', 'grad program', 'graduate engineer',
    'graduate analyst', 'graduate cyber', 'graduate security',
    'early career program', 'emerging talent', 'early talent', 'rotational',
    'rotation program', 'recent graduate', 'launch program',
    'associate program',
    'new graduate', 'university hire', 'campus recruit',
    # Defense contractors run new-grad cohorts as "development programs".
    'leadership development program', 'graduate development program',
    'cyber development program', 'early career development',
    'pathways program',
]


def _cohort_years(span=2):
    """Hiring-cycle years accepted in titles: current year through current+span.

    Computed relative to today so cohort/new-grad detection keeps working past
    2028 without a code edit (the old hardcoded 2026-2028 window was a silent
    time-bomb).
    """
    year = datetime.now().year
    return [year + i for i in range(span + 1)]


def _cohort_year_re(span=2):
    # Optional leading digit absorbs Northrop's year typos like "22026".
    century = str(datetime.now().year)[:2]
    yrs = '|'.join(str(y)[2:] for y in _cohort_years(span))
    return re.compile(rf'\b\d?{century}(?:{yrs})\b')


# Titles carrying a target start year ("2026 Associate Cyber Software
# Engineer") are campus-cohort reqs.
COHORT_YEAR_RE = _cohort_year_re()

# "Class of 2026", "2027 grad" — generated from the same rolling window so the
# year phrases never go stale.
NEWGRAD_YEAR_SIGNALS = [
    phrase for y in _cohort_years() for phrase in (f'class of {y}', f'{y} grad')
]

EARLYCAREER_SIGNALS = [
    'entry level', 'entry-level', 'early career', 'junior', 'apprentice',
    'associate', 'tier 1', 'tier i', 'tier 2', 'tier ii', 'level 1', 'level 2',
    'early in career',
]
# Word-bounded so 'level 1' doesn't match 'level 10' and 'associate' doesn't
# match 'associated'. 'tier ii' is listed explicitly so it isn't lost when
# 'tier i' stops substring-matching it.
EARLYCAREER_RE = re.compile('|'.join(_term_regex(t) for t in EARLYCAREER_SIGNALS))

# "Analyst I", "Engineer 1", "SOC Analyst II" — I/II count as early career,
# III+ is rejected by LEVELED_SENIOR_RE above.
LEVELED_TITLE_RE = re.compile(
    r'\b(analyst|engineer|consultant|specialist|administrator|technician|'
    r'developer|tester|responder|investigator)\s+(i|ii|1|2)\b'
)

# Strong phrases in a job description that mark a role as early career.
# Deliberately tight — descriptions are noisy (boilerplate like "from early
# career to executive" would false-positive on looser phrases).
DESCRIPTION_SIGNALS = [
    '0-2 years', '0–2 years', '0 to 2 years', 'no prior experience',
    'entry level role', 'entry-level role', 'entry level position',
    'entry-level position', 'entry level opportunity',
]

# A description stating a low experience ceiling marks an early-career role
# even when the title carries no level marker (used, gated, at security_company
# employers to recover recall on flat "Security Engineer" titles).
MAX_YOE_RES = [re.compile(p) for p in (
    r'\b0\s*[-–]\s*2\s*years?\b', r'\b0 to 2 years?\b',
    r'\b1\s*[-–]\s*2\s*years?\b', r'\b1 to 2 years?\b',
    r'\bup to 2 years?\b', r'\bless than 2 years?\b',
    r'\bminimum of 0 years?\b', r'\bno (?:prior )?experience (?:is )?required\b',
)]

CLEARANCE_SIGNALS = [
    'clearance', 'ts/sci', 'top secret', 'polygraph', 'us citizen',
    'u.s. citizen', 'us citizenship', 'u.s. citizenship', 'public trust',
    'secret-level',
]

# Ordered buckets; first matching regex wins. 'privacy' lives in Security
# Engineering (not GRC) so a "Security and Privacy" research role keeps a
# security tag instead of being read as governance/risk.
CATEGORY_RULES = [
    ('AI Security & Safety', r'ai security|ml security|llm security|'
                             r'model security|genai security|ai safety|'
                             r'ai risk|ai governance|responsible ai|'
                             r'trustworthy ai|ai red team|adversarial|'
                             r'alignment|safeguards'),
    ('Offensive Security', r'penetration|pentest|red team|offensive|exploit|'
                           r'vulnerability research|purple team'),
    ('SOC & Detection', r'\bsoc\b|security operations|detection|blue team|'
                        r'incident response|threat hunt|csirt|siem|'
                        r'cyber defense|defensive cyber|triage'),
    ('Threat Intelligence', r'threat intel|\bcti\b|intelligence analyst|'
                            r'threat research|adversary'),
    ('Forensics & IR', r'forensic|\bdfir\b|malware analy|reverse engineer'),
    ('AppSec & ProdSec', r'application security|product security|appsec|'
                         r'secure code|devsecops|software security'),
    ('Cloud & Infra Security', r'cloud security|infrastructure security|'
                               r'network security|platform security|'
                               r'systems security'),
    ('Identity & IAM', r'\biam\b|identity|access management|zero trust'),
    ('GRC & Risk', r'\bgrc\b|governance|risk|compliance|audit|policy|'
                   r'information assurance'),
    ('Security Engineering', r'security|cyber|infosec|cryptograph|privacy'),
]
CATEGORY_RULES = [(name, re.compile(pattern)) for name, pattern in CATEGORY_RULES]

# The AI-lab flat-title acceptance path keys off this first rule.
AI_CATEGORY_RE = CATEGORY_RULES[0][1]
assert CATEGORY_RULES[0][0] == 'AI Security & Safety'

# Fallback categories used when no rule matches; combined with the rule names
# they are the single source of truth for what process_approved.py accepts.
FALLBACK_CATEGORIES = ('Engineering @ Security Co', 'Security Engineering')
CATEGORY_NAMES = [name for name, _ in CATEGORY_RULES]
CATEGORY_ALLOWLIST = set(CATEGORY_NAMES) | set(FALLBACK_CATEGORIES)

# ---------------------------------------------------------------------------
# Location filtering (United States only)
# ---------------------------------------------------------------------------

US_STATE_ABBRS = {
    'alabama': 'AL', 'alaska': 'AK', 'arizona': 'AZ', 'arkansas': 'AR',
    'california': 'CA', 'colorado': 'CO', 'connecticut': 'CT', 'delaware': 'DE',
    'florida': 'FL', 'georgia': 'GA', 'hawaii': 'HI', 'idaho': 'ID',
    'illinois': 'IL', 'indiana': 'IN', 'iowa': 'IA', 'kansas': 'KS',
    'kentucky': 'KY', 'louisiana': 'LA', 'maine': 'ME', 'maryland': 'MD',
    'massachusetts': 'MA', 'michigan': 'MI', 'minnesota': 'MN',
    'mississippi': 'MS', 'missouri': 'MO', 'montana': 'MT', 'nebraska': 'NE',
    'nevada': 'NV', 'new hampshire': 'NH', 'new jersey': 'NJ',
    'new mexico': 'NM', 'new york': 'NY', 'north carolina': 'NC',
    'north dakota': 'ND', 'ohio': 'OH', 'oklahoma': 'OK', 'oregon': 'OR',
    'pennsylvania': 'PA', 'rhode island': 'RI', 'south carolina': 'SC',
    'south dakota': 'SD', 'tennessee': 'TN', 'texas': 'TX', 'utah': 'UT',
    'vermont': 'VT', 'virginia': 'VA', 'washington': 'WA',
    'west virginia': 'WV', 'wisconsin': 'WI', 'wyoming': 'WY',
    'district of columbia': 'DC',
}
US_STATES = set(US_STATE_ABBRS.values())
CA_PROVINCES = {'AB', 'BC', 'MB', 'NB', 'NL', 'NS', 'NT', 'NU', 'ON', 'PE',
                'QC', 'SK', 'YT'}

US_SUBSTRINGS = [
    'united states', 'usa', 'u.s.', 'us only', 'us-remote', 'remote - us',
    'remote (us', 'remote, us', 'us remote', 'anywhere in the us',
    'new york', 'san francisco', 'los angeles', 'seattle', 'boston',
    'chicago', 'austin', 'denver', 'atlanta', 'miami', 'dallas', 'houston',
    'raleigh', 'washington, d', 'washington d', 'arlington', 'reston',
    'mclean', 'annapolis', 'fort meade', 'huntsville', 'colorado springs',
    'san antonio', 'menlo park', 'palo alto', 'mountain view', 'san jose',
    'sunnyvale', 'redwood city', 'bellevue', 'redmond', 'portland',
    'salt lake city', 'phoenix', 'philadelphia', 'pittsburgh', 'columbus',
    'minneapolis', 'nashville', 'charlotte', 'tampa', 'orlando',
    'baltimore', 'detroit', 'kansas city', 'st. louis', 'san diego',
    'sacramento', 'boulder', 'santa clara', 'irvine', 'cambridge',
]

NON_US_SUBSTRINGS = [
    'canada', 'toronto', 'vancouver', 'montreal', 'ottawa', 'calgary',
    'waterloo', 'ontario', 'british columbia', 'quebec',
    'london', 'united kingdom', ' uk', '(uk)', 'u.k.', 'england', 'scotland',
    'ireland', 'dublin', 'belfast',
    'germany', 'berlin', 'munich', 'frankfurt',
    'france', 'paris', 'netherlands', 'amsterdam', 'belgium', 'brussels',
    'spain', 'madrid', 'barcelona', 'portugal', 'lisbon', 'italy', 'milan',
    'poland', 'warsaw', 'krakow', 'czech', 'prague', 'romania', 'bucharest',
    'sweden', 'stockholm', 'norway', 'oslo', 'denmark', 'copenhagen',
    'finland', 'helsinki', 'switzerland', 'zurich', 'austria', 'vienna',
    'estonia', 'tallinn', 'hungary', 'budapest', 'greece', 'athens',
    'israel', 'tel aviv', 'jerusalem',
    'india', 'bangalore', 'bengaluru', 'hyderabad', 'pune', 'mumbai',
    'delhi', 'chennai', 'noida', 'gurgaon', 'gurugram',
    'singapore', 'japan', 'tokyo', 'korea', 'seoul', 'china', 'beijing',
    'shanghai', 'hong kong', 'taiwan', 'taipei', 'philippines', 'manila',
    'vietnam', 'malaysia', 'indonesia', 'jakarta', 'thailand', 'bangkok',
    'australia', 'sydney', 'melbourne', 'brisbane', 'new zealand', 'auckland',
    'mexico city', ', mexico', 'brazil', 'sao paulo', 'argentina',
    'buenos aires', 'colombia', 'bogota', 'chile', 'santiago', 'costa rica',
    'peru', 'uruguay',
    'dubai', 'uae', 'saudi', 'riyadh', 'qatar', 'egypt', 'cairo',
    'nigeria', 'lagos', 'south africa', 'kenya', 'nairobi',
    'emea', 'apac', 'latam',
]

NON_US_RE = re.compile('|'.join(_term_regex(t) for t in NON_US_SUBSTRINGS))

REGION_CODE_RE = re.compile(r',\s*([A-Za-z]{2})\.?\s*$')
# An embedded 2-letter US state token even without a trailing comma, e.g.
# "Office - USA - VA - Reston", "US - CA - San Jose".
EMBEDDED_STATE_RE = re.compile(r'\b([A-Z]{2})\b')

# "Remote (US/Canada)", "US or Remote", "Austin; Remote" split into parts.
LOCATION_SPLIT_RE = re.compile(r'[;|•/]|\bor\b')
REMOTE_FULL_RE = re.compile(r'remote(\s*\(.*\))?|work from home|nationwide')


def _strip_accents(text):
    # Fold "Zürich"/"Bogotá" to ASCII so the NON_US blocklist still catches
    # accented foreign-city spellings.
    return ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c))


def _part_is_us(part):
    """True if a single location part positively resolves to the US."""
    p = part.strip()
    if not p:
        return False
    low = p.lower()
    # A part that names a foreign place is not a US part, even if it also says
    # "remote" ("Remote (EMEA)").
    if NON_US_RE.search(_strip_accents(low)):
        return False
    if REMOTE_FULL_RE.fullmatch(low):
        return True
    m = REGION_CODE_RE.search(p)
    if m:
        code = m.group(1).upper()
        if code in US_STATES:
            return True
        if code in CA_PROVINCES:
            return False
    region = p.rsplit(',', 1)[-1].strip().lower()
    if region in US_STATE_ABBRS:
        return True
    for code in EMBEDDED_STATE_RE.findall(p):
        if code in US_STATES:
            return True
    if re.search(r'\b(us|usa|u\.s\.a?|united states)\b', low):
        return True
    return any(s in low for s in US_SUBSTRINGS)


# Split on every delimiter that separates co-equal location options so a US
# token is tested as its own segment, not as an 'us' buried in prose.
SEGMENT_SPLIT_RE = re.compile(r'[,/;|•\-–]|\bor\b')
US_COUNTRY_SEGMENTS = {'us', 'usa', 'unitedstates'}


def _has_strong_us_token(location):
    """True only for an unambiguous, delimited US token.

    Accepts a comma-joined multi-country string like "Remote - US, UK" (the
    splitter can't break it up) where "US" is its own segment, and a trailing
    ", ST" that rescues a US city whose name collides with a foreign one
    (Vienna VA, Paris TX). Rejects an 'us' embedded in prose ("India (US
    hours)") and a mid-string state code followed by a country
    ("Chennai, TN, India").
    """
    for seg in SEGMENT_SPLIT_RE.split(location):
        if re.sub(r'[^a-z]', '', seg.strip().lower()) in US_COUNTRY_SEGMENTS:
            return True
    # End-anchored: the state code must be the trailing token.
    m = REGION_CODE_RE.search(location)
    return bool(m and m.group(1).upper() in US_STATES)


def is_us_location(location):
    """True if any part of a (possibly multi-) location string is in the US.

    A multi-region posting like "Remote (US/Canada)" is US-eligible: the US
    part wins even though Canada is also named. Only reject when NO part
    resolves to the US.
    """
    if not location or not location.strip():
        return False

    parts = [p for p in LOCATION_SPLIT_RE.split(location) if p.strip()]
    if any(_part_is_us(p) for p in parts):
        return True

    # An unambiguous US token anywhere means the role lists a US option even in
    # a comma-joined multi-country string the splitter left whole.
    if _has_strong_us_token(location):
        return True

    # Otherwise fall back to the whole-string scan, but only when no foreign
    # country is named.
    loc = location.lower()
    if NON_US_RE.search(_strip_accents(loc)):
        return False
    if REMOTE_FULL_RE.fullmatch(loc.strip()):
        return True
    return any(s in loc for s in US_SUBSTRINGS)


# Opaque Workday facility codes ("CASD14", "TXSA08UNK").
FACILITY_CODE_RE = re.compile(r'^[A-Z]{2,}\d[A-Z0-9]*$')


def _normalize_single_location(location):
    location = location.strip()
    # Amazon: "US, MA, Boston" -> "Boston, MA"
    m = re.fullmatch(r'(?:USA?|United States),\s*([A-Z]{2}),\s*(.+)', location)
    if m:
        return f'{m.group(2).strip()}, {m.group(1)}'
    # Northrop-style Workday: "United States-California-Palmdale"
    m = re.fullmatch(r'(?:USA?|United States)-([A-Za-z .]+)-(.+)', location)
    if m:
        abbr = US_STATE_ABBRS.get(m.group(1).strip().lower())
        if abbr:
            return f'{m.group(2).strip()}, {abbr}'
    # PANW-style Workday: "Office - USA - CA - Headquarters" / "Office - USA - TX"
    m = re.fullmatch(r'(?:Office|Remote|Virtual Location)\s*-\s*USA?\s*-\s*'
                     r'([A-Z]{2})(?:\s*-\s*(.+))?', location, flags=re.IGNORECASE)
    if m:
        state = m.group(1).upper()
        site = (m.group(2) or '').strip()
        if site and site.lower() not in ('headquarters', 'hq', 'remote', 'office'):
            return f'{site}, {state}'
        return f'{state} (US)' if state in US_STATES else (site or 'United States')
    # "Virtual Location - Virginia, VA" / "Virtual Location - Remote" -> remote
    if re.fullmatch(r'Virtual Location\s*-\s*.+', location, flags=re.IGNORECASE):
        return 'Remote (US)'
    # GDIT-style: "USA OH Dayton" -> "Dayton, OH"
    m = re.fullmatch(r'USA?\s+([A-Z]{2})\s+(.+)', location)
    if m:
        return f'{m.group(2).strip()}, {m.group(1)}'
    location = re.sub(r'^(usa?|united states)\s*[-–:]\s*', '', location,
                      flags=re.IGNORECASE)
    loc_l = location.lower()
    if 'remote' in loc_l and (loc_l == 'remote'
                              or re.search(r'\busa?\b|\bunited states\b', loc_l)):
        return 'Remote (US)'
    # Opaque facility code with no human-readable city — drop it.
    if FACILITY_CODE_RE.match(location.strip()):
        return ''
    parts = [p.strip() for p in location.split(',')]
    if len(parts) >= 2 and parts[-1].lower() in ('usa', 'us', 'united states'):
        parts = parts[:-1]  # "Arlington, Virginia, USA" -> "Arlington, Virginia"
    if len(parts) >= 2:
        abbr = US_STATE_ABBRS.get(parts[-1].lower())
        if abbr:
            return f'{", ".join(parts[:-1])}, {abbr}'
    return ', '.join(parts)


def _location_city_key(loc):
    # "Austin" and "Austin, TX" share this key so the bare-city duplicate can
    # fold into the qualified one.
    return loc.split(',')[0].strip().lower()


def normalize_location(location):
    """Convert "USA - Austin, Texas" -> "Austin, TX"; collapse remote variants.

    Multi-location strings drop a bare-city part ("Austin") when the same city
    also appears qualified ("Austin, TX"); genuinely distinct qualified parts
    ("Portland, OR; Portland, ME") are kept.
    """
    if not location:
        return location
    seen = []
    for raw in location.split(';'):
        if not raw.strip():
            continue
        norm = _normalize_single_location(raw)
        if norm and norm not in seen:
            seen.append(norm)
    qualified_cities = {_location_city_key(s) for s in seen if ',' in s}
    result = [s for s in seen
              if ',' in s or _location_city_key(s) not in qualified_cities]
    return '; '.join(result)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Descriptions are only skimmed for a few short signals; cap the length so the
# tag-strip regex can't be driven quadratic by a pathological '<'-heavy body.
MAX_DESCRIPTION_CHARS = 100_000


def strip_html(text):
    if not text:
        return ''
    text = html.unescape(text[:MAX_DESCRIPTION_CHARS])
    # `[^<>]` excludes '<' too, so an unclosed-tag run of '<' can't be consumed
    # and re-backtracked — linear on every Python version (no ReDoS).
    return re.sub(r'<[^<>]*>', ' ', text)


def is_rejected_title(title):
    t = title.lower()
    if any(re.search(p, t) for p in SENIORITY_REJECT):
        return True
    if LEVELED_SENIOR_RE.search(t):
        return True
    if FUNCTION_REJECT_RE.search(t):
        return True
    if SECURITY_OFFICER_RE.search(t) and not any(h in t for h in INFOSEC_OFFICER_HINTS):
        return True
    return False


def _is_cyber_keyword_hit(t):
    for kw in CYBER_KEYWORDS:
        if kw not in t:
            continue
        # A bare "Safeguards Analyst" is AI safety, but an IAEA/nuclear
        # non-proliferation one (no 'nuclear' word for FUNCTION_REJECT to catch)
        # is not — keep scanning so a co-occurring real cyber keyword can win.
        if kw == 'safeguards' and NUCLEAR_SAFEGUARDS_RE.search(t):
            continue
        return True
    return False


def is_cyber_title(title, security_company=False):
    t = title.lower()
    if _is_cyber_keyword_hit(t):
        return True
    if any(p.search(t) for p in CYBER_REGEXES):
        return True
    if security_company and any(kw in t for kw in TECH_KEYWORDS):
        return True
    return False


def classify_level(title, description='', intern_hint=False):
    """Return 'intern', 'newgrad', 'earlycareer', or None."""
    t = title.lower()
    # Intern wins first: "SOC Intern - Summer 2027" must not fall through to
    # the cohort-year rule and come out as newgrad. `intern_hint` carries an
    # ATS employment-type field for postings whose title omits "intern".
    if intern_hint or any(p.search(t) for p in INTERN_TITLE_RES):
        return 'intern'
    if any(kw in t for kw in NEWGRAD_SIGNALS) or any(kw in t for kw in NEWGRAD_YEAR_SIGNALS):
        return 'newgrad'
    if re.search(r'\bgraduate\b', t) and 'graduate degree' not in t:
        return 'newgrad'
    # A leveled I/II marker beats a bare cohort year: "Analyst II (Windows
    # Server 2026)" is early career, not a 2026 campus cohort.
    if LEVELED_TITLE_RE.search(t):
        return 'earlycareer'
    # Season + cohort year with no new-grad wording is an internship req;
    # checked before the bare cohort-year rule, which would claim it.
    if SUMMER_RE.search(t) and COHORT_YEAR_RE.search(t):
        return 'intern'
    if COHORT_YEAR_RE.search(t):
        return 'newgrad'
    if EARLYCAREER_RE.search(t):
        return 'earlycareer'
    if description:
        d = strip_html(description).lower()
        if any(kw in d for kw in ('new grad', 'new graduate', 'recent graduate')):
            return 'newgrad'
        if any(kw in d for kw in DESCRIPTION_SIGNALS):
            return 'earlycareer'
    return None


def permits_early_experience(description):
    """True if the description states an experience ceiling of ~2 years or less.

    Used only at security_company employers to recover flat "Security Engineer"
    titles that carry no level marker; kept tight to avoid precision loss.
    """
    if not description:
        return False
    d = strip_html(description).lower()
    return any(p.search(d) for p in MAX_YOE_RES)


def requires_clearance(title, description=''):
    text = f'{title} {strip_html(description)}'.lower()
    return any(kw in text for kw in CLEARANCE_SIGNALS)


# "3+ years", "5 years of experience", "3 or more years", "3+ yrs". Whitespace
# runs are bounded ({0,3}) so a digit followed by a huge space run can't drive
# the two adjacent \s* quadratic.
YEARS_RE = re.compile(r'\b(\d{1,2})\s{0,3}\+?\s{0,3}(?:or more\s{1,3})?(?:years?|yrs?)\b')
# Spelled-out counts that matter for the low end of "N+ years".
SPELLED_YEARS = ('three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten')
SPELLED_YEARS_RE = re.compile(
    r'\b(?:' + '|'.join(SPELLED_YEARS) + r')\s{0,3}\+?\s{0,3}(?:or more\s{1,3})?(?:years?|yrs?)\b')
# A requirement verb close before the count, e.g. "minimum 3 years".
REQUIREMENT_VERB_RE = re.compile(
    r'\b(minimum|at least|require[sd]?|must have|need)\b', re.IGNORECASE)


def _year_in_requirement_context(low, start, end):
    # "N years of experience" (experience shortly after) or "requires N years"
    # (a requirement verb shortly before) — but NOT a bare mention like
    # "analyzed the past 3 years of incidents".
    return ('experience' in low[end:end + 30]
            or bool(REQUIREMENT_VERB_RE.search(low[max(0, start - 40):start])))


def requires_experience(description):
    """True if the description asks for 3+ years of experience anywhere."""
    if not description:
        return False
    low = strip_html(description).lower()
    for m in YEARS_RE.finditer(low):
        if int(m.group(1)) >= 3 and _year_in_requirement_context(low, m.start(), m.end()):
            return True
    for m in SPELLED_YEARS_RE.finditer(low):
        if _year_in_requirement_context(low, m.start(), m.end()):
            return True
    return False


def infer_category(title, security_company=False):
    t = title.lower()
    for category, pattern in CATEGORY_RULES:
        if pattern.search(t):
            return category
    if security_company:
        return 'Engineering @ Security Co'
    return 'Security Engineering'


def evaluate_job(title, location, description='', security_company=False,
                 intern_hint=False):
    """Run the full filter pipeline. Returns (level, category) or None."""
    if not title or is_rejected_title(title):
        return None
    if not is_cyber_title(title, security_company):
        return None
    level = classify_level(title, description, intern_hint)
    if level is None:
        # A flat "Security Engineer" title at a security company with a low
        # experience ceiling in its description is an early-career role.
        if security_company and permits_early_experience(description):
            level = 'earlycareer'
        # AI labs use flat titles ("Software Engineer, AI Safety") with no
        # level marker. Accept AI security/safety roles unless the posting asks
        # for 3+ years of experience.
        elif AI_CATEGORY_RE.search(title.lower()) and not requires_experience(description):
            level = 'earlycareer'
        else:
            return None
    if not is_us_location(location):
        return None
    return level, infer_category(title, security_company)


def listing_dedup_key(company, role, location):
    """Secondary dedup identity: the same (company, role, location) is one job.

    Complements URL dedup, which can't collapse a role reposted per-location
    under distinct req-ID URLs.
    """
    def norm(value):
        return re.sub(r'\s+', ' ', (value or '').strip()).lower()
    return norm(company), norm(role), norm(location)


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def purge_stale_listings(listings, today, max_age_days=60):
    """Drop closed listings older than N days; return (kept, removed_count).

    Only closed rows are eligible — an old but still-open posting is kept, so
    the board doesn't silently discard a live long-running req. Age is measured
    from closed_date, falling back to date_added. Community rows are exempt:
    they can't self-heal through the scraper's revival path (they never appear
    in raw_jobs), so a transient link-check failure must not delete them.
    """
    cutoff = (_parse_date(today) or datetime.now().date()) - timedelta(days=max_age_days)
    kept, removed = [], 0
    for entry in listings:
        if entry.get('closed') and entry.get('source') != 'Community':
            stamp = _parse_date(entry.get('closed_date') or entry.get('date_added'))
            if stamp and stamp < cutoff:
                removed += 1
                continue
        kept.append(entry)
    return kept, removed


def prune_seen(seen, today, ttl_days=45):
    """Drop scraper job-ids not refreshed within ttl_days; return a new dict.

    Bounds unbounded growth of seen_jobs and lets a requisition that vanished
    from an ATS (so its id stops being refreshed) become re-discoverable if it
    reopens. The TTL survives a transient one-run board outage.
    """
    fallback = _parse_date(today) or datetime.now().date()
    cutoff = fallback - timedelta(days=ttl_days)
    return {jid: stamp for jid, stamp in seen.items()
            if (_parse_date(stamp) or fallback) >= cutoff}


def reclassify_listings(listings):
    """Re-run title-only classification over stored rows, mutating in place.

    Returns (listings, changes) where changes is a list of
    (company, role, old_type, new_type). Community rows reflect a maintainer's
    judgment and are left alone; intern rows may derive from an ATS
    employment-type hint a title can't reproduce, so a title-only pass would
    wrongly demote them. A title that yields no signal (None) keeps the stored,
    possibly description-derived, type.
    """
    changes = []
    for entry in listings:
        if entry.get('source') == 'Community' or entry.get('type') == 'intern':
            continue
        level = classify_level(entry.get('role', ''))
        if level and level != entry.get('type'):
            changes.append((entry.get('company', ''), entry.get('role', ''),
                            entry.get('type'), level))
            entry['type'] = level
    return listings, changes
