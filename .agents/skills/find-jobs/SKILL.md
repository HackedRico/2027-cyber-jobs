---
name: find-jobs
description: Searches the board's open cybersecurity internships, new-grad and early-career roles for what a student wants to do, where, and at what level, and returns apply links. Use when someone asks to find, list, recommend or compare roles or companies on this board, such as "a SOC internship in Virginia" or "new grad cloud roles with no clearance".
---

# Find jobs

`find_jobs.py` searches the open rows of `listings.json`. The search is read-only, and so is
this skill: a student's request never edits the data.

```bash
python3 .github/scripts/find_jobs.py --type intern --category soc --state VA --remote --no-clearance
```

## Steps

1. **Refresh the clone.** Run `git pull`. The bots add roles twice a day, and the script
   prints a note when the clone's data is older than a day.
2. **Translate the request into filters.** Run `find_jobs.py --categories` for what each
   category covers, then map the student's words to flags:
   - The kind of work goes to `--category` and `--keyword`. "Pentesting" is Offensive
     Security; "malware" is Forensics & IR. A keyword matches the start of a word in the
     title or the category, so use one for work no category names, such as `--keyword
     kubernetes`.
   - The level goes to `--type`: `intern`, `newgrad` for new-grad programs, `earlycareer`
     for 0 to 2 years. A graduating senior wants both `newgrad` and `earlycareer`.
   - An internship term lives only in the title, so `--keyword summer` keeps the titles that
     name it. Many titles name no term; list those apart rather than dropping them.
   - The place goes to `--state` and `--remote`, which together match either.
   - A student on F-1, OPT or H-1B gets `--no-clearance`.
   - "What's new" gets `--since 7`.
   Values of one flag match any of them, and different flags must all match. `--help` lists
   every flag.
3. **Search, then tune.** Under five matches, widen: drop `--state`, add `--remote`, or add
   the catch-all Security Engineering category beside a narrow one. Over fifty, narrow by
   level, place or `--since`. Run `--json` when you need to sort, group or count.
4. **Answer.** List each match with company, role, location and apply link, give the scrape
   date the script prints, and mark every 🇺🇸 row as asking for a clearance or U.S.
   citizenship. A row holds the title only, so open the posting before calling a role a
   strong fit, and say which rows you judged by title alone. For a student on a visa, a row
   without 🇺🇸 is unknown on sponsorship, not a yes: the posting decides, and an F-1
   internship also needs CPT from the school's international office.

The answer is done when every role it names came from this run's output, and the student can
see which filters produced it.

## When nothing fits

- Federal and intelligence roles post on their own sites and never reach this board. The
  README's "New to cyber recruiting?" section links USAJOBS Pathways, NSA, the intelligence
  community and CyberCorps.
- A role the student found elsewhere that meets CONTRIBUTING.md's rules can be submitted
  with the Add Job issue form. Give them the link to open; the submission is theirs to send.
