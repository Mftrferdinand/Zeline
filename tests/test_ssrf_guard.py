"""SSRF guard: a host with ANY internal DNS record must be blocked.

The previous guard required every resolved record to be internal, so a DNS
name with one public and one private A record passed the check while the OS
could still connect over the private one — a classic mixed-record bypass.
"""
from __future__ import annotations

import ipaddress
import os
import sys
import unittest
from unittest import mock

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SOURCE_ROOT not in sys.path:
    sys.path.insert(0, SOURCE_ROOT)


def _info(address: str):
    return (mock.ANY, mock.ANY, mock.ANY, mock.ANY, (address, 0))


class InternalIPGuardTests(unittest.TestCase):
    def setUp(self):
        from zeline import tools

        self.tools = tools

    def test_mixed_records_block(self):
        public = "93.184.216.34"
        private = "10.0.0.5"
        with mock.patch.object(
            self.tools.socket, "getaddrinfo",
            return_value=[_info(public), _info(private)],
        ):
            self.assertTrue(self.tools._is_internal_ip("mixed.example"))

    def test_all_internal_blocks(self):
        with mock.patch.object(
            self.tools.socket, "getaddrinfo",
            return_value=[_info("192.168.1.1"), _info("10.0.0.5")],
        ):
            self.assertTrue(self.tools._is_internal_ip("internal.example"))

    def test_all_public_allows(self):
        with mock.patch.object(
            self.tools.socket, "getaddrinfo",
            return_value=[_info("93.184.216.34"), _info("1.1.1.1")],
        ):
            self.assertFalse(self.tools._is_internal_ip("public.example"))

    def test_link_local_literal_blocks(self):
        self.assertTrue(self.tools._addr_is_internal(ipaddress.ip_address("169.254.1.1")))


if __name__ == "__main__":
    unittest.main()
