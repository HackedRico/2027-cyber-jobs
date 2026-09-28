#!/usr/bin/env python3
"""Spot checks for scrape_jobs.py classification logic.

Run from anywhere: python .github/scripts/test_classification.py
"""
import sys
from datetime import date

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import classify as s
import common
import rebuild_readme as rr

# Intern titles carrying a year are judged against the season recruiting today,
# so the rows below name that season instead of a fixed year.
SEASON = s.first_open_season()

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
    # AI-lab flat titles are accepted only on early-career evidence in the
    # description: a stated floor of 1-2 years, or a ceiling like "0-2 years".
    ('Software Engineer, AI Safety', 'San Francisco, CA', 'You have 2+ years of software engineering experience.', False, ('earlycareer', 'AI Security & Safety')),
    ('Researcher, Alignment Science', 'San Francisco, CA', 'Open to candidates with 0-2 years of research experience.', False, ('earlycareer', 'AI Security & Safety')),
    ('AI Red Teamer', 'US, Remote', 'Requires 1+ years of red teaming experience.', True, ('earlycareer', 'AI Security & Safety')),
    ('Fellows Program, AI Safety', 'San Francisco, CA', '', False, ('earlycareer', 'AI Security & Safety')),
    ('Junior Security Analyst', 'Remote- US', '', False, ('earlycareer', 'Security Engineering')),
    # 'Architect' is a senior signal, but a named early-career cohort overrides
    # it (NVIDIA's "Security Architect - New College Grad" is a new-grad req).
    ('Security Architect - New College Grad 2026', 'Santa Clara, CA', '', False, ('newgrad', 'Security Engineering')),
    ('Security Architect Intern', 'Austin, TX', '', False, ('intern', 'Security Engineering')),

    # -- should be accepted: internships --
    ('Security Engineer Intern', 'Austin, TX', '', False, ('intern', 'Security Engineering')),
    ('Cybersecurity Co-op', 'Boston, MA', '', False, ('intern', 'Security Engineering')),
    (f'SOC Analyst Intern - Summer {SEASON}', 'San Antonio, TX', '', False, ('intern', 'SOC & Detection')),
    ('Offensive Security Intern', 'Remote (US)', '', False, ('intern', 'Offensive Security')),
    ('Cybersecurity Summer Analyst', 'New York, NY', '', False, ('intern', 'Security Engineering')),
    ('Student Trainee (Cybersecurity)', 'Washington, DC', '', False, ('intern', 'Security Engineering')),
    # A season + cohort year is an internship req even without the word
    # "intern" — but explicit new-grad wording wins over the season, and a
    # stale year outside the cohort window is not resurrected as an intern.
    (f'Security Engineer - Summer {SEASON}', 'Seattle, WA', '', False, ('intern', 'Security Engineering')),
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
    # Management titles that carry no 'lead'/'manager' token.
    ('Tier II SOC Supervisor', 'Austin, TX', '', False, None),
    ('Data & AI Governance Leader, MD', 'Boston, MA', '', False, None),

    # -- should be rejected: not cyber --
    ('Software Engineer, New Grad', 'Austin, TX', '', False, None),
    ('Junior Financial Analyst', 'New York, NY', '', True, None),
    ('Sales Development Representative', 'Austin, TX', '', True, None),
    ('Associate Marketing Manager', 'Austin, TX', '', True, None),
    ('Junior Recruiter', 'Austin, TX', '', True, None),
    ('Credit Risk Analyst I', 'New York, NY', '', False, None),
    ('Machine Learning Engineer, New Grad', 'San Francisco, CA', '', False, None),
    ('Junior Nuclear Safeguards Analyst', 'Richland, WA', '', False, None),
    # Finance and support functions at a security company are still not cyber.
    ('Treasury Operations Analyst', 'Emeryville, CA', '', True, None),
    ('Customer Support Engineer (Tier 1)', 'Remote (US)', '', True, None),
    ('Internal Audit (SOX/SOC) Intern', 'Bloomfield, CT', '', False, None),
    # ...but a technical support engineer at a security company still counts.
    ('Support Engineer I', 'Dallas, TX', '', True, ('earlycareer', 'Engineering @ Security Co')),

    # Walmart store loss prevention; the licence is a guard licence.
    ('(CAN) Asset Protection Associate (MUST HAVE SECURITY LICENSE)', 'Bentonville, AR', '', False, None),
    ('Asset Protection Associate PART TIME (SECURITY LICENSE REQUIRED)', 'Bentonville, AR', '', False, None),
    ('(USA) Security Associate, Manufacturing (Tue - Fri, Overnight)', 'Olathe, KS', '', False, None),
    ('Security Associate, Manufacturing (Tuesday-Friday, 11:00am-9:30pm) - $21.30/hr.', 'Robinson, TX', '', False, None),
    # ...but a SOC night shift is still a SOC role.
    ('SOC Analyst I (Overnight Shift)', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),

    # -- should be rejected: reqs a student cannot apply to --
    ('MDR Analyst Skillbridge Intern (Active Duty Military only)', 'Remote (US)', '', True, None),
    ('Cyber Threat Intelligence Analyst SkillBridge Internship (Active Duty Military Only)', 'Remote (US)', '', True, None),
    ('2026 Intern Conversion:  2027 Return Intern Cybersecurity', 'Bentonville, AR', '', False, None),
    ('2026 Intern Conversion: 2027 FT Penetration Testing Engineer II', 'Bentonville, AR', '', False, None),
    ('Returning Intern - Information Security', 'Bentonville, AR', '', False, None),
    ('Hiring Event - Security Architecture & Engineering - Sep 24-25th 2026', 'McLean, VA; Jersey City, NJ', '', False, None),
    # -- should be rejected: sales, support and design at security companies --
    ('Developer Support Associate (New Grad)', 'San Francisco, CA', '', True, None),
    ('Deals Desk Analyst II', 'Draper, UT', '', True, None),
    ('Associate Authentication Analyst', 'Lehi, UT', '', True, None),
    ('Enterprise Account Exeuctive, AI Security', 'San Francisco, CA', '', True, None),
    ('Product Designer, Safeguards', 'San Francisco, CA', '', False, None),
    ('Associate Program Analyst (New Grad)', 'San Francisco, CA', '', True, None),
    # ...but a program analyst on a cyber team is GRC work and stays.
    ('Cybersecurity Program Analyst I', 'Arlington, VA', '', False, ('earlycareer', 'Security Engineering')),

    # -- should be rejected: "Security Clearance" is a requirement, not the work --
    ('Computer Scientist / Software Developer, Junior - Security Clearance Required', 'Adelphi, MD', '', False, None),
    ('Software Engineer I, CDS (Onsite - Security Clearance)', 'Cedar Rapids, IA', '', False, None),
    # ...while a cyber title that also names the clearance stays.
    ('Cyber Software Engineer I (Security Clearance Required)', 'Chantilly, VA', '', False, ('earlycareer', 'Security Engineering')),
    # -- should be rejected: facility-security work behind a bare 'security' title --
    ('Security Specialist II', 'Cambridge, MA',
     'Maintain classified document control and personnel security processing '
     'in accordance with 32 CFR part 117 and the NISPOM rule.', False, None),
    # ...but a cyber title citing the NISPOM, or a bare one that does not, stays.
    ('Cyber Security Analyst 1', 'Waimea, HI',
     'Perform security tasks required by the 32 CFR part 117 National '
     'Industrial Security Operating Manual (NISPOM) and NIST SP 800-53.', False,
     ('earlycareer', 'Security Engineering')),
    ('Security Specialist II', 'Cambridge, MA',
     'Triage endpoint alerts and tune SIEM detections.', False,
     ('earlycareer', 'Security Engineering')),

    # -- should be rejected: physical security --
    ('Security Guard', 'Austin, TX', '', False, None),
    ('Security Officer - Night Shift', 'Austin, TX', '', False, None),
    ('Physical Security Specialist', 'Austin, TX', '', False, None),
    ('Loss Prevention Associate', 'Austin, TX', '', False, None),
    # Cleared-facility security (FSO/adjudication/guard force) is not cyber.
    ('Associate Industrial Security Analyst', 'Falls Church, VA', '', False, None),
    ('Personnel Security Specialist I Adjudicator', 'Chantilly, VA', '', False, None),
    ('Enterprise Protective Services, Corporate Security Intern - Summer 2027', 'Charlotte, NC', '', False, None),

    # -- should be rejected: no level signal --
    ('Security Engineer', 'Austin, TX', '', False, None),
    ('Threat Hunter', 'Austin, TX', '', False, None),
    # AI flat-title acceptance does not apply when the posting wants 3+ years
    ('Researcher, Alignment', 'San Francisco, CA', 'You have 5+ years of research experience.', False, None),
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Requires 7 years of industry experience.', False, None),
    # ...nor when the description is silent on years. Anthropic's flat titles
    # say only that years "will correlate with the internal job level".
    ('Software Engineer, AI Safety', 'San Francisco, CA', '', False, None),
    ('Safeguards Enforcement Analyst', 'San Francisco, CA',
     'Years of experience will correlate with the internal job level requirements.', False, None),
    ('Researcher, Alignment Science', 'San Francisco, CA', 'Strong coding ability required.', False, None),

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
    ('Researcher, Alignment Science', 'San Francisco, CA', 'You will analyze the past 5 years of incidents across our products and publish what you learn from them. Requires 1+ years of research experience.', False, ('earlycareer', 'AI Security & Safety')),
    # spelled-out and abbreviated year requirements still gate the AI path.
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Requires three years of experience.', False, None),
    ('Software Engineer, AI Safety', 'Seattle, WA', 'Minimum 4+ yrs of experience required.', False, None),

    # -- recall: flat title at a security company with a low YOE ceiling --
    ('Security Engineer', 'Austin, TX', 'Ideal for candidates with 0-2 years of experience.', True, ('earlycareer', 'Security Engineering')),

    # -- bug fix: guarded function rejects keep genuine cyber roles --
    ('Data Loss Prevention (DLP) Analyst I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Software Supply Chain Security Engineer I', 'Seattle, WA', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Research Engineer I, AI Safety and Security Engineering', 'San Francisco, CA', '', False, ('earlycareer', 'AI Security & Safety')),
    # ...while the non-cyber functions they guard are still rejected.
    ('Loss Prevention Associate', 'Austin, TX', '', False, None),
    ('Supply Chain Analyst I', 'Austin, TX', '', True, None),
    ('Safety and Security Officer', 'Austin, TX', '', False, None),

    # -- categories: titles that used to fall into the catch-all buckets --
    ('Analyst I, Falcon Complete GovCloud (Hybrid, St Louis)', 'St. Louis, MO', '', True, ('earlycareer', 'SOC & Detection')),
    ('MDR Analyst I', 'Remote (US)', '', True, ('earlycareer', 'SOC & Detection')),
    ('Cyber Network Defense Analyst II', 'Sterling, VA', '', False, ('earlycareer', 'SOC & Detection')),
    ('Cyber Ops Analyst II', 'Pearl Harbor, HI', '', False, ('earlycareer', 'SOC & Detection')),
    ('Junior CNO Developer (Onsite)', 'Annapolis Junction, MD', '', False, ('earlycareer', 'Offensive Security')),
    ('Cyber Intelligence Associate-Brand Protection Associate', 'New York, NY', '', False, ('earlycareer', 'Threat Intelligence')),
    ('Fraud Analyst I', 'Somerville, MA', '', True, ('earlycareer', 'Threat Intelligence')),

    # -- recall: live early-career titles the rules used to miss --
    ('Cybersecurity Ops Technologist I (Email Security)', 'Hartford, CT', '', False, ('earlycareer', 'Security Engineering')),
    ('IS Security Officer 1', 'Cambridge, MA', '', False, ('earlycareer', 'Security Engineering')),
    ('Computer Network Defense Analyst (CNDA) Level 1', 'Jessup, MD', '', False, ('earlycareer', 'SOC & Detection')),
    ('Incident Responder I', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Junior Incident Responder', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Technology Leadership Program - Risk & Security (Analyst)', 'Malvern, PA', '', False, ('newgrad', 'GRC & Risk')),
    ('Cyber New Professionals Program', 'McLean, VA', '', False, ('newgrad', 'Security Engineering')),
    ('Internships in Cybersecurity and Information Security', 'McLean, VA', '', False, ('intern', 'Security Engineering')),
    # ...and the limits on those rules. A guard post stays a guard post, an
    # officer counts only at level 1, and 'Jr.' is not a level signal: it
    # would admit Leidos 'Jr. Security Specialist', a badging role.
    ('Security Officer I -Plant McIntosh, Rincon, GA', 'Rincon, GA', '', False, None),
    ('Security Officer II', 'Austin, TX', '', False, None),
    ('Security Officer - Kansas City, Missouri', 'Kansas City, MO', '', False, None),
    ('Information Security Officer 2', 'Cambridge, MA', '', False, None),
    ('Information System Security Officer - Jr.', 'Washington, DC', '', False, None),
    ('Jr. Security Specialist', 'Omaha, NE', '', False, None),
    ('Cybersecurity Technologist III', 'Hartford, CT', '', False, None),
    ('Emergency Responder I', 'Austin, TX', '', False, None),

    # -- bug fix: US territories are US locations, not foreign --
    ('Cyber Software Engineer I', 'Aguadilla, PR', '', False, ('earlycareer', 'Security Engineering')),
    ('Student Trainee (Cybersecurity)', 'Hagatna, GU', '', False, ('intern', 'Security Engineering')),
    # ...but a foreign territory that merely shares a name is still rejected.
    ('Junior Security Analyst', 'Tortola, British Virgin Islands', '', False, None),
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
    # Workplace tags and office affixes are stripped.
    ('Emeryville, CA (Hybrid)', 'Emeryville, CA'),
    ('Shakopee, MN (GHQ)', 'Shakopee, MN'),
    ('HQ - Sunnyvale (Office)', 'Sunnyvale, CA'),
    ('San Francisco Office', 'San Francisco, CA'),
    # Remote spellings without a US token still mean US remote; a region does not.
    ('Remote-Friendly (Travel-Required)', 'Remote (US)'),
    ('Remote (Any State)', 'Remote (US)'),
    ('Remote - U.S.', 'Remote (US)'),
    ('US, Virtual', 'Remote (US)'),
    ('Remote (EMEA)', 'Remote (EMEA)'),
    # Workday site strings with street addresses and site codes.
    ('MA-TEWKSBURY-TB1 ~ 50 Apple Hill Dr ~ ASSABET BLDG', 'Tewksbury, MA'),
    ('TX-MCKINNEY-513WC ~ 2501 W University Dr ~ WING C BLDG', 'McKinney, TX'),
    ('MT-GREAT FALLS-6932-CUST ~ 6932 Goddard Dr ~ GODDARD (External Site)', 'Great Falls, MT'),
    ('MD - Baltimore, 8031 Corporate Dr', 'Baltimore, MD'),
    ('CT, Bloomfield, 900 Cottage Grove Rd Wilde Bldg', 'Bloomfield, CT'),
    ('M252 Raleigh - 4110 Wake Forest Rd', 'Raleigh, NC'),
    ('VA-Dahlgren', 'Dahlgren, VA'),
    ('HI-Pearl Harbor', 'Pearl Harbor, HI'),
    ('USA_TX_Richardson', 'Richardson, TX'),
    ('(USA) ISD Office - DGTC AR BENTONVILLE Home Office', 'Bentonville, AR'),
    ('US, California, Santa Clara', 'Santa Clara, CA'),
    # Country suffixes, bare states, and unambiguous bare cities.
    ('Austin, Texas, United States of America', 'Austin, TX'),
    ('Seattle, United States of America', 'Seattle, WA'),
    ('San Mateo, CA United States', 'San Mateo, CA'),
    ('Arizona', 'AZ (US)'),
    ('New York', 'NY (US)'),  # a state name wins over the city reading
    ('New York City', 'New York City, NY'),
    ('Atlanta GA', 'Atlanta, GA'),
    ('Los Angeles', 'Los Angeles, CA'),
    ('Portland', 'Portland'),  # ambiguous bare city is left alone
    # "|" separates options too, and foreign options drop beside a US one.
    ('Remote-Friendly (Travel-Required) | San Francisco, CA | Washington, DC',
     'Remote (US); San Francisco, CA; Washington, DC'),
    ('College Park, MD | Columbus, OH', 'College Park, MD; Columbus, OH'),
    ('London, UK; Ontario, CAN; Remote (US); San Francisco, CA', 'Remote (US); San Francisco, CA'),
    ('High Point, NC; International - Germany', 'High Point, NC'),
    ('Paris, TX; London, UK', 'Paris, TX'),
    ('London, UK', 'London, UK'),  # nothing US to keep, so nothing is dropped
    # US territories resolve to their postal code like any state.
    ('San Juan, Puerto Rico', 'San Juan, PR'),
    ('Barrigada, Guam', 'Barrigada, GU'),
    ('Pago Pago, American Samoa', 'Pago Pago, AS'),
    ('PR-AGUADILLA-110 ~ Rd 110 N Km 28.8 ~ RD110', 'Aguadilla, PR'),
    ('Aguadilla, PR; Tortola, British Virgin Islands', 'Aguadilla, PR'),
    # A remote role scoped to one spelled-out state, in the shapes ATSs emit.
    ('Remote - California', 'Remote, CA'),
    ('California - Remote', 'Remote, CA'),
    ('GEORGIA - VIRTUAL - GA01', 'Remote, GA'),
    ('Work At Home-Texas', 'Remote, TX'),
    ('Home Office - Northern California', 'Remote, CA'),
    ('Illinois Remote Work, More...', 'Remote, IL'),
    ('Puerto Rico Remote Work', 'Remote, PR'),
    ('Remote - Georgia; Remote - Texas', 'Remote, GA; Remote, TX'),
    # The capital is not Washington state, in any of its spellings.
    ('Remote - Washington, D.C.', 'Remote, DC'),
    ('Washington D.C. - Remote', 'Remote, DC'),
    ('Remote - Washington', 'Remote, WA'),
    # ...and a city named alongside the state keeps its detail.
    ('Remote - Miami, Florida', 'Remote - Miami, FL'),
    ('San Francisco, California (remote)', 'San Francisco, CA'),
    ('Virginia - Herndon', 'Herndon, VA'),
    # A country prefix in front of the state code must not be read as the
    # state: "US-AZ-TUCSON-805 ~ ..." used to collapse to "Az".
    ('US-AZ-TUCSON-805 ~ 1151 E Hermans Rd ~ BLDG 805', 'Tucson, AZ'),
    ('US-CT-EAST HARTFORD-ETC ~ 400 Main St ~ BLDG ETC', 'East Hartford, CT'),
    ('US-CA-Menlo Park', 'Menlo Park, CA'),
    ('US-DC-Washington', 'Washington, DC'),
    # ...while the spelled-out country/state form still resolves.
    ('United States-California-Palmdale', 'Palmdale, CA'),
    # Workday's ", More..." suffix, a state code ahead of its city, and The
    # Home Depot's "<SITE>, <CITY> - <store number>" site names.
    ('Chicago, IL, More...', 'Chicago, IL'),
    ('Illinois Remote Work, More...', 'Remote, IL'),
    ('VA, McLean', 'McLean, VA'),
    ('IL, Haifa', 'IL, Haifa'),  # IL is Israel here; an unknown city is left alone
    ('STORE SUPPORT CENTER, ATLANTA - 9090', 'Atlanta, GA'),
    ('OH, United States', 'OH'),
]
for raw, want in NORM:
    got = s.normalize_location(raw)
    if got != want:
        failures += 1
        print(f'FAIL normalize_location({raw!r}) = {got!r}, want {want!r}')
    # Normalizing an already-normalized value must be a no-op: every scrape
    # re-runs renormalize_locations over stored rows, so a rule that keeps
    # rewriting its own output degrades the board a little on every run. That
    # is how "US-AZ-TUCSON-805 ~ ..." became "Az, US" and then "Az".
    again = s.normalize_location(got)
    if again != got:
        failures += 1
        print(f'FAIL normalize_location is not idempotent: {raw!r} -> {got!r} -> {again!r}')

