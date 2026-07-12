#!/usr/bin/env python3
"""
Scrape ATS job-board APIs for US new-grad and early-career cybersecurity roles.

Pipeline per job:
  1. Hard rejects (seniority, interns, non-cyber functions, physical security)
  2. Cyber relevance (title keywords; generic engineering titles allowed at
     pure-play security companies flagged `security_company: true`)
  3. Level classification -> newgrad | earlycareer (title first, then strong
     phrases in the description when the ATS returns one)
  4. US-only location filter
  5. Clearance/citizenship detection -> 🇺🇸 marker
  6. Category inference (SOC, AppSec, Offensive Security, GRC, ...)

Accepted jobs are appended to listings.json (deduped by normalized URL) and
the README tables are rebuilt once at the end.
"""

import html
import json
import re
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
import yaml

LISTINGS_FILE = Path('listings.json')
SEEN_JOBS_FILE = Path('.github/data/seen_jobs.json')

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; cyber-jobs-scraper/1.0)'}

# ---------------------------------------------------------------------------
# Title classification keywords
# ---------------------------------------------------------------------------

# Roles too senior for an early-career board.
SENIORITY_REJECT = [
    r'\bsenior\b', r'\bsr\b\.?', r'\bstaff\b', r'\bprincipal\b', r'\blead\b',
    r'\bmanager\b', r'\bdirector\b', r'\bvp\b', r'\bvice president\b',
    r'\bhead of\b', r'\bchief\b', r'\bdistinguished\b', r'\bfellow\b',
    r'\barchitect\b', r'\bexecutive\b', r'\biii\b', r'\biv\b', r'\bexpert\b',
    r'\b3\b', r'\b4\b',
]

# Full-time board: internships and co-ops live elsewhere.
INTERN_REJECT = [r'\bintern\b', r'\binternship\b', r'\bco-?op\b', r'\bcoop\b']

# Physical security and non-cyber business functions.
FUNCTION_REJECT = [
    'security guard', 'physical security', 'loss prevention', 'public safety',
    'executive protection', 'transportation security', 'safety and security',
    'security screener', 'campus safety', 'alarm technician',
    'sales', 'account executive', 'account manager', 'marketing',
    'recruiter', 'recruiting', 'talent acquisition', 'human resources',
    'people technology', 'people operations', 'channel systems',
    'customer success', 'business development', 'partner manager',
    'payroll', 'accountant', 'accounting', 'finance', 'financial analyst',
    'fp&a', 'revenue', 'billing', 'procurement', 'supply chain',
    'attorney', 'counsel', 'paralegal', 'executive assistant',
    'administrative assistant', 'workplace', 'facilities',
    'copywriter', 'community manager', 'social media',
    # Hardware/manufacturing — "SoC" (system-on-chip) titles are not SOC roles.
    'asic', 'rtl design', 'soc design', 'soc verification', 'soc architect',
    'silicon', 'chip design', 'tapeout', 'manufacturing engineer',
    'process engineer', 'mechanical engineer', 'electrical engineer',
    'chemical engineer', 'industrial engineer', 'civil engineer',
    'photolithography', 'metrology',
]

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
]

# Short acronyms need word boundaries ('soc' is inside 'associate'), and
# 'SoC' must not match system-on-chip hardware titles.
CYBER_REGEXES = [re.compile(p) for p in
                 (r'\bsoc\b(?!\s+(asic|design|verification|rtl|silicon|power|hardware))',
                  r'\bcnd\b', r'\bcno\b', r'\bdfir\b', r'\bir analyst\b')]

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
    'class of 2026', 'class of 2027', '2026 grad', '2027 grad',
    'new graduate', 'university hire', 'campus recruit',
    # Defense contractors run new-grad cohorts as "development programs".
    'leadership development program', 'graduate development program',
    'cyber development program', 'early career development',
    'pathways program',
]

# Titles carrying a target start year ("2026 Associate Cyber Software
# Engineer") are campus-cohort reqs. The optional leading digit absorbs
# Northrop's year typos like "22026".
COHORT_YEAR_RE = re.compile(r'\b2?20(2[6-8])\b')

