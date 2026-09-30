#!/usr/bin/env python3
"""Pure classification + location logic for the cyber-jobs scraper.

Deliberately dependency-free (stdlib only): no `requests`, no `yaml`. That
keeps test_classification.py runnable with a bare interpreter and lets the
scraper, the community-submission scripts, and the tests share one source of
truth for the taxonomy.

Pipeline per job (see evaluate_job, and judge_job for the deciding gate):
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
    r'\bexecutive\b', r'\bexpert\b',
    r'\bsme\b', r'\bsubject matter expert\b',
    # Management titles the bare 'lead'/'manager' terms miss: "Tier II SOC
    # Supervisor", "Data & AI Governance Leader, MD". 'leader' is word-bounded
    # so "Cyber Leadership Development Program" (a new-grad cohort) survives.
    r'\bsupervisor\b', r'\bleader\b',
]
# Seniority words inside a level name or an org unit, not the role: Anthropic
# 'Member of Technical Staff, Security - New Grad', MIT Lincoln Laboratory
# 'Cyber Security Researcher - Associate Staff', 'Intern, Office of the Chief
# Information Security Officer'. Removed before SENIORITY_REJECT runs, so a
# plain 'Staff Security Engineer' or CISO title still rejects.
SENIORITY_EXEMPT_RE = re.compile(
    r'\bmember of (?:the )?technical staff\b|\bassociate staff\b'
    r'|\boffice of the chief [a-z ]{0,40}?officer\b')

# 'Architect' usually marks a senior IC, but named early-career cohorts run
# "Security Architect - New College Grad 2026" reqs, and presales teams hire
# Tenable 'Associate Services Architect'. It rejects only when the title
# carries no new-grad, intern or early-career word — the other seniority terms
# above still hard-reject even inside a cohort title. A bare I/II is not enough:
# Honeywell 'Cyber Security Architect/Engineer II' reads as a mid-level req.
ARCHITECT_RE = re.compile(r'\barchitect\b')

# Senior levels are rejected only when III/IV/3/4 qualifies a role/level noun,
# so "SOC Analyst III" and "Tier 3 Responder" are rejected but "Layer 3 Network
# Analyst I", "PCI DSS 4.0 Compliance Analyst", and "Cyber IV&V Engineer" pass.
#
# The level can also follow a separator ("Analyst - III", "Analyst (IV)") and
# sit in the L-scale ("L3 SOC Analyst"). A separated numeral has to end the
# title or a bracketed part, so "Analyst - 4 days onsite" is not a level.
_LEVEL_NOUNS = (r'analyst|engineer|consultant|specialist|administrator|technician|'
                r'developer|tester|responder|investigator|hunter|technologist')
_LEVEL_TAIL = r'\s*(?=$|[)\-–,/|(])'
LEVELED_SENIOR_RE = re.compile(
    r'\b(?:' + _LEVEL_NOUNS + r'|scientist|researcher|officer|tier|level)'
    r'\s+(?:iii|iv|3|4)\b'
    r'|\b(?:' + _LEVEL_NOUNS + r'|officer)\s*[-–,(]\s*(?:level\s+)?(?:iii|iv|3|4)'
    + _LEVEL_TAIL + r'|\bl[34]\b'
)

# Internships and co-ops get their own level. Title signals only — job
# descriptions mention "our internship program" as boilerplate far too often
# to be trusted. Word boundaries keep 'intern' from matching 'internal'.
INTERN_TITLE_RES = [re.compile(p) for p in (
    # Plural 'Internships' names a program page (MITRE 'Internships in
    # Cybersecurity and Information Security'). Bare 'intern' stays singular:
    # 'For 2026 Interns Only' titles are return offers.
    r'\bintern\b', r'\binternships?\b', r'\bco-?op\b',
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
    'security guard', 'physical security', 'public safety',
    'executive protection', 'transportation security',
    'security screener', 'campus safety', 'alarm technician',
    # Walmart's store loss-prevention job, '(CAN) Asset Protection Associate
    # (MUST HAVE SECURITY LICENSE)'; the licence is a guard licence.
    'asset protection', 'security license', 'security licence',
    # Cleared-facility security functions (FSO/NISPOM work, clearance
    # adjudication, guard forces) carry the word "security" but are not cyber.
    'industrial security', 'personnel security', 'protective services',
    'fire operation',
    'nuclear safeguards',  # 'safeguards' alone is an AI-safety signal
    # Camera and door-badge presales: Motorola Solutions 'Pre-Sales Solutions
    # Engineer - Video Security & Access Control'.
    'video security',
    'account executive', 'account manager', 'marketing',
    'recruiter', 'talent acquisition',
    'people technology', 'people operations', 'channel systems',
    'customer success', 'business development', 'partner manager',
    # JPMorgan 'Finance & Business Management Associate - Cybersecurity &
    # Technology Controls' runs the cyber org's budget, not its security work.
    'business management',
    'accountant', 'accounting', 'financial analyst', 'fp&a',
    # Finance-audit work; "SOX/SOC" in an audit title is SOC 1/2 reporting, not
    # a security operations center.
    'internal audit', 'sox',
    'attorney', 'counsel', 'paralegal', 'executive assistant',
    'administrative assistant', 'facilities',
    'copywriter', 'community manager', 'social media',
    'hackathon', 'general interest', 'talent community', 'talent network',
    # Hardware/manufacturing — "SoC" (system-on-chip) titles are not SOC roles.
    'asic', 'rtl design', 'soc design', 'soc verification', 'soc architect',
    'chip design', 'tapeout', 'manufacturing engineer',
    'process engineer', 'mechanical engineer', 'electrical engineer',
    'chemical engineer', 'industrial engineer', 'civil engineer',
    'photolithography', 'metrology',
    # Reqs a student cannot apply to: return offers for current interns
    # (Walmart '2026 Intern Conversion: 2027 Return Intern Cybersecurity'),
    # DoD SkillBridge slots for active-duty members (Blackpoint Cyber), and
    # dated recruiting events (JPMorgan 'Hiring Event - Security Architecture
    # & Engineering - Sep 24-25th 2026', whose event year read as a cohort).
    'intern conversion', 'return intern', 'returning intern', 'skillbridge',
    'active duty', 'hiring event',
    # Sales, support and design roles that security-company 'analyst' and
    # 'developer' titles let in: Proofpoint 'Deals Desk Analyst II', Okta
    # 'Developer Support Associate (New Grad)', DigiCert 'Associate
    # Authentication Analyst' (certificate validation), Lakera 'Enterprise
    # Account Exeuctive' (the typo slips past 'account executive'), Anthropic
    # 'Product Designer, Safeguards'.
    'deals desk', 'deal desk', 'developer support', 'authentication analyst',
    'enterprise account', 'product designer',
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


# These terms would also reject genuine cyber roles, so they carry a guard
# instead of sitting in the plain list:
#   'loss prevention'    — retail LP, but "Data Loss Prevention (DLP) Analyst"
#                          is a core security control.
#   'safety and security' — a guard-force function, but "AI Safety and Security
#                          Engineering" is an AI-lab security team.
#   'sales'              — a quota-carrying seller, but a sales engineer is the
#                          technical presales role: ESET 'Sales Engineer I',
#                          Palo Alto Networks 'Sales Engineer - Intern'. Only a
#                          cyber keyword or a security_company flag admits one.
GUARDED_FUNCTION_REJECTS = [
    r'\bsales\b(?!\s+(?:solutions?\s+)?engineer)',
    r'(?<!data )\bloss prevention\b',
    # Hourly shift posts: Walmart 'Asset Protection / Security Associate,
    # Manufacturing (Tuesday-Friday, 11:00am-9:30pm) - $21.30/hr.' and its
    # overnight twin are plant guards. 'Overnight' alone would also catch SOC
    # night shifts, so the pay rate and the manufacturing site carry it.
    r'\$\d+(?:\.\d+)?\s*/\s*(?:hr|hour)\b',
    r'\bsecurity associate, manufacturing\b',
    r'(?<!ai )\bsafety and security\b',
]

FUNCTION_REJECT_RE = re.compile(
    '|'.join([_term_regex(t) for t in FUNCTION_REJECT] + GUARDED_FUNCTION_REJECTS))

# Departments that name the team a security role supports as often as the job
# itself: 'Cybersecurity Analyst - Finance Systems', 'Security Engineer I,
# Payments & Billing', 'Supply Chain Cyber Risk Analyst I', 'Silicon Security
# Researcher'. They reject only a title that does not also name security work
# (see _names_security_work), so 'Treasury Operations Analyst' and 'Supply
# Chain Analyst I' at a security company stay out. Sales (bar the sales
# engineer) and marketing are never security work and stay in FUNCTION_REJECT,
# as do 'recruiter' and 'talent acquisition', which name the job rather than a
# team.
#
# Support, recruiting and workplace teams run their own security engineers:
# 'Security Engineer I, Customer Support Tools', 'Security Engineer,
# Recruiting Systems', 'Security Engineer I, Workplace Technology'. As hard
# rejects these terms dropped them, while 'Customer Support Engineer I',
# 'Recruiting Coordinator' and 'Workplace Services Intern' still fail here.
DEPARTMENT_REJECT = [
    'finance', 'treasury', 'revenue', 'billing', 'human resources', 'payroll',
    'procurement', 'silicon', 'customer support', 'recruiting', 'workplace',
]
DEPARTMENT_REJECT_RE = re.compile(
    '|'.join([_term_regex(t) for t in DEPARTMENT_REJECT]
             + [r'\bsupply chain\b(?! security)']))

# 'Security' senses that are not information security: 'Social Security
# Intern', 'Food Security Analyst I', 'Homeland Security Intern', 'Campus
# Security Intern', 'Security Forces Intern', 'Security Badging Intern'.
# Stripped before the cyber-keyword scan, as 'national security' is, so a
# title needs its own cyber term ('Cybersecurity Intern, Homeland Security').
# A corporate security engineer secures the company's own IT, and 'Corporate
# Security Engineer I - Workplace' was rejected as a guard-force title.
NON_CYBER_SECURITY_RE = re.compile(
    r'\b(?:social|food|energy|border|homeland|campus|event)\s+security\b'
    r'|\bcorporate\s+security\b(?!\s+engineer)'
    r'|\bsecurity\s+(?:forces|badging)\b')

# A bare 'security' before these role nouns is usually a facility, badging or
# guard job: RTX 'Security Specialist II' (COMSEC, NISPOM), SAIC 'Security
# Associate', KBR 'Associate Security Specialist', GDIT 'Security Specialist -
# Administrative (Junior)', 'Security Access Control Technician I'. Such a
# title needs a second cyber term ('Cyber Security Specialist I', 'IT Security
# Specialist I') or a technical role noun ('Associate Security Analyst').
WEAK_SECURITY_ROLE_RE = re.compile(
    r'\b(?:specialist|technician|associate|assistant|coordinator|officer)s?\b')
TECH_SECURITY_ROLE_RE = re.compile(
    r'\b(?:analyst|engineer|engineering|consultant|administrator|developer|tester|'
    r'responder|investigator|researcher|scientist|architect|auditor|hunter)s?\b')
# The role noun has to follow the word 'security' closely for a department
# title to count as security work: 'Silicon Security Researcher' does,
# 'Revenue Platform Engineer, Security' does not.
SECURITY_ROLE_RE = re.compile(
    r'\bsecurity\s+(?:[a-z]+\s+)?(?:analyst|engineer|consultant|administrator|'
    r'developer|tester|responder|investigator|researcher|scientist|architect|'
    r'auditor|hunter)s?\b')
# A word that makes 'security' information security by itself.
INFOSEC_QUALIFIER_RE = re.compile(
    r'\b(?:information|info|it|is|systems?|network|cloud|application|app|data|'
    r'computer|product|software|endpoint|email|web|platform|infrastructure|'
    r'offensive|defensive|digital|mobile|database|identity|ot|ics|iot)\s+security\b')

# "Security Officer" is usually a guard; keep it only when clearly infosec.
SECURITY_OFFICER_RE = re.compile(r'\bsecurity officer\b')
INFOSEC_OFFICER_HINTS = ['information security', 'cyber', 'ciso', 'iso ']
# ISSO wording the substring hints miss: Draper 'IS Security Officer 1', CACI
# 'Information System Security Officer - Jr.'. Word-bounded, since 'isso' sits
# inside 'Missouri'.
ISSO_HINT_RE = re.compile(
    r'\b(?:information systems? security|is security|isso|issm)\b')

# A program analyst runs budgets and schedules (Okta 'Associate Program Analyst
# (New Grad)', let in by the security-company 'analyst' allowance), but a
# gov-contractor 'Cybersecurity Program Analyst' is GRC work, so the term
# rejects only a title with no cyber keyword.
PROGRAM_ANALYST_RE = re.compile(r'\bprogram analyst\b')

# A title containing any of these is a cybersecurity role.
CYBER_KEYWORDS = [
    'security', 'cyber', 'infosec', 'information assurance',
    'penetration test', 'pentest', 'red team', 'blue team', 'purple team',
    'threat', 'incident response', 'forensic', 'malware', 'vulnerability',
    # 'Incident Responder I' misses 'incident response', and AeroVironment's
    # 'Computer Network Defense Analyst (CNDA) Level 1' carries no other term.
    'incident responder', 'network defense',
    'appsec', 'exploit', 'reverse engineer', 'cryptograph', 'grc',
    # DLP titles carry neither 'security' nor 'cyber' ("Data Loss Prevention
    # (DLP) Analyst"); 'insider threat' already matches on 'threat'.
    'data loss prevention',
    'siem', 'detection engineer', 'detection and response',
    'devsecops', 'identity and access', 'zero trust', 'privacy engineer',
    # 'Identity Access Management Analyst I', 'Governance, Risk and Compliance
    # Analyst I'. '&' is read as 'and' before the scan.
    'identity access management', 'governance, risk',
    # Spellings the terms above miss: Leidos 'Junior Cloud/SecDevOps Engineer',
    # BlackRock 'Access & Identity Management Engineer, Associate'.
    'secdevops', 'access and identity', 'identity management', 'privileged access',
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

# 'SOC 1', 'SOC 2' and 'SOC Reporting' are audit reports, not a security
# operations center: 'SOC 1 Analyst I', 'SOC 1 Audit Associate'.
SOC_AUDIT_GUARD = r'(?![\s-]*[12]\b)(?!\s+reporting\b)'

# Short acronyms need word boundaries ('soc' is inside 'associate'), and
# 'SoC' must not match system-on-chip hardware titles. Qualcomm lists SoC among
# chip disciplines ("Hardware (CPU, GPU, SoC, Digital Design, DV) Engineering
# Internship") and as a prefix ("SoC Performance Architect"), so a comma-listed
# neighbour counts as well as a following word.
CYBER_REGEXES = [re.compile(p) for p in
                 (r'(?<!pu, )\bsoc\b(?![\s,/-]+(asic|design|digital design|verification|'
                  r'rtl|silicon|power|performance|hardware))' + SOC_AUDIT_GUARD,
                  # SOC 2 is the security audit report, so its compliance work
                  # is GRC; SOC 1 covers financial controls and stays out.
                  r'\bsoc[\s-]*2\s+compliance\b',
                  r'\bcnd\b', r'\bcno\b', r'\bdfir\b', r'\bir analyst\b',
                  r'\bdlp\b', r'\biam\b', r'\bcsirt\b')]

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
    # Presales and services roles at a security vendor run its products for
    # customers: Palo Alto Networks 'Solutions Consultant 1' and 'Domain
    # Consultant 2 - NetSec', Tenable 'Associate Services Architect',
    # CrowdStrike 'Readiness Services Consultant'.
    'solutions consultant', 'solution consultant', 'domain consultant',
    'services consultant', 'technical consultant', 'architect',
]

# A software team named for security work puts an engineer on a security team
# even when the title never says 'security': Microsoft 'Software Engineer II -
# Compromise & Fraud Protection', Palantir 'Privacy & Civil Liberties Engineer
# - New Grad'. The team word counts only beside an engineering noun, so fraud
# call-centre reqs (JPMorgan 'Credit Card Fraud Specialist I') and content
# moderators (Accenture 'Trust & Safety New Associate') stay out.
SECURITY_TEAM_RE = re.compile(
    r'\b(?:identity|authentication|privacy|trust (?:and|&) safety|anti-?abuse|'
    r'fraud protection|compromise)\b')
ENGINEERING_ROLE_RE = re.compile(r'\b(?:engineer|engineering|developer)\b')

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
    # Vanguard 'Technology Leadership Program - Risk & Security' is "a
    # two-year rotational development experience designed for graduating
    # students"; MITRE runs a 'Cyber New Professionals Program'.
    'technology leadership program', 'new professionals program',
]


def _cohort_years(span=2, today=None):
    """Hiring-cycle years accepted in titles: current year through current+span.

    Computed relative to today so cohort/new-grad detection keeps working past
    2028 without a code edit (the old hardcoded 2026-2028 window was a silent
    time-bomb). `today` (a date) is injectable so the rollover can be tested.
    """
    year = (today or datetime.now().date()).year
    return [year + i for i in range(span + 1)]


def _cohort_year_re(span=2, today=None):
    # Optional leading digit absorbs Northrop's year typos like "22026".
    years = _cohort_years(span, today)
    century = str(years[0])[:2]
    yrs = '|'.join(str(y)[2:] for y in years)
    return re.compile(rf'\b\d?{century}(?:{yrs})\b')


def _newgrad_year_signals(today=None):
    return [phrase for y in _cohort_years(today=today)
            for phrase in (f'class of {y}', f'{y} grad')]


# Titles carrying a target start year ("2026 Associate Cyber Software
# Engineer") are campus-cohort reqs.
COHORT_YEAR_RE = _cohort_year_re()

# "Class of 2026", "2027 grad" — generated from the same rolling window so the
# year phrases never go stale.
NEWGRAD_YEAR_SIGNALS = _newgrad_year_signals()

EARLYCAREER_SIGNALS = [
    'entry level', 'entry-level', 'early career', 'junior', 'apprentice',
    'associate', 'tier 1', 'tier i', 'tier 2', 'tier ii', 'level 1', 'level 2',
    'early in career',
    # 'Jr. Security Analyst', 'Jr SOC Analyst'. The badging role that kept 'jr'
    # out, Leidos 'Jr. Security Specialist', now fails WEAK_SECURITY_ROLE_RE.
    'jr',
    # 'Security Analyst, Level I', 'L1 SOC Analyst', 'SOC Analyst L2'.
    'level i', 'level ii', 'l1', 'l2',
    # A cohort, not the senior title 'Fellow' that SENIORITY_REJECT holds. It
    # keeps Anthropic 'Fellows Program, AI Safety & Security' once AI flat
    # titles need early-career evidence.
    'fellows program',
]
# Word-bounded so 'level 1' doesn't match 'level 10' and 'associate' doesn't
# match 'associated'. 'tier ii' is listed explicitly so it isn't lost when
# 'tier i' stops substring-matching it.
EARLYCAREER_RE = re.compile('|'.join(_term_regex(t) for t in EARLYCAREER_SIGNALS))

# "Analyst I", "Engineer 1", "SOC Analyst II" — I/II count as early career,
# III+ is rejected by LEVELED_SENIOR_RE above. 'Technologist' is Travelers'
# level noun ('Cybersecurity Ops Technologist I'). 'Officer' counts at level 1
# only: Draper's 'Information Security Officer 2' asks for 3-5 years.
# Separated forms follow the same rule as LEVELED_SENIOR_RE: 'Security Analyst
# - I', 'Security Analyst (I)'. 'Hunter' is a level noun ('Cyber Threat
# Hunter I').
LEVELED_TITLE_RE = re.compile(
    r'\b(?:(?:' + _LEVEL_NOUNS + r')\s+(?:i|ii|1|2)|officer\s+(?:i|1))\b'
    r'|\b(?:' + _LEVEL_NOUNS + r')\s*[-–,(]\s*(?:level\s+)?(?:i|ii|1|2)' + _LEVEL_TAIL
)

# A req posted at several levels can be filled at the top one: Northrop
# 'Classified Cybersecurity Analyst 2/3 - Secret', 'Cyber Systems Engineer
# (Level 2 or 3)', 'SOC Analyst Tier 1-3', 'Cyber Analyst I/II/III'. The I/II
# marker still levels the title, but judge_job accepts it only on a
# description that passes the experience gate. A hyphen joins levels only
# unspaced, so "Analyst II - 3 days onsite" is not a span, and V is no level,
# so "Engineer - V&V" is not one either.
_LEVEL_TOKEN = r'(?:iii|ii|iv|i|[1-9])'
MULTI_LEVEL_RE = re.compile(
    r'\b(?:(?:' + _LEVEL_NOUNS + r'|scientist|researcher|officer)\s*[-–(]?\s*'
    r'(?:level\s+|tier\s+)?|(?:level|tier)\s+)'
    r'(' + _LEVEL_TOKEN + r'(?:(?:\s*/\s*|[-–]|\s+(?:or|to|through|and)\s+|\s*&\s*)'
    + _LEVEL_TOKEN + r')+)\b')
_SENIOR_LEVEL_TOKEN_RE = re.compile(r'\b(?:iii|iv|[3-9])\b')


def _spans_senior_level(t):
    return any(_SENIOR_LEVEL_TOKEN_RE.search(m.group(1)) for m in MULTI_LEVEL_RE.finditer(t))

# Strong phrases in a job description that mark a role as early career.
# Deliberately tight — descriptions are noisy (boilerplate like "from early
# career to executive" would false-positive on looser phrases).
DESCRIPTION_SIGNALS = [
    '0-2 years', '0–2 years', '0 to 2 years', 'no prior experience',
    'entry level role', 'entry-level role', 'entry level position',
    'entry-level position', 'entry level opportunity',
]

# A description levels a flat title as new grad only when it addresses the
# reader: "open to recent graduates", "designed for new graduates", "new grad
# role". A bare mention does not: "Our teams include everyone from recent
# graduates to industry veterans" put a flat 'Security Engineer' on the
# new-grad table.
DESCRIPTION_NEWGRAD_RE = re.compile(
    r'\b(?:for|open to|aimed at|targeting|welcomes?)\s+(?:recent|new)\s+'
    r'(?:college\s+|university\s+)?grad(?:uate)?s?\b'
    r'|\bnew[- ]grad(?:uate)?\s+(?:role|position|program|opportunity|hire)s?\b'
    r'|\b(?:recent|new)\s+grad(?:uate)?s?\s+(?:are\s+)?(?:encouraged|welcome)\b')

# A description stating a low experience ceiling marks an early-career role
# even when the title carries no level marker (used, gated, at security_company
# employers to recover recall on flat "Security Engineer" titles).
#
# Only the ceilings that open at zero may outrank a larger stated floor in
# exceeds_experience_cap. A "1-2 years" band sits beside real floors: Tenable's
# 'AI Information Security Engineer' asks for "5 or more years ... with at
# least 1-2 years focused on securing AI/ML systems".
ZERO_ANCHORED_YOE_RES = [re.compile(p) for p in (
    r'\b0\s*[-–]\s*2\s*years?\b', r'\b0 to 2 years?\b',
    r'\bup to 2 years?\b', r'\bless than 2 years?\b',
    r'\bminimum of 0 years?\b', r'\bno (?:prior )?experience (?:is )?required\b',
    # A degreed route with no experience bar: SAIC's "Bachelor's and 0
    # years", Northrop's "Master's degree with 0 years".
    r'\b(?:with|and)\s+0\s+years?\b',
)]
MAX_YOE_RES = ZERO_ANCHORED_YOE_RES + [re.compile(p) for p in (
    r'\b1\s*[-–]\s*2\s*years?\b', r'\b1 to 2 years?\b',
)]

CLEARANCE_SIGNALS = [
    'clearance', 'ts/sci', 'top secret', 'polygraph', 'us citizen',
    'u.s. citizen', 'us citizenship', 'u.s. citizenship', 'public trust',
    'secret-level',
    # Spellings the substrings above missed: 'TS / SCI', 'Top-Secret', 'Must be
    # a United States citizen'.
    'ts / sci', 'top-secret', 'united states citizen',
]
# Phrases that name a clearance or citizenship only to waive it. CACI prints
# "Clearance Level Must Currently Possess: None" on every req, so these are
# removed before the scan. The form field must not reach past its own value:
# "... Possess: None Clearance Level Must Be Able to Obtain: Secret" still flags.
CLEARANCE_NEGATION_RE = re.compile(
    r"\b(?:u\.?s\.? )?(?:clearance|polygraph|citizenship)(?: [a-z/']+){0,6}? ?: ?"
    r'(?:none|no|n/a|not required|not applicable)\b'
    r'|\bno (?:active |security |government )*(?:clearance|polygraph)s? '
    r'(?:is |are )?(?:required|needed|necessary)\b'
    r'|\b(?:does|do|will) not require (?:a |an |any )?(?:active |security |government )*'
    r'(?:clearance|polygraph|(?:u\.?s\.? )?citizenship)\b'
    r'|\b(?:(?:u\.?s\.? )?citizenship|clearance|polygraph)s? (?:is |are )?not '
    r'(?:required|needed|necessary)\b')
# ITAR/EAR export control restricts a role to a "U.S. Person" without asking for
# a clearance: Amazon 'Security Engineer I, Threat Hunting' had no 🇺🇸. These
# are word-bounded, unlike the substrings above, so "US personnel" is not one.
CLEARANCE_WORD_SIGNALS = ['u.s. person', 'us person', 'u.s. persons', 'us persons']
CLEARANCE_WORD_RE = re.compile(
    '|'.join(_term_regex(t) for t in CLEARANCE_WORD_SIGNALS))

# Ordered buckets; first matching regex wins. 'privacy' lives in Security
# Engineering (not GRC) so a "Security and Privacy" research role keeps a
# security tag instead of being read as governance/risk.
CATEGORY_RULES = [
    ('AI Security & Safety', r'ai security|ml security|llm security|'
                             r'model security|genai security|ai safety|'
                             r'ai risk|ai governance|responsible ai|'
                             r'trustworthy ai|ai red team|'
                             # Bare 'adversarial' and 'alignment' put 'Security
                             # Engineer, Adversary & Adversarial Emulation' and
                             # 'Security Engineer, Alignment Tooling' on the
                             # AI flat-title path, so each needs an ML object.
                             r'adversarial (?:machine learning|ml|robustness|ai|'
                             r'examples?|attacks? on (?:ai|ml|models?))\b|'
                             r'ai alignment|alignment (?:science|research|team)|'
                             r'model alignment|safeguards'),
    ('Offensive Security', r'penetration|pentest|red team|offensive|exploit|'
                           r'vulnerability research|purple team|'
                           # Computer network operations: Nightwing 'Junior
                           # CNO Developer'.
                           r'\bcno\b'),
    ('SOC & Detection', r'\bsoc\b' + SOC_AUDIT_GUARD
                        + r'|security operations|detection|blue team|'
                        r'incident response|threat hunt|csirt|siem|'
                        r'cyber defense|defensive cyber|triage|'
                        # Managed detection and network-defense titles that
                        # fell to the catch-alls: CrowdStrike 'Analyst I,
                        # Falcon Complete GovCloud', Nightwing 'Cyber Network
                        # Defense Analyst II', Amentum 'Cyber Ops Analyst II',
                        # 'Incident Responder I'.
                        r'\bmdr\b|falcon complete|network defense|cyber ops\b|'
                        r'responder'),
    ('Threat Intelligence', r'threat intel|\bcti\b|intelligence analyst|'
                            r'threat research|adversary|'
                            # JPMorgan 'Cyber Intelligence Associate', Recorded
                            # Future 'Fraud Analyst'.
                            r'cyber intelligence|\bfraud analyst'),
    ('Forensics & IR', r'forensic|\bdfir\b|malware analy|reverse engineer'),
    ('AppSec & ProdSec', r'application security|product security|appsec|'
                         r'secure code|devsecops|secdevops|software security'),
    ('Cloud & Infra Security', r'cloud security|infrastructure security|'
                               r'network security|platform security|'
                               r'systems security'),
    ('Identity & IAM', r'\biam\b|identity|access management|zero trust|'
                       r'authentication|privileged access'),
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

# US territories and commonwealths. A posting in San Juan or Hagatna is a US
# posting — its residents are US citizens/nationals and no visa sponsorship is
# involved — but with only the 50 states + DC recognized, every one of them was
# filtered out as foreign (RTX's Aguadilla, PR reqs, federal Pathways roles in
# Guam). 'Virgin Islands' maps to VI only in its US spelling so a British
# Virgin Islands posting is not claimed as domestic.
US_TERRITORY_ABBRS = {
    'puerto rico': 'PR', 'guam': 'GU', 'american samoa': 'AS',
    'u.s. virgin islands': 'VI', 'us virgin islands': 'VI',
    'united states virgin islands': 'VI',
    'northern mariana islands': 'MP',
    'commonwealth of the northern mariana islands': 'MP',
}
US_STATE_ABBRS.update(US_TERRITORY_ABBRS)

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
    # Metro shorthands ATSs use in place of a city: "Remote - SF Bay Area",
    # "Remote - NYC", "US Remote (New England)".
    'nyc', 'bay area', 'socal', 'new england',
]

NON_US_SUBSTRINGS = [
    'canada', 'toronto', 'vancouver', 'montreal', 'ottawa', 'calgary',
    'waterloo', 'ontario', 'british columbia', 'quebec',
    'london', 'united kingdom', ' uk', '(uk)', 'u.k.', 'scotland',
    'ireland', 'dublin', 'belfast',
    'germany', 'berlin', 'munich', 'frankfurt',
    'france', 'paris', 'netherlands', 'amsterdam', 'belgium', 'brussels',
    'spain', 'madrid', 'barcelona', 'portugal', 'lisbon', 'italy', 'milan',
    'poland', 'warsaw', 'krakow', 'czech', 'prague', 'romania', 'bucharest',
    'sweden', 'stockholm', 'norway', 'oslo', 'denmark', 'copenhagen',
    'finland', 'helsinki', 'switzerland', 'zurich', 'austria', 'vienna',
    'estonia', 'tallinn', 'hungary', 'budapest', 'greece', 'athens',
    'israel', 'tel aviv', 'jerusalem',
    # Named so the country Georgia is not read as the US state.
    'tbilisi',
    'india', 'bangalore', 'bengaluru', 'hyderabad', 'pune', 'mumbai',
    'delhi', 'chennai', 'noida', 'gurgaon', 'gurugram', 'goa',
    # State names, so 'Mysuru, Karnataka, IN' does not read as Indiana.
    'karnataka', 'tamil nadu', 'maharashtra', 'telangana', 'kerala',
    'haryana', 'uttar pradesh', 'west bengal', 'gujarat', 'andhra pradesh',
    'singapore', 'japan', 'tokyo', 'korea', 'seoul', 'china', 'beijing',
    'shanghai', 'hong kong', 'taiwan', 'taipei', 'philippines', 'manila',
    'vietnam', 'malaysia', 'indonesia', 'jakarta', 'thailand', 'bangkok',
    'australia', 'sydney', 'melbourne', 'brisbane', 'perth', 'new zealand',
    'auckland', 'western australia', 'new south wales', 'queensland',
    # Continents a remote scope names: "Remote (Europe)", "Remote (Asia)".
    'europe', 'asia', 'latin america',
    'mexico city', ', mexico', 'brazil', 'sao paulo', 'argentina',
    'buenos aires', 'colombia', 'bogota', 'chile', 'santiago', 'costa rica',
    'peru', 'uruguay',
    # Named explicitly so it is rejected by rule rather than by the absence of
    # a US signal — 'virgin islands' alone resolves to the US territory.
    'british virgin islands',
    'dubai', 'uae', 'saudi', 'riyadh', 'qatar', 'egypt', 'cairo',
    'nigeria', 'lagos', 'south africa', 'kenya', 'nairobi',
    'emea', 'apac', 'latam',
]

# 'england' carries a lookbehind rather than sitting in the list above: the
# plain term also matched "US Remote (New England)" and rejected a domestic
# role as British.
ENGLAND_RE = r'(?<!new )\bengland\b'
# The same lookbehind for "Remote (Mexico)", which ', mexico' missed, without
# claiming "Albuquerque, New Mexico".
MEXICO_RE = r'(?<!new )\bmexico\b'
NON_US_RE = re.compile('|'.join([_term_regex(t) for t in NON_US_SUBSTRINGS]
                                + [ENGLAND_RE, MEXICO_RE]))

REGION_CODE_RE = re.compile(r',\s*([A-Za-z]{2})\.?\s*$')

# Foreign places whose own country or region code is also a US state code.
# A trailing code rescues "Paris, TX" and "Perth Amboy, NJ", but "Pune, IN"
# is India, "Chennai, TN" is Tamil Nadu, "Goa, GA" is Goa, "Perth, WA" is
# Western Australia and "Toronto, Ontario, CA" is Canada. Each place pattern
# lists only the codes that country uses, so "Athens, GA" and "Melbourne, FL"
# stay US. Ladakh's LA is left out for Delhi, Louisiana.
FOREIGN_CODE_COLLISIONS = [
    (re.compile(r'\b(?:india|bangalore|bengaluru|hyderabad|pune|mumbai|delhi|'
                r'chennai|noida|gurgaon|gurugram|goa|karnataka|tamil nadu|'
                r'maharashtra|telangana|kerala|haryana|uttar pradesh|west bengal|'
                r'gujarat|andhra pradesh)\b'),
     {'IN', 'AR', 'AS', 'CT', 'GA', 'MN', 'MP', 'OR', 'TN', 'UT'}),
    (re.compile(r'\b(?:australia|sydney|melbourne|brisbane|perth|'
                r'western australia)\b(?! amboy)'),
     {'WA'}),
    (re.compile(r'\b(?:canada|toronto|montreal|calgary|ottawa|quebec|'
                r'british columbia)\b'),
     {'CA'}),
]


def _us_region_code(location):
    """The trailing US state code of `location`, unless a foreign place uses it."""
    m = REGION_CODE_RE.search(location)
    if not m or m.group(1).upper() not in US_STATES:
        return None
    code = m.group(1).upper()
    head = _strip_accents(location[:m.start()].lower())
    for places, codes in FOREIGN_CODE_COLLISIONS:
        if code in codes and places.search(head):
            return None
    return code
# An embedded 2-letter US state token even without a trailing comma, e.g.
# "Office - USA - VA - Reston", "US - CA - San Jose".
EMBEDDED_STATE_RE = re.compile(r'\b([A-Z]{2})\b')
# 'AS' (American Samoa) is excluded from the undelimited scan only: it is the
# one region code that is also an ordinary English word, so an all-caps site
# string ("REMOTE AS NEEDED") would otherwise read as a US location. A
# comma-delimited "Pago Pago, AS" still resolves through REGION_CODE_RE.
EMBEDDED_STATE_CODES = US_STATES - {'AS'}

# A spelled-out state/territory name anywhere in the string, not just as the
# trailing comma segment. Without this, the whole family of state-scoped remote
# postings ATSs emit — "Remote - California", "Illinois Remote Work",
# "GEORGIA - VIRTUAL - GA01", "Work At Home-Texas", "Field-Virginia",
# "Northern Virginia" — read as non-US and was dropped. Longest name first so
# "West Virginia" wins over "Virginia" and "North Carolina" over "Carolina".
STATE_NAME_RE = re.compile(
    r'\b(' + '|'.join(re.escape(n) for n in
                      sorted(US_STATE_ABBRS, key=len, reverse=True)) + r')\b',
    re.IGNORECASE)


# The capital spells out as "Washington" too, so it has to be consumed before
# the state scan or "Remote - Washington, D.C." resolves to Washington state.
DC_SPELLING_RE = re.compile(r'\bwashington\s*,?\s*d\.?\s*c\.?(?![a-z])',
                            re.IGNORECASE)


def _state_names(text):
    """Distinct state/territory abbreviations named in full in `text`."""
    found = []
    if DC_SPELLING_RE.search(text):
        found.append('DC')
        text = DC_SPELLING_RE.sub(' ', text)
    for name in STATE_NAME_RE.findall(text):
        abbr = US_STATE_ABBRS[name.lower()]
        if abbr not in found:
            found.append(abbr)
    return found


# Words an ATS uses to say "no fixed office" — the other half of the
# state-scoped remote forms ("Remote - California", "Work At Home-Texas").
REMOTE_WORD_RE = re.compile(
    r'\b(?:remote|virtual|telework|telecommute|work at home|work from home|'
    r'home office)\b', re.IGNORECASE)
# "Virginia - Herndon": some Workday tenants lead with the spelled-out state.
NAME_DASH_NAME_RE = re.compile(r"([A-Za-z .]+?)\s*[-–]\s*([A-Za-z .']+)")
# Padding that surrounds a state name in these strings. Anything else left over
# is a city ("Remote - Miami, Florida") whose detail must survive, so the
# state-scoped rewrite backs off and the generic path keeps the city.
REMOTE_SCOPE_FILLER = {
    'work', 'more', 'office', 'hq', 'us', 'usa', 'united', 'states', 'only',
    'anywhere', 'area', 'state', 'based', 'the', 'in', 'at', 'of', 'and', 'or',
    'northern', 'southern', 'eastern', 'western', 'central',
    'north', 'south', 'east', 'west', 'upstate', 'greater',
}


def _remote_state_scope(location):
    """The one state a remote-only location is scoped to, or None.

    Matches "Remote - California", "Illinois Remote Work, More...",
    "GEORGIA - VIRTUAL - GA01" and "Work At Home-Texas", and declines anything
    that also names a city or a second state.
    """
    if not REMOTE_WORD_RE.search(location):
        return None
    names = _state_names(location)
    if len(names) != 1:
        return None
    rest = REMOTE_WORD_RE.sub(' ', STATE_NAME_RE.sub(
        ' ', DC_SPELLING_RE.sub(' ', location)))
    for word in re.findall(r'[A-Za-z]{2,}', rest):
        # The state's own postal code is not a city ("... - GA01").
        if word.upper() != names[0] and word.lower() not in REMOTE_SCOPE_FILLER:
            return None
    return names[0]

# "Remote (US/Canada)", "US or Remote", "Austin; Remote" split into parts.
LOCATION_SPLIT_RE = re.compile(r'[;|•/]|\bor\b')
REMOTE_FULL_RE = re.compile(r'remote(?:\s*\((?P<scope>[^()]*)\))?|work from home|nationwide')
# Words a remote parenthetical may hold and still mean US remote: "Remote
# (Hybrid)", "Remote (Any State)". Any other scope has to name the US or a
# state, since "Remote (Europe)", "Remote (Worldwide)" and "Remote (Anywhere)"
# all read as US while the parenthetical was unchecked.
REMOTE_WORKPLACE_WORDS = {
    'hybrid', 'remote', 'on', 'site', 'onsite', 'office', 'in', 'flexible',
    'optional', 'full', 'part', 'time', 'travel', 'required', 'any', 'state',
    'home', 'based', 'telework', 'virtual', 'eligible', 'friendly', 'first',
    'only', 'or', 'and', 'with', 'occasional', 'days', 'per', 'week',
}


def _is_us_remote(text):
    """True for "Remote" alone or with a US, state or workplace parenthetical."""
    m = REMOTE_FULL_RE.fullmatch(text)
    if not m:
        return False
    scope = m.group('scope')
    if scope is None or US_TOKEN_RE.search(scope) or _state_names(scope):
        return True
    if scope.strip().upper() in US_STATES:
        return True
    return all(w in REMOTE_WORKPLACE_WORDS for w in re.findall(r'[a-z]+', scope))


def _strip_accents(text):
    # Fold "Zürich"/"Bogotá" to ASCII so the NON_US blocklist still catches
    # accented foreign-city spellings.
    return ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c))


US_TOKEN_RE = re.compile(r'\b(us|usa|u\.s\.a?|united states)\b')


def _is_bare_remote(part):
    # "Remote" or "Remote (Hybrid)", but not "Remote (US)" or "Remote (Texas)".
    low = part.strip().lower()
    return (_is_us_remote(low) and not US_TOKEN_RE.search(low)
            and not _state_names(part))


def _is_foreign_only(part):
    low = _strip_accents(part.lower())
    if not NON_US_RE.search(low):
        return False
    # A comma-joined part can name US cities beside a foreign one: "New York
    # City, Toronto, Chicago, or Remote".
    return not (any(s in low for s in US_SUBSTRINGS) or _has_strong_us_token(part))


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
    if _is_us_remote(low):
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
        if code in EMBEDDED_STATE_CODES:
            return True
    if US_TOKEN_RE.search(low):
        return True
    if _state_names(p):
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
    hours)"), a mid-string state code followed by a country ("Chennai, TN,
    India"), and a code the named foreign place uses itself ("Pune, IN").
    """
    for seg in SEGMENT_SPLIT_RE.split(location):
        if re.sub(r'[^a-z]', '', seg.strip().lower()) in US_COUNTRY_SEGMENTS:
            return True
    # End-anchored: the state code must be the trailing token.
    return _us_region_code(location) is not None