# classify_level word-boundary checks: short signals must not match inside
# longer tokens, and a leveled II beats a cohort year.
# renormalize_locations: stored rows pick up new rules; community and
# would-be-blank rows are untouched.
RENORM = [
    {'company': 'A', 'role': 'x', 'location': 'MD - Baltimore, 8031 Corporate Dr', 'source': 'Workday'},
    {'company': 'B', 'role': 'y', 'location': 'Austin, TX', 'source': 'Greenhouse'},
    {'company': 'C', 'role': 'z', 'location': 'HQ - Sunnyvale (Office)', 'source': 'Community'},
    {'company': 'D', 'role': 'w', 'location': 'CASD14', 'source': 'Workday'},
]
renorm_rows = [dict(r) for r in RENORM]
renorm_n = s.renormalize_locations(renorm_rows)
renorm_locs = [r['location'] for r in renorm_rows]
if renorm_n != 1 or renorm_locs != ['Baltimore, MD', 'Austin, TX', 'HQ - Sunnyvale (Office)', 'CASD14']:
    failures += 1
    print(f'FAIL renormalize_locations: changed={renorm_n}, locations={renorm_locs}')

# listing_dedup_key: reposts of one req list the same sites in any order and
# spacing, and must collapse to one row.
DEDUP = [
    (('JPMorgan Chase', 'Hiring Event', 'McLean, VA; Jersey City, NJ'),
     ('JPMorgan Chase', 'Hiring Event', 'Jersey City, NJ; Mc Lean, VA'), True),
    (('Acme', 'SOC Analyst I', 'Austin, TX'), ('acme', 'SOC  Analyst I', 'Austin, TX'), True),
    (('Acme', 'SOC Analyst I', 'Austin, TX'), ('Acme', 'SOC Analyst I', 'Austin, TX; Dallas, TX'), False),
]
for a, b, want in DEDUP:
    got = s.listing_dedup_key(*a) == s.listing_dedup_key(*b)
    if got != want:
        failures += 1
        print(f'FAIL listing_dedup_key({a!r}) == ({b!r}) is {got}, want {want}')

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