EARLYCAREER_SIGNALS = [
    'entry level', 'entry-level', 'early career', 'junior', 'apprentice',
    'associate', 'tier 1', 'tier i', 'level 1', 'early in career',
]

# "Analyst I", "Engineer 1", "SOC Analyst II" — I/II count as early career,
# III+ is rejected by SENIORITY_REJECT above.
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

CLEARANCE_SIGNALS = [
    'clearance', 'ts/sci', 'top secret', 'polygraph', 'us citizen',
    'u.s. citizen', 'us citizenship', 'u.s. citizenship', 'public trust',
    'secret-level',
]

# Ordered buckets; first matching regex wins.
CATEGORY_RULES = [
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
                   r'information assurance|privacy'),
    ('Security Engineering', r'security|cyber|infosec|cryptograph'),
]
CATEGORY_RULES = [(name, re.compile(pattern)) for name, pattern in CATEGORY_RULES]

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


def _term_regex(term):
    # Word-bound each term so 'india' doesn't match 'Indianapolis'.
    pat = re.escape(term)
    if term[0].isalnum():
        pat = r'\b' + pat
    if term[-1].isalnum():
        pat += r'\b'
    return pat


NON_US_RE = re.compile('|'.join(_term_regex(t) for t in NON_US_SUBSTRINGS))

REGION_CODE_RE = re.compile(r',\s*([A-Za-z]{2})\.?\s*$')


def is_us_location(location):
    """True if every part of a (possibly multi-) location string is in the US."""
    if not location or not location.strip():
        return False
    loc = location.lower()

    if NON_US_RE.search(loc):
        return False

    parts = re.split(r'[;|•]|\bor\b', location)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = REGION_CODE_RE.search(part)
        if m:
            code = m.group(1).upper()
            if code in US_STATES:
                return True
            if code in CA_PROVINCES:
                return False
        # "Albuquerque, New Mexico" — full state name after the last comma
        region = part.rsplit(',', 1)[-1].strip().lower()
        if region in US_STATE_ABBRS:
            return True

    if re.fullmatch(r'remote(\s*\(.*\))?|work from home|nationwide', loc.strip()):
        return True

    return any(s in loc for s in US_SUBSTRINGS)


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
    parts = [p.strip() for p in location.split(',')]
    if len(parts) >= 2 and parts[-1].lower() in ('usa', 'us', 'united states'):
        parts = parts[:-1]  # "Arlington, Virginia, USA" -> "Arlington, Virginia"
    if len(parts) >= 2:
        abbr = US_STATE_ABBRS.get(parts[-1].lower())
        if abbr:
            return f'{", ".join(parts[:-1])}, {abbr}'
    return ', '.join(parts)


def normalize_location(location):
    """Convert "USA - Austin, Texas" -> "Austin, TX"; collapse remote variants."""
    if not location:
        return location
    parts = [p.strip() for p in location.split(';') if p.strip()]
    return '; '.join(dict.fromkeys(_normalize_single_location(p) for p in parts))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def strip_html(text):
    if not text:
        return ''
    text = html.unescape(text)
    return re.sub(r'<[^>]+>', ' ', text)


def is_rejected_title(title):
    t = title.lower()
    if any(re.search(p, t) for p in SENIORITY_REJECT):
        return True
    if any(re.search(p, t) for p in INTERN_REJECT):
        return True
    if any(kw in t for kw in FUNCTION_REJECT):
        return True
    if SECURITY_OFFICER_RE.search(t) and not any(h in t for h in INFOSEC_OFFICER_HINTS):
        return True
    return False


def is_cyber_title(title, security_company=False):
    t = title.lower()
    if any(kw in t for kw in CYBER_KEYWORDS):
        return True
    if any(p.search(t) for p in CYBER_REGEXES):
        return True
    if security_company and any(kw in t for kw in TECH_KEYWORDS):
        return True
    return False


