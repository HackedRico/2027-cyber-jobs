"""Pin the clock and cut the network for the test scripts.

Every test script imports this before the modules it tests:

    import testkit  # noqa: E402,F401

classify.py builds its cohort-year window from today's date when it is
imported, and the suites hold real 2026 titles, so on the real clock they
passed through 2026 and failed on 4 Jan 2027 with no code change. With the
clock pinned, a suite gives the same result on any day. The rollover itself is
tested by passing `today=` explicitly, as test_classification.py's COHORT and
SEASONS tables do.

The network is cut so a test that forgot a `responses` mock or reached for DNS
fails at once instead of passing on whatever the internet said that day.
Loopback stays open for the tests that run a local HTTP server, and the proxy
variables are cleared so no loopback proxy can relay a request out.

Test files read neither clock directly; test_wiring.py checks that, since a
test file binds the real datetime classes before it imports this module.
"""
import datetime as _dt
import ipaddress
import os
import socket
import time

# The date the fixture rows were written against. Move it only together with
# the rows whose years it decides, then rerun every suite.
TODAY = _dt.date(2026, 10, 1)

_RealDate, _RealDatetime = _dt.date, _dt.datetime


class _DateType(type):
    # A date made before the pin, or returned by arithmetic, is still a date.
    def __instancecheck__(cls, obj):
        return isinstance(obj, _RealDate)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _RealDate)


class _DatetimeType(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _RealDatetime)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, _RealDatetime)


class _PinnedDate(_RealDate, metaclass=_DateType):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


class _PinnedDatetime(_RealDatetime, metaclass=_DatetimeType):
    @classmethod
    def now(cls, tz=None):
        # Noon UTC, converted, so every zone sees the same instant and date.
        noon = _RealDatetime(TODAY.year, TODAY.month, TODAY.day, 12, tzinfo=_dt.UTC)
        moment = noon.astimezone(tz) if tz else noon.replace(tzinfo=None)
        return cls(moment.year, moment.month, moment.day, moment.hour, tzinfo=moment.tzinfo)

    @classmethod
    def today(cls):
        return cls.now()

    @classmethod
    def utcnow(cls):
        return cls.now()


_dt.date = _PinnedDate
_dt.datetime = _PinnedDatetime
# Naive local times then read as UTC on every machine.
os.environ['TZ'] = 'UTC'
if hasattr(time, 'tzset'):
    time.tzset()


class NetworkAccess(BaseException):
    """A test reached past loopback. Mock the request with `responses`.

    A BaseException, so the scrapers' `except Exception` handlers cannot turn
    it into a quiet failed board.
    """


def _loopback(host):
    if host in ('localhost', ''):
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


def _literal(host):
    try:
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return host == 'localhost'


for _name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy',
              'all_proxy'):
    os.environ.pop(_name, None)
os.environ['NO_PROXY'] = os.environ['no_proxy'] = '*'

_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo
_real_gethostbyname = socket.gethostbyname
_real_gethostbyname_ex = socket.gethostbyname_ex


def _connect(self, address):
    if isinstance(address, tuple) and not _loopback(str(address[0])):
        raise NetworkAccess(f'tests run offline: a test connected to {address[0]}')
    return _real_connect(self, address)


def _connect_ex(self, address):
    if isinstance(address, tuple) and not _loopback(str(address[0])):
        raise NetworkAccess(f'tests run offline: a test connected to {address[0]}')
    return _real_connect_ex(self, address)


def _getaddrinfo(host, *args, **kwargs):
    # An IP literal resolves without a lookup, which the SSRF guards in
    # common.py rely on; a hostname would ask real DNS.
    if host is not None and not _literal(str(host)):
        raise NetworkAccess(f'tests run offline: a test looked up {host}')
    return _real_getaddrinfo(host, *args, **kwargs)


def _gethostbyname(host):
    if not _literal(str(host)):
        raise NetworkAccess(f'tests run offline: a test looked up {host}')
    return _real_gethostbyname(host)


def _gethostbyname_ex(host):
    if not _literal(str(host)):
        raise NetworkAccess(f'tests run offline: a test looked up {host}')
    return _real_gethostbyname_ex(host)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
socket.getaddrinfo = _getaddrinfo
socket.gethostbyname = _gethostbyname
socket.gethostbyname_ex = _gethostbyname_ex