COUNTRY_CODE_PREFIX_RE = re.compile(r'^\(([A-Z]{3})\)\s')


def is_us_location(location):
    """True if any part of a (possibly multi-) location string is in the US.

    A multi-region posting like "Remote (US/Canada)" is US-eligible: the US
    part wins even though Canada is also named. Only reject when NO part
    resolves to the US.
    """
    if not location or not location.strip():
        return False

    parts = [p for p in LOCATION_SPLIT_RE.split(location) if p.strip()]
    # Walmart leads each site with a country code, '(USA) AR BENTONVILLE ...'
    # or '(CAN) ON CAMBRIDGE 03152 WM SUPERCENTER'; the bare Cambridge read as
    # Massachusetts. When every part carries a code, the codes decide.
    codes = [COUNTRY_CODE_PREFIX_RE.match(p.strip()) for p in parts]
    if codes and all(codes):
        return any(m.group(1) == 'USA' for m in codes)
    us_parts = [p for p in parts if _part_is_us(p)]
    # A bare "Remote" is US only when nothing else places the role. ExtraHop's
    # 'Support Engineer I - UK' is 'Remote | United Kingdom', and its lone
    # "Remote" part stored it as Remote (US).
    bare_remote_abroad = (all(_is_bare_remote(p) for p in us_parts)
                          and any(_is_foreign_only(p) for p in parts))
    if us_parts and not bare_remote_abroad:
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
    if _is_us_remote(loc.strip()):
        return True
    return any(s in loc for s in US_SUBSTRINGS)