def classify_level(title, description=''):
    """Return 'newgrad', 'earlycareer', or None."""
    t = title.lower()
    if any(kw in t for kw in NEWGRAD_SIGNALS):
        return 'newgrad'
    if re.search(r'\bgraduate\b', t) and 'graduate degree' not in t:
        return 'newgrad'
    if COHORT_YEAR_RE.search(t):
        return 'newgrad'
    if any(kw in t for kw in EARLYCAREER_SIGNALS):
        return 'earlycareer'
    if LEVELED_TITLE_RE.search(t):
        return 'earlycareer'
    if description:
        d = strip_html(description).lower()
        if any(kw in d for kw in ('new grad', 'new graduate', 'recent graduate')):
            return 'newgrad'
        if any(kw in d for kw in DESCRIPTION_SIGNALS):
            return 'earlycareer'
    return None


def requires_clearance(title, description=''):
    text = f'{title} {strip_html(description)}'.lower()
    return any(kw in text for kw in CLEARANCE_SIGNALS)


def infer_category(title, security_company=False):
    t = title.lower()
    for category, pattern in CATEGORY_RULES:
        if pattern.search(t):
            return category
    if security_company:
        return 'Engineering @ Security Co'
    return 'Security Engineering'


def evaluate_job(title, location, description='', security_company=False):
    """Run the full filter pipeline. Returns (level, category) or None."""
    if not title or is_rejected_title(title):
        return None
    if not is_cyber_title(title, security_company):
        return None
    level = classify_level(title, description)
    if level is None:
        return None
    if not is_us_location(location):
        return None
    return level, infer_category(title, security_company)


# ---------------------------------------------------------------------------
# ATS scrapers — each yields dicts with:
#   id, company, title, location, url, board, description (optional)
# ---------------------------------------------------------------------------

# Some boards put the workplace type where the location belongs; the real
# location is then in `offices` / "Job Posting Location" metadata.
WORKPLACE_LABELS = {'in-office', 'hybrid', 'distributed', 'remote', 'onsite',
                    'on-site', 'flexible', ''}


def greenhouse_location(job):
    loc = (job.get('location') or {}).get('name', '') or ''
    if loc.strip().lower() not in WORKPLACE_LABELS:
        return loc
    parts = [o.get('name') for o in job.get('offices') or [] if o.get('name')]
    for m in job.get('metadata') or []:
        if isinstance(m, dict) and 'location' in (m.get('name') or '').lower():
            v = m.get('value')
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            elif v:
                parts.append(str(v))
    return '; '.join(dict.fromkeys(parts)) if parts else loc


def scrape_greenhouse(company, slug):
    url = f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true'
    resp = requests.get(url, timeout=15, headers=HEADERS)
    if resp.status_code != 200:
        print(f'  [{company}] Greenhouse HTTP {resp.status_code}')
        return []
    jobs = []
    for job in resp.json().get('jobs', []):
        jobs.append({
            'id': f'greenhouse_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('title', ''),
            'location': greenhouse_location(job),
            'url': job.get('absolute_url', ''),
            'board': 'Greenhouse',
            'description': job.get('content', ''),
        })
    return jobs


def scrape_lever(company, slug):
    url = f'https://api.lever.co/v0/postings/{slug}?mode=json'
    resp = requests.get(url, timeout=15, headers=HEADERS)
    if resp.status_code != 200:
        print(f'  [{company}] Lever HTTP {resp.status_code}')
        return []
    jobs = []
    for job in resp.json():
        country = job.get('country', '')
        if country and country.upper() != 'US':
            continue
        jobs.append({
            'id': f'lever_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('text', ''),
            'location': job.get('categories', {}).get('location', ''),
            'url': job.get('hostedUrl', ''),
            'board': 'Lever',
            'description': job.get('descriptionPlain', ''),
        })
    return jobs


