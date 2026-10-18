"""Additional mixed-route accounting; frozen double-hop evidence stays strict."""
from collections import Counter
from copy import deepcopy

from proxy_loopback_support import DeliveryEvidence


class MixedDeliveryEvidence(DeliveryEvidence):
    """Authorize ordinary DNS by exact fresh name/type, never observed baseline."""
    def __init__(self, previous):
        super().__init__(previous.answers, previous.ordinary)
        self.targets = previous.targets
        self.dns = previous.dns
        self.udp = previous.udp
        self.ordinary_dns = Counter()

    def expect_ordinary_dns(self, host):
        self.ordinary_dns.update(((host, 1), (host, 28)))

    def ordinary_snapshot(self):
        with self.ordinary.lock:
            return list(self.ordinary.queries)

    def assert_exact(self, case):
        self.assert_targets(case)
        case.assertEqual(Counter(self.answers.snapshot()), self.dns,
                         "Protected DNS count/source/name differs from authorized lookups")
        case.assertEqual(Counter(self.ordinary_snapshot()), self.ordinary_dns,
                         "Ordinary DNS differs from explicitly authorized direct lookups")


def client_shell(port, controller_port):
    """Only fixture listener/selection settings, never a replacement node."""
    return {"mixed-port": port, "bind-address": "127.0.0.1", "allow-lan": False,
            "external-controller": f"127.0.0.1:{controller_port}",
            "secret": "fake-inbound-controller-only", "mode": "rule",
            "log-level": "debug", "ipv6": False, "find-process-mode": "off",
            "geo-auto-update": False,
            "profile": {"store-selected": False, "store-fake-ip": False},
            "rules": ["MATCH,ENTRY"]}


def yaml_client(artifact, names, port, controller_port):
    """Copy actual ordinary exported nodes without changing any protocol field."""
    nodes = artifact["proxies"]
    if len(nodes) != len(names) or {node["name"] for node in nodes} != set(names):
        raise AssertionError("Ordinary export does not contain the exact selected entries")
    config = client_shell(port, controller_port)
    config.update(proxies=deepcopy(nodes), **{"proxy-groups": [
        {"name": "ENTRY", "type": "select", "proxies": list(names)},
        {"name": "GLOBAL", "type": "select", "proxies": ["ENTRY"]}]})
    return config


def uri_client(provider, port, controller_port):
    config = client_shell(port, controller_port)
    config.update(**{"proxy-providers": {"ordinary": {"type": "file", "path": str(provider)}},
        "proxy-groups": [{"name": "ENTRY", "type": "select", "use": ["ordinary"]},
                         {"name": "GLOBAL", "type": "select", "proxies": ["ENTRY"]}]})
    return config