# Opaque Workday facility codes ("CASD14", "TXSA08UNK").
FACILITY_CODE_RE = re.compile(r'^[A-Z]{2,}\d[A-Z0-9]*$')

# Workplace-type tags an ATS appends to a real location: "Emeryville, CA
# (Hybrid)", "Shakopee, MN (GHQ)", "San Francisco Office", "HQ - Sunnyvale".
WORKPLACE_TAG_RE = re.compile(
    r'\s*\((?:hybrid|remote|on-?site|in-?office|office|hq|ghq|external site)\)\s*$',
    re.IGNORECASE)
OFFICE_AFFIX_RE = re.compile(r'^hq\s*-\s*|\s+office$', re.IGNORECASE)

# Remote spellings that carry no US token: "Remote-Friendly (Travel-Required)",
# "Remote (Any State)". A parenthetical naming a region ("Remote (EMEA)") is
# left alone so a foreign remote option is never relabeled as US.
REMOTE_VARIANT_RE = re.compile(
    r'^remote(?:-friendly)?(?:\s*\((?:any state|travel[ -]?required|u\.?s\.?a?\.?|'
    r'united states|us only)\))?$', re.IGNORECASE)

# Site strings that lead with a state code and end in a street address:
# "MD - Baltimore, 8031 Corporate Dr", "CT, Bloomfield, 900 Cottage Grove Rd".
STATE_CITY_ADDRESS_RE = re.compile(r'^([A-Z]{2})\s*[-,]\s*([A-Za-z .\']+?),?\s+\d.*$')
# "US-AZ-TUCSON-805 ~ ...", "US-CA-Menlo Park" — a country prefix in front of a
# two-letter state code, stripped before the site patterns below.
COUNTRY_PREFIX_RE = re.compile(r'^(?:USA?|United States)-(?=[A-Z]{2}-)',
                               re.IGNORECASE)
