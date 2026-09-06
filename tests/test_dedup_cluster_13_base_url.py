"""Кластер-13 дедупликации: DaemonClient.base_url и McpDaemon.base_url должны
собирать URL через общий gemini_translator.mcp.paths.build_base_url, а не
дублировать IPv6-логику каждый в своём классе.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gemini_translator.mcp import client as client_mod
from gemini_translator.mcp import daemon as daemon_mod
from gemini_translator.mcp import paths as paths_mod


class BuildBaseUrlCharacterizationTests(unittest.TestCase):
    """Характеризационные тесты канонической функции: крайние случаи, которые
    различали бы копии (IPv4, голый IPv6, уже забракетованный IPv6, hostname).
    """

    def test_ipv4_host_has_no_brackets(self):
        self.assertEqual(paths_mod.build_base_url("127.0.0.1", 8080), "http://127.0.0.1:8080")

    def test_hostname_has_no_brackets(self):
        self.assertEqual(paths_mod.build_base_url("localhost", 65016), "http://localhost:65016")

    def test_bare_ipv6_gets_wrapped_in_brackets(self):
        self.assertEqual(paths_mod.build_base_url("::1", 9000), "http://[::1]:9000")

    def test_already_bracketed_ipv6_is_not_double_wrapped(self):
        self.assertEqual(paths_mod.build_base_url("[::1]", 9000), "http://[::1]:9000")


class BaseUrlRoutingTests(unittest.TestCase):
    """Маршрутизация: оба бывших места дублирования обязаны звать
    paths.build_base_url, а не иметь собственную копию логики.
    """

    def test_daemon_client_base_url_calls_shared_helper_directly(self):
        client = client_mod.DaemonClient("127.0.0.1", 12345, "tok")
        with mock.patch.object(client_mod, "build_base_url") as fake:
            fake.return_value = "http://sentinel:0"
            self.assertEqual(client.base_url, "http://sentinel:0")
            fake.assert_called_once_with(client.host, client.port)

    def test_mcp_daemon_base_url_calls_shared_helper_directly(self):
        daemon = daemon_mod.McpDaemon.__new__(daemon_mod.McpDaemon)
        daemon.host = "127.0.0.1"
        daemon.port = 54321
        with mock.patch.object(daemon_mod, "build_base_url") as fake:
            fake.return_value = "http://sentinel:0"
            self.assertEqual(daemon.base_url, "http://sentinel:0")
            fake.assert_called_once_with(daemon.host, daemon.port)


if __name__ == "__main__":
    unittest.main()
