#!/usr/bin/env python3
"""Spot checks for scrape_jobs.py classification logic.

Run from anywhere: python .github/scripts/test_classification.py
"""
import sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import scrape_jobs as s

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
]
for raw, want in NORM:
    got = s.normalize_location(raw)
    if got != want:
        failures += 1
        print(f'FAIL normalize_location({raw!r}) = {got!r}, want {want!r}')

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
u1 = s.normalize_url('https://boards.greenhouse.io/acme/jobs/123?gh_src=abc&utm_source=x')
u2 = s.normalize_url('https://boards.greenhouse.io/acme/jobs/123/')
assert u1 == u2, f'{u1} != {u2}'
w1 = s.normalize_url('https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin-TX/Security-Analyst_R123')
w2 = s.normalize_url('https://acme.wd5.myworkdayjobs.com/job/Austin-TX/Security-Analyst_R123')
assert w1 == w2, f'{w1} != {w2}'
print('URL normalization OK')

sys.exit(1 if failures else 0)