def scrape_ashby(company, slug):
    url = f'https://api.ashbyhq.com/posting-api/job-board/{slug}'
    resp = requests.get(url, timeout=15, headers=HEADERS)
    if resp.status_code != 200:
        print(f'  [{company}] Ashby HTTP {resp.status_code}')
        return []
    data = resp.json()
    jobs = []
    for job in data.get('jobs') or data.get('jobPostings') or []:
        if job.get('employmentType', '') == 'Intern' or job.get('isListed') is False:
            continue
        locations = [job.get('location', '') or job.get('locationName', '')]
        locations += [s.get('location', '') for s in job.get('secondaryLocations') or []]
        location = '; '.join(dict.fromkeys(l for l in locations if l))
        apply_url = (
            job.get('jobUrl', '')
            or job.get('applyUrl', '')
            or f'https://jobs.ashbyhq.com/{slug}/{job.get("id", "")}'
        )
        jobs.append({
            'id': f'ashby_{slug}_{job["id"]}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': apply_url,
            'board': 'Ashby',
            'description': job.get('descriptionPlain', ''),
        })
    return jobs


def scrape_smartrecruiters(company, identifier):
    url = f'https://api.smartrecruiters.com/v1/companies/{identifier}/postings'
    params = {'limit': 100, 'offset': 0}
    jobs = []
    while True:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f'  [{company}] SmartRecruiters HTTP {resp.status_code}')
            break
        data = resp.json()
        content = data.get('content', [])
        if not content:
            break
        for job in content:
            loc = job.get('location', {})
            if loc.get('country', '').lower() != 'us' and not loc.get('remote'):
                continue
            city = loc.get('city', '')
            region = loc.get('region', '')
            if loc.get('remote'):
                location = 'Remote (US)'
            elif city and region:
                location = f'{city}, {region}'
            else:
                location = city or 'United States'
            job_id = job.get('id', '')
            jobs.append({
                'id': f'smartrecruiters_{identifier}_{job_id}',
                'company': company,
                'title': job.get('name', ''),
                'location': location,
                'url': f'https://jobs.smartrecruiters.com/{identifier}/{job_id}',
                'board': 'SmartRecruiters',
            })
        total = data.get('totalFound', 0)
        params['offset'] += len(content)
        if params['offset'] >= total:
            break
        time.sleep(0.3)
    return jobs


def scrape_workable(company, slug):
    url = f'https://apply.workable.com/api/v1/widget/accounts/{slug}'
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f'  [{company}] Workable HTTP {resp.status_code}')
        return []
    jobs = []
    for job in resp.json().get('jobs', []):
        loc = job.get('location', {})
        country = loc.get('countryCode', '').upper()
        if country and country != 'US' and not loc.get('remote'):
            continue
        city = loc.get('city', '')
        region = loc.get('region', '')
        if loc.get('remote'):
            location = 'Remote (US)' if country in ('US', '') else ''
        elif city and region:
            location = f'{city}, {region}'
        else:
            location = city
        job_id = job.get('shortcode', job.get('id', ''))
        jobs.append({
            'id': f'workable_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': f'https://apply.workable.com/{slug}/j/{job_id}/',
            'board': 'Workable',
        })
    return jobs


def scrape_recruitee(company, slug):
    url = f'https://{slug}.recruitee.com/api/offers/'
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f'  [{company}] Recruitee HTTP {resp.status_code}')
        return []
    jobs = []
    for job in resp.json().get('offers', []):
        country = (job.get('country') or '').lower()
        remote = job.get('remote', False)
        if country not in ('us', 'united states') and not remote:
            continue
        city = job.get('city') or ''
        region = job.get('province') or ''
        if remote:
            location = 'Remote (US)'
        elif city and region:
            location = f'{city}, {region}'
        else:
            location = city
        job_id = str(job.get('id', ''))
        jobs.append({
            'id': f'recruitee_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': job.get('careers_url',
                           f'https://{slug}.recruitee.com/o/{job.get("slug", job_id)}'),
            'board': 'Recruitee',
        })
    return jobs


