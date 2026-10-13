import socket
from unittest.mock import patch

import pytest

from outbound_security import pinned_request


def _addr(ip):
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))


def test_pins_domain_to_public_ip():
    with patch("outbound_security.socket.getaddrinfo", return_value=[_addr("93.184.215.14")]):
        target, headers, opts = pinned_request("https://recipes.example.org/a?b=1")
    assert target == "https://93.184.215.14/a?b=1"
    assert headers["Host"] == "recipes.example.org"
    assert opts["sni_hostname"] == "recipes.example.org"


def test_rejects_mixed_dns_answer():
    with patch("outbound_security.socket.getaddrinfo", return_value=[
        _addr("93.184.215.14"), _addr("10.2.3.4")]):
        with pytest.raises(ValueError):
            pinned_request("https://recipes.example.org/")


def test_rejects_unexpected_port():
    with pytest.raises(ValueError):
        pinned_request("https://recipes.example.org:8443/")