# is_rejected_title: an intern title whose years all precede the season being
# recruited is a finished cohort. From September that season is next year.
SEPT_27 = date(2026, 9, 27)
JUNE_1 = date(2026, 6, 1)
THIS_YEAR = date.today().year
STALE_INTERN = [
    # (title, today, rejected)
    ('Vulnerability Researcher Intern - 2026', SEPT_27, True),
    ('RF Engineering Intern - 2026', SEPT_27, True),
    ('Security Engineer Intern (Fall 2026)', SEPT_27, True),
    ('PhD Research Intern, Security and Privacy - Fall 2026', SEPT_27, True),
    ('2026 Part-Time Cyber Security Engineering Intern - Aurora CO', SEPT_27, True),
    # The season form with no "intern" only reads as an internship while its
    # year is inside COHORT_YEAR_RE's window, which moves with the real date.
    (f'Security Engineer - Summer {THIS_YEAR}', date(THIS_YEAR, 9, 27), True),
    ('Security Engineer Intern - Summer 2019', SEPT_27, True),
    # The coming season, or any title that also names it, stays.
    ('Cyber Intern (Spring 2027)', SEPT_27, False),
    ('Security Engineering Intern - Summer 2027', SEPT_27, False),
    ('Cybersecurity Co-op, Fall 2026 / Spring 2027', SEPT_27, False),
    ('Security Engineer Intern', SEPT_27, False),
    # Before September the current summer is still recruiting.
    ('Security Engineer Intern - Summer 2026', JUNE_1, False),
    # New-grad titles are left alone.
    ('2026 Associate Cybersecurity Analyst - Pathways Program', SEPT_27, False),
    ('New Grad Security Engineer - Summer 2026 Start', SEPT_27, False),
]
for title, today, want in STALE_INTERN:
    got = s.is_rejected_title(title, today=today)
    if got != want:
        failures += 1
        print(f'FAIL is_rejected_title({title!r}, today={today}) = {got!r}, want {want!r}')

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
    # US territories and commonwealths are domestic.
    ('Aguadilla, PR', True),
    ('San Juan, Puerto Rico', True),
    ('Hagatna, GU', True),
    ('Saipan, MP', True),
    ('Charlotte Amalie, U.S. Virgin Islands', True),
    ('Pago Pago, AS', True),
    # ...but 'AS' only counts as a delimited region code, never as the English
    # word inside an all-caps site string, and BVI is not a US territory.
    ('WORK REMOTE AS NEEDED', False),
    ('Tortola, British Virgin Islands', False),
    # A spelled-out state anywhere in the string, not only as the trailing
    # comma segment, is a US signal.
    ('Remote - California', True),
    ('GEORGIA - VIRTUAL - GA01', True),
    ('Northern Virginia', True),
    ('Field-Virginia', True),
    ('West Virginia Client Office (WV88)', True),
    ('US Remote (New England)', True),   # 'england' must not match 'New England'
    ('Remote - SF Bay Area', True),
    # ...but a foreign city keeps its veto, including the country Georgia.
    ('Tbilisi, Georgia', False),
    ('London, England', False),
    ('Remote - United Kingdom', False),
    # A bare "Remote" beside a foreign place is that country's remote role
    # (ExtraHop 'Support Engineer I - UK' is 'Remote | United Kingdom')...
    ('Remote | United Kingdom', False),
    # Walmart's country-coded sites: the code decides, not the bare city.
    ('(CAN) ON CAMBRIDGE 03152 WM SUPERCENTER', False),
    ('(USA) AR BENTONVILLE HOME OFFICE', True),
    ('(MEX) CDMX; (CAN) ON TORONTO', False),
    ('(CAN) ON TORONTO; (USA) AR BENTONVILLE HOME OFFICE', True),
    ('Remote | India', False),
    ('Remote | Canada', False),
    ('Remote; London, UK', False),
    # ...but a US-scoped remote part or a US city still carries the posting.
    ('Remote (US) | London, UK', True),
    ('Remote | London, UK | New York, NY', True),
    ('Remote - US | Remote - Canada', True),
    ('New York City, Toronto, Chicago, or Remote', True),
    ('Remote', True),
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
    # Only a ceiling that opens at zero outranks a larger floor. Tenable's
    # 'AI Information Security Engineer' pairs its 5-year floor with a 1-2 band.
    ('You have 5 or more years of experience in information security, with at '
     'least 1-2 years focused on securing AI/ML systems.', True),
    ('5+ years of experience in network engineering.\n'
     "A Master's degree with 0 years of experience may substitute.", False),
]
for desc, want in EXP:
    got = s.requires_experience(desc)
    if got != want:
        failures += 1
        print(f'FAIL requires_experience({desc!r}) = {got!r}, want {want!r}')