# RTX/Collins Workday sites: "MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG".
RTX_SITE_RE = re.compile(r'^([A-Z]{2})-([A-Z .\']+?)-[A-Z0-9-]+\s*(?:\(.*\))?\s*~')
# Northrop site code + address: "M252 Raleigh - 4110 Wake Forest Rd".
SITE_CODE_CITY_ADDRESS_RE = re.compile(r'^(?:[A-Z]\d+\s+)?([A-Za-z .\']+?)\s*-\s*\d+\s+.*$')
# "VA-Dahlgren", "HI-Pearl Harbor", "USA_TX_Richardson", "Atlanta GA".
STATE_DASH_CITY_RE = re.compile(r'^([A-Z]{2})-([A-Za-z .\']+)$')
USA_UNDERSCORE_RE = re.compile(r'^USA?_([A-Z]{2})_(.+)$')
# Walmart Workday: "(USA) ISD Office - DGTC AR BENTONVILLE Home Office".
HOME_OFFICE_RE = re.compile(r'\b([A-Z]{2})\s+([A-Z][A-Z ]+?)\s+Home Office$')
CITY_SPACE_STATE_RE = re.compile(r'^([A-Za-z .\']+)\s+([A-Z]{2})$')
# Workday appends ", More..." when a req lists further sites: "Chicago, IL,
# More...".
MORE_SITES_RE = re.compile(r',?\s*more\.{3}$', re.IGNORECASE)
# A state code before the city: JPMorgan's "VA, McLean".
STATE_COMMA_CITY_RE = re.compile(r'^([A-Z]{2}),\s*([A-Za-z .\']+)$')
# The Home Depot's site names: "STORE SUPPORT CENTER, ATLANTA - 9090".
SITE_CITY_STORE_RE = re.compile(r'^[A-Z .&\']+,\s*([A-Z .\']+?)\s*-\s*\d+$')