def scrape_pinpoint(company, slug):
    url = f'https://{slug}.pinpointhq.com/postings.json'
    resp = requests.get(url, headers=HEADERS, timeout=15)
    if resp.status_code != 200:
        print(f'  [{company}] Pinpoint HTTP {resp.status_code}')
        return []
    jobs = []
    for job in resp.json().get('data', []):
        loc = job.get('location') or {}
        if job.get('workplace_type') == 'remote':
            location = 'Remote'
        else:
            location = ', '.join(p for p in (loc.get('city'), loc.get('province')) if p)
        job_id = str(job.get('id', ''))
        jobs.append({
            'id': f'pinpoint_{slug}_{job_id}',
            'company': company,
            'title': job.get('title', ''),
            'location': location,
            'url': job.get('url', f'https://{slug}.pinpointhq.com/postings/{job_id}'),
            'board': 'Pinpoint',
            'description': job.get('description', ''),
        })
    return jobs


MULTI_LOCATION_RE = re.compile(r'^\d+ locations$', re.IGNORECASE)


def fetch_workday_detail(cxs_root, path, wd_headers):
    """Fetch a posting's real locations and description (list view hides both)."""
    try:
        resp = requests.get(f'{cxs_root}{path}', headers=wd_headers, timeout=15)
        if resp.status_code != 200:
            return None, ''
        info = resp.json().get('jobPostingInfo', {})
        locations = [info.get('location', '')]
        locations += info.get('additionalLocations', []) or []
        location = '; '.join(dict.fromkeys(l for l in locations if l))
        return location, info.get('jobDescription', '')
    except requests.RequestException:
        return None, ''


def scrape_workday(company, tenant, instance, board, security_company=False):
    if board:
        cxs_root = f'https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}/{board}'
    else:
        cxs_root = f'https://{tenant}.{instance}.myworkdayjobs.com/wday/cxs/{tenant}'
    api_url = f'{cxs_root}/jobs'
    base_url = f'https://{tenant}.{instance}.myworkdayjobs.com'
    wd_headers = {**HEADERS, 'Content-Type': 'application/json',
                  'Accept': 'application/json'}

    search_terms = ['cyber', 'security', 'new grad', 'early career']
    if security_company:
        search_terms += ['graduate', 'associate engineer', 'engineer i']

    jobs = []
    seen_paths = set()
    for term in search_terms:
        offset = 0
        while True:
            payload = {'appliedFacets': {}, 'limit': 20, 'offset': offset,
                       'searchText': term}
            try:
                resp = requests.post(api_url, json=payload, headers=wd_headers,
                                     timeout=15)
                if resp.status_code != 200:
                    print(f'  [{company}] Workday HTTP {resp.status_code} for "{term}"')
                    break
                data = resp.json()
                postings = data.get('jobPostings', [])
                if not postings:
                    break
                for job in postings:
                    path = job.get('externalPath', '')
                    if not path or path in seen_paths:
                        continue
                    seen_paths.add(path)
                    # Job pages 404 without the board segment in the URL.
                    public_root = f'{base_url}/{board}' if board else base_url
                    jobs.append({
                        'id': f'workday_{tenant}_{path}',
                        'company': company,
                        'title': job.get('title', ''),
                        'location': job.get('locationsText', ''),
                        'url': f'{public_root}{path}',
                        'board': 'Workday',
                        '_path': path,
                    })
                total = data.get('total', 0)
                offset += len(postings)
                if offset >= total:
                    break
                time.sleep(0.3)
            except requests.RequestException as e:
                print(f'  [{company}] Workday error for "{term}": {e}')
                break

    # The list view gives no description and hides multi-location postings
    # behind "N Locations". Fetch details for the few title-level candidates
    # so the US filter and clearance detection see real data.
    for job in jobs:
        title = job['title']
        if (is_rejected_title(title) or not is_cyber_title(title, security_company)
                or classify_level(title) is None):
            continue
        needs_locations = MULTI_LOCATION_RE.match(job['location'].strip())
        location, description = fetch_workday_detail(cxs_root, job.pop('_path'), wd_headers)
        if needs_locations and location:
            job['location'] = location
        if description:
            job['description'] = description
        time.sleep(0.3)
    for job in jobs:
        job.pop('_path', None)
    return jobs


