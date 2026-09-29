"""Table-driven tests for the SSRF guard (MASTERSPEC §15).

DNS is stubbed for hostname cases so the suite is deterministic and offline:
the real resolver would make results depend on the network the tests run on.
Literal-address cases need no stubbing and exercise the parser directly.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

import pytest

from app.security.ssrf import (
    UrlBlocked,
    parse_ip_literal,
    validate_url,
)

# --------------------------------------------------------------------------- #
# DNS stubbing
# --------------------------------------------------------------------------- #

# hostname -> the addresses the stubbed resolver returns
FAKE_DNS: dict[str, list[str]] = {
    "example.com": ["93.184.216.34"],
    "public-v6.example": ["2606:2800:220:1:248:1893:25c8:1946"],
    "dual-stack.example": ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"],
    # Resolves straight to loopback -- the classic DNS-based bypass.
    "localtest.me": ["127.0.0.1"],
    "internal.example": ["10.0.0.5"],
    "metadata.example": ["169.254.169.254"],
    # Public on the first record, private on the second. Checking only the first
    # record would let this through, so it must be rejected.
    "rebind.example": ["93.184.216.34", "127.0.0.1"],
    "v6-loopback.example": ["::1"],
    "mapped.example": ["::ffff:127.0.0.1"],
    "demo-site": ["172.18.0.3"],
}


@pytest.fixture(autouse=True)
def stub_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace getaddrinfo with the FAKE_DNS table."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        addresses = FAKE_DNS.get(host.lower().rstrip("."))
        if addresses is None:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        results = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, port, 0, 0) if family == socket.AF_INET6 else (address, port)
            results.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return results

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# --------------------------------------------------------------------------- #
# The table
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Case:
    url: str
    allowed: bool
    why: str


CASES: tuple[Case, ...] = (
    # ---- allowed: ordinary public URLs ---------------------------------- #
    Case("https://example.com/", True, "plain https"),
    Case("http://example.com/", True, "plain http"),
    Case("https://example.com:8443/path?q=1#frag", True, "explicit port, query, fragment"),
    Case("https://EXAMPLE.COM/", True, "host case is normalised"),
    Case("https://example.com./", True, "fully-qualified trailing dot"),
    Case("https://public-v6.example/", True, "AAAA-only host"),
    Case("https://dual-stack.example/", True, "both records public"),
    Case("https://93.184.216.34/", True, "public IPv4 literal"),
    Case("https://[2606:2800:220:1:248:1893:25c8:1946]/", True, "public IPv6 literal"),
    # ---- blocked: scheme ------------------------------------------------ #
    Case("file:///etc/passwd", False, "file scheme"),
    Case("ftp://example.com/", False, "ftp scheme"),
    Case("gopher://example.com:70/_", False, "gopher scheme (request smuggling)"),
    Case("data:text/html,<script>alert(1)</script>", False, "data scheme"),
    Case("javascript:alert(1)", False, "javascript scheme"),
    Case("//example.com/", False, "scheme-relative, no scheme"),
    Case("example.com", False, "bare host, no scheme"),
    Case("", False, "empty string"),
    Case("   ", False, "whitespace only"),
    # ---- blocked: credentials and malformed ----------------------------- #
    Case("http://user:pass@example.com/", False, "embedded credentials"),
    Case("http://trusted.example@127.0.0.1/", False, "credentials disguising the real host"),
    Case("http:///path", False, "no host"),
    # ---- blocked: loopback in every notation ---------------------------- #
    Case("http://127.0.0.1/", False, "loopback dotted quad"),
    Case("http://127.0.0.1:8000/api", False, "loopback with port"),
    Case("http://localhost/", False, "localhost is not resolvable in the stub"),
    Case("http://2130706433/", False, "loopback as a decimal integer"),
    Case("http://0x7f000001/", False, "loopback as hex"),
    Case("http://0177.0.0.1/", False, "loopback with an octal first octet"),
    Case("http://127.1/", False, "loopback in short form"),
    Case("http://127.0.0.2/", False, "the whole 127/8 range, not just .1"),
    Case("http://[::1]/", False, "IPv6 loopback"),
    Case("http://[::ffff:127.0.0.1]/", False, "IPv4-mapped IPv6 loopback"),
    Case("http://0.0.0.0/", False, "unspecified address"),
    Case("http://[::]/", False, "IPv6 unspecified"),
    # ---- blocked: private ranges ---------------------------------------- #
    Case("http://10.0.0.1/", False, "RFC 1918 10/8"),
    Case("http://172.16.0.1/", False, "RFC 1918 172.16/12"),
    Case("http://172.31.255.254/", False, "upper end of 172.16/12"),
    Case("http://192.168.1.1/", False, "RFC 1918 192.168/16"),
    Case("http://100.64.0.1/", False, "carrier-grade NAT"),
    Case("http://[fc00::1]/", False, "IPv6 unique local"),
    Case("http://[fd12:3456::1]/", False, "IPv6 ULA in fd00::/8"),
    # ---- blocked: link-local and metadata ------------------------------- #
    Case("http://169.254.169.254/", False, "AWS/GCP/Azure metadata"),
    Case(
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        False,
        "metadata credentials path",
    ),
    Case("http://169.254.170.2/v2/credentials", False, "ECS task metadata"),
    Case("http://100.100.100.200/", False, "Alibaba Cloud metadata"),
    Case("http://192.0.0.192/", False, "Oracle Cloud metadata"),
    Case("http://169.254.1.1/", False, "link-local generally"),
    Case("http://[fe80::1]/", False, "IPv6 link-local"),
    Case("http://[fd00:ec2::254]/", False, "AWS IPv6 metadata"),
    # ---- blocked: reserved, multicast, documentation --------------------- #
    Case("http://224.0.0.1/", False, "IPv4 multicast"),
    Case("http://255.255.255.255/", False, "broadcast / reserved"),
    Case("http://192.0.2.1/", False, "TEST-NET-1 documentation range"),
    Case("http://198.51.100.1/", False, "TEST-NET-2 documentation range"),
    Case("http://203.0.113.1/", False, "TEST-NET-3 documentation range"),
    Case("http://198.18.0.1/", False, "benchmarking range"),
    Case("http://[2001:db8::1]/", False, "IPv6 documentation range"),
    Case("http://[ff02::1]/", False, "IPv6 multicast"),
    # ---- blocked: DNS that lands somewhere private ---------------------- #
    Case("http://localtest.me/", False, "public name resolving to 127.0.0.1"),
    Case("http://internal.example/", False, "name resolving into RFC 1918"),
    Case("http://metadata.example/", False, "name resolving to the metadata IP"),
    Case("http://rebind.example/", False, "second A record is private"),
    Case("http://v6-loopback.example/", False, "name resolving to ::1"),
    Case("http://mapped.example/", False, "name resolving to IPv4-mapped loopback"),
    Case("http://nxdomain.invalid/", False, "host does not resolve"),
    # ---- blocked: IPv6 transition embeddings ---------------------------- #
    Case("http://[2002:7f00:0001::]/", False, "6to4 wrapping 127.0.0.1"),
    Case("http://[64:ff9b::7f00:1]/", False, "NAT64 wrapping 127.0.0.1"),
)


def test_case_table_is_large_enough():
    """MASTERSPEC §15 asks for at least 30 cases; keep that honest."""
    assert len(CASES) >= 30, f"only {len(CASES)} cases"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.why)
def test_validate_url_table(case: Case):
    if case.allowed:
        result = validate_url(case.url)
        assert result.resolved_ips, f"{case.url} should record what it resolved to"
    else:
        with pytest.raises(UrlBlocked):
            validate_url(case.url)


# --------------------------------------------------------------------------- #
# ALLOWED_LOCAL_HOSTS override
# --------------------------------------------------------------------------- #


def test_local_host_is_blocked_without_the_override():
    with pytest.raises(UrlBlocked):
        validate_url("http://127.0.0.1:8081/")


def test_allow_list_permits_a_named_local_host():
    result = validate_url("http://127.0.0.1:8081/", allowed_local_hosts=("127.0.0.1",))
    assert result.allowed_by_override is True
    assert result.host == "127.0.0.1"
    assert result.port == 8081


def test_allow_list_permits_a_docker_service_name():
    result = validate_url("http://demo-site:8081/", allowed_local_hosts=("demo-site",))
    assert result.allowed_by_override is True
    assert result.resolved_ips == ("172.18.0.3",)


def test_allow_list_accepts_a_host_and_port_entry():
    result = validate_url("http://127.0.0.1:8081/", allowed_local_hosts=("127.0.0.1:8081",))
    assert result.allowed_by_override is True


def test_allow_list_does_not_leak_to_other_hosts():
    """Allowing one local host must not allow every local host."""
    with pytest.raises(UrlBlocked):
        validate_url("http://169.254.169.254/", allowed_local_hosts=("127.0.0.1",))
    with pytest.raises(UrlBlocked):
        validate_url("http://10.0.0.1/", allowed_local_hosts=("demo-site",))


def test_allow_list_entries_are_case_insensitive():
    result = validate_url("http://DEMO-SITE:8081/", allowed_local_hosts=("demo-site",))
    assert result.allowed_by_override is True


# --------------------------------------------------------------------------- #
# The numeric-host parser
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("2130706433", "127.0.0.1"),
        ("0x7f000001", "127.0.0.1"),
        ("0177.0.0.1", "127.0.0.1"),
        ("127.1", "127.0.0.1"),
        ("127.0.1", "127.0.0.1"),
        ("8.8.8.8", "8.8.8.8"),
        ("0", "0.0.0.0"),
        ("[::1]", "::1"),
        ("::1", "::1"),
        ("fe80::1%eth0", "fe80::1"),
        ("127.0.0.1.", "127.0.0.1"),
    ],
)
def test_parse_ip_literal_numeric_forms(host: str, expected: str):
    parsed = parse_ip_literal(host)
    assert parsed is not None, f"{host} should parse as an address"
    assert str(parsed) == expected


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "sub.example.co.uk",
        "0x.example.com",
        "1.2.3.4.example.com",
        "deadbeef",
        "cafe.example",
        "",
        "   ",
        "not-an-ip",
    ],
)
def test_parse_ip_literal_rejects_hostnames(host: str):
    assert parse_ip_literal(host) is None


# --------------------------------------------------------------------------- #
# Error reporting
# --------------------------------------------------------------------------- #


def test_blocked_error_carries_the_api_error_code():
    with pytest.raises(UrlBlocked) as excinfo:
        validate_url("http://169.254.169.254/")
    assert excinfo.value.code == "URL_BLOCKED"


def test_blocked_error_explains_metadata_specifically():
    with pytest.raises(UrlBlocked) as excinfo:
        validate_url("http://169.254.169.254/")
    assert "metadata" in str(excinfo.value).lower()


def test_blocked_error_names_the_offending_resolution():
    with pytest.raises(UrlBlocked) as excinfo:
        validate_url("http://internal.example/")
    message = str(excinfo.value)
    assert "internal.example" in message
    assert "10.0.0.5" in message


def test_validated_url_records_every_resolved_address():
    result = validate_url("https://dual-stack.example/")
    assert len(result.resolved_ips) == 2