# Bare cities that need no state to be unambiguous on a US board. Names that
# exist in several states (Portland, Columbia, Arlington, Cambridge) are
# deliberately absent, as are "New York" and "Washington": a state name wins
# there, since bare "New York" shows up in Workday state lists.
BARE_CITY_STATE = {
    'san francisco': 'CA', 'los angeles': 'CA', 'san jose': 'CA', 'san diego': 'CA',
    'sunnyvale': 'CA', 'palo alto': 'CA', 'mountain view': 'CA', 'menlo park': 'CA',
    'santa clara': 'CA', 'redwood city': 'CA', 'irvine': 'CA', 'sacramento': 'CA',
    'seattle': 'WA', 'redmond': 'WA', 'bellevue': 'WA',
    'new york city': 'NY', 'nyc': 'NY',
    'boston': 'MA', 'chicago': 'IL', 'austin': 'TX', 'dallas': 'TX', 'houston': 'TX',
    'san antonio': 'TX', 'denver': 'CO', 'boulder': 'CO', 'colorado springs': 'CO',
    'atlanta': 'GA', 'reston': 'VA', 'mclean': 'VA', 'herndon': 'VA', 'chantilly': 'VA',
    'fort meade': 'MD', 'annapolis junction': 'MD', 'baltimore': 'MD',
    'philadelphia': 'PA', 'pittsburgh': 'PA', 'miami': 'FL', 'tampa': 'FL',
    'orlando': 'FL', 'phoenix': 'AZ', 'salt lake city': 'UT', 'minneapolis': 'MN',
    'detroit': 'MI', 'raleigh': 'NC', 'durham': 'NC', 'charlotte': 'NC',
    'nashville': 'TN', 'las vegas': 'NV', 'huntsville': 'AL', 'st. louis': 'MO',
}


def _title_city(name):
    """Title-case an ALL-CAPS site city ("CEDAR RAPIDS", "MCKINNEY") only."""
    name = name.strip()
    if not name.isupper():
        return name
    name = name.title()
    return re.sub(r'\bMc([a-z])', lambda m: 'Mc' + m.group(1).upper(), name)


def _is_foreign_part(part):
    # A trailing US state code rescues a US city that shares a foreign name
    # ("Paris, TX", "Vienna, VA"), but not a foreign place's own code.
    if _us_region_code(part):
        return False
    return bool(NON_US_RE.search(_strip_accents(part.lower())))


def _normalize_single_location(location):
    location = location.strip()
    # Drop a leading country prefix before the site patterns run. The strip
    # further down happens too late: "US-AZ-TUCSON-805 ~ 1151 E Hermans Rd"
    # reached RTX_SITE_RE with the country still attached, so it read "US" as
    # the state and "AZ" as the city and returned "Az, US" — which a second
    # normalization pass then shortened to "Az", losing Tucson entirely. The
    # lookahead requires a two-letter state code, so the spelled-out
    # "United States-California-Palmdale" form below still matches.
    location = COUNTRY_PREFIX_RE.sub('', location)
    location = MORE_SITES_RE.sub('', location).strip()
    # A leading code is a country as often as a state ("IL, Haifa"), so only a
    # city this module already places in that state is rewritten.
    m = STATE_COMMA_CITY_RE.fullmatch(location)
    if m and BARE_CITY_STATE.get(m.group(2).strip().lower()) == m.group(1):
        return f'{m.group(2).strip()}, {m.group(1)}'
    m = SITE_CITY_STORE_RE.fullmatch(location)
    if m:
        city = _title_city(m.group(1))
        state = BARE_CITY_STATE.get(city.lower())
        if state:
            return f'{city}, {state}'
    # Amazon: "US, MA, Boston" -> "Boston, MA"; Intel: "US, Oregon, Hillsboro"
    m = re.fullmatch(r'(?:USA?|United States),\s*([A-Za-z .]+),\s*(.+)', location)
    if m:
        region = m.group(1).strip()
        abbr = region if region in US_STATES else US_STATE_ABBRS.get(region.lower())
        if abbr:
            return f'{m.group(2).strip()}, {abbr}'
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
    m = RTX_SITE_RE.match(location)
    if m and m.group(1) in US_STATES:
        return f'{_title_city(m.group(2))}, {m.group(1)}'
    m = USA_UNDERSCORE_RE.fullmatch(location)
    if m:
        return f'{m.group(2).strip()}, {m.group(1)}'
    m = HOME_OFFICE_RE.search(location)
    if m and m.group(1) in US_STATES:
        return f'{_title_city(m.group(2))}, {m.group(1)}'
    m = STATE_CITY_ADDRESS_RE.fullmatch(location)
    if m and m.group(1) in US_STATES:
        return f'{_title_city(m.group(2))}, {m.group(1)}'
    m = STATE_DASH_CITY_RE.fullmatch(location)
    if m and m.group(1) in US_STATES:
        return f'{m.group(2).strip()}, {m.group(1)}'
    location = re.sub(r'^(usa?|united states)\s*[-–:]\s*', '', location,
                      flags=re.IGNORECASE)
    loc_l = location.lower()
    if 'remote' in loc_l and (loc_l == 'remote'
                              or re.search(r'\busa?\b|\bu\.s\.a?\.?|\bunited states\b',
                                           loc_l)):
        return 'Remote (US)'
    if REMOTE_VARIANT_RE.fullmatch(location):
        return 'Remote (US)'
    # Amazon: "US, Virtual"
    if re.fullmatch(r'(?:usa?|united states),\s*virtual', loc_l):
        return 'Remote (US)'
    # A remote role scoped to one state, e.g. "Remote - California".
    scope = None if REGION_CODE_RE.search(location) else _remote_state_scope(location)
    if scope:
        return f'Remote, {scope}'
    # "Virginia - Herndon" -> "Herndon, VA".
    m = NAME_DASH_NAME_RE.fullmatch(location)
    if m:
        abbr = US_STATE_ABBRS.get(m.group(1).strip().lower())
        if abbr:
            return f'{m.group(2).strip()}, {abbr}'
    # Opaque facility code with no human-readable city — drop it.
    if FACILITY_CODE_RE.match(location.strip()):
        return ''
    m = SITE_CODE_CITY_ADDRESS_RE.fullmatch(location)
    if m:
        location = m.group(1)
    location = OFFICE_AFFIX_RE.sub('', WORKPLACE_TAG_RE.sub('', location)).strip()
    parts = [p.strip() for p in location.split(',')]
    if len(parts) >= 2 and parts[-1].lower() in (
            'usa', 'us', 'united states', 'united states of america'):
        parts = parts[:-1]  # "Arlington, Virginia, USA" -> "Arlington, Virginia"
    if len(parts) >= 2:
        abbr = US_STATE_ABBRS.get(parts[-1].lower())
        if abbr:
            return f'{", ".join(parts[:-1])}, {abbr}'
        # "San Mateo, CA United States": country glued onto the state code.
        m = re.fullmatch(r'([A-Z]{2})\s+(?:USA?|United States)', parts[-1])
        if m and m.group(1) in US_STATES:
            return f'{", ".join(parts[:-1])}, {m.group(1)}'
    if len(parts) == 1:
        single = parts[0]
        abbr = US_STATE_ABBRS.get(single.lower())
        if abbr:
            return f'{abbr} (US)'  # bare state name: "Arizona"
        state = BARE_CITY_STATE.get(single.lower())
        if state:
            return f'{single}, {state}'
        m = CITY_SPACE_STATE_RE.fullmatch(single)
        if m and m.group(2) in US_STATES:
            return f'{m.group(1).strip()}, {m.group(2)}'
    return ', '.join(parts)


def _location_city_key(loc):
    # "Austin" and "Austin, TX" share this key so the bare-city duplicate can
    # fold into the qualified one.
    return loc.split(',')[0].strip().lower()


def normalize_location(location):
    """Convert "USA - Austin, Texas" -> "Austin, TX"; collapse remote variants.

    Multi-location strings (";" or "|" separated) drop a bare-city part
    ("Austin") when the same city also appears qualified ("Austin, TX");
    genuinely distinct qualified parts ("Portland, OR; Portland, ME") are
    kept. Foreign options are dropped once any US part remains, since the
    board is US-only and "London, UK" beside "Remote (US)" is noise.
    """
    if not location:
        return location
    seen = []
    for raw in re.split(r'[;|]', location):
        if not raw.strip():
            continue
        norm = _normalize_single_location(raw)
        if norm and norm not in seen:
            seen.append(norm)
    domestic = [s for s in seen if not _is_foreign_part(s)]
    if domestic:
        seen = domestic
    qualified_cities = {_location_city_key(s) for s in seen if ',' in s}
    result = [s for s in seen
              if ',' in s or _location_city_key(s) not in qualified_cities]
    return '; '.join(result)


def renormalize_locations(listings):
    """Re-run `normalize_location` over stored rows; return how many changed.

    A row keeps the string the normalizer produced when it landed, so a rule
    added later never reaches the board without this pass. Community rows stay
    as the maintainer wrote them, and a result that would blank the location
    (an opaque facility code alone) keeps the old value.
    """
    changed = 0
    for entry in listings:
        if entry.get('source') == 'Community':
            continue
        before = entry.get('location', '')
        after = normalize_location(before)
        if after and after != before:
            entry['location'] = after
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Descriptions are only skimmed for a few short signals; cap the length so the
# tag-strip regex can't be driven quadratic by a pathological '<'-heavy body.
MAX_DESCRIPTION_CHARS = 100_000

# Workday serves a description as one line of <p>/<li>/<br> markup. Stripped to
# spaces, a single "Preferred Qualifications" heading anywhere in the body
# shadowed the whole line, so required_years read Nightwing's "5+ years" req
# JR101442 as 0 and the experience gate never fired on a Workday row.
BLOCK_TAG_RE = re.compile(r'<\s*/?(?:p|br|li|ul|ol|div|h[1-6])\b[^<>]*>',
                          re.IGNORECASE)


def strip_html(text):
    """Plain text of an HTML description, with block tags kept as line breaks."""
    if not text:
        return ''
    # Northrop writes "associate's degree" with U+2019, which DEGREE_ALT_RE's
    # straight apostrophe missed.
    text = html.unescape(text[:MAX_DESCRIPTION_CHARS]).replace('\u2019', "'")
    text = BLOCK_TAG_RE.sub('\n', text)
    # `[^<>]` excludes '<' too, so an unclosed-tag run of '<' can't be consumed
    # and re-backtracked — linear on every Python version (no ReDoS).
    return re.sub(r'<[^<>]*>', ' ', text)


