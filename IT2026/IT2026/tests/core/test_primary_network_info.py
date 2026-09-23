from types import SimpleNamespace

from zvagent.collectors import system


def test_primary_network_prefers_platform_route_source_and_matching_mac(monkeypatch):
    wireless = [
        SimpleNamespace(family=system.socket.AF_INET, address="192.168.31.42"),
        SimpleNamespace(family=system.psutil.AF_LINK, address="AA-BB-CC-DD-EE-FF"),
    ]
    vpn = [
        SimpleNamespace(family=system.socket.AF_INET, address="172.30.242.137"),
        SimpleNamespace(family=system.psutil.AF_LINK, address="11-22-33-44-55-66"),
    ]
    monkeypatch.setattr(system, "_get_platform_route_source_ip", lambda: "192.168.31.42")
    monkeypatch.setattr(system.psutil, "net_if_addrs", lambda: {"Wi-Fi": wireless, "VPN": vpn})

    assert system.get_primary_network_info() == ("192.168.31.42", "AA-BB-CC-DD-EE-FF")


def test_primary_network_falls_back_when_route_source_has_no_matching_mac(monkeypatch):
    ethernet = [
        SimpleNamespace(family=system.socket.AF_INET, address="10.0.0.20"),
        SimpleNamespace(family=system.psutil.AF_LINK, address="00-11-22-33-44-55"),
    ]
    monkeypatch.setattr(system, "_get_platform_route_source_ip", lambda: "192.168.31.42")
    monkeypatch.setattr(system.psutil, "net_if_addrs", lambda: {"Ethernet": ethernet})
    monkeypatch.setattr(
        system.psutil,
        "net_if_stats",
        lambda: {"Ethernet": SimpleNamespace(isup=True, speed=1000)},
    )

    assert system.get_primary_network_info() == ("10.0.0.20", "00-11-22-33-44-55")
<<<<<<< HEAD


def test_platform_route_interface_name_matches_route_source(monkeypatch):
    wireless = [
        SimpleNamespace(family=system.socket.AF_INET, address="172.17.40.137"),
        SimpleNamespace(family=system.psutil.AF_LINK, address="98-AF-65-AB-D3-8F"),
    ]
    tunnel = [
        SimpleNamespace(family=system.socket.AF_INET, address="172.30.242.137"),
        SimpleNamespace(family=system.psutil.AF_LINK, address="11-22-33-44-55-66"),
    ]
    monkeypatch.setattr(system, "_get_platform_route_source_ip", lambda: "172.17.40.137")
    monkeypatch.setattr(system.psutil, "net_if_addrs", lambda: {"WLAN": wireless, "vgate0": tunnel})

    assert system.get_platform_route_interface_name() == "WLAN"
=======
>>>>>>> ab632a1a204ed06fef467d45a60e80c7a951259a
