#!/usr/bin/env python3
"""Spot checks for scrape_jobs.py classification logic.

Run from anywhere: python .github/scripts/test_classification.py
"""
import sys

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import classify as s
import common
import rebuild_readme as rr

CASES = [
    # (title, location, description, security_company, expected)
    # -- should be accepted --
    ('Associate Security Analyst', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('SOC Analyst I', 'San Antonio, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('SOC Analyst II', 'Remote (US)', '', False, ('earlycareer', 'SOC & Detection')),
    ('Cybersecurity Engineer, New Grad', 'New York, NY', '', False, ('newgrad', 'Security Engineering')),
    ('Security Engineer, University Grad', 'Menlo Park, CA', '', False, ('newgrad', 'Security Engineering')),
    ('Graduate Cybersecurity Analyst', 'Washington, DC', '', False, ('newgrad', 'Security Engineering')),
    ('Junior Penetration Tester', 'Arlington, VA', '', False, ('earlycareer', 'Offensive Security')),
    ('Incident Response Analyst I', 'Chicago, IL', '', False, ('earlycareer', 'SOC & Detection')),
    ('Associate Consultant, Offensive Security', 'Remote', '', False, ('earlycareer', 'Offensive Security')),
    ('Entry Level Cyber Threat Intelligence Analyst', 'Reston, VA', '', False, ('earlycareer', 'Threat Intelligence')),
    ('Software Engineer, New Grad', 'Sunnyvale, CA', '', True, ('newgrad', 'Engineering @ Security Co')),
    ('Associate Detection Engineer', 'Denver, CO', '', True, ('earlycareer', 'SOC & Detection')),
    ('Cyber Warfare Developer, Early Career', 'Fort Meade, MD', '', False, ('earlycareer', 'Security Engineering')),
    ('Information Security Analyst', 'Boston, MA', 'This is an entry level role for recent graduates.', False, ('newgrad', 'Security Engineering')),
    ('Security Engineer', 'Seattle, WA', 'We are looking for candidates with 0-2 years of experience.', False, ('earlycareer', 'Security Engineering')),
    ('GRC Analyst I', 'Tampa, FL', '', False, ('earlycareer', 'GRC & Risk')),
    ('Application Security Engineer I', 'Remote (US)', '', False, ('earlycareer', 'AppSec & ProdSec')),
    ('Cybersecurity Rotational Program', 'Charlotte, NC', '', False, ('newgrad', 'Security Engineering')),
    ('IAM Analyst - Early Career', 'Columbus, OH', '', False, ('earlycareer', 'Identity & IAM')),
    ('Digital Forensics Analyst, Associate', 'Huntsville, AL', '', False, ('earlycareer', 'Forensics & IR')),
    ('Cybersecurity Analyst Pathways Program', 'Palmdale, CA', '', False, ('newgrad', 'Security Engineering')),
    ('Cyber Leadership Development Program', 'Fort Worth, TX', '', False, ('newgrad', 'Security Engineering')),
    ('2026 Strategic Security Analyst - Early Career Rotation Program', 'Costa Mesa, CA', '', False, ('newgrad', 'Security Engineering')),
    ('2026 -  Associate Cyber Software Engineer', 'Annapolis Junction, MD', '', False, ('newgrad', 'Security Engineering')),
    ('R10206390 22026 Associate Cyber Software Engineer', 'Chantilly, VA', '', False, ('newgrad', 'Security Engineering')),
    # A year in the title only means a cohort when it's a hiring-cycle year.
    ('Cybersecurity Analyst II (Windows Server 2022)', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Junior SRE, Detection Platform', 'Austin, TX', '', True, ('earlycareer', 'SOC & Detection')),
    ('AI Security Engineer, Early Career', 'San Francisco, CA', '', False, ('earlycareer', 'AI Security & Safety')),
    ('Junior Adversarial ML Researcher', 'New York, NY', '', False, ('earlycareer', 'AI Security & Safety')),
    ('Research Engineer, Alignment Science - New Grad', 'San Francisco, CA', '', False, ('newgrad', 'AI Security & Safety')),
    ('Associate LLM Security Analyst', 'Seattle, WA', '', False, ('earlycareer', 'AI Security & Safety')),
    ('Analyst I, Safeguards', 'Remote (US)', '', False, ('earlycareer', 'AI Security & Safety')),
    ('AI Red Team Specialist, Entry Level', 'Washington, DC', '', False, ('earlycareer', 'AI Security & Safety')),
    # AI-lab flat titles are accepted unless the posting wants 3+ years.
    ('Software Engineer, AI Safety', 'San Francisco, CA', '', False, ('earlycareer', 'AI Security & Safety')),
    ('Researcher, Alignment Science', 'San Francisco, CA', 'Strong coding ability required.', False, ('earlycareer', 'AI Security & Safety')),
    ('AI Red Teamer', 'US, Remote', '', True, ('earlycareer', 'AI Security & Safety')),
    ('Junior Security Analyst', 'Remote- US', '', False, ('earlycareer', 'Security Engineering')),
    # 'Architect' is a senior signal, but a named early-career cohort overrides
    # it (NVIDIA's "Security Architect - New College Grad" is a new-grad req).
    ('Security Architect - New College Grad 2026', 'Santa Clara, CA', '', False, ('newgrad', 'Security Engineering')),
    ('Security Architect Intern', 'Austin, TX', '', False, ('intern', 'Security Engineering')),

    # -- should be accepted: internships --
    ('Security Engineer Intern', 'Austin, TX', '', False, ('intern', 'Security Engineering')),
    ('Cybersecurity Co-op', 'Boston, MA', '', False, ('intern', 'Security Engineering')),
    ('SOC Analyst Intern - Summer 2027', 'San Antonio, TX', '', False, ('intern', 'SOC & Detection')),
    ('Offensive Security Intern', 'Remote (US)', '', False, ('intern', 'Offensive Security')),
    ('Cybersecurity Summer Analyst', 'New York, NY', '', False, ('intern', 'Security Engineering')),
    ('Student Trainee (Cybersecurity)', 'Washington, DC', '', False, ('intern', 'Security Engineering')),
    # A season + cohort year is an internship req even without the word
    # "intern" — but explicit new-grad wording wins over the season, and a
    # stale year outside the cohort window is not resurrected as an intern.
    ('Security Engineer - Summer 2026', 'Seattle, WA', '', False, ('intern', 'Security Engineering')),
    ('New Grad Security Engineer - Summer 2026 Start', 'Austin, TX', '', False, ('newgrad', 'Security Engineering')),
    ('Security Engineer - Summer 2019', 'Seattle, WA', '', False, None),
    ('Software Engineer Intern', 'Austin, TX', '', True, ('intern', 'Engineering @ Security Co')),
    # "Internal" must not trip the intern regex.
    ('Internal Tools Security Analyst I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),

    # -- should be rejected: intern edge cases --
    ('Software Engineer Intern', 'Austin, TX', '', False, None),      # not cyber
    ('Security Engineer Intern', 'Toronto, ON', '', False, None),     # not US
    ('Marketing Intern', 'Austin, TX', '', True, None),               # non-tech function

    # -- should be rejected: seniority --
    ('Senior Security Engineer', 'Austin, TX', '', False, None),
    ('Staff Security Engineer', 'Austin, TX', '', False, None),
    ('Principal Cybersecurity Architect', 'Austin, TX', '', False, None),
    ('Security Engineering Manager', 'Austin, TX', '', False, None),
    ('SOC Analyst III', 'Austin, TX', '', False, None),
    ('Lead Incident Responder', 'Austin, TX', '', False, None),
    ('Sr. Security Analyst', 'Austin, TX', '', False, None),
    ('Security Analyst, Sr', 'Austin, TX', '', False, None),
    # 'Architect' with no early-career signal is still a senior IC, and an
    # explicit senior word wins even inside a cohort title.
    ('Security Architect', 'Austin, TX', '', False, None),
    ('Senior Security Architect - New Grad', 'Austin, TX', '', False, None),

    # -- should be rejected: not cyber --
    ('Software Engineer, New Grad', 'Austin, TX', '', False, None),
    ('Junior Financial Analyst', 'New York, NY', '', True, None),
    ('Sales Development Representative', 'Austin, TX', '', True, None),
    ('Associate Marketing Manager', 'Austin, TX', '', True, None),
    ('Junior Recruiter', 'Austin, TX', '', True, None),
    ('Credit Risk Analyst I', 'New York, NY', '', False, None),
    ('Machine Learning Engineer, New Grad', 'San Francisco, CA', '', False, None),
    ('Junior Nuclear Safeguards Analyst', 'Richland, WA', '', False, None),

    # -- should be rejected: physical security --
    ('Security Guard', 'Austin, TX', '', False, None),
    ('Security Officer - Night Shift', 'Austin, TX', '', False, None),
    ('Physical Security Specialist', 'Austin, TX', '', False, None),
    ('Loss Prevention Associate', 'Austin, TX', '', False, None),

    # -- should be rejected: no level signal --
    ('Security Engineer', 'Austin, TX', '', False, None),
    ('Threat Hunter', 'Austin, TX', '', False, None),
    # AI flat-title acceptance does not apply when the posting wants 3+ years
    ('Researcher, Alignment', 'San Francisco, CA', 'You have 5+ years of research experience.', False, None),
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Requires 7 years of industry experience.', False, None),

    # -- should be rejected: not US --
    ('Junior Security Analyst', 'London, United Kingdom', '', False, None),
    ('SOC Analyst I', 'Toronto, ON', '', False, None),
    ('Associate Security Engineer', 'Bangalore, India', '', False, None),
    ('New Grad Security Engineer', 'Waterloo, ON', '', False, None),
    ('Graduate Cyber Analyst', 'Sydney, Australia', '', False, None),
    ('Junior Security Engineer', 'Remote (EMEA)', '', False, None),

    # -- bug fix: leveled numerals reject only in role-noun context --
    # "III/IV/3/4" no longer bare-match version/layer/standard numbers.
    ('Cybersecurity Analyst I (PCI DSS 4.0)', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Layer 3 Network Security Analyst I', 'Reston, VA', '', False, ('earlycareer', 'Cloud & Infra Security')),
    ('Cyber IV&V Engineer I', 'Huntsville, AL', '', False, ('earlycareer', 'Security Engineering')),
    # ...but a real leveled-senior marker is still rejected.
    ('SOC Analyst III', 'Austin, TX', '', False, None),
    ('Tier 3 Incident Responder', 'Austin, TX', '', False, None),
    ('Security Engineer IV', 'Austin, TX', '', False, None),

    # -- bug fix: multi-region remote roles with a US option are accepted --
    ('Backend Security Engineer I', 'New York, San Francisco, or Remote (US/Canada)', '', False, ('earlycareer', 'Security Engineering')),
    ('Junior Penetration Tester', 'Remote - US or Canada', '', False, ('earlycareer', 'Offensive Security')),
    # ...but an all-foreign multi-location is still rejected.
    ('Security Engineer I', 'Toronto, ON; London, UK', '', False, None),
    ('Junior Security Analyst', 'Zürich', '', False, None),  # accented foreign city

    # -- bug fix: FUNCTION_REJECT short terms are word-bounded --
    ('Salesforce Security Engineer, New Grad', 'Austin, TX', '', False, ('newgrad', 'Security Engineering')),
    # ...but a genuine sales role is still rejected.
    ('Sales Engineer, Security Products', 'Austin, TX', '', True, None),

    # -- bug fix: a leveled I/II marker beats a bare cohort year --
    ('Cybersecurity Analyst II (Windows Server 2026)', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),

    # -- bug fix: bare 'safeguards' with a nuclear context is not AI security --
    ('Radiological Safeguards Analyst', 'Remote (US)', '', False, None),

    # -- bug fix: requires_experience anchors to a requirement context --
    # incidental "past 5 years" is not an experience requirement (AI role kept).
    ('Researcher, Alignment Science', 'San Francisco, CA', 'You will analyze the past 5 years of incidents.', False, ('earlycareer', 'AI Security & Safety')),
    # spelled-out and abbreviated year requirements still gate the AI path.
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Requires three years of experience.', False, None),
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Minimum 4+ yrs of experience required.', False, None),

    # -- recall: flat title at a security company with a low YOE ceiling --
    ('Security Engineer', 'Austin, TX', 'Ideal for candidates with 0-2 years of experience.', True, ('earlycareer', 'Security Engineering')),
]

failures = 0
for title, loc, desc, sec_co, expected in CASES:
    got = s.evaluate_job(title, loc, desc, sec_co)
    ok = got == expected
    if not ok:
        failures += 1
        print(f'FAIL: {title!r} @ {loc!r} (sec_co={sec_co})')
        print(f'      expected {expected}, got {got}')
print(f'\n{len(CASES) - failures}/{len(CASES)} passed')

# Location normalization checks
NORM = [
    ('Austin, Texas', 'Austin, TX'),
    ('Remote', 'Remote (US)'),
    ('remote - us', 'Remote (US)'),
    ('Fort Meade, Maryland', 'Fort Meade, MD'),
    ('New York, NY', 'New York, NY'),
    # Workday "Office - USA - <ST>[- <site>]" forms.
    ('Office - USA - VA - Reston', 'Reston, VA'),
    ('Office - USA - CA - Headquarters', 'CA (US)'),
    ('Office - USA - TX', 'TX (US)'),
    ('Virtual Location - Virginia, VA', 'Remote (US)'),
    # Opaque facility codes are dropped; a real city in the same multi-location
    # string survives.
    ('Annapolis Junction, MD; CASD14; TXSA08UNK', 'Annapolis Junction, MD'),
    # Bare-city duplicate folds into the qualified spelling.
    ('Austin, TX; Austin', 'Austin, TX'),
    ('Portland, OR; Portland, ME', 'Portland, OR; Portland, ME'),
]
for raw, want in NORM:
    got = s.normalize_location(raw)
    if got != want:
        failures += 1
        print(f'FAIL normalize_location({raw!r}) = {got!r}, want {want!r}')

# classify_level word-boundary checks: short signals must not match inside
# longer tokens, and a leveled II beats a cohort year.
LEVEL = [
    ('SOC Level 10 Analyst', None),           # 'level 1' not inside 'level 10'
    ('Associated Bank Security Analyst', None),  # 'associate' not in 'associated'
    ('Cybersecurity Analyst II (Windows Server 2026)', 'earlycareer'),
    ('Tier 2 SOC Analyst', 'earlycareer'),    # 'tier 2' survives word-bounding
]
for title, want in LEVEL:
    got = s.classify_level(title)
    if got != want:
        failures += 1
        print(f'FAIL classify_level({title!r}) = {got!r}, want {want!r}')

# is_us_location: multi-region acceptance and accent-aware foreign rejection.
US_LOC = [
    ('Remote (US/Canada)', True),
    ('New York, NY; Toronto, Canada', True),
    ('Remote - US, UK', True),
    ('Remote (EMEA)', False),
    ('Zürich', False),
    ('Bogotá, Colombia', False),
    ('Office - USA - VA - Reston', True),
    # A US city whose name collides with a foreign one is rescued by ", ST".
    ('Vienna, VA', True),
    ('Paris, TX', True),
    # ...but an 'us' buried in prose or a mid-string state before a country
    # must NOT leak a foreign role onto this US-only board.
    ('Bangalore, India (US hours)', False),
    ('Remote - India (US business hours)', False),
    ('London, UK - reports to US team', False),
    ('Chennai, TN, India', False),  # TN=Tamil Nadu collides with Tennessee
    ('Berlin (must overlap US business hours)', False),
]
for loc, want in US_LOC:
    got = s.is_us_location(loc)
    if got != want:
        failures += 1
        print(f'FAIL is_us_location({loc!r}) = {got!r}, want {want!r}')

# requires_experience: phrasing coverage + no false positive on incidental years.
EXP = [
    ('Minimum of 3 years of experience.', True),
    ('Requires 5+ yrs of experience.', True),
    ('You need three years of experience.', True),
    ('We reviewed the past 3 years of CVEs.', False),
    ('Ideal for candidates with 0-2 years of experience.', False),
    ('', False),
]
for desc, want in EXP:
    got = s.requires_experience(desc)
    if got != want:
        failures += 1
        print(f'FAIL requires_experience({desc!r}) = {got!r}, want {want!r}')

# reclassify_listings: skip community + intern; flip a stale earlycareer row.
RECLASS = [
    {'company': 'A', 'role': 'Cybersecurity Rotational Program',
     'type': 'earlycareer', 'source': 'Greenhouse'},   # -> newgrad
    {'company': 'B', 'role': 'Software Engineer Intern',
     'type': 'intern', 'source': 'Ashby'},              # intern: untouched
    {'company': 'C', 'role': 'New Grad Security Engineer',
     'type': 'earlycareer', 'source': 'Community'},     # community: untouched
    {'company': 'D', 'role': 'SOC Analyst II',
     'type': 'earlycareer', 'source': 'Lever'},         # already correct
]
_, reclass_changes = s.reclassify_listings([dict(r) for r in RECLASS])
if len(reclass_changes) != 1 or reclass_changes[0][0] != 'A' or reclass_changes[0][3] != 'newgrad':
    failures += 1
    print(f'FAIL reclassify_listings changes = {reclass_changes!r}, '
          f"want one A earlycareer->newgrad")

# purge_stale_listings: drop long-closed rows, keep recent-closed and open ones.
LIFECYCLE = [
    {'company': 'A', 'role': 'x', 'closed': True, 'closed_date': '2026-01-01'},  # old -> drop
    {'company': 'B', 'role': 'y', 'closed': True, 'closed_date': '2026-07-10'},  # recent -> keep
    {'company': 'C', 'role': 'z', 'date_added': '2026-01-01'},                   # open & old -> keep
]
kept, removed = s.purge_stale_listings([dict(r) for r in LIFECYCLE], '2026-07-18', max_age_days=60)
if removed != 1 or {e['company'] for e in kept} != {'B', 'C'}:
    failures += 1
    print(f'FAIL purge_stale_listings: removed={removed}, kept={[e["company"] for e in kept]}')

# prune_seen: expire ids not refreshed within the TTL.
pruned = s.prune_seen({'a': '2026-07-18', 'b': '2026-01-01'}, '2026-07-18', ttl_days=45)
if pruned != {'a': '2026-07-18'}:
    failures += 1
    print(f'FAIL prune_seen = {pruned!r}, want {{a: 2026-07-18}}')

# requires_clearance drives the 🇺🇸 marker on every listing but had no coverage.
CLEAR = [
    ('Cyber Analyst', 'Active TS/SCI clearance required.', True),
    ('Cyber Analyst', 'Must be a US citizen.', True),
    ('Cyber Analyst', 'Remote, no clearance needed but a public trust helps.', True),
    ('Security Engineer', 'No special requirements.', False),
]
for title, desc, want in CLEAR:
    got = s.requires_clearance(title, desc)
    if got != want:
        failures += 1
        print(f'FAIL requires_clearance({title!r}, {desc!r}) = {got!r}, want {want!r}')

# ATS employment-type hint: intern reqs whose titles omit the word.
hint_cases = [
    # (title, intern_hint, expected level)
    ('Security Engineer, University Program', True, 'intern'),
    ('Security Engineer, University Program', False, None),
]
for title, hint, want_level in hint_cases:
    got = s.evaluate_job(title, 'Austin, TX', '', False, intern_hint=hint)
    got_level = got[0] if got else None
    if got_level != want_level:
        failures += 1
        print(f'FAIL intern_hint={hint}: {title!r} -> {got!r}, want level {want_level!r}')

# URL normalization checks
u1 = common.normalize_url('https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=x')
u2 = common.normalize_url('https://boards.greenhouse.io/acme/jobs/123/')
assert u1 == u2, f'{u1} != {u2}'
w1 = common.normalize_url('https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/Security-Analyst_R123')
w2 = common.normalize_url('https://acme.wd5.myworkdayjobs.com/job/Austin-TX/Security-Analyst_R123')
assert w1 == w2, f'{w1} != {w2}'
# Rendering safety: no field can break out of a README table row or inject a
# working link. A cell can only ever contain escaped pipes/brackets/backticks.
RENDER = [
    ('escape_cell newline collapses', '\n' not in rr.escape_cell('Analyst\n| x | y |')),
    ('escape_cell escapes bare pipe', rr.escape_cell('a | b') == 'a \\| b'),
    ('escape_cell escapes backslash before pipe', rr.escape_cell('a\\|b') == 'a\\\\\\|b'),
    ('escape_cell escapes angle brackets', rr.escape_cell('<b>') == '&lt;b&gt;'),
    ('apply_btn rejects whitespace in url', rr.apply_btn('https://x/j\n| Fake |') == '🔒'),
    ('apply_btn rejects javascript:', rr.apply_btn('javascript:alert(1)') == '🔒'),
    ('apply_btn escapes pipe in url', '|' not in rr.apply_btn('https://x/a|b')),
    ('apply_btn renders a clean https url', rr.apply_btn('https://x/job').startswith('<a href="https://x/job"')),
]
for name, ok in RENDER:
    if not ok:
        failures += 1
        print(f'FAIL render: {name}')

# strip_html caps pathological input so the tag-strip regex stays sub-quadratic.
_huge = '<' * 300000
if len(s.strip_html(_huge)) > s.MAX_DESCRIPTION_CHARS + 10:
    failures += 1
    print('FAIL strip_html did not cap description length')

# Greenhouse serves the same board under two hosts; they must dedupe.
g1 = common.normalize_url('https://boards.greenhouse.io/acme/jobs/9')
g2 = common.normalize_url('https://job-boards.greenhouse.io/acme/jobs/9')
assert g1 == g2, f'{g1} != {g2}'
# Workday locale + multiple pre-/job/ segments collapse to the bare /job/ form.
m1 = common.normalize_url('https://acme.wd5.myworkdayjobs.com/en-US/CompanyCareers/External/job/Austin/Sec_R1')
m2 = common.normalize_url('https://acme.wd5.myworkdayjobs.com/job/Austin/Sec_R1')
assert m1 == m2, f'{m1} != {m2}'
print('URL normalization OK')

sys.exit(1 if failures else 0)