# A year in an intern title names the season it hires for, and nothing retires
# a finished season whose req stays live: Nightwing 'Vulnerability Researcher
# Intern - 2026' (posted Feb 10) was still on the board in late September.
# Optional leading digit absorbs Northrop's "22026" typo, as COHORT_YEAR_RE does.
TITLE_YEAR_RE = re.compile(r'\b\d?(20\d\d)\b')
# From this month on, next summer is the season students are recruiting for.
SEASON_ROLLOVER_MONTH = 9


def first_open_season(today=None):
    """Earliest intern season still recruiting: this year until September, then next."""
    today = today or datetime.now().date()
    return today.year if today.month < SEASON_ROLLOVER_MONTH else today.year + 1


def _is_stale_intern_title(title, today):
    years = [int(y) for y in TITLE_YEAR_RE.findall(title)]
    # New-grad titles are exempt: Northrop's '2026 Associate Cybersecurity
    # Analyst - Pathways Program' was posted Sep 24 2026 and is a current req.
    if not years or classify_level(title, today=today) != 'intern':
        return False
    return max(years) < first_open_season(today)


def is_senior_architect(title, today=None):
    """True for an architect title with no new-grad, intern or early-career word."""
    t = title.lower()
    if not ARCHITECT_RE.search(t):
        return False
    if classify_level(title, today=today) in ('newgrad', 'intern'):
        return False
    return not EARLYCAREER_RE.search(t)


def is_rejected_title(title, today=None):
    """True if the title alone rules the role out: too senior, not cyber work,
    a physical-security guard post, or an internship whose season has passed.

    `today` (a date) is injectable so the season check can be tested.
    """
    t = title.lower()
    if any(re.search(p, SENIORITY_EXEMPT_RE.sub(' ', t)) for p in SENIORITY_REJECT):
        return True
    if is_senior_architect(title, today):
        return True
    if LEVELED_SENIOR_RE.search(t):
        return True
    if FUNCTION_REJECT_RE.search(t):
        return True
    if (SECURITY_OFFICER_RE.search(t) and not ISSO_HINT_RE.search(t)
            and not any(h in t for h in INFOSEC_OFFICER_HINTS)):
        return True
    if PROGRAM_ANALYST_RE.search(t) and not _has_cyber_keyword(t):
        return True
    if NON_CYBER_SECURITY_RE.search(t) and not _has_cyber_keyword(t):
        return True
    if DEPARTMENT_REJECT_RE.search(t) and not _names_security_work(t):
        return True
    if (re.search(r'\bsecurity\b', _strip_non_cyber_security(t))
            and WEAK_SECURITY_ROLE_RE.search(t) and not TECH_SECURITY_ROLE_RE.search(t)
            and not _has_second_cyber_term(t)):
        return True
    return _is_stale_intern_title(title, today)


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


# "Security Clearance" names a hiring requirement, not the work: ICF 'Computer
# Scientist / Software Developer, Junior - Security Clearance Required' and RTX
# 'Software Engineer I, CDS (Onsite - Security Clearance)' matched 'security'.
SECURITY_CLEARANCE_RE = re.compile(r'\bsecurity clearance\b')
# "National Security" names a customer or a business unit, not the work:
# Salesforce 'Systems Engineering Associate - GovCloud [Salesforce National
# Security]' and KBR 'National Security Solutions (NSS) Semiconductor Research
# Internship' matched 'security'. A title that also says 'cyber' keeps it.
NATIONAL_SECURITY_RE = re.compile(r'\bnational security(?: solutions)?\b')


def _strip_non_cyber_security(t):
    t = SECURITY_CLEARANCE_RE.sub(' ', t)
    return NON_CYBER_SECURITY_RE.sub(' ', NATIONAL_SECURITY_RE.sub(' ', t))


def _has_cyber_keyword(t):
    # 'Identity & Access Management Intern' reads as 'identity and access'.
    t = re.sub(r'\s+', ' ', _strip_non_cyber_security(t).replace('&', ' and '))
    return _is_cyber_keyword_hit(t) or any(p.search(t) for p in CYBER_REGEXES)


def _has_second_cyber_term(t):
    # A cyber term besides the bare word 'security', or a qualifier that makes
    # 'security' information security.
    return (_has_cyber_keyword(re.sub(r'\bsecurity\b', ' ', t))
            or bool(INFOSEC_QUALIFIER_RE.search(t)) or bool(ISSO_HINT_RE.search(t))
            or any(h in t for h in INFOSEC_OFFICER_HINTS))


def _names_security_work(t):
    return _has_second_cyber_term(t) or bool(SECURITY_ROLE_RE.search(t))


# Defense security companies also staff intelligence-support analysts, whom the
# bare 'analyst' allowance let in: Nightwing 'Junior Geospatial / Full-Motion
# Video (FMV) Analyst'. They reject only when 'analyst' is the title's one tech
# term, so a 'Geospatial Software Engineer' at the same employer stays, and a
# cyber keyword ('SIGINT Cyber Analyst') is checked before this ever runs.
INTEL_SUPPORT_RE = re.compile(
    r'\b(?:geospatial|full[- ]motion video|fmv|imagery|all[- ]source|targeting|'
    r'linguist|signals collection)\b')

# The same allowance let in business analysts and researchers, which the
# charter's "engineering role at a security company" does not cover: 'Business
# Analyst I', 'Associate Pricing Analyst', 'Analyst I, Market Intelligence',
# 'Associate UX Researcher', and Osano 'Jr IT Analyst (part-time)'. They reject
# only when an analyst or researcher term is the title's one tech signal, so
# 'Research Analyst I' and 'Fraud Analyst' stay, as does 'Detection Engineer I'.
BUSINESS_ANALYST_RE = re.compile(
    r'\b(?:business|legal|pricing|operations|(?:market|competitive) intelligence|'
    r'sales|revenue|finance|ux|user research|it support|help ?desk|it analyst)\b')
ANALYST_TECH_KEYWORDS = {'analyst', 'data analyst', 'researcher'}


def _fold(title):
    # ATS titles carry non-breaking spaces ("Access\xa0& Identity\xa0Management")
    # that break the multi-word keywords.
    return ' '.join(title.lower().split())


def is_cyber_title(title, security_company=False):
    t = _fold(title)
    if _has_cyber_keyword(t):
        return True
    if SECURITY_TEAM_RE.search(t) and ENGINEERING_ROLE_RE.search(t):
        return True
    if not security_company:
        return False
    tech = [kw for kw in TECH_KEYWORDS if kw in t]
    if INTEL_SUPPORT_RE.search(t) and all('analyst' in kw for kw in tech):
        return False
    if BUSINESS_ANALYST_RE.search(t) and set(tech) <= ANALYST_TECH_KEYWORDS:
        return False
    return bool(tech)


# Cleared-facility security (the FSO function FUNCTION_REJECT excludes by name)
# also hides behind plain titles: RTX 'Security Specialist II' maintains
# "classified document control ... NISPOM ... COMSEC". Cyber roles cite the
# NISPOM too (Amentum 'Cyber Security Analyst 1' performs RMF tasks "required by
# the 32 CFR part 117 NISPOM"), so the description decides only when the bare
# word 'security' is the title's one cyber signal.
FACILITY_SECURITY_DESC_RE = re.compile(
    r'nispom|32 cfr (?:part )?117|classified document control')
INFOSEC_TITLE_RE = re.compile(
    r'\b(?:information|systems?|network|cloud|application|data|isso|issm)\b')


def _is_facility_security_role(title, description):
    t = SECURITY_CLEARANCE_RE.sub(' ', title.lower())
    if not re.search(r'\bsecurity\b', t) or INFOSEC_TITLE_RE.search(t):
        return False
    if _has_cyber_keyword(re.sub(r'\bsecurity\b', ' ', t)):
        return False
    return bool(FACILITY_SECURITY_DESC_RE.search(strip_html(description).lower()))


def classify_level(title, description='', intern_hint=False, today=None):
    """Return 'intern', 'newgrad', 'earlycareer', or None.

    `today` (a date) moves the cohort-year window for tests; by default the
    window is the one computed at import.
    """
    t = title.lower()
    if today is None:
        cohort_year_re, newgrad_year_signals = COHORT_YEAR_RE, NEWGRAD_YEAR_SIGNALS
    else:
        cohort_year_re = _cohort_year_re(today=today)
        newgrad_year_signals = _newgrad_year_signals(today)
    # Intern wins first: "SOC Intern - Summer 2027" must not fall through to
    # the cohort-year rule and come out as newgrad. `intern_hint` carries an
    # ATS employment-type field for postings whose title omits "intern".
    if intern_hint or any(p.search(t) for p in INTERN_TITLE_RES):
        return 'intern'
    if any(kw in t for kw in NEWGRAD_SIGNALS) or any(kw in t for kw in newgrad_year_signals):
        return 'newgrad'
    if re.search(r'\bgraduate\b', t) and 'graduate degree' not in t:
        return 'newgrad'
    # A leveled I/II marker beats a bare cohort year: "Analyst II (Windows
    # Server 2026)" is early career, not a 2026 campus cohort.
    if LEVELED_TITLE_RE.search(t):
        return 'earlycareer'
    # Season + cohort year with no new-grad wording is an internship req;
    # checked before the bare cohort-year rule, which would claim it.
    if SUMMER_RE.search(t) and cohort_year_re.search(t):
        return 'intern'
    if cohort_year_re.search(t):
        return 'newgrad'
    if EARLYCAREER_RE.search(t):
        return 'earlycareer'
    if description:
        d = strip_html(description).lower()
        if DESCRIPTION_NEWGRAD_RE.search(d):
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
    """True if the posting asks for a clearance, US citizenship or US-person status."""
    # Whitespace collapses first so "U.S.\ncitizen" and "U.S.&nbsp;citizen"
    # read as one phrase.
    text = re.sub(r'\s+', ' ', f'{title} {strip_html(description)}'.lower())
    text = CLEARANCE_NEGATION_RE.sub(' ', text)
    return (any(kw in text for kw in CLEARANCE_SIGNALS)
            or bool(CLEARANCE_WORD_RE.search(text)))


# ---------------------------------------------------------------------------
# Years-of-experience floor
# ---------------------------------------------------------------------------
#
# The board's charter is 0-2 years. A posting whose *required* experience floor
# is above that ceiling does not belong here regardless of how junior its title
# reads — "Security Engineer II" and "Analyst II" reqs routinely ask for 4-8
# years. Reading the floor correctly means three things the old any-match-over-3
# scan got wrong:
#
#   1. Preferred/nice-to-have counts are not a floor. Amazon's "3+ years"
#      basic qual and "2+ years" preferred qual are not interchangeable.
#   2. Conjunctive bullets each bind, so the floor is the LARGEST of them
#      ("3+ years of scripting" AND "4+ years of infosec" -> 4).
#   3. Degree-paired bands are alternatives, so the floor is the SMALLEST of
#      them ("BS with 5 years; MS with 3 years; PhD with 0 years" -> 0, and
#      "HS Diploma & 5 years" in place of a BS does not raise the floor).

# The board accepts up to this many years of required experience.
MAX_ALLOWED_YEARS = 2