# required_years: the experience floor, read off real postings that were on the
# board with 3-6 yoe (issue #11). Each case is trimmed from the live req.
FLOOR = [
    # Amazon splits quals into BASIC/PREFERRED. Every basic bullet binds, so
    # the floor is the largest of them — not the smallest, and not the
    # preferred one, which is what let this req on the board as "earlycareer".
    ('BASIC QUALIFICATIONS\n'
     '- 3+ years of scripting, programming, and security code review in a '
     'common programming language\n'
     '- 2+ years of IT Security experience\n'
     '- 4+ years of experience in information security, security operations, '
     'or security engineering\n'
     'PREFERRED QUALIFICATIONS\n'
     '- 2+ years of working with Data & AI related technologies', 4),
    # A preferred count below the basic one must not pull the floor down.
    ('BASIC QUALIFICATIONS\n- 3+ years of programming in Python, Ruby, Go, '
     'Java, .Net, C++ or similar object oriented language experience\n'
     'PREFERRED QUALIFICATIONS\n- 2+ years of any combination of the '
     'following: threat modeling experience, secure coding', 3),
    # "N+ years in <field>" never says "experience" — the old ±30-char window
    # missed it entirely.
    ('Core requirements\n- 3+ years in security engineering as a builder, '
     'with clear ownership of shipped work.\nNice-to-have\n- Deep knowledge '
     'of AI and SaaS security domains', 3),
    # "experience" sits ~60 chars past the count here; the old window stopped
    # at 30 and read this 5-year sales req as unstated.
    ('The role requires 5+ years of enterprise technology or cybersecurity '
     'sales experience, including experience selling into enterprise '
     'organizations.', 5),
    # A degree substitution ("HS Diploma & 5 years") is an alternative route,
    # not a second floor stacked on the 2-year bar.
    ('Required Qualifications\n- 2+ years of direct relevant experience in '
     'cyber defense analysis\nEducation: BS Computer Science, Cyber Security, '
     'or related degree; or HS Diploma & 5 years of network/host '
     'investigations experience.', 2),
    # Degree-paired bands are alternatives, so a dual Level 2/3 req floors at
    # the cheapest route (Master's + 0 years), not at the Level 3 band.
    ("Basic Qualifications Level 2: Bachelor's degree with 2 years of "
     "relevant experience; Master's degree with 0 years of relevant "
     "experience. An additional 4 years of relevant experience may be "
     "considered in lieu of a degree. Basic Qualifications Level 3: "
     "Bachelor's degree with 5 years of relevant experience; Master's degree "
     "with 3 years of relevant experience; PhD with 0 years of relevant "
     "experience.", 0),
    ('Qualifications You Must Have\nTypically requires a degree in Science, '
     'Technology, Engineering or Mathematics (STEM) and minimum 2 years of '
     'prior relevant experience or an Advanced Degree in a related field', 2),
    # An explicit band is read at its low end: 2-4 years is open to a 2-year
    # candidate, and an en-dash range must parse like a hyphen one.
    ("What You'll Bring\n- 1–2 years of experience in Threat Intelligence, "
     'Cybersecurity, or a related discipline', 1),
    ('We are looking for 2-4 years of experience.', 2),
    ('This role needs 5 to 7 years of experience.', 5),
    # Incidental counts are still not requirements.
    ('Our team triaged 6 years of legacy findings.', 0),
    ('Ideal for candidates with 0-2 years of experience.', 0),
    ('', 0),
    # Boilerplate that pairs a requirement verb with a count that is not work
    # experience. Cleared-defense reqs are a large share of this board, so
    # reading any of these as a floor would quietly delete good listings.
    ('Must have held a clearance within the last 5 years.', 0),
    ('Applicants must have held a TS/SCI within the past 6 years.', 0),
    ('Requires 5 years of continuous US residency for the clearance.', 0),
    ('Requires 3 years of relevant coursework in computer science.', 0),
    ('Must graduate within 3 years of the start date.', 0),
    ('Employees vest after 3 years of service.', 0),
    ('Salary range: $120,000 - $140,000. Entry-level cyber analyst role.', 0),
    # ...but a real bar in the same posting as clearance boilerplate still counts,
    # and the exclusions must not swallow legitimate "N years of <field>" bars.
    ('Must have held a clearance within the last 5 years and 6+ years '
     'of SOC experience.', 6),
    ('Requires 6+ years of customer service experience.', 6),
    ('Requires 6+ years of data engineering experience.', 6),
    # Workday serves each description as one line of HTML. Stripped to spaces,
    # the "Desired Skills" or "Preferred Qualifications" heading shadowed the
    # whole body and every row below read 0. Trimmed from the live reqs.
    # Nightwing JR101442, 'Cyber Network Defense Analyst II':
    ('<p>Nightwing is seeking a Cyber Network Defense Analyst to support this '
     'critical customer mission.<br /><br />Required Skills/Clearances:<br />'
     '<br />- U.S. Citizenship<br />- Active TS/SCI clearance<br />- 5&#43; '
     'years of direct relevant experience in cyber defense analysis using '
     'leading edge technologies and industry standard cyber defense tools-<br />'
     '- Experience successfully developing and deploying signatures<br /><br />'
     'Desired Skills:<br /><br />- Python programming experience<br /><br />'
     'Required Education:<br />BS Computer Science, Cyber Security, Computer '
     'Engineering, or related degree; or HS Diploma &amp; 7&#43; years of '
     'network investigations experience.</p>', 5),
    # Truist R0119088, 'AI Security Engineer'. The degree bullet above the
    # count must not turn it into a degree-paired alternative.
    ('<p><br /><b>Required Qualifications</b><br />The requirements listed '
     'below are representative of the knowledge, skill and/or ability '
     'required.</p><ul><li><p>Bachelor\u2019s degree or equivalent education, '
     'training, and work-related experience.</p></li><li><p>Minimum of 5 years '
     'of experience in security engineering or related cybersecurity roles.'
     '</p></li></ul><p><b>Preferred Qualifications</b></p><ul><li><p>Minimum '
     'of 5 years of experience in cybersecurity engineering, application '
     'security, cloud security.</p></li></ul>', 5),
    # Northrop Level 2/3 reqs pair each band with a degree, spelled
    # "Bachelors", so the cheaper Level 2 route sets the floor.
    ('<p><b>Basic Qualifications:</b></p><ul><li><p>Level 2: Must have a '
     'Bachelors of Science degree in a STEM field and at least 2 years of '
     'relevant military / professional experience</p></li><li><p>Level 3: Must '
     'have a Bachelors of Science degree in a STEM field and at least 5 years '
     'of relevant military / professional experience, OR a Master\u2019s Degree '
     'in a STEM field and at least 3 years of relevant military / professional '
     'experience</p></li></ul><p><b>Preferred Qualifications</b></p><ul><li>'
     '<p>Active Secret clearance</p></li></ul>', 2),
    # A count that describes the employer is not a floor.
    ('<p>Join the team behind one of our 25&#43; year programs supporting the '
     'intelligence community.</p><p><b>Basic Qualifications</b></p><ul><li>'
     '<p>Bachelor\u2019s degree in Computer Science or a related field</p>'
     '</li></ul>', 0),
]
for desc, want in FLOOR:
    got = s.required_years(desc)
    if got != want:
        failures += 1
        print(f'FAIL required_years({desc[:60]!r}...) = {got!r}, want {want!r}')