def scrape_amazon():
    base_url = 'https://www.amazon.jobs/en/search.json'
    params = {
        'base_query': 'security engineer OR "security analyst" OR cybersecurity',
        'loc_query': 'united states',
        'result_limit': 100,
        'offset': 0,
    }
    jobs = []
    while True:
        resp = requests.get(base_url, params=params, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f'  [Amazon] HTTP {resp.status_code}')
            break
        data = resp.json()
        postings = data.get('jobs', [])
        if not postings:
            break
        for job in postings:
            job_id = str(job.get('id_icims', job.get('id', '')))
            job_path = job.get('job_path', '')
            url = (f'https://www.amazon.jobs{job_path}' if job_path
                   else f'https://www.amazon.jobs/en/jobs/{job_id}')
            jobs.append({
                'id': f'amazon_{job_id}',
                'company': 'Amazon',
                'title': job.get('title', ''),
                'location': job.get('location', ''),
                'url': url,
                'board': 'Amazon Jobs',
                'description': job.get('description', ''),
            })
        total = data.get('hits', 0)
        params['offset'] += len(postings)
        if params['offset'] >= total or len(postings) < params['result_limit']:
            break
        time.sleep(0.5)
    return jobs


def scrape_usajobs():
    """Federal cyber roles for recent grads. Needs USAJOBS_API_KEY + USAJOBS_EMAIL."""
    api_key = os.environ.get('USAJOBS_API_KEY')
    email = os.environ.get('USAJOBS_EMAIL', 'cyber-jobs-scraper@example.com')
    if not api_key:
        return []
    headers = {
        'Host': 'data.usajobs.gov',
        'User-Agent': email,
        'Authorization-Key': api_key,
    }
    jobs = []
    page = 1
    while True:
        params = {
            'Keyword': 'cybersecurity',
            'HiringPath': 'graduates',
            'ResultsPerPage': 250,
            'Page': page,
        }
        try:
            resp = requests.get('https://data.usajobs.gov/api/search',
                                params=params, headers=headers, timeout=20)
            if resp.status_code != 200:
                print(f'  [USAJOBS] HTTP {resp.status_code}')
                break
            result = resp.json().get('SearchResult', {})
            items = result.get('SearchResultItems', [])
            if not items:
                break
            for item in items:
                d = item.get('MatchedObjectDescriptor', {})
                job_id = item.get('MatchedObjectId', '')
                locations = d.get('PositionLocation', [])
                loc = locations[0].get('LocationName', '') if locations else ''
                jobs.append({
                    'id': f'usajobs_{job_id}',
                    'company': d.get('OrganizationName', 'US Federal Government'),
                    'title': d.get('PositionTitle', ''),
                    'location': loc,
                    'url': d.get('PositionURI', ''),
                    'board': 'USAJOBS',
                })
            if page >= int(result.get('UserArea', {}).get('NumberOfPages', 1)):
                break
            page += 1
            time.sleep(0.5)
        except requests.RequestException as e:
            print(f'  [USAJOBS] Error: {e}')
            break
    return jobs


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

STRIP_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'utm_id', 'source', 'src', 'ref', 'referer', 'lever-source',
    'lever-origin', 'gh_src',
}


def normalize_url(url):
    try:
        p = urlparse(url.strip())
        params = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items()
                  if k.lower() not in STRIP_PARAMS}
        u = urlunparse(p._replace(
            scheme=p.scheme.lower(),
            netloc=p.netloc.lower(),
            path=p.path.rstrip('/'),
            query=urlencode(sorted(params.items()), doseq=True),
            fragment='',
        ))
        return re.sub(r'(myworkdayjobs\.com)/en-[A-Z]{2}/([^/]+/)?job/', r'\1/job/', u)
    except Exception:
        return url


def load_seen_jobs():
    if SEEN_JOBS_FILE.exists():
        with open(SEEN_JOBS_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen_jobs(seen):
    SEEN_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEN_JOBS_FILE, 'w') as f:
        json.dump(sorted(seen), f, indent=2)


def load_listings():
    if LISTINGS_FILE.exists():
        with open(LISTINGS_FILE) as f:
            return json.load(f)
    return []