# "3+ years", "5 years of experience", "3 or more years", "3+ yrs". Whitespace
# runs are bounded ({0,3}) so a digit followed by a huge space run can't drive
# the two adjacent \s* quadratic. Contract reqs write the count twice, "3
# (three) years" and "seven (7) years", which skipped both scans.
_COUNT_ECHO = r'(?:\(\s{0,3}(?:\d{1,2}|[a-z]+)\s{0,3}\+?\s{0,3}\)\s{0,3})?'
YEARS_RE = re.compile(
    r'\b(\d{1,2})\s{0,3}' + _COUNT_ECHO
    + r'(\+)?\s{0,3}(or more\s{1,3})?(?:years?|yrs?)\b')
# An explicit band, "0-2 years" / "5 to 7 years". The low end is the real bar:
# a posting open to 2-4 years is open to a 2-year candidate. Matched (and
# consumed) before the single-count scan so "2-4 years" doesn't read as 4.
YEARS_RANGE_RE = re.compile(
    r'\b(\d{1,2})\s{0,3}(?:[-–—]|to)\s{0,3}(\d{1,2})\s{0,3}(?:years?|yrs?)\b')
# Spelled-out counts that matter for the low end of "N+ years". 'one' and
# 'two' only ever lower a floor: SEI's "or MS in the same with one (1) year"
# is the cheapest route into 'Associate Security Researcher'.
SPELLED_YEARS = ('one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight',
                 'nine', 'ten')
SPELLED_VALUES = {word: n for n, word in enumerate(SPELLED_YEARS, start=1)}
SPELLED_YEARS_RE = re.compile(
    r'\b(' + '|'.join(SPELLED_YEARS) + r')\s{0,3}' + _COUNT_ECHO
    + r'(\+)?\s{0,3}(or more\s{1,3})?(?:years?|yrs?)\b')
# A requirement verb close before the count, e.g. "minimum 3 years".
REQUIREMENT_VERB_RE = re.compile(
    r'\b(minimum|at least|require[sd]?|must have|need)\b', re.IGNORECASE)

# A backward-looking window, never a floor: "held a clearance within the last 5
# years", "the past 3 years of CVEs". Anchored to the text immediately before
# the count so it only fires on the phrase that owns it.
RECENCY_RE = re.compile(
    r'\b(?:within|in|over|during|for|across)\s+the\s+'
    r'(?:last|past|previous|prior)\s*$')
# Things a candidate can be asked for "N years of" that are not work
# experience. Cleared-defense reqs — a large share of this board — pair a
# requirement verb with clearance recency, residency, and coursework counts,
# and reading those as an experience floor would quietly delete good listings.
# Deliberately short and unambiguous: 'service' and 'data' are omitted because
# "customer service experience" and "data engineering experience" are real
# experience bars.
NON_EXPERIENCE_OBJECT_RE = re.compile(
    r'\s*(?:of|in)\s+(?:[a-z.&/-]+\s+){0,3}?'
    r'(?:coursework|education|schooling|studies|residency|residence|'
    r'citizenship|clearances?|tenure|age)\b')
# A count used as a modifier describes the employer, not the candidate: "one of
# our 25+ year programs", "a 30-year history". Newline-preserving HTML stripping
# exposed this cleared-defense boilerplate to the parser, which read it as a
# 25-year floor.
# "Applicants must be at least 21 years old" is an age bar, not a floor.
YEAR_MODIFIER_RE = re.compile(
    r'\s*(?:programs?|contracts?|histor(?:y|ies)|legacy|heritage|'
    r'partnerships?|relationships?|anniversary|old)\b')
# The employer's own track record, not the candidate's: "Leveraging our 50+
# years of experience", "Acme has more than 25 years of experience serving the
# DoD". A 'has' or 'have' counts only with a subject that is not the reader,
# since "The ideal candidate has 5+ years" and a bare "Have 3+ years" bullet
# are real floors.
EMPLOYER_COUNT_RE = re.compile(
    r'\b(our|has|have)\s+(?:(?:more than|over|nearly|almost|close to|about)\s+)?$')
READER_SUBJECT_RE = re.compile(
    r'\b(?:you|your|candidates?|applicants?|individuals?|person|hire|who|must|'
    r'should|will|shall|ideally)\b')

# Markers that open text describing counts the candidate does NOT have to meet.
# Every section noun is plural-tolerant — "Preferred Qualifications" is the
# single most common heading in the corpus and `qualification\b` misses it.
#
# 'additional' is not a marker: "Minimum 6 years of experience, with additional
# experience in cloud security" read as preferred and floored at 0.
PREFERRED_MARKER_RE = re.compile(
    r'\b(?:preferred|desired|optional|bonus)\b[^.\n]{0,25}?'
    r'\b(?:qualifications?|requirements?|skills?|experiences?)\b'
    r'|\bpreferred\s*:'
    r'|\bnice[- ]to[- ]haves?\b|\bbonus points\b|\beven better\b'
    r'|\b(?:is|are|would be)\s+a\s+(?:big\s+)?plus\b|\bit\'?s a plus\b')
# Markers that hand control back to must-have territory, so a posting that
# lists preferred quals before required ones is still read correctly.
REQUIRED_MARKER_RE = re.compile(
    r'\b(?:basic|minimum|required|must[- ]have|essential|mandatory)\b[^.\n]{0,25}?'
    r'\b(?:qualifications?|requirements?|skills?|experiences?)\b'
    r'|\bqualifications you must have\b|\bwhat you\'?ll need\b'
    r'|\bwhat we\'?re looking for\b|\bwhat you\'?ll bring\b|\bwho you are\b'
    r'|\brequirements\s*:')
# A line that is only a section heading ends a preferred section as well:
# "<h2>Preferred Qualifications</h2>...<h2>Requirements</h2><li>6+ years"
# never closed and read the 6 as preferred.
SECTION_HEADING_RE = re.compile(
    r"(?:requirements|qualifications|what you(?:'ll)? bring|you have|about you|"
    r"(?:key )?responsibilities|what you(?:'ll)? need|what you(?:'ll)? do|"
    r"the role|about the role|job requirements|skills)\s*:?")
# Words that soften only their own clause: "3+ years of experience
# preferred", "ideally you have 4 years". A clause that also says 'required'
# or 'minimum' keeps its count, so "5+ years required and CISSP preferred"
# still floors at 5.
CLAUSE_PREFERENCE_RE = re.compile(r'\b(?:preferred|preferably|ideally|desired)\b')
CLAUSE_REQUIREMENT_RE = re.compile(
    r'\b(?:required|requires?|minimum|at least|must)\b')

# An education alternative near the count: "Bachelor's with 2 years",
# "Master's with 3 years". Counts in this shape are alternative routes into the
# same job, so they bound the floor together rather than each on their own.
#
# 'bachelors' and 'masters' are spelled without an apostrophe often enough to
# matter: Northrop's 'Level 2/3 Cyber Systems Engineer - AISR&T Contingent'
# reads "a Bachelors of Science degree in a STEM field and at least 2 years".
DEGREE_ALT_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|phd|ph\.d|doctorate|associate'?s degree|"
    r"(?:bs|ms)(?= (?:degree|in)\b)|b\.s|m\.s|"
    r"hs diploma|high school|ged|undergraduate|graduate degree|"
    r"advanced degree|in lieu of|in place of|equivalent|additional)\b")
# A doctoral route sets the floor only when it is the sole route on offer. SEI
# 'AI Security Researcher' asks BS + 8, MS + 5 or PhD + 2 years, and the PhD
# route alone read as an early-career floor of 2.
DOCTORAL_RE = re.compile(r'phd|ph\.d|doctorate')
# How far back from a count DEGREE_ALT_RE looks, never past the start of the
# count's own line. Degree routes run long ("a Bachelors of Science degree in
# a STEM field and at least 5 years"), and the line bound keeps a degree named
# in the bullet above from pairing with this one.
DEGREE_ALT_WINDOW = 120

# Years offered *instead of* a degree: "an additional 4 years ... in lieu of a
# degree", "BS in CS; or HS Diploma & 5 years". A candidate who has the degree
# owes none of those years, so this route implies a 0-year floor — but only
# when a degreed route is actually on offer beside it, so a lone "HS Diploma
# and 8 years" still reads as 8.
DEGREE_SUB_BEFORE_RE = re.compile(
    r'\bin lieu of\b|\bin place of\b|\bhs diploma\b|\bhigh school\b|\bged\b'
    r'|\badditional\b|\bwithout a\b')
DEGREE_SUB_AFTER_RE = re.compile(
    r'\bin lieu of\b|\bin place of\b|\bwithout a (?:degree|bachelor)\b')
DEGREE_NOUN_RE = re.compile(
    r"\b(bachelor'?s?|master'?s?|phd|ph\.d|doctorate|degree|b\.?s\.?|m\.?s\.?)\b")


# Bullet/heading decoration that can sit between the start of a line and a
# heading word without making the marker mid-sentence.
_BULLET_CHARS = ' \t*-–—•·#>|>'


def _clause_start(low, pos, marks='.;'):
    return max(low.rfind(c, 0, pos) + 1 for c in marks + '\n')


def _clause_end(low, pos, marks='.;'):
    ends = [i for i in (low.find(c, pos) for c in marks + '\n') if i != -1]
    return min(ends, default=len(low))


def _heading_line_starts(low):
    starts, pos = [], 0
    for line in low.split('\n'):
        if SECTION_HEADING_RE.fullmatch(line.strip(_BULLET_CHARS)):
            starts.append(pos)
        pos += len(line) + 1
    return starts


def _preferred_spans(low):
    """Character ranges of `low` that describe preferred, not required, quals.

    A marker that opens its own line ("Preferred Qualifications:") is a section
    heading and shadows everything up to the next must-have heading or plain
    section heading. A marker buried mid-line ("experience with Go is a plus")
    shadows from the start of its own sentence or clause to the end of the
    line, so "5+ years of security engineering; experience with Go is a plus"
    keeps its 5. A clause word ("3+ years preferred") shadows its clause only.
    """
    closers = sorted([m.start() for m in REQUIRED_MARKER_RE.finditer(low)]
                     + _heading_line_starts(low))
    spans = []
    for m in PREFERRED_MARKER_RE.finditer(low):
        start = m.start()
        line_start = low.rfind('\n', 0, start) + 1
        if low[line_start:start].strip(_BULLET_CHARS):
            line_end = low.find('\n', m.end())
            spans.append((_clause_start(low, start),
                          len(low) if line_end == -1 else line_end))
        else:
            spans.append((start, next((c for c in closers if c > start), len(low))))
    for m in CLAUSE_PREFERENCE_RE.finditer(low):
        # Commas split here too: "3+ years of experience, preferably in a
        # SOC" softens the setting, not the count.
        begin = _clause_start(low, m.start(), '.;,')
        end = _clause_end(low, m.end(), '.;,')
        clause = low[begin:end]
        if not CLAUSE_REQUIREMENT_RE.search(clause):
            spans.append((begin, end))
    return spans


def _in_spans(spans, pos):
    return any(start <= pos < end for start, end in spans)


def _is_requirement(low, start, end, emphatic):
    """True if a year count states a requirement rather than trivia.

    "N+ years"/"N or more years" is emphatic enough on its own — nobody writes
    "we shipped 3+ years of releases". Otherwise the count needs 'experience'
    nearby (the window is wide because the noun phrase in between can run long:
    "5 years of enterprise technology or cybersecurity sales experience") or a
    requirement verb shortly before it. A bare "the past 3 years of incidents"
    matches none of these.
    """
    before = low[max(0, start - 60):start]
    # Disqualifiers first, so neither the emphatic form nor a requirement verb
    # can promote a clearance-recency or coursework count into a floor.
    if (RECENCY_RE.search(before) or NON_EXPERIENCE_OBJECT_RE.match(low, end)
            or YEAR_MODIFIER_RE.match(low, end) or _is_employer_count(low, start)):
        return False
    if emphatic:
        return True
    if 'experience' in low[end:end + 80] or 'experience' in before:
        return True
    return bool(REQUIREMENT_VERB_RE.search(before))