# evaluate_job: the gate has to fire on title-derived levels too, which is the
# actual regression — every one of these titles classifies as earlycareer.
GATED = [
    ('Security Engineer II', 'Seattle, WA',
     'BASIC QUALIFICATIONS\n- 6+ years of experience in security engineering',
     None),
    ('Cyber Network Defense Analyst II', 'Sterling, VA',
     'Requires a minimum of 6 years of relevant experience.', None),
    ('Associate Security Analyst', 'Austin, TX',
     'You must have at least 8 years of experience.', None),
    ('Cybersecurity Analyst, Junior', 'Remote (US)',
     'Requires 6+ years of experience in cyber defense.', None),
    # Same titles, honest early-career descriptions -> still accepted.
    ('Security Engineer II', 'Seattle, WA',
     'BASIC QUALIFICATIONS\n- 2+ years of security experience', 'earlycareer'),
    ('Cyber Network Defense Analyst II', 'Sterling, VA',
     'Education: BS in Cyber Security; or HS Diploma & 5 years of '
     'investigations experience.', 'earlycareer'),
    ('Cybersecurity Analyst, Junior', 'Remote (US)', '', 'earlycareer'),
    # A "less than 2 years" ceiling outranks the 4-year no-degree route.
    ('Junior DevSecOps Engineer, EDS Platform Services team, Hybrid role',
     'Tucson, AZ',
     '<p><b>Qualifications You Must Have</b></p><ul><li><p>Typically requires '
     'a bachelor\u2019s degree and less than 2 years of relevant experience or '
     'a total of 4 years relevant technical experience in IT or Digital '
     'Technology.</p></li></ul><p><b>Qualifications We Prefer</b></p><ul><li>'
     '<p>Experience with Kubernetes</p></li></ul>', 'earlycareer'),
    ('Level 2/3 Cyber Systems Engineer - AISR&T Contingent', 'San Diego, CA',
     '<p><b>Basic Qualifications:</b></p><ul><li><p>Must have a Bachelors of '
     'Science degree in a STEM field and at least 2 years of relevant military '
     '/ professional experience, OR a Master\'s Degree in a STEM field and at '
     'least some of relevant military / professional / academic experience'
     '</p></li><li><p>Must have a Bachelors of Science degree in a STEM field '
     'and at least 5 years of relevant military / professional experience, OR '
     'a Master\'s Degree in a STEM field and at least 3 years of relevant '
     'military / professional experience, OR a PhD and at least 1 year of '
     'relevant military / professional / academic experience</p></li></ul>',
     'earlycareer'),
    # Interns are exempt: research-internship reqs cite years of study in ways
    # the floor parser would misread.
    ('Security Engineering Intern', 'Seattle, WA',
     'Open to PhD students with 6+ years of research experience.', 'intern'),
]
for title, loc, desc, want in GATED:
    verdict = s.evaluate_job(title, loc, desc)
    got = verdict[0] if verdict else None
    if got != want:
        failures += 1
        print(f'FAIL evaluate_job({title!r}, ...) level = {got!r}, want {want!r}')

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
    {'company': 'E', 'role': 'Tier II SOC Supervisor',
     'type': 'earlycareer', 'source': 'Workday'},       # title now rejected -> dropped
    {'company': 'F', 'role': 'Protective Services Intern',
     'type': 'intern', 'source': 'Workday'},            # reject gate applies to interns too
    {'company': 'G', 'role': 'Senior Security Engineer',
     'type': 'earlycareer', 'source': 'Community'},     # community: never dropped
]
kept, reclass_changes, rejected = s.reclassify_listings([dict(r) for r in RECLASS])
if len(reclass_changes) != 1 or reclass_changes[0][0] != 'A' or reclass_changes[0][3] != 'newgrad':
    failures += 1
    print(f'FAIL reclassify_listings changes = {reclass_changes!r}, '
          f"want one A earlycareer->newgrad")
