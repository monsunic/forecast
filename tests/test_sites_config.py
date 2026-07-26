"""Tests for get_sites() registry."""


def test_get_sites_contains_configured_ports():
    from plotter.core.config_loader import get_sites

    sites = get_sites()
    ids = {s["id"] for s in sites}
    assert ids == {
        "haiphong",
        "ho_chi_minh",
        "laem_chabang",
        "manila",
        "port_klang",
        "singapore",
        "tanjung_perak",
        "tanjung_priok",
    }
    sg = next(s for s in sites if s["id"] == "singapore")
    assert sg["name"] == "Port of Singapore"
    assert abs(sg["lat"] - 1.2644) < 1e-6
    assert abs(sg["lon"] - 103.8200) < 1e-6


def test_get_sites_are_alphabetical_by_name():
    from plotter.core.config_loader import get_sites

    names = [s["name"] for s in get_sites()]
    # Registry is authored alphabetically by the common port name; the frontend
    # relies on this order for the dropdown.
    assert names == [
        "Port of Hai Phong",
        "Saigon Port (Ho Chi Minh)",
        "Laem Chabang Port",
        "Port of Manila",
        "Port Klang",
        "Port of Singapore",
        "Tanjung Perak (Surabaya)",
        "Tanjung Priok (Jakarta)",
    ]