def save_listings(listings):
    tmp = LISTINGS_FILE.with_suffix('.tmp')
    with open(tmp, 'w') as f:
        json.dump(listings, f, indent=2)
    tmp.replace(LISTINGS_FILE)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        with open('companies.yml') as f:
            config = yaml.safe_load(f) or {}
    except Exception as e:
        print(f'ERROR: Failed to load companies.yml: {e}')
        sys.exit(1)

    seen = load_seen_jobs()
    raw_jobs = []
    sec_flags = {}

    def run_scraper(label, fn, *args, security_company=False):
        print(f'Checking {label}...')
        try:
            found = fn(*args)
            for job in found:
                sec_flags[job['id']] = security_company
            raw_jobs.extend(found)
        except Exception as e:
            print(f'  [{label}] Scraper crashed: {e}')
        time.sleep(0.4)

    simple_boards = {
        'greenhouse': scrape_greenhouse,
        'lever': scrape_lever,
        'ashby': scrape_ashby,
        'smartrecruiters': scrape_smartrecruiters,
        'workable': scrape_workable,
        'recruitee': scrape_recruitee,
        'pinpoint': scrape_pinpoint,
    }
    for board, scraper in simple_boards.items():
        for entry in config.get(board, []) or []:
            run_scraper(f'{entry["name"]} ({board}/{entry["slug"]})',
                        scraper, entry['name'], entry['slug'],
                        security_company=entry.get('security_company', False))

    for entry in config.get('workday', []) or []:
        run_scraper(
            f'{entry["name"]} (workday/{entry["tenant"]})',
            scrape_workday, entry['name'], entry['tenant'], entry['instance'],
            entry.get('board', ''), entry.get('security_company', False),
            security_company=entry.get('security_company', False),
        )

    run_scraper('Amazon (amazon.jobs)', scrape_amazon)
    run_scraper('USAJOBS (data.usajobs.gov)', scrape_usajobs)

    print(f'\nScraped {len(raw_jobs)} raw postings; filtering...')

    listings = load_listings()

    # Let classifier improvements reach already-scraped listings. Title-only:
    # descriptions are not stored, so None means "no title signal" and the
    # stored (possibly description-derived) type is kept. Community rows
    # reflect a maintainer's judgment — leave them alone.
    reclassified = 0
    for entry in listings:
        if entry.get('source') == 'Community':
            continue
        level = classify_level(entry['role'])
        if level and level != entry.get('type'):
            print(f'  RECLASSIFY [{entry.get("type")} -> {level}] '
                  f'{entry["company"]} — {entry["role"]}')
            entry['type'] = level
            reclassified += 1

    existing_urls = {normalize_url(e.get('url', '')) for e in listings}
    today = datetime.now().strftime('%Y-%m-%d')
    added = 0

    for job in raw_jobs:
        if job['id'] in seen:
            continue
        verdict = evaluate_job(
            job.get('title', ''), job.get('location', ''),
            job.get('description', ''), sec_flags.get(job['id'], False),
        )
        if verdict is None:
            continue
        level, category = verdict
        seen.add(job['id'])

        url = job.get('url', '')
        if not url or normalize_url(url) in existing_urls:
            continue
        existing_urls.add(normalize_url(url))

        listings.append({
            'company': job['company'],
            'role': job['title'].strip(),
            'location': normalize_location(job.get('location', '')),
            'type': level,
            'category': category,
            'clearance': requires_clearance(job.get('title', ''),
                                            job.get('description', '')),
            'url': url,
            'source': job.get('board', ''),
            'date_added': today,
        })
        added += 1
        print(f'  NEW [{level}] {job["company"]} — {job["title"]} @ {job.get("location", "")}')

    print(f'\nAdded {added} new listing(s), reclassified {reclassified}')

    if added or reclassified:
        save_listings(listings)
        result = subprocess.run(
            [sys.executable, '.github/scripts/rebuild_readme.py'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f'rebuild_readme.py failed:\n{result.stderr}')
            sys.exit(1)
        print(result.stdout.strip())

    save_seen_jobs(seen)
    print('Done')


if __name__ == '__main__':
    main()