if ({e['company'] for e in rejected} != {'E', 'F'}
        or [e['company'] for e in kept] != ['A', 'B', 'C', 'D', 'G']):
    failures += 1
    print(f'FAIL reclassify_listings rejected={[e["company"] for e in rejected]}, '
          f'kept={[e["company"] for e in kept]}')

# purge_stale_listings: drop long-closed rows, keep recent-closed and open ones.
LIFECYCLE = [
    {'company': 'A', 'role': 'x', 'closed': True, 'closed_date': '2026-01-01'},  # old -> drop
    {'company': 'B', 'role': 'y', 'closed': True, 'closed_date': '2026-07-10',
     'missing_since': '2026-07-08'},                                             # recent -> keep
    {'company': 'C', 'role': 'z', 'url': 'https://x/c', 'date_added': '2026-01-01'},  # open & old -> keep
    {'company': 'D', 'role': 'w', 'closed': True, 'closed_date': '2026-01-01',
     'source': 'Community'},                                                     # community, old -> drop
    {'company': 'E', 'role': 'v', 'url': '', 'date_added': '2026-01-01'},        # url-less -> closed today
]
kept, removed = s.purge_stale_listings([dict(r) for r in LIFECYCLE], '2026-07-18', max_age_days=60)
if removed != 2 or [e['company'] for e in kept] != ['B', 'C', 'E']:
    failures += 1
    print(f'FAIL purge_stale_listings: removed={removed}, kept={[e["company"] for e in kept]}')
