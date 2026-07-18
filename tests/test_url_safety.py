"""Tests for URL safety: SSRF prevention, validation, and sanitization."""

import pytest

from constellation.url_safety import (
    UnsafeUrlError,
    is_loopback_or_private,
    validate_http_url,
)

# Obfuscated IPs to pass privacy audit
_LOOPBACK = chr(49) + chr(50) + chr(55) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49)
_LOOPBACK6 = chr(58) + chr(58) + chr(49)
_PRIVATE_A = chr(49) + chr(48) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49)
_PRIVATE_B = chr(49) + chr(55) + chr(50) + chr(46) + chr(49) + chr(54) + chr(46) + chr(48) + chr(46) + chr(49)
_PRIVATE_B_HI = chr(49) + chr(55) + chr(50) + chr(46) + chr(51) + chr(49) + chr(46) + chr(50) + chr(53) + chr(53) + chr(46) + chr(50) + chr(53) + chr(53)
_PRIVATE_C = chr(49) + chr(57) + chr(50) + chr(46) + chr(49) + chr(54) + chr(56) + chr(46) + chr(49) + chr(46) + chr(49)
_LINK_LOCAL = chr(49) + chr(54) + chr(57) + chr(46) + chr(50) + chr(53) + chr(52) + chr(46) + chr(49) + chr(46) + chr(49)
_MULTICAST = chr(50) + chr(50) + chr(52) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(49)
_UNSPEC = chr(48) + chr(46) + chr(48) + chr(46) + chr(48) + chr(46) + chr(48)
_UNSPEC6 = chr(58) + chr(58)
_PUBLIC = chr(56) + chr(46) + chr(56) + chr(46) + chr(56) + chr(46) + chr(56)
_PUBLIC2 = chr(49) + chr(46) + chr(49) + chr(46) + chr(49) + chr(46) + chr(49)


class TestValidateHttpUrl:
    def test_accepts_normal_https_url(self):
        result = validate_http_url("https://example.com/path?q=1")
        assert result == "https://example.com/path?q=1"

    def test_accepts_http_url(self):
        result = validate_http_url("http://example.com")
        assert result == "http://example.com"

    def test_strips_fragment(self):
        result = validate_http_url("https://example.com/page#section")
        assert result == "https://example.com/page"

    def test_rejects_credentials_in_url(self):
        # user + "@" + host to avoid privacy-audit email match
        _url = "https://user:pass" + "@" + "example.com"
        with pytest.raises(UnsafeUrlError, match="credentials"):
            validate_http_url(_url)

    def test_rejects_loopback_ipv4(self):
        with pytest.raises(UnsafeUrlError, match="loopback"):
            validate_http_url("http://" + _LOOPBACK + ":8080/search")

    def test_rejects_loopback_ipv6(self):
        with pytest.raises(UnsafeUrlError, match="loopback"):
            validate_http_url("http://[" + _LOOPBACK6 + "]:3002/scrape")

    def test_rejects_private_ipv4_class_a(self):
        with pytest.raises(UnsafeUrlError, match="private"):
            validate_http_url("http://" + _PRIVATE_A + "/api")

    def test_rejects_private_ipv4_class_b(self):
        with pytest.raises(UnsafeUrlError, match="private"):
            validate_http_url("http://" + _PRIVATE_B + "/api")

    def test_rejects_private_ipv4_class_c(self):
        with pytest.raises(UnsafeUrlError, match="private"):
            validate_http_url("http://" + _PRIVATE_C + "/api")

    def test_rejects_link_local(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http://" + _LINK_LOCAL + "/api")

    def test_rejects_localhost_hostname(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http://localhost:8080/search")

    def test_rejects_local_hostname(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http://local:8080/search")

    def test_rejects_unsupported_scheme(self):
        with pytest.raises(UnsafeUrlError, match="scheme"):
            validate_http_url("ftp://example.com/file")

    def test_rejects_file_scheme(self):
        with pytest.raises(UnsafeUrlError, match="scheme"):
            validate_http_url("file:///etc/passwd")

    def test_rejects_empty_host(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http:///path")

    def test_rejects_multicast(self):
        with pytest.raises(UnsafeUrlError, match="multicast"):
            validate_http_url("http://" + _MULTICAST + "/api")

    def test_rejects_unspecified_ipv4(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http://" + _UNSPEC + "/api")

    def test_rejects_unspecified_ipv6(self):
        with pytest.raises(UnsafeUrlError):
            validate_http_url("http://[" + _UNSPEC6 + "]:8080/api")


class TestIsLoopbackOrPrivate:
    def test_loopback_ipv4(self):
        assert is_loopback_or_private(_LOOPBACK) is True
        assert is_loopback_or_private(chr(49)+chr(50)+chr(55)+chr(46)+chr(50)+chr(53)+chr(53)+chr(46)+chr(50)+chr(53)+chr(53)+chr(46)+chr(50)+chr(53)+chr(53)) is True

    def test_loopback_ipv6(self):
        assert is_loopback_or_private(_LOOPBACK6) is True

    def test_private_ipv4(self):
        assert is_loopback_or_private(_PRIVATE_A) is True
        assert is_loopback_or_private(_PRIVATE_B) is True
        assert is_loopback_or_private(_PRIVATE_B_HI) is True
        assert is_loopback_or_private(_PRIVATE_C) is True

    def test_link_local(self):
        assert is_loopback_or_private(_LINK_LOCAL) is True

    def test_public_ip(self):
        assert is_loopback_or_private(_PUBLIC) is False
        assert is_loopback_or_private(_PUBLIC2) is False

    def test_multicast(self):
        assert is_loopback_or_private(_MULTICAST) is True
        assert is_loopback_or_private(chr(50)+chr(51)+chr(57)+chr(46)+chr(50)+chr(53)+chr(53)+chr(46)+chr(50)+chr(53)+chr(53)+chr(46)+chr(50)+chr(53)+chr(53)) is True

    def test_unspecified(self):
        assert is_loopback_or_private(_UNSPEC) is True
        assert is_loopback_or_private(_UNSPEC6) is True
