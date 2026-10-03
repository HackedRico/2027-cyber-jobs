#!/usr/bin/env python3
"""Spot checks for scrape_jobs.py classification logic.

Run from anywhere: python .github/scripts/test_classification.py
"""
import sys
from datetime import date

import testkit

sys.path.insert(0, str(__import__('pathlib').Path(__file__).parent))
import classify as s
import common
import rebuild_readme as rr

# Intern titles carrying a year are judged against the season recruiting today,
# so the rows below name that season instead of a fixed year.
SEASON = s.first_open_season()

# Northrop lists each level's bar in one posting; the easiest route is level 2's.
NORTHROP_LEVELS = ("Level 2: Bachelor's + 2 years of related experience. "
                   "Level 3: Bachelor's + 5 years of related experience.")

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
    # 'Adversarial' and 'alignment' mark an AI title only with an ML object.
    ('Adversarial ML Researcher', 'New York, NY', 'Requires 2+ years of experience.', False, ('earlycareer', 'AI Security & Safety')),
    ('Research Engineer, Alignment Science', 'San Francisco, CA', 'Requires 2+ years of experience.', False, ('earlycareer', 'AI Security & Safety')),
    ('AI Alignment Intern', 'San Francisco, CA', '', False, ('intern', 'AI Security & Safety')),
    ('Adversarial Robustness Research Intern', 'San Francisco, CA', '', False, ('intern', 'AI Security & Safety')),
    ('Security Engineer, Adversary & Adversarial Emulation', 'Austin, TX', 'Requires 2+ years of experience.', False, None),
    ('Security Engineer, Alignment Tooling', 'Austin, TX', 'Requires 2+ years of experience.', False, None),
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
    # "SoC" as system-on-chip, from Qualcomm's Eightfold board.
    ('Hardware (CPU, GPU, SoC, Digital Design, DV) Engineering Internship - Summer 2027', 'San Diego, CA', '', False, None),
    ('Hardware (CPU, GPU, SoC) Engineering Internship - Summer 2027', 'San Diego, CA', '', False, None),
    ('SoC Performance Architect (Server CPU) - PhD New Grads Welcome!', 'Santa Clara, CA', '', False, None),
    # The SoC lookahead missed physical design (Micron, Simplify intern list).
    ('HBM SoC Physical Design Engineer Intern', 'Boise, ID', '', False, None),
    ('Security (Product, Systems, Cyber) Engineering Internship - Summer 2027', 'San Diego, CA', '', False, ('intern', 'Security Engineering')),
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
    # -- should be rejected: intelligence-support analysts at security companies --
    ('Junior Geospatial / Full-Motion Video (FMV) Analyst', 'Lumber Bridge, NC', '', True, None),
    ('Associate Imagery Analyst', 'Springfield, VA', '', True, None),
    ('Junior All-Source Analyst', 'Tampa, FL', '', True, None),
    ('Targeting Analyst I', 'Fort Meade, MD', '', True, None),
    ('Junior Linguist Analyst', 'Fort Gordon, GA', '', True, None),
    ('Signals Collection Analyst I', 'Fort Meade, MD', '', True, None),
    # ...but a cyber-signalled analyst, or an engineer on the same team, stays.
    ('Cyber Intelligence Analyst I', 'Herndon, VA', '', True, ('earlycareer', 'Threat Intelligence')),
    ('Junior Threat Intelligence Analyst', 'Herndon, VA', '', True, ('earlycareer', 'Threat Intelligence')),
    ('SIGINT Cyber Analyst I', 'Fort Meade, MD', '', True, ('earlycareer', 'Security Engineering')),
    ('Junior Geospatial Software Engineer', 'Herndon, VA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Junior Data Analyst', 'Herndon, VA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    # -- should be rejected: business analysts and researchers at security companies --
    ('Business Analyst I', 'Austin, TX', '', True, None),
    ('Associate Legal Analyst', 'Austin, TX', '', True, None),
    ('Associate Pricing Analyst', 'Austin, TX', '', True, None),
    ('Associate Operations Analyst', 'Austin, TX', '', True, None),
    ('IT Support Analyst I', 'Austin, TX', '', True, None),
    ('Analyst I, Market Intelligence', 'Austin, TX', '', True, None),
    ('Associate UX Researcher', 'Austin, TX', '', True, None),
    ('Jr IT Analyst (part-time)', 'Remote (US)', '', True, None),
    # ...but a security, data or research analyst, or an engineer, stays.
    ('Threat Intelligence Analyst I', 'Austin, TX', '', True, ('earlycareer', 'Threat Intelligence')),
    ('Security Analyst I', 'Austin, TX', '', True, ('earlycareer', 'Security Engineering')),
    ('SOC Analyst I', 'Austin, TX', '', True, ('earlycareer', 'SOC & Detection')),
    ('Data Analyst I', 'Austin, TX', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Research Analyst I', 'Somerville, MA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    # 'Researcher I' is not a level marker yet, so the description levels it.
    ('Malware Researcher I', 'Austin, TX', 'Open to candidates with 0-2 years of experience.', True,
     ('earlycareer', 'Engineering @ Security Co')),
    ('Fraud Analyst', 'Somerville, MA', 'Open to candidates with 0-2 years of experience.', True,
     ('earlycareer', 'Threat Intelligence')),
    ('Detection Engineer I', 'Austin, TX', '', True, ('earlycareer', 'SOC & Detection')),
    ('IT Security Analyst I', 'Austin, TX', '', True, ('earlycareer', 'Security Engineering')),

    # -- should be rejected: "Security Clearance" is a requirement, not the work --
    ('Computer Scientist / Software Developer, Junior - Security Clearance Required', 'Adelphi, MD', '', False, None),
    ('Software Engineer I, CDS (Onsite - Security Clearance)', 'Cedar Rapids, IA', '', False, None),
    # ...while a cyber title that also names the clearance stays.
    ('Cyber Software Engineer I (Security Clearance Required)', 'Chantilly, VA', '', False, ('earlycareer', 'Security Engineering')),
    # -- should be rejected: "National Security" is a customer, not the work --
    ('Systems Engineering Associate - GovCloud [Salesforce National Security]', 'Reston, VA', '', False, None),
    ('National Security Solutions (NSS) Semiconductor Research Internship', 'Huntsville, AL', '', False, None),
    ('Associate Software Engineer, National Security Programs', 'Columbia, MD', '', False, None),
    # ...while a title with its own cyber signal stays.
    ('National Security Cyber Analyst I', 'Columbia, MD', '', False, ('earlycareer', 'Security Engineering')),
    ('Associate Security Engineer, National Security', 'Reston, VA', '', False, ('earlycareer', 'Security Engineering')),
    ('Junior Threat Analyst - National Security Solutions', 'Chantilly, VA', '', False, ('earlycareer', 'Security Engineering')),
    # -- should be rejected: facility-security work behind a bare 'security' title --
    ('Security Analyst II', 'Cambridge, MA',
     'Maintain classified document control and personnel security processing '
     'in accordance with 32 CFR part 117 and the NISPOM rule.', False, None),
    # ...but a cyber title citing the NISPOM, or a bare one that does not, stays.
    ('Cyber Security Analyst 1', 'Waimea, HI',
     'Perform security tasks required by the 32 CFR part 117 National '
     'Industrial Security Operating Manual (NISPOM) and NIST SP 800-53.', False,
     ('earlycareer', 'Security Engineering')),
    ('Security Analyst II', 'Cambridge, MA',
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
    ('Junior Security Engineer', 'Remote (Europe)', '', False, None),
    ('SOC Analyst I', 'Pune, IN', '', False, None),
    ('SOC Analyst I', 'Perth, WA', '', False, None),

    # -- bug fix: leveled numerals reject only in role-noun context --
    # "III/IV/3/4" no longer bare-match version/layer/standard numbers.
    ('Cybersecurity Analyst I (PCI DSS 4.0)', 'Austin, TX', '', False, ('earlycareer', 'GRC & Risk')),
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
    # ...but a flat sales engineer title still needs a level signal.
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
    # officer counts only at level 1, and Leidos 'Jr. Security Specialist', a
    # badging role, fails the bare-security role rule now that 'Jr.' levels.
    ('Security Officer I -Plant McIntosh, Rincon, GA', 'Rincon, GA', '', False, None),
    ('Security Officer II', 'Austin, TX', '', False, None),
    ('Security Officer - Kansas City, Missouri', 'Kansas City, MO', '', False, None),
    ('Information Security Officer 2', 'Cambridge, MA', '', False, None),
    ('Jr. Security Specialist', 'Omaha, NE', '', False, None),
    ('Cybersecurity Technologist III', 'Hartford, CT', '', False, None),
    ('Emergency Responder I', 'Austin, TX', '', False, None),

    # -- security work under titles with no security word --
    # Spellings of known terms: Leidos, and BlackRock's non-breaking spaces.
    ('Junior Cloud/SecDevOps Engineer', 'Clarksburg, WV', '', False, ('earlycareer', 'AppSec & ProdSec')),
    ('Access\xa0& Identity\xa0Management Engineer, Associate', 'Wilmington, DE', '', False, ('earlycareer', 'Identity & IAM')),
    # Engineers on a team named for security work.
    ('Software Engineer II - Compromise & Fraud Protection', 'Redmond, WA', '', False, ('earlycareer', 'Security Engineering')),
    ('Privacy & Civil Liberties Engineer - New Grad', 'New York, NY', '', False, ('newgrad', 'Security Engineering')),
    ('Software Engineer I, Authentication', 'Seattle, WA', '', False, ('earlycareer', 'Identity & IAM')),
    # Presales and services at a security vendor.
    ('Solutions Consultant 1', 'Tallahassee, FL', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Domain Consultant 2 - NetSec', 'Reston, VA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Sales Engineer I', 'San Diego, CA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Sales Engineer - Intern', 'Austin, TX', '', True, ('intern', 'Engineering @ Security Co')),
    ('Associate Services Architect', 'Boston, MA', '', True, ('earlycareer', 'Engineering @ Security Co')),
    ('Associate Solutions Architect, Security', 'Seattle, WA', '', False, ('earlycareer', 'Security Engineering')),
    # ...and the limits. Network, sysadmin and SRE titles with no security word
    # stay out, as do a team word with no engineering noun, camera presales, a
    # leveled architect and a seller.
    ('Network Engineer I', 'Shiloh, IL', '', False, None),
    ('SYSTEMS ADMINISTRATOR 2 (LINUX)', 'Waimea, HI', '', False, None),
    ('Site Reliability Engineer II', 'Chicago, IL', '', False, None),
    ('Credit Card Fraud Specialist I', 'Heathrow, FL', '', False, None),
    ('Trust & Safety New Associate', 'Austin, TX', '', False, None),
    ('Signal and Power Integrity Engineer - New College Grad 2026', 'Santa Clara, CA', '', False, None),
    ('Pre-Sales Solutions Engineer I - Video Security & Access Control', 'San Juan, PR', '', False, None),
    ('Cyber Security Architect/Engineer II', 'Minneapolis, MN', '', False, None),
    ('Associate Sales Representative', 'Austin, TX', '', True, None),

    # -- cloud engineering is in the charter at any employer --
    ('Cloud Engineer II', 'San Francisco, CA', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Cloud Network Engineer II', 'Redmond, WA', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Associate AWS DevOps Engineer', 'Sioux Falls, SD', '', False, ('earlycareer', 'Cloud Engineering')),
    (f'IT Infrastructure & Cloud Engineering Internship - Summer {SEASON}', 'San Diego, CA', '', False, ('intern', 'Cloud Engineering')),
    ('Cloud Engineer, New Grad', 'Austin, TX', '', False, ('newgrad', 'Cloud Engineering')),
    ('Junior Azure Cloud Engineer', 'Remote (US)', '', False, ('earlycareer', 'Cloud Engineering')),
    # A security word keeps the security category, and a cloud engineer at a
    # security company is filed as cloud engineering.
    ('Cloud Security Engineer Intern', 'Seattle, WA', '', False, ('intern', 'Cloud & Infra Security')),
    ('Cloud Engineer I', 'Austin, TX', '', True, ('earlycareer', 'Cloud Engineering')),
    # ...and the limits: a level is still required, the cloud word has to lead
    # the role noun, CRM and ERP suites named Cloud are out, and so are cloud
    # sales and support roles.
    ('Cloud Engineer', 'Austin, TX', '', False, None),
    ('Software Engineer II - Windows in Cloud', 'Redmond, WA', '', False, None),
    ('Salesforce Service Cloud Developer, Associate', 'Austin, TX', '', False, None),
    ('Oracle Cloud HCM Developer I', 'Austin, TX', '', False, None),
    ('Cloud Sales Associate', 'Austin, TX', '', False, None),
    ('Associate Banker, Saint Cloud, MN', 'Saint Cloud, MN', '', False, None),
    ('Senior Cloud Engineer', 'Austin, TX', '', False, None),
    ('Cloud Engineer II', 'Austin, TX', 'Requires 5+ years of AWS experience.', False, None),
    # Support, operations, administration and consulting roles, and an
    # infrastructure engineer that names the cloud after the role.
    ('Cloud Support Associate', 'Seattle, WA', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Associate Cloud Consultant', 'Austin, TX', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Cloud Operations Analyst I', 'Peoria, IL', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Azure Administrator I', 'Austin, TX', '', False, ('earlycareer', 'Cloud Engineering')),
    ('DevOps Engineer I - AWS', 'Austin, TX', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Platform Engineer I, Azure', 'Austin, TX', '', False, ('earlycareer', 'Cloud Engineering')),
    # ...but not cost, data-centre or town names.
    ('Cloud FinOps Analyst I', 'Austin, TX', '', False, None),
    ('Associate Cloud Cost Analyst', 'Austin, TX', '', False, None),
    ('DCO Technician I, AWS Data Center Operations', 'Ashburn, VA', '', False, None),
    ('Saint Cloud Operations Associate', 'Saint Cloud, MN', '', False, None),
    ('DevOps Engineer I', 'Austin, TX', '', False, None),
    # Found in review: a non-technical job on a cloud team, other senses of
    # cloud, and SaaS suites named Cloud.
    ('Junior Scrum Master, Cloud Engineering', 'Austin, TX', '', False, None),
    ('Associate Technical Writer, AWS Developer Documentation', 'Seattle, WA', '', False, None),
    ('Associate Learning Specialist, AWS Support', 'Seattle, WA', '', False, None),
    ('Associate Cloud Business Analyst', 'Austin, TX', '', False, None),
    ('Cloud Partner Specialist I', 'Austin, TX', '', False, None),
    ('Point Cloud Engineer I', 'Austin, TX', '', False, None),
    ('Cloud Seeding Technician I', 'Boulder, CO', '', False, None),
    ('Associate SAP Cloud Developer', 'Austin, TX', '', False, None),
    ('Workday Cloud Administrator I', 'Austin, TX', '', False, None),
    ('Oracle Cloud Developer I', 'Austin, TX', '', False, None),
    ('Cloud Hardware Development Engineer I, Annapurna Labs, Early Career - 2027', 'Austin, TX', '', False, None),
    ('Oracle Cloud Infrastructure Engineer I', 'Austin, TX', '', False, ('earlycareer', 'Cloud Engineering')),

    # -- solutions architecture and presales at any employer, early career only --
    ('Associate Solutions Architect, AGS-Tech, Early Career - 2027', 'Seattle, WA', '', False, ('newgrad', 'Solutions Architecture')),
    ('Associate Solution Engineer', 'San Mateo, CA', '', False, ('earlycareer', 'Solutions Architecture')),
    ('Associate Cloud Sales Engineer', 'Remote - US', '', False, ('earlycareer', 'Cloud Engineering')),
    ('Associate Software Sales Engineer', 'Remote - US', '', False, ('earlycareer', 'Solutions Architecture')),
    (f'Solutions Architect Intern - Summer {SEASON}', 'Austin, TX', '', False, ('intern', 'Solutions Architecture')),
    ('Cloud Solution Architect, New Grad', 'Redmond, WA', '', False, ('newgrad', 'Cloud Engineering')),
    ('Associate Solutions Architect, Security', 'Seattle, WA', '', False, ('earlycareer', 'Security Engineering')),
    # ...but a flat or senior title, a customer engineer and a seller stay out.
    ('Solutions Architect', 'Austin, TX', '', False, None),
    ('Solutions Engineer', 'Austin, TX', '', False, None),
    ('Senior Sales Engineer', 'Austin, TX', '', False, None),
    ('Customer Engineer I', 'Dayton, OH', '', False, None),
    ('Associate Solutions Manager', 'Austin, TX', '', False, None),
    # Found in review: a leveled II is not an early-career word, and a sales
    # engineer with no technology word is industrial sales.
    ('Solutions Engineer II', 'Austin, TX', '', False, None),
    ('Associate Sales Engineer, SE Desk - Northeast', 'Remote - US', '', False, None),
    ('Field Sales Engineer I - HVAC', 'Austin, TX', '', False, None),
    ('Sales Engineer (Field) Intern', 'Austin, TX', '', False, None),
    ('Customer Solutions Engineer I', 'Austin, TX', '', False, None),
    # A security title on a cloud or presales team keeps its security category.
    ('Cloud Vulnerability Analyst I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Vulnerability Solutions Engineer, Associate', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),

    # -- SOC, blue team and DoD acronyms --
    ('MDR Analyst I', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('CSOC Analyst I', 'Huntsville, AL', '', False, ('earlycareer', 'SOC & Detection')),
    ('Junior CSSP Analyst', 'Norfolk, VA', '', False, ('earlycareer', 'SOC & Detection')),
    ('SecOps Analyst I', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Junior EDR Analyst', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Junior RMF Analyst', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('eMASS Analyst I', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('Junior ACAS Analyst', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('Junior ISSO', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('Junior ISSE', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('ISSO I', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('ISSO III', 'Arlington, VA', '', False, None),
    # Bank and Big 4 GRC titles, and NICE work-role nouns as level nouns.
    ('IT Risk Analyst I', 'Charlotte, NC', '', False, ('earlycareer', 'GRC & Risk')),
    ('IT Auditor I', 'Charlotte, NC', '', False, ('earlycareer', 'GRC & Risk')),
    ('Internal Audit - IT Audit Analyst I', 'Charlotte, NC', '', False, ('earlycareer', 'GRC & Risk')),
    ('Technology Controls Analyst I', 'Columbus, OH', '', False, ('earlycareer', 'GRC & Risk')),
    ('PCI Compliance Analyst I', 'Austin, TX', '', False, ('earlycareer', 'GRC & Risk')),
    ('Third Party Cyber Risk Analyst I', 'Minneapolis, MN', '', False, ('earlycareer', 'GRC & Risk')),
    ('Security Controls Assessor I', 'Arlington, VA', '', False, ('earlycareer', 'GRC & Risk')),
    ('Digital Forensics Examiner I', 'Austin, TX', '', False, ('earlycareer', 'Forensics & IR')),
    ('Cyber Sys Secur Engr Asc', 'Orlando, FL', '', False, ('earlycareer', 'Security Engineering')),
    # Security products beside a technical role noun.
    ('Junior Splunk Engineer', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Junior SailPoint Engineer', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Internal Audit - Technology Audit Associate', 'Charlotte, NC', '', False, ('earlycareer', 'GRC & Risk')),
    (f'Cybersecurity Summer Fellowship {SEASON}', 'Austin, TX', '', False, ('intern', 'Security Engineering')),
    ('Cybersecurity Residency', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Firewall Engineer I', 'Austin, TX', '', False, ('earlycareer', 'Cloud & Infra Security')),
    ('PKI Engineer I', 'Austin, TX', '', False, ('earlycareer', 'Cloud & Infra Security')),
    ('Junior Microsoft Sentinel Engineer', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    # Named cohorts that carry no usual level word.
    ('Cybersecurity Development Program', 'Dallas, TX', '', False, ('newgrad', 'Security Engineering')),
    ('Technology Analyst Program - Cybersecurity', 'Tampa, FL', '', False, ('newgrad', 'Security Engineering')),
    ('Cyber Apprenticeship Program', 'Fort Meade, MD', '', False, ('earlycareer', 'Security Engineering')),
    ('Cybersecurity Trainee', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Analyst, Early Careers', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    # ...and what stays out: physical security, credit and supplier risk,
    # observability, NOC-only and fraud work, finance audit, a vendor's own
    # support title, ambiguous product words, and cohorts that are not cyber.
    ('Global Security Operations Center Analyst I', 'Austin, TX', '', False, None),
    ('Threat Assessment Analyst I - Workplace Violence', 'Austin, TX', '', False, None),
    ('Electronic Security Systems Engineer I', 'Austin, TX', '', False, None),
    ('Third Party Risk Analyst I - Supplier Financial Health', 'Austin, TX', '', False, None),
    ('Market Access Management Associate', 'Austin, TX', '', False, None),
    ('Splunk Observability Engineer I', 'Austin, TX', '', False, None),
    ('NOC Technician I', 'Austin, TX', '', False, None),
    ('Internal Audit Analyst I', 'Austin, TX', '', False, None),
    ('Okta Customer Support Engineer I', 'Austin, TX', '', False, None),
    ('Sentinel Program Engineer I', 'Roy, UT', '', False, None),
    ('EDR Equipment Technician I', 'Austin, TX', '', False, None),
    ('MDR Regulatory Affairs Associate', 'Austin, TX', '', False, None),
    ('Business Development Program Associate', 'Austin, TX', '', False, None),
    # Found in review: a GSOC watch floor, badge and patient access offices, a
    # vendor's own titles naming its product, medical device reporting, other
    # senses of SOAR and ACAS, program staff, and a passed fellowship season.
    ('GSOC Watch Floor Analyst I', 'Austin, TX', '', False, None),
    ('GSOC Incident Handler I', 'Austin, TX', '', False, None),
    ('Security Access Management Specialist I', 'Austin, TX', '', False, None),
    ('Patient Access Management Associate', 'Austin, TX', '', False, None),
    ('Associate Pricing Analyst, Okta', 'Austin, TX', '', True, None),
    ('Splunk Technical Support Engineer I', 'Austin, TX', '', False, None),
    ('Software Engineer I - Splunk', 'Austin, TX', '', False, None),
    ('Junior Medical Device Reporting (MDR) Specialist', 'Austin, TX', '', False, None),
    ('MDR Complaint Analyst I', 'Austin, TX', '', False, None),
    ('Soar Technology Junior Software Engineer', 'Ann Arbor, MI', '', False, None),
    ('ACAS X Software Engineer I', 'McLean, VA', '', False, None),
    ('Incident Handler I - Customer Service', 'Austin, TX', '', False, None),
    ('IT Compliance Specialist I - Computer System Validation', 'Austin, TX', '', False, None),
    ('IT Audit Associate - Financial Audit', 'Austin, TX', '', False, None),
    ('Information Security Analyst Programmer', 'Austin, TX', '', False, None),
    ('Cyber Workforce Development Program Specialist', 'Austin, TX', '', False, None),
    ('Summer 2025 Cybersecurity Fellowship', 'Austin, TX', '', False, None),

    # -- rows the new bank, pharma and Big 4 boards would have added --
    ('Associate, Equity Research - Cybersecurity & Data', 'New York, NY', '', False, None),
    ('2027 Future Talent Program- Forensic Services Laboratory Intern', 'West Point, PA', '', False, None),
    (f'Strategic Assurance and SOC Services Associate - Summer {SEASON}', 'Fort Lauderdale, FL', '', False, None),
    ('Digital Forensics Lab Intern', 'Austin, TX', '', False, ('intern', 'Forensics & IR')),
    (f'{SEASON} Future Talent Program - North America Regional Security Team - Intern', 'Rahway, NJ', '', False, None),
    (f'{SEASON} Future Talent Program - Global Security Resiliency Center - Intern', 'Rahway, NJ', '', False, None),
    # A Big 4 'Associate - Summer <year>' is a full-time start, but an intern
    # title and a bank's 'Summer Associate' are internships.
    (f'Cybersecurity and Privacy Associate - Summer {SEASON}', 'Los Angeles, CA', '', False, ('newgrad', 'Security Engineering')),
    (f'Advisory Intern, Cyber, Compliance & Assessment - Summer {SEASON}', 'McLean, VA', '', False, ('intern', 'GRC & Risk')),
    (f'Cybersecurity Summer Associate {SEASON}', 'New York, NY', '', False, ('intern', 'Security Engineering')),

    # -- bug fix: US territories are US locations, not foreign --
    ('Cyber Software Engineer I', 'Aguadilla, PR', '', False, ('earlycareer', 'Security Engineering')),
    ('Student Trainee (Cybersecurity)', 'Hagatna, GU', '', False, ('intern', 'Security Engineering')),
    # ...but a foreign territory that merely shares a name is still rejected.
    ('Junior Security Analyst', 'Tortola, British Virgin Islands', '', False, None),

    # -- should be rejected: 'security' that is not information security --
    ('Social Security Intern', 'Baltimore, MD', '', False, None),
    ('Food Security Analyst I', 'Austin, TX', '', False, None),
    ('Energy Security Intern', 'Austin, TX', '', False, None),
    ('Border Security Intern', 'Austin, TX', '', False, None),
    ('Homeland Security Intern', 'Washington, DC', '', False, None),
    ('Campus Security Intern', 'Austin, TX', '', False, None),
    ('Event Security Intern', 'Austin, TX', '', False, None),
    ('Corporate Security Intern', 'Charlotte, NC', '', False, None),
    ('Security Forces Intern', 'Austin, TX', '', False, None),
    ('Security Badging Intern', 'Austin, TX', '', False, None),
    # ...and a bare 'security' before a facility-style role noun.
    ('Security Access Control Technician I', 'Austin, TX', '', False, None),
    ('Security Specialist II', 'Cambridge, MA', '', False, None),
    ('Security Specialist - Administrative (Junior)', 'Falls Church, VA', '', False, None),
    ('Associate Security Specialist', 'Huntsville, AL', '', False, None),
    ('Security Associate', 'Reston, VA', '', False, None),
    # ...while a second cyber term, a technical role noun, or its own cyber
    # term beside the other sense keeps the title.
    ('Security Analyst I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Cyber Security Specialist I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Information Security Specialist I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('IT Security Specialist I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Cybersecurity Intern, Homeland Security', 'Washington, DC', '', False, ('intern', 'Security Engineering')),
    ('Access Control Analyst I', 'Austin, TX', '', True, ('earlycareer', 'Engineering @ Security Co')),

    # -- department words do not reject a title that names security work --
    ('Cybersecurity Analyst - Finance Systems', 'Austin, TX',
     'Open to candidates with 0-2 years of experience.', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer I, Payments & Billing', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer I, Revenue Platform', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Information Security Analyst I - Human Resources Systems', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Cyber Security Analyst I, Treasury Systems', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Supply Chain Cyber Risk Analyst I', 'Austin, TX', '', False, ('earlycareer', 'GRC & Risk')),
    # ...but a business-management role inside the cyber org is still not cyber work.
    ('Finance & Business Management Associate - Cybersecurity & Technology Controls',
     'Plano, TX', '', False, None),
    ('Silicon Security Researcher - New Grad', 'Austin, TX', '', False, ('newgrad', 'Security Engineering')),
    # Support, recruiting and workplace teams run their own security engineers.
    ('Security Engineer I, Customer Support Tools', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer, Recruiting Systems', 'Austin, TX',
     'Open to candidates with 0-2 years of experience.', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer I, Workplace Technology', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Corporate Security Engineer I - Workplace', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    # ...while the support, recruiting and workplace jobs themselves stay out.
    ('Customer Support Engineer I', 'Austin, TX', '', True, None),
    ('Customer Support Engineer I', 'Austin, TX', '', False, None),
    ('Customer Support Specialist', 'Austin, TX', '', True, None),
    ('Recruiting Coordinator', 'Austin, TX', '', True, None),
    ('Technical Recruiter', 'Austin, TX', '', True, None),
    ('Talent Acquisition Associate', 'Austin, TX', '', True, None),
    ('Workplace Experience Associate', 'Austin, TX', '', True, None),
    ('Workplace Services Intern', 'Austin, TX', '', True, None),
    # ...but sales, account and department-only titles stay out.
    ('Cyber Sales Intern', 'Austin, TX', '', True, None),
    ('Security Account Executive I', 'Austin, TX', '', True, None),
    ('Revenue Platform Engineer, Security', 'Austin, TX', '', True, None),
    ('Human Resources Analyst I', 'Austin, TX', '', True, None),

    # -- level markers: Jr, separated numerals, Level I/II, L1/L2 --
    ('Jr. Security Analyst', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Jr SOC Analyst', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('JR SOC ANALYST', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Information System Security Officer - Jr.', 'Washington, DC', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Analyst - I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Analyst (I)', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Analyst, Level I', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Cybersecurity Analyst Level II', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('L1 SOC Analyst', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('SOC Analyst L2', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    ('Cyber Threat Hunter I', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),
    # ...while the senior forms stay out.
    ('Security Analyst Level III', 'Austin, TX', '', False, None),
    ('Security Analyst - III', 'Austin, TX', '', False, None),
    ('L3 SOC Analyst', 'Austin, TX', '', False, None),
    ('Threat Hunter III', 'Austin, TX', '', False, None),
    ('Security Analyst - 4 days onsite', 'Austin, TX', '', False, None),
    # A req posted at several levels up to III is early career only on a
    # description whose floor passes the experience gate.
    ('Cyber Analyst II / III', 'Austin, TX', '', False, None),
    ('Cyber Analyst I/II/III', 'Austin, TX', '', False, None),
    ('Security Analyst 2/3', 'Austin, TX', '', False, None),
    ('SOC Analyst Tier 1-3', 'Austin, TX', '', False, None),
    ('SOC Analyst II-III', 'Austin, TX', '', False, None),
    ('Classified Cybersecurity Analyst 2/3 - Secret', 'Linthicum, MD', '', False, None),
    ('Cybersecurity Analyst 2/3', 'Linthicum, MD', NORTHROP_LEVELS, False, ('earlycareer', 'Security Engineering')),
    ('Cybersecurity Analyst 2/3', 'Linthicum, MD',
     "Level 3: Bachelor's + 5 years of related experience.", False, None),
    # A description that states no count is no evidence either.
    ('Cybersecurity Analyst 2/3', 'Linthicum, MD',
     'Join our team protecting national security missions.', False, None),
    ('Cybersecurity Analyst 2/3', 'Linthicum, MD',
     'Open to candidates with 0-2 years of experience.', False,
     ('earlycareer', 'Security Engineering')),
    # ...while a single level, or a span that stops at II, levels on the title.
    ('Cyber Analyst II', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Security Engineer 2', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),
    ('Cyber Software Engineer - Level 1/2', 'Austin, TX', '', False, ('earlycareer', 'Security Engineering')),

    # -- keyword gaps --
    ('Identity & Access Management Intern', 'Austin, TX', '', False, ('intern', 'Identity & IAM')),
    ('Identity Access Management Analyst I', 'Austin, TX', '', False, ('earlycareer', 'Identity & IAM')),
    ('IAM Intern', 'Austin, TX', '', False, ('intern', 'Identity & IAM')),
    ('Governance, Risk and Compliance Analyst I', 'Austin, TX', '', False, ('earlycareer', 'GRC & Risk')),
    ('CSIRT Analyst I', 'Austin, TX', '', False, ('earlycareer', 'SOC & Detection')),

    # -- SOC 1 and SOC 2 are audit reports, not a security operations center --
    ('SOC 1 Analyst I', 'Austin, TX', '', False, None),
    ('SOC 1 Audit Associate', 'Austin, TX', '', False, None),
    ('SOC Reporting Intern', 'Austin, TX', '', False, None),
    ('SOC 2 Compliance Analyst I', 'Austin, TX', '', False, ('earlycareer', 'GRC & Risk')),

    # -- seniority words inside a level name or an org unit --
    ('Member of Technical Staff, Security - New Grad', 'San Francisco, CA', '', False, ('newgrad', 'Security Engineering')),
    ('Cyber Security Researcher - Associate Staff', 'Lexington, MA', '', False, ('earlycareer', 'Security Engineering')),
    ('Intern, Office of the Chief Information Security Officer', 'Austin, TX', '', False, ('intern', 'Security Engineering')),
    ('Chief Information Security Officer', 'Austin, TX', '', False, None),

    # -- a description levels a flat title only when it addresses the reader --
    ('Security Engineer', 'Austin, TX',
     'Our teams include everyone from recent graduates to industry veterans.', False, None),
    ('Security Engineer', 'Austin, TX', 'This role is open to recent graduates.', False,
     ('newgrad', 'Security Engineering')),
    ('Security Engineer', 'Austin, TX', 'A new grad role on our detection team.', False,
     ('newgrad', 'Security Engineering')),
    # Palo Alto Networks' new-grad 'Software Engineer' reqs name the level only
    # as a degree requirement; a flat title counts there as a security company.
    ('Software Engineer', 'Santa Clara, CA',
     "<li>Bachelor's degree earned recently or anticipated to be earned within the next "
     '12 months</li>', True, ('newgrad', 'Engineering @ Security Co')),
    ('Software Engineer', 'Santa Clara, CA',
     "Bachelor's degree earned recently or anticipated within 12 months.", False, None),
    ('Security Engineer', 'Austin, TX', "Bachelor's degree required.", True, None),
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
    ('Perth Amboy, NJ; Perth, WA', 'Perth Amboy, NJ'),
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
    # Parsons leads with an ISO country code; Melbourne alone reads as
    # Australia, and a Canadian site is left for the US filter to drop.
    ('US - FL, Melbourne', 'Melbourne, FL'),
    ('US, WV - Summit Point', 'Summit Point, WV'),
    ('US - Remote (Any Location)', 'Remote (US)'),
    ('CA - YT, Faro', 'CA - YT, Faro'),
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
    # Multi-level titles still level on the title, so the scraper fetches
    # their Workday and Oracle descriptions for judge_job to read.
    ('Classified Cybersecurity Analyst 2/3 - Secret', 'earlycareer'),
    ('Cyber Analyst I/II/III', 'earlycareer'),
    ('SOC Analyst Tier 1-3', 'earlycareer'),
    ('Cyber Systems Engineer (Level 2 or 3)', 'earlycareer'),
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
THIS_YEAR = testkit.TODAY.year
STALE_INTERN = [
    # (title, today, rejected)
    ('Vulnerability Researcher Intern - 2026', SEPT_27, True),
    ('RF Engineering Intern - 2026', SEPT_27, True),
    ('Security Engineer Intern (Fall 2026)', SEPT_27, True),
    ('PhD Research Intern, Security and Privacy - Fall 2026', SEPT_27, True),
    ('2026 Part-Time Cyber Security Engineering Intern - Aurora CO', SEPT_27, True),
    # The season form with no "intern" only reads as an internship while its
    # year is inside COHORT_YEAR_RE's window, which moves with the date.
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

# classify_level across the new year: the cohort window drops the old year on
# Jan 1, so a still-open "2026" new-grad row with a junior or associate word
# re-levels to early career, and one with no other signal loses its level.
DEC_31 = date(2026, 12, 31)
JAN_1 = date(2027, 1, 1)
COHORT = [
    # (title, today, expected level)
    ('2026 Junior Analyst', DEC_31, 'newgrad'),
    ('2026 Junior Analyst', JAN_1, 'earlycareer'),
    ('2026 Associate Cyber Systems Engineer', DEC_31, 'newgrad'),
    ('2026 Associate Cyber Systems Engineer', JAN_1, 'earlycareer'),
    ('Cyber Software Engineer - Class of 2026', DEC_31, 'newgrad'),
    ('Cyber Software Engineer - Class of 2026', JAN_1, None),
    # The next cohorts are unaffected, and the window gains 2029.
    ('2027 Associate Cyber Systems Engineer', JAN_1, 'newgrad'),
    ('2029 Cyber Analyst', DEC_31, None),
    ('2029 Cyber Analyst', JAN_1, 'newgrad'),
    # A named program keeps its level whatever the year.
    ('2026 Cyber Analyst - Pathways Program', JAN_1, 'newgrad'),
]
for title, today, want in COHORT:
    got = s.classify_level(title, today=today)
    if got != want:
        failures += 1
        print(f'FAIL classify_level({title!r}, today={today}) = {got!r}, want {want!r}')

# first_open_season turns over to next summer on September 1.
SEASONS = [
    (date(2026, 8, 31), 2026),
    (date(2026, 9, 1), 2027),
    (DEC_31, 2027),
    (JAN_1, 2027),
    (date(2027, 8, 31), 2027),
    (date(2027, 9, 1), 2028),
]
for today, want in SEASONS:
    got = s.first_open_season(today)
    if got != want:
        failures += 1
        print(f'FAIL first_open_season({today}) = {got!r}, want {want!r}')

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
    # GDIT leads with country and state: 'Junior Tactical All Source Threat
    # Intelligence Analyst' in Vienna, Virginia read as Austria.
    ('USA VA Vienna', True),
    ('USA VA Vienna; USA MD Fort Meade', True),
    ('London, UK; USA VA Vienna', True),
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
    # Parsons' ISO prefix: CA is Canada there, so its Yukon mine's 'Security
    # and Mine Rescue Technician I' sat in California. The US prefix keeps a
    # US site, and Tenable's state-first 'MA - Boston' is not a country code.
    ('US - FL, Melbourne', True),
    ('US - VA (Field Location)', True),
    ('US, WV - Summit Point', True),
    ('US - Remote (Any Location)', True),
    ('CA - YT, Faro', False),
    ('CA - BC (Field Location)', False),
    ('CA, NS - Halifax', False),
    ('CA - Remote (Any Location)', False),
    ('IN - Remote (Any Location)', False),
    ('DE - Ramstein Air Force Base', False),
    ('CA - ON, Ottawa; US - VA, Centreville', True),
    ('CA - YT, Faro; SA - Riyadh', False),
    ('MA - Boston - Office, US - Headquarters - Maryland - Columbia', True),
    ('CA - San Francisco', True),
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
    # A remote parenthetical has to name the US, a state, or a workplace type.
    ('Remote (Europe)', False),
    ('Remote (EU)', False),
    ('Remote (Latin America)', False),
    ('Remote (Worldwide)', False),
    ('Remote (Anywhere)', False),
    ('Remote (Mexico)', False),
    ('Remote (Asia)', False),
    ('Remote | Europe', False),
    ('Remote (US)', True),
    ('Remote (United States)', True),
    ('Remote (Virginia)', True),
    ('Remote (Hybrid)', True),
    ('Remote (Any State)', True),
    ('Albuquerque, New Mexico', True),
    # A foreign place's own code is not a US state code...
    ('Bengaluru, Karnataka, IN', False),
    ('Mysuru, Karnataka, IN', False),
    ('Pune, IN', False),
    ('Chennai, TN', False),
    ('Goa, GA', False),
    ('Perth, WA', False),
    ('Toronto, Ontario, CA', False),
    # ...but US towns with foreign names keep their state.
    ('London, KY', True),
    ('Athens, GA', True),
    ('Perth Amboy, NJ', True),
    ('Melbourne, FL', True),
    ('Vancouver, WA', True),
    ('Delhi, NY', True),
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
    # An inline "a plus" shadows its own clause onward, not the count before it.
    ('<p>Requirements: 5+ years of experience in security engineering; '
     'experience with Go is a plus.</p>', 5),
    ('5+ years of Go is a plus.', 0),
    # 'additional' does not mark a preference.
    ('Minimum 6 years of experience, with additional experience in cloud security', 6),
    # A count echoed in parentheses.
    ('Requires seven (7) years of experience.', 7),
    ('Five (5) years of experience required.', 5),
    ('Requires 3 (three) years of experience.', 3),
    # A preferred section that comes first ends at the next plain heading.
    ('<h2>Preferred Qualifications</h2><li>x</li><h2>Requirements</h2>'
     '<li>6+ years of experience</li>', 6),
    ('<h2>Nice to Have</h2><li>CISSP</li><h2>What You Bring</h2>'
     '<li>4+ years of experience</li>', 4),
    # The employer's track record and an age bar are not floors...
    ('Acme has more than 25 years of experience serving the DoD.', 0),
    ('Leveraging our 50+ years of experience', 0),
    ('We have over 30 years of experience in defense.', 0),
    ('Applicants must be at least 21 years old.', 0),
    # ...but the reader as subject still is.
    ('The ideal candidate has 5+ years of experience.', 5),
    ('- Have 3+ years of experience in SOC operations', 3),
    ('You have 4+ years of experience.', 4),
    # A trailing preference word softens only its own clause.
    ("Bachelor's degree required. 3+ years of experience preferred.", 0),
    ('Ideally you have 4+ years of experience.', 0),
    ('5+ years of experience required and CISSP preferred.', 5),
    ('3+ years of experience, preferably in a SOC.', 3),
    # Degrees abbreviated BS/MS are routes too: SEI 'Associate Security
    # Researcher' read 3 once the parenthesised counts parsed.
    ('BS degree in Computer Science or related quantitative discipline, with three (3) '
     'years of relevant professional experience, or MS in the same with one (1) year '
     'of relevant professional experience, or PhD in the same.', 1),
    ('B.S. in Computer Science and 4 years of experience, or M.S. and 2 years.', 2),
    # A PhD route is the floor only when no bachelor's or master's route is given.
    ('You have BS in machine learning, cybersecurity, statistics, or related discipline '
     'with eight (8) years of experience; OR MS in the same fields with five (5) years '
     'of experience; OR PhD in the same fields with two (2) years of experience.', 5),
    ('PhD in computer science with 2 years of research experience.', 2),
    # Years in lieu of a degree do not undercut the degreed route's own count.
    ("Bachelor's degree and 5 years of related experience; an additional four (4) "
     'years of relevant experience may be accepted in lieu of a degree.', 5),
    (NORTHROP_LEVELS, 2),
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
    # Boilerplate about the employer or the applicant's age is not a floor.
    ('Cyber Analyst I', 'Austin, TX',
     'Acme has more than 25 years of experience serving the DoD.', 'earlycareer'),
    ('Cyber Analyst I', 'Austin, TX', 'Leveraging our 50+ years of experience', 'earlycareer'),
    ('Cyber Analyst I', 'Austin, TX', 'Applicants must be at least 21 years old.', 'earlycareer'),
    ('Security Engineer I', 'Austin, TX',
     "Bachelor's degree required. 3+ years of experience preferred.", 'earlycareer'),
    # A count before an inline "a plus" still gates the title.
    ('Security Engineer I', 'Austin, TX',
     '<p>Requirements: 5+ years of experience in security engineering; '
     'experience with Go is a plus.</p>', None),
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
if ({(e['company'], reason) for e, reason in rejected}
        != {('E', 'rejected-title'), ('F', 'rejected-title')}
        or [e['company'] for e in kept] != ['A', 'B', 'C', 'D', 'G']):
    failures += 1
    print(f'FAIL reclassify_listings rejected={[(e["company"], r) for e, r in rejected]}, '
          f'kept={[e["company"] for e in kept]}')

# reclassify_listings with the companies.yml flag map: a title that passed only
# on a security_company flag goes when the company loses it, even with no live
# posting to re-judge. (company, title, source, flags, expected reason or None)
FLAGGED = [
    ('Jumio', 'Research Engineer - Machine Learning & Robotics', 'Greenhouse',
     {'Jumio': False}, 'not-cyber'),
    ('Illumio', 'Software Engineer, New Grad', 'Greenhouse', {'Illumio': True}, None),
    # A board since removed from companies.yml: the flag that admitted it is unknown.
    ('Todyl', 'Software Engineer, New Grad', 'Greenhouse', {}, None),
    ('ICF', 'Computer Scientist / Software Developer, Junior - Security Clearance Required',
     'Workday', {'ICF': False}, 'not-cyber'),
    ('Jumio', 'Software Engineer Intern', 'Greenhouse', {'Jumio': False}, 'not-cyber'),
    ('Jumio', 'Software Engineer, New Grad', 'Community', {'Jumio': False}, None),
]
for company, title, source, flags, want in FLAGGED:
    row = {'company': company, 'role': title, 'type': 'newgrad', 'source': source}
    _, _, rejected = s.reclassify_listings([row], flags)
    got = rejected[0][1] if rejected else None
    if got != want:
        failures += 1
        print(f'FAIL reclassify_listings({company!r}, {title!r}, {flags!r}) = {got!r}, '
              f'want {want!r}')

# judge_job names the gate evaluate_job failed on, and agrees with it.
JUDGED = [
    # (title, location, description, security_company, expected reason)
    ('Security Analyst Intern', 'Austin, TX', '', False, None),
    ('Senior Security Engineer', 'Austin, TX', '', False, 'rejected-title'),
    ('Research Engineer - Machine Learning & Robotics', 'Austin, TX', '', False, 'not-cyber'),
    ('Security Analyst II', 'Austin, TX',
     'Maintains classified document control per the NISPOM.', False, 'facility-security'),
    ('Security Engineer', 'Austin, TX', '', False, 'no-level'),
    ('Software Engineer, AI Safety', 'San Francisco, CA',
     'Years of experience required will correlate with the internal job level requirements',
     False, 'no-level'),
    ('Security Engineer II', 'Austin, TX', 'Requires 6+ years of experience.', False,
     'over-experienced'),
    ('Classified Cybersecurity Analyst 2/3 - Secret', 'Linthicum, MD', '', False, 'no-level'),
    ('Support Engineer I - UK', 'Remote | United Kingdom', '', True, 'non-us-location'),
]
for title, loc, desc, sec, want in JUDGED:
    verdict, reason = s.judge_job(title, loc, desc, sec)
    if reason != want or (verdict is None) != (want is not None):
        failures += 1
        print(f'FAIL judge_job({title!r}, {loc!r}) = {(verdict, reason)!r}, want {want!r}')
    if verdict != s.evaluate_job(title, loc, desc, sec):
        failures += 1
        print(f'FAIL judge_job and evaluate_job disagree on {title!r}')
    if reason is not None and reason not in s.JUDGE_REASONS:
        failures += 1
        print(f'FAIL judge_job reason {reason!r} missing from JUDGE_REASONS')

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
    # Export control restricts a role to US Persons without naming a clearance.
    ('Security Engineer I, Threat Hunting',
     'BASIC QUALIFICATIONS\n- Must be a U.S. Person as defined by ITAR (22 CFR 120.62).', True),
    ('Security Engineer I', 'Candidates must be US persons as defined by the EAR.', True),
    ('Security Engineer I', 'This role requires a U.S.&nbsp;Person.', True),
    # ...but "US personnel" is a workforce, not a requirement.
    ('Security Engineer I', 'You will partner with US personnel across the org.', False),
    ('Security Engineer I', 'Train U.S. personnel on phishing response.', False),
    # A clearance or citizenship named only to waive it is no requirement...
    ('Cyber Analyst', 'Clearance Level Must Currently Possess: None', False),
    ('Cyber Analyst', 'No clearance required.', False),
    ('Cyber Analyst', 'This role does not require a clearance.', False),
    ('Cyber Analyst', 'Polygraph: None', False),
    ('Cyber Analyst', 'US Citizenship Required: No', False),
    ('Cyber Analyst', 'U.S. citizenship is not required.', False),
    # ...but the obtainable level on the same CACI form still flags.
    ('Cyber Analyst', '<p>Clearance Level Must Currently Possess: None</p>'
     '<p>Clearance Level Must Be Able to Obtain: Secret</p>', True),
    # Spellings split by markup or punctuation.
    ('Cyber Analyst', 'Must be a U.S.&nbsp;citizen.', True),
    ('Cyber Analyst', 'Must be a U.S.\ncitizen.', True),
    ('Cyber Analyst', 'Active TS / SCI required.', True),
    ('Cyber Analyst', 'Top-Secret eligibility.', True),
    ('Cyber Analyst', 'Must hold an active Secret clearance.', True),
    ('Cyber Analyst', 'Must be a United States citizen.', True),
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
    # GFM autolinks a bare URL, www. and user@host inside a table cell.
    ('escape_cell defangs a bare url',
     rr.escape_cell('Intern apply at https://evil.example/login')
     == 'Intern apply at https&#8203;://evil.example/login'),
    ('escape_cell defangs www. and an email address',
     rr.escape_cell('See www.evil.example or jobs@evil.example')
     == 'See www&#8203;.evil.example or jobs@&#8203;evil.example'),
    ('escape_cell leaves a spaced @ and a bare & readable',
     rr.escape_cell('Engineering @ Security Co, R&D') == 'Engineering @ Security Co, R&amp;D'),
    ('apply_btn keeps a url escape_cell would defang',
     rr.apply_btn('https://www.acme.com/jobs/1', 'Acme https://x @y').startswith(
         '<a href="https://www.acme.com/jobs/1"><img')),
    ('a README row defangs the role but keeps the apply link',
     (lambda r: 'https&#8203;://evil.example' in r and 'href="https://boards.greenhouse.io/a/jobs/1"'
      in r)(rr.format_row({'company': 'Acme', 'role': 'Intern https://evil.example', 'url':
                           'https://boards.greenhouse.io/a/jobs/1', 'location': 'Austin, TX',
                           'date_added': '2026-09-01', 'category': 'SOC & Detection'},
                          'Acme', '2026-09-30'))),
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

# companies.md lists and counts a company once however many boards it runs on a
# platform: Idaho National Laboratory has two Oracle sites, GDIT two Workday boards.
tracked, total = rr.tracked_board_lines({
    'oracle': [{'name': 'Idaho National Laboratory'}, {'name': 'Idaho National Laboratory'}],
    'workday': [{'name': 'GDIT'}, {'name': 'Boeing'}, {'name': 'GDIT'},
                {'name': 'Palo Alto Networks', 'security_company': True}],
})
if (total != 4 or tracked.count('- GDIT') != 1 or '### Workday (3)' not in tracked
        or tracked.count('- Idaho National Laboratory') != 1 or '### Oracle (1)' not in tracked
        or '- Palo Alto Networks 🛡️' not in tracked):
    failures += 1
    print(f'FAIL tracked_board_lines should list each company once: {total} {tracked!r}')

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