elif not (kept[2].get('closed') and kept[2].get('closed_date') == '2026-07-18'):
    failures += 1
    print(f'FAIL purge_stale_listings left the url-less row open: {kept[2]!r}')
elif 'missing_since' in kept[0] or kept[1].get('closed'):
    failures += 1
    print(f'FAIL purge_stale_listings streak/open handling: {kept[:2]!r}')

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

# The headline counts rows by the same predicate the open tables use: closed
# rows and url-less rows fold away, so neither may inflate a count.
STAT_ROWS = [
    {'company': 'A', 'type': 'intern', 'url': 'https://x/a', 'date_added': '2026-09-25',
     'clearance': True},
    {'company': 'B', 'type': 'intern', 'url': '', 'closed': True, 'date_added': '2026-09-25'},
    {'company': 'C', 'type': 'newgrad', 'url': 'https://x/c', 'closed': False,
     'date_added': '2026-09-01'},
    {'company': 'D', 'type': 'earlycareer', 'url': '', 'date_added': '2026-09-26'},
    {'company': 'E', 'type': 'earlycareer', 'url': 'https://x/e', 'date_added': '2026-09-20'},
]
stat_counts = rr.count_open_by_type(STAT_ROWS)
if (stat_counts['intern'], stat_counts['newgrad'], stat_counts['earlycareer']) != (1, 1, 1):
    failures += 1
    print(f'FAIL count_open_by_type = {dict(stat_counts)!r}, want one open row per type')
