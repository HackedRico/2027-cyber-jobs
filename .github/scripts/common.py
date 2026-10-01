#!/usr/bin/env python3
"""Shared helpers used by more than one script.

Kept dependency-free (stdlib only) so the classification test suite and the
scraper both import the SAME url/issue logic. The URL dedup guard only works
if the scraper and the community-submission flow normalize identically.
"""

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlsplit, urlunparse

from classify import DC_SPELLING_RE, REGION_CODE_RE, US_STATES, normalize_location

# Tracking params stripped before URL comparison so the same posting under
# different campaign tags dedupes to one listing.
STRIP_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'utm_id', 'source', 'src', 'ref', 'referer', 'lever-source',
    'lever-origin', 'gh_src',
}

# Greenhouse serves the same board under two hostnames; canonicalize so a job
# under both doesn't dedupe as two.
_GREENHOUSE_HOST_RE = re.compile(r'^job-boards\.greenhouse\.io$')
# Trailing application-step suffixes that don't change the posting identity.
_TRAILING_STEP_RE = re.compile(r'/(?:apply|application)$', re.IGNORECASE)


def normalize_url(url):
    try:
        p = urlparse(url.strip())
        params = {k: v for k, v in parse_qs(p.query, keep_blank_values=True).items()
                  if k.lower() not in STRIP_PARAMS}
        netloc = _GREENHOUSE_HOST_RE.sub('boards.greenhouse.io', p.netloc.lower())
        path = _TRAILING_STEP_RE.sub('', p.path.rstrip('/'))
        u = urlunparse(p._replace(
            scheme=p.scheme.lower(),
            netloc=netloc,
            path=path,
            query=urlencode(sorted(params.items()), doseq=True),
            fragment='',
        ))
        # Collapse every segment before /job/, locale or not: a submitter pastes
        # ".../en-US/NW/job/..." while the scraper builds ".../NW/job/...", and
        # requiring the locale left the scraper's form uncollapsed, so the two
        # never matched.
        return re.sub(r'(myworkdayjobs\.com)/(?:[^/]+/)*?job/',
                      r'\1/job/', u, flags=re.IGNORECASE)
    except Exception:
        return url


_AUTOLINK_RE = re.compile(r'\b(https?|ftp)(?=://)|\b(www)(?=\.)', re.IGNORECASE)
# Only an & that opens a character reference: "&#64;octocat" rendered as a
# live @octocat in a release, while "Cloud & Infra" should stay as typed.
_ENTITY_START_RE = re.compile(r'&(?=#|[A-Za-z][A-Za-z0-9]*;)')


def defang_autolinks(text):
    """Put a zero-width space where GitHub would autolink or mention.

    Breaks a bare URL scheme, `www.`, an @mention or email address, and an
    issue reference like #12 or owner/repo#12. Takes text that is already
    escaped, since the zero-width space goes in as an entity.
    """
    text = re.sub(r'#(?=\d)', '#&#8203;', text)
    text = _AUTOLINK_RE.sub(lambda m: (m.group(1) or m.group(2)) + '&#8203;', text)
    return re.sub(r'@(?=\w)', '@&#8203;', text)


def md_escape(text):
    """Render untrusted text as inert inline markdown.

    Scraped and submitted fields reach bot comments and releases. There they
    must not open links, HTML or emphasis, ping a user, or autolink a bare URL:
    a submitted Category once rendered a phishing link and @mentions in the
    verdict comment, in the bot's voice. A bare & is left alone so the
    plain-text email reads "Cloud & Infra", not "&amp;"; one that starts an
    entity is escaped.
    """
    text = re.sub(r'\s+', ' ', text or '').strip()
    text = _ENTITY_START_RE.sub('&amp;', text)
    text = text.replace('\\', '\\\\').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'([\[\]`*_~|])', r'\\\1', text)
    return defang_autolinks(text)


def md_code(text):
    """Render untrusted text as one inline code span it cannot close.

    A code span shows its text literally, so no link, mention or HTML inside it
    renders. The fence is one backtick longer than any run in the text, since a
    single backtick in a submitted value closed the old fixed fence.
    """
    text = re.sub(r'\s+', ' ', text or '').strip()
    if not text:
        return '(empty)'
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    if not longest:
        return f'`{text}`'
    # The padding spaces keep a leading or trailing backtick off the fence;
    # CommonMark strips one from each side.
    fence = '`' * (longest + 1)
    return f'{fence} {text} {fence}'


