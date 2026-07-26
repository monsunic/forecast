"""Tests for get_sites() registry."""


def test_get_sites_contains_five_ports():
    from plotter.core.config_loader import get_sites

    sites = get_sites()
    ids = {s["id"] for s in sites}
    assert ids == {
        "singapore",
        "port_klang",
        "tanjung_pelepas",
        "laem_chabang",
        "tanjung_priok",
    }
    sg = next(s for s in sites if s["id"] == "singapore")
    assert sg["name"] == "Port of Singapore"
    assert abs(sg["lat"] - 1.2788) < 1e-6
    assert abs(sg["lon"] - 103.7566) < 1e-6