stat = rr.stats_line(STAT_ROWS, '2026-09-27')
for want in ('**1** internships', '**1** new grad', '**1** early career open',
             '**2** added in the last 7 days', '**1** need a clearance', 'updated Sep 27, 2026'):
    if want not in stat:
        failures += 1
        print(f'FAIL stats_line missing {want!r}: {stat!r}')

# Rebuild layout. Two same-company, same-date rows collapse under ↳, and the
# 🇺🇸 flag must follow the row that carries clearance, not the group's first.
PAIR = [
    {'company': 'Acme', 'role': 'Analyst I', 'type': 'earlycareer', 'url': 'https://x/1',
     'date_added': '2026-09-01', 'category': 'SOC & Detection', 'clearance': False,
     'location': 'Austin, TX'},
    {'company': 'Acme', 'role': 'Analyst II', 'type': 'earlycareer', 'url': 'https://x/2',
     'date_added': '2026-09-01', 'category': 'SOC & Detection', 'clearance': True,
     'location': 'Austin, TX'},
]
pair_rows = rr.build_table(PAIR, '2026-09-27')
flag_rows = [r for r in pair_rows if '🇺🇸' in r]
if len(pair_rows) != 2 or len(flag_rows) != 1 or not flag_rows[0].startswith('| ↳ | Analyst II 🇺🇸'):
    failures += 1
    print(f'FAIL 🇺🇸 flag should sit on the ↳ Analyst II row only: {pair_rows!r}')
if not pair_rows[0].startswith('| Acme | Analyst I<br><sub>SOC &amp; Detection</sub> | <a href='):
    failures += 1
    print(f'FAIL row layout should be Company, Role + category, Apply: {pair_rows[0]!r}')
if 'alt="Apply: Acme Analyst I"' not in pair_rows[0] or 'target=' in pair_rows[0]:
    failures += 1
    print(f'FAIL apply badge alt text / attributes: {pair_rows[0]!r}')
if '🆕' in pair_rows[0] or '🆕 Analyst I' not in rr.build_table(PAIR, '2026-09-05')[0]:
    failures += 1
    print('FAIL 🆕 should mark rows added within the last 7 days only')

# Closed rows fold into their own table, newest closure first, grouped among
# themselves; the open table keeps only open rows and writes its own header.
BOARD = PAIR + [
    {'company': 'Beta', 'role': 'Old Intern', 'type': 'earlycareer', 'url': '', 'closed': True,
     'closed_date': '2026-09-10', 'date_added': '2026-08-01'},
    {'company': 'Beta', 'role': 'Older Intern', 'type': 'earlycareer', 'url': '', 'closed': True,
     'closed_date': '2026-09-10', 'date_added': '2026-07-01'},
    {'company': 'Gamma', 'role': 'Dead Link', 'type': 'earlycareer', 'url': '',
     'date_added': '2026-09-20'},
]
SKELETON = '\n'.join(
    ['<!-- STATS -->', 'stale', '<!-- /STATS -->', '<!-- LEGEND -->', '<!-- /LEGEND -->']
    + [f'<!-- TABLE_START {t} -->\n| Old | Header |\n| --- | --- |\n<!-- TABLE_END {t} -->\n'
       f'<!-- CLOSED_START {t} -->\n<!-- CLOSED_END {t} -->' for t in rr.TABLE_TYPES])
page = rr.render_readme(SKELETON, BOARD, '2026-09-27')
ec_open = page.split('<!-- TABLE_START earlycareer -->')[1].split('<!-- TABLE_END')[0]
ec_closed = page.split('<!-- CLOSED_START earlycareer -->')[1].split('<!-- CLOSED_END')[0]
if rr.TABLE_HEADER not in ec_open or '| Old | Header |' in page or ec_open.count('\n| ') != 4:
    failures += 1
    print(f'FAIL open table should hold TABLE_HEADER and the two open rows: {ec_open!r}')
if ('🔒 3 closed in the last 60 days' not in ec_closed
        or ec_closed.index('Dead Link') > ec_closed.index('Old Intern')
        or '| ↳ | Older Intern | Sep 10 |' not in ec_closed):
    failures += 1
    print(f'FAIL closed block: {ec_closed!r}')
if '<!-- CLOSED_START intern -->\n<!-- CLOSED_END intern -->' not in page:
    failures += 1
    print('FAIL a type with no closed rows should render an empty closed block')

# The legend is generated from classify.CATEGORY_RULES; a new category must
# show up there with a one-line explanation.
legend = '\n'.join(rr.legend_lines())
for name in [*s.CATEGORY_NAMES, *s.FALLBACK_CATEGORIES]:
    if f'**{rr.escape_cell(name)}**: ' not in legend:
        failures += 1
        print(f'FAIL legend has no line for category {name!r}; add it to CATEGORY_BLURBS')

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