def _is_employer_count(low, start):
    before = low[max(0, start - 40, _clause_start(low, start)):start]
    m = EMPLOYER_COUNT_RE.search(before)
    if not m:
        return False
    if m.group(1) == 'our':
        return True
    subject = before[:m.start()]
    return bool(subject.strip(_BULLET_CHARS + ',')) and not READER_SUBJECT_RE.search(subject)


def _year_in_requirement_context(low, start, end):
    # Retained for callers that only need the yes/no context test.
    return _is_requirement(low, start, end, emphatic=False)


def _experience_counts(description):
    """Collect year counts as (conjunctive, alternative, doctoral, substituted)."""
    conjunctive, alternative, doctoral, substituted = [], [], [], []
    if not description:
        return conjunctive, alternative, doctoral, substituted
    low = strip_html(description).lower()
    preferred = _preferred_spans(low)
    consumed = []

    def record(value, start, end, emphatic=False):
        if _in_spans(preferred, start) or not _is_requirement(low, start, end, emphatic):
            return
        before = low[max(0, start - 60):start]
        after = low[end:end + 60]
        if ((DEGREE_SUB_BEFORE_RE.search(before) or DEGREE_SUB_AFTER_RE.search(after))
                and DEGREE_NOUN_RE.search(low[max(0, start - 110):end + 60])):
            substituted.append(0)   # the degreed route needs no years
        else:
            window = low[max(0, start - DEGREE_ALT_WINDOW,
                              low.rfind('\n', 0, start) + 1):start]
            routes = DEGREE_ALT_RE.findall(window)
            if routes and DOCTORAL_RE.fullmatch(routes[-1]):
                doctoral.append(value)
            elif routes:
                alternative.append(value)
            else:
                conjunctive.append(value)

    for m in YEARS_RANGE_RE.finditer(low):
        consumed.append((m.start(), m.end()))
        record(int(m.group(1)), m.start(), m.end())
    for m in YEARS_RE.finditer(low):
        if _in_spans(consumed, m.start()):
            continue
        record(int(m.group(1)), m.start(), m.end(),
               emphatic=bool(m.group(2) or m.group(3)))
    for m in SPELLED_YEARS_RE.finditer(low):
        record(SPELLED_VALUES[m.group(1)], m.start(), m.end(),
               emphatic=bool(m.group(2) or m.group(3)))
    return conjunctive, alternative, doctoral, substituted


def required_years(description):
    """Lowest number of years a candidate must actually have, or 0 if unstated.

    Every conjunctive bullet binds at once, so they set the floor together via
    max(). Degree-paired bands are alternative routes, so they only set the
    floor when nothing conjunctive does, and then via min().
    """
    conjunctive, alternative, doctoral, substituted = _experience_counts(description)
    if conjunctive:
        return max(conjunctive)
    # Years offered in lieu of a degree say the degreed route needs none, but
    # only when that route names no count of its own: SAIC 'Tier II or III ...
    # IAM Administrator' reads "Bachelor's degree and 5 years; an additional
    # four (4) years ... in lieu of a degree", and the degreed route is 5.
    if alternative:
        return min(alternative)
    if substituted:
        return 0
    if doctoral:
        return min(doctoral)
    return 0


def exceeds_experience_cap(description, cap=MAX_ALLOWED_YEARS):
    """True if the posting's required experience floor is above the board's cap.

    An explicit ceiling that opens at zero ("0-2 years", "less than 2 years",
    "no prior experience required") names the target audience outright, so it
    outranks a floor inferred from individual bullets. RTX's 'Junior DevSecOps
    Engineer' reads "bachelor's degree and less than 2 years ... or a total of
    4 years" and stays. A "1-2 years" band does not outrank a larger floor.
    """
    if required_years(description) <= cap:
        return False
    d = strip_html(description).lower()
    return not any(p.search(d) for p in ZERO_ANCHORED_YOE_RES)


def requires_experience(description):
    """True if the description asks for more than an early-career amount.

    Kept as the historical name/threshold (3+ years) used elsewhere; the floor
    parser above is what decides.
    """
    return exceeds_experience_cap(description)


def infer_category(title, security_company=False):
    t = _fold(title)
    for category, pattern in CATEGORY_RULES:
        if pattern.search(t):
            return category
    if security_company:
        return 'Engineering @ Security Co'
    return 'Security Engineering'


def evaluate_job(title, location, description='', security_company=False,
                 intern_hint=False):
    """Run the full filter pipeline. Returns (level, category) or None."""
    return judge_job(title, location, description, security_company, intern_hint)[0]


# Every reason judge_job can give. 'no-level' is the one an empty description
# can cause by itself: a flat title fails it for want of description evidence.
JUDGE_REASONS = ('rejected-title', 'not-cyber', 'facility-security', 'no-level',
                 'over-experienced', 'non-us-location')


def judge_job(title, location, description='', security_company=False,
              intern_hint=False):
    """Run the `evaluate_job` pipeline and name the gate that decided it.

    Returns ((level, category), None) for an accepted posting, or (None, reason)
    with reason one of JUDGE_REASONS. The stored-row pass logs the reason, and
    needs it to tell a gate an empty description can trip from the others.
    """
    if not title or is_rejected_title(title):
        return None, 'rejected-title'
    if not is_cyber_title(title, security_company):
        return None, 'not-cyber'
    if description and _is_facility_security_role(title, description):
        return None, 'facility-security'
    level = classify_level(title, description, intern_hint)
    # The title levels a multi-level req, but only the description can say it
    # is hired below level 3: a stated count the experience gate below then
    # bounds, or a low ceiling. A missing or silent one is 'no-level', which
    # the stored-row pass in scrape_jobs.py keeps when the body is missing.
    if (level not in (None, 'intern') and _spans_senior_level(title.lower())
            and not (permits_early_experience(description)
                     or any(_experience_counts(description)))):
        return None, 'no-level'
    if level is None:
        # A flat "Security Engineer" title at a security company with a low
        # experience ceiling in its description is an early-career role.
        if security_company and permits_early_experience(description):
            level = 'earlycareer'
        # AI labs use flat titles ("Software Engineer, AI Safety") with no
        # level marker, so an AI security/safety title needs early-career
        # evidence in its description: a low ceiling, or a stated floor of
        # at most two years. A silent description is not evidence: Anthropic
        # says only that years "will correlate with the internal job level",
        # and that put its flat Safeguards titles on the early-career table.
        elif AI_CATEGORY_RE.search(title.lower()) and (
                permits_early_experience(description)
                or 0 < required_years(description) <= MAX_ALLOWED_YEARS):
            level = 'earlycareer'
        else:
            return None, 'no-level'
    # A junior-sounding title is not proof of a junior role: "Security Engineer
    # II" and "Cyber Analyst II" reqs regularly ask for 4-8 years. Gate every
    # full-time level on the stated experience floor, not just the flat-title
    # fallback that used to be the only caller of this check (issue #11).
    # Internships are exempt: their level comes from an unambiguous title or
    # ATS signal, and research-internship reqs cite years of study in ways this
    # parser would misread as a floor.
    if level != 'intern' and exceeds_experience_cap(description):
        return None, 'over-experienced'
    if not is_us_location(location):
        return None, 'non-us-location'
    return (level, infer_category(title, security_company)), None


def listing_dedup_key(company, role, location):
    """Secondary dedup identity: the same (company, role, location) is one job.

    Complements URL dedup, which can't collapse a role reposted per-location
    under distinct req-ID URLs.
    """
    def norm(value):
        return re.sub(r'\s+', ' ', (value or '').strip()).lower()
    # Reposts of one req list the same sites in any order and spacing:
    # JPMorgan's two 'Hiring Event' reqs read 'McLean, VA; Jersey City, NJ' and
    # 'Jersey City, NJ; Mc Lean, VA'.
    sites = {re.sub(r'\s+', '', p).lower()
             for p in re.split(r'[;|]', location or '') if p.strip()}
    return norm(company), norm(role), ';'.join(sorted(sites))


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def purge_stale_listings(listings, today, max_age_days=60):
    """Drop closed listings older than N days; return (kept, removed_count).

    Only closed rows are eligible, so an old but still-open posting is kept and
    the board doesn't silently discard a live long-running req. Age is measured
    from closed_date, falling back to date_added. Community rows share the
    clock: the link check now needs two dead days before it closes one, so a
    single flake cannot start it.

    A row with no url but no closed flag is closed here, stamped today. Amazon
    'Software Dev Engineer II, Customer Service Security' sat that way as a
    padlocked row that counted as open and could never age out. Closed rows
    also shed their missing_since and dead_since streaks, which otherwise keep
    the scraper saving listings.json for rows nothing will judge again.
    """
    today_date = _parse_date(today) or datetime.now().date()
    cutoff = today_date - timedelta(days=max_age_days)
    kept, removed = [], 0
    for entry in listings:
        if not entry.get('closed') and not (entry.get('url') or '').strip():
            entry['closed'] = True
            entry.setdefault('closed_date', today_date.isoformat())
        if entry.get('closed'):
            entry.pop('missing_since', None)
            entry.pop('dead_since', None)
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


def reclassify_listings(listings, sec_flags=None):
    """Re-run title-only classification over stored rows.

    Returns (kept, changes, rejected). `changes` lists
    (company, role, old_type, new_type) re-levelings. `rejected` holds
    (row, reason) pairs: 'rejected-title' when the title now fails
    `is_rejected_title`, 'not-cyber' when it fails `is_cyber_title` under the
    company's current flag. A seniority or function term added after a row
    landed, or a `security_company` flag its employer lost, then retires the
    row instead of leaving it on the board until its link dies. The same title
    gates run at ingestion, so this never drops a row the scraper would accept
    today.

    `sec_flags` maps company name to its companies.yml `security_company` flag.
    This pass reaches rows whose posting has left the feed, which the
    live-posting pass in scrape_jobs.py cannot: Jumio 'Research Engineer -
    Machine Learning & Robotics' stayed a new-grad row after Jumio lost the
    flag. A company missing from the map, a board since removed, skips the
    cyber-title gate, because the flag that admitted its rows is unknown.

    Community rows reflect a maintainer's judgment and are left alone entirely.
    Intern rows may derive from an ATS employment-type hint a title can't
    reproduce, so they are exempt from re-leveling (not from the title gates).
    A title that yields no signal (None) keeps the stored, possibly
    description-derived, type.
    """
    sec_flags = sec_flags or {}
    kept, changes, rejected = [], [], []
    for entry in listings:
        if entry.get('source') == 'Community':
            kept.append(entry)
            continue
        company, role = entry.get('company', ''), entry.get('role', '')
        if is_rejected_title(role):
            rejected.append((entry, 'rejected-title'))
            continue
        if company in sec_flags and not is_cyber_title(role, sec_flags[company]):
            rejected.append((entry, 'not-cyber'))
            continue
        kept.append(entry)
        if entry.get('type') == 'intern':
            continue
        level = classify_level(role)
        if level and level != entry.get('type'):
            changes.append((company, role, entry.get('type'), level))
            entry['type'] = level
    return kept, changes, rejected