# C0 controls other than tab, newline and carriage return, and DEL. JSON
# carries them fine, but they are invalid in the Atom feeds and invisible in
# a rendered issue, so a maintainer approves text they cannot see.
CONTROL_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
# Every character a log viewer or the runner could read as a line break or a
# terminal escape.
_LOG_BREAK_RE = re.compile(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]+')


def strip_controls(text):
    """Text with CONTROL_RE characters removed."""
    return CONTROL_RE.sub('', text or '')


def oneline(text):
    """Untrusted text made safe for one line of a workflow log.

    The runner reads a log line that starts with `::` as a workflow command,
    so a newline in a scraped title or submitted role could open one.
    """
    return _LOG_BREAK_RE.sub(' ', str(text))


# Cloud metadata endpoints. Azure's WireServer sits on an address
# ipaddress calls global, and link-local is listed as well so the rule does
# not rest on the stdlib's table alone.
BLOCKED_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '168.63.129.16/32', '169.254.0.0/16', 'fd00:ec2::254/128'))
ALLOWED_PORTS = frozenset({80, 443})
_NAT64 = ipaddress.ip_network('64:ff9b::/96')


def _embedded_ipv4(ip):
    # ::127.0.0.1 and 64:ff9b::7f00:1 read as global but reach an IPv4 host.
    if ip.version != 6:
        return None
    if ip.ipv4_mapped:
        return ip.ipv4_mapped
    if int(ip) >> 32 == 0 or ip in _NAT64:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip.sixtofour


def is_public_ip(address):
    """True when an IP address string is on the public internet.

    An IPv6 form of an IPv4 address (mapped, compatible, NAT64 or 6to4) is
    judged by the IPv4 address inside it too.
    """
    try:
        ip = ipaddress.ip_address(str(address).split('%', 1)[0])
    except ValueError:
        return False
    candidates = [ip]
    inner = _embedded_ipv4(ip)
    if inner is not None:
        candidates = [inner] if ip.ipv4_mapped or int(ip) >> 32 == 0 else [ip, inner]
    return all(c.is_global and not c.is_multicast
               and not any(c in net for net in BLOCKED_NETWORKS
                           if net.version == c.version)
               for c in candidates)


def public_address(host, port=443):
    """One address to connect to for `host`, or None when it is not public.

    None when any address the name resolves to is not public, since a name
    with one public and one private record is a rebinding setup. Prefers
    IPv4, which every runner can reach. Raises OSError when the name does
    not resolve.
    """
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [info[4][0] for info in infos]
    if not addresses or not all(is_public_ip(a) for a in addresses):
        return None
    return next((a for a in addresses if ':' not in a), addresses[0])


