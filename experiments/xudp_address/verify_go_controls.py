"""Reject arbitrary build/test errors masquerading as the expected RED control."""
import argparse
import json
import ipaddress
import re
from pathlib import Path

PREFIX = "TestExperimentXUDP"
RED = {PREFIX + x for x in (
    "RemoteAddrCapturesInitialLogicalDestination",
    "ConnectedWrapperExactReplyTupleAndPayload",
    "SerialResolvedAddressIsActualSelectedAddress",
)}
GREEN = {PREFIX + x for x in (
    "ConnectedWritesBindChangedFramesToOldTargetNotReject",
    "UnboundPerPacketDestinationAndSourceUnchanged",
)}
REMOTE = PREFIX + "RemoteAddrCapturesInitialLogicalDestination"
TUPLE = PREFIX + "ConnectedWrapperExactReplyTupleAndPayload"
WRITE = PREFIX + "ConnectedWritesBindChangedFramesToOldTargetNotReject"
SELECTED = PREFIX + "SerialResolvedAddressIsActualSelectedAddress"
FAILURE_MARKERS = {
    **{REMOTE + "/" + address: "D2_EXPECTED_REMOTE_ADDR got=127.0.0.2:24443 want=" + address
       for address in ("127.0.0.3:12345", "[2001:db8::3]:12345", "app.example.test:12345", "a_b.example.test:12345")},
    **{TUPLE + "/" + method: "D2_EXPECTED_REPLY_TUPLE got=127.0.0.2:24443 want=127.0.0.3:12345"
       for method in ("ReadFrom", "ReadPacket")},
    SELECTED: "D2_EXPECTED_SELECTED_ADDR got=127.0.0.2:24443 want=192.0.2.20:12345",
}
CHILDREN = {name for name in FAILURE_MARKERS if "/" in name} | {WRITE + "/WriteTo", WRITE + "/WritePacket"}


def canonical_marker(marker):
    # Fixed sing's wrapper renders IPv4 as mapped IPv6; compare the actual
    # address and port, never weaken the seven exact assertion identities.
    match = re.fullmatch(r"([A-Z_]+) got=(\S+) want=(\S+)", marker)
    if match is None:
        raise ValueError("Malformed Go address failure marker")

    def endpoint(value):
        host, separator, port = value.rpartition(":")
        if not separator or not re.fullmatch(r"[1-9][0-9]{0,4}", port) or int(port) > 65535:
            raise ValueError("Malformed Go address endpoint")
        if "%" in host:
            raise ValueError("Scoped Go address endpoint is outside the frozen fixture")
        if host.startswith("[") and host.endswith("]"):
            address = ipaddress.IPv6Address(host[1:-1])
            address = address.ipv4_mapped or address
        elif ":" in host or "[" in host or "]" in host or not host:
            raise ValueError("Malformed Go address host")
        else:
            try:
                address = ipaddress.IPv4Address(host)
            except ValueError:
                return host + ":" + port  # Exact FQDN spelling is retained.
        return ("[" + str(address) + "]" if address.version == 6 else str(address)) + ":" + port

    return match[1] + " got=" + endpoint(match[2]) + " want=" + endpoint(match[3])


def verify(directory):
    summary = {}
    for phase in ("baseline", "patched"):
        rows = [json.loads(line) for line in (directory / (phase + "-go-tests.jsonl")).read_text().splitlines()]
        if not rows or any(x.get("Action") == "skip" for x in rows):
            raise ValueError("Empty or skipped Go experiment: " + phase)
        terminal = [x for x in rows if x.get("Action") in ("pass", "fail") and
                    x.get("Test", "").startswith(PREFIX) and "/" not in x["Test"]]
        outcomes = {x["Test"]: x["Action"] for x in terminal}
        expected = {name: "fail" if phase == "baseline" and name in RED else "pass" for name in RED | GREEN}
        if len(terminal) != 5 or outcomes != expected:
            raise ValueError("Go RED/GREEN names differ: " + phase + ": " + repr(outcomes))
        children = [x for x in rows if x.get("Action") in ("pass", "fail") and "/" in x.get("Test", "")]
        expected_children = {name: "fail" if phase == "baseline" and name in FAILURE_MARKERS else "pass"
                             for name in CHILDREN}
        if len(children) != len(CHILDREN) or {x["Test"]: x["Action"] for x in children} != expected_children:
            raise ValueError("Go subtest RED/GREEN coverage differs: " + phase)
        markers = [(x.get("Test"), canonical_marker(line.split("D2_EXPECTED_", 1)[-1]))
                   for x in rows for line in x.get("Output", "").splitlines() if "D2_EXPECTED_" in line]
        expected_markers = [(name, canonical_marker(marker.split("D2_EXPECTED_", 1)[1])) for name, marker in FAILURE_MARKERS.items()]
        if sorted(markers) != sorted(expected_markers if phase == "baseline" else []):
            raise ValueError("Go failure is not the seven exact original address/tuple assertions: " + phase)
        package = [x for x in rows if "Test" not in x and x.get("Action") in ("pass", "fail")]
        exit_code = json.loads((directory / (phase + "-go-test-exit.json")).read_text())["returncode"]
        if len(package) != 1 or package[0]["Action"] != ("fail" if phase == "baseline" else "pass") or exit_code != (1 if phase == "baseline" else 0):
            raise ValueError("Unexpected Go package/exit status: " + phase)
        summary[phase] = {"top_level": outcomes, "subtests": expected_children,
                          "expected_address_failure_count": len(markers), "returncode": exit_code,
                          "package_seconds": package[0].get("Elapsed")}
    (directory / "go-control-verification.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2))