def fetch_target_problem(url):
    """Why an untrusted link must not be fetched, or None when it may be.

    Only http(s) on ports 80 and 443: a link to another port on a public
    host is still a way to probe services a job posting never runs on.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return 'the link is not a valid URL'
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return 'only http(s) links are fetched'
    if port is not None and port not in ALLOWED_PORTS:
        return f'port {port} is not fetched, only 80 and 443'
    return None


def link_host(url):
    """The link's hostname in ASCII, with IDNA labels as punycode.

    Punycode shows a lookalike: greenhouse.io spelled with a Cyrillic o reads
    as xn--greenhuse-52h.io.
    """
    try:
        host = urlsplit(url or '').hostname or ''
    except ValueError:
        return ''
    host = host.rstrip('.')
    try:
        return host.encode('idna').decode('ascii')
    except UnicodeError:
        # The idna codec is IDNA 2003 and refuses some labels; encode each
        # non-ASCII label by hand rather than show the raw Unicode.
        return '.'.join(label if label.isascii()
                        else 'xn--' + label.encode('punycode').decode('ascii')
                        for label in host.split('.'))


def host_matches(host, domains):
    """True when `host` is one of `domains` or a subdomain of one."""
    host = (host or '').lower().rstrip('.')
    return any(host == d or host.endswith('.' + d) for d in domains)


def gh_headers(token):
    return {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }


def parse_issue_body(body):
    """Parse a GitHub issue form body into {field header: value}.

    First occurrence of each `### Header` wins, so a free-text field appended
    later in the body cannot override a structured value that already passed
    validation. A missing/null body (GitHub returns body: null for a bodyless
    issue) yields an empty field set rather than raising.
    """
    fields = {}
    for section in re.split(r'^### ', body or '', flags=re.MULTILINE):
        if not section.strip():
            continue
        lines = section.strip().split('\n')
        header = lines[0].strip()
        if header in fields:
            continue
        value = '\n'.join(lines[1:]).strip()
        fields[header] = '' if value == '_No response_' else value
    return fields


REMOTE_RE = re.compile(r'^remote\s*(\(us\)|\(usa\)|\(united states\))?$', re.IGNORECASE)
BARE_COUNTRY_RE = re.compile(r'^(us|usa|united states|nationwide)$', re.IGNORECASE)
CITY_STATE_RE = re.compile(r'^.+,\s*([A-Z]{2})$')
SUBMITTED_SPLIT_RE = re.compile(r'[;\n]')


def _fix_typed_spelling(part):
    # normalize_location reads scraper shapes; a person types "Washington,
    # D.C." and "McLean, Va", which it passes through unchanged.
    part = DC_SPELLING_RE.sub('Washington, DC', part.strip())
    return REGION_CODE_RE.sub(lambda m: f', {m.group(1).upper()}', part)


def normalize_submitted_location(location):
    """Return a form location in the board's "City, ST; Remote (US)" shape.

    Runs the scraper's normalize_location over the whole string, so a foreign
    option beside a US one is dropped the same way it is for scraped rows.
    """
    parts = [_fix_typed_spelling(p) for p in SUBMITTED_SPLIT_RE.split(location or '')
             if p.strip()]
    return normalize_location('; '.join(parts)) or ''


def validate_location(location):
    """Return a list of error strings for a submitted location, empty if valid.

    The location is normalized first, so the spellings the scraper's location
    rules accept for a US place ("Arlington, Virginia", "Arlington VA",
    "Washington, D.C.") pass here too.
    """
    parts = [p.strip() for p in normalize_submitted_location(location).split(';')
             if p.strip()]
    if not parts:
        return ['location is empty']
    errors = []
    for part in parts:
        if REMOTE_RE.match(part) or BARE_COUNTRY_RE.match(part):
            continue
        m = CITY_STATE_RE.match(part)
        if not m:
            # These strings reach bot comments, so the submitted part is
            # quoted in a code span it cannot close.
            errors.append(
                f'{md_code(part)}: use "City, ST" format (e.g. "Arlington, VA") '
                f'or "Remote (US)"'
            )
        elif m.group(1) not in US_STATES:
            errors.append(
                f'{md_code(part)}: {md_code(m.group(1))} is not a US state code. '
                f'This board is US-only.'
            )
    return errors


def security_company_names(config):
    """Lowercased names of companies.yml entries flagged `security_company`.

    Takes the parsed config so this module stays stdlib-only.
    """
    names = set()
    for entries in (config or {}).values():
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get('security_company'):
                names.add(str(entry.get('name', '')).strip().lower())
    return names


def security_company_flags(config):
    """Map each companies.yml entry's name to its `security_company` flag.

    A company listed under two ATSes counts as a security company when either
    entry says so. Names keep their case, since they must equal the `company`
    field on stored rows.
    """
    flags = {}
    for entries in (config or {}).values():
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get('name'):
                name = str(entry['name'])
                flags[name] = flags.get(name, False) or bool(entry.get('security_company'))
    return flags


def write_run_events(path, added, revived=(), retired=()):
    """Write one run's inserted, revived and retired rows for notify.py.

    Only the writer knows which rows are new: diffing listings.json
    over-reports whenever renormalisation rewrites a location or a closure
    blanks a url. The scrape and the community path both write this shape.
    """
    events = {
        'schema_version': 1,
        'run_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'added': list(added),
        'revived': list(revived),
        'retired': list(retired),
    }
    Path(path).write_text(json.dumps(events, indent=2))
