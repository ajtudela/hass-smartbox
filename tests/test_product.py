"""Product labels derived from the node version PID."""

from __future__ import annotations

from custom_components.smartbox.product import (
    PRODUCT_CODE_LABELS,
    product_code,
    product_label,
)


def test_product_code_uses_pid_characters_three_and_four() -> None:
    assert product_code("0a1a") == "1A"
    assert product_code("081c") == "1C"
    assert product_code("0104") == "04"
    assert product_code("02ff") == "FF"
    assert product_code("12") is None
    assert product_code(None) is None


def test_product_labels_by_control_category() -> None:
    assert product_label("htr", "0a1a") == "Triac heater"
    assert product_label("acm", "081c") == "Storage heater"
    assert product_label("thm", "0104") == "Thermostat"
    assert product_label("pmo", "0103") == "Power monitor"
    assert product_label("htr", "020a") == "Heater module"
    assert product_label("htr_mod", "02ff") == "Heater"
    assert product_label("thm", "0109") == "Thermostat"


def test_product_label_falls_back_to_the_wire_type() -> None:
    assert product_label("htr", None) == "Heater"
    assert product_label("htr", "zzzz") == "Heater"
    assert product_label("htr_mod", None) == "Heater"
    assert product_label("thm", None) == "Thermostat"
    assert product_label("acm", None) == "Storage heater"
    assert product_label("pmo", None) == "Power monitor"
    # An unknown wire type keeps its raw value rather than inventing a label.
    assert product_label("towel_rail_heater", None) == "towel_rail_heater"


def test_product_code_map_covers_every_documented_code_except_unknown() -> None:
    """The app's EProductCodeValue table is the source (api-contract.md §3)."""
    assert set(PRODUCT_CODE_LABELS) == {
        "01",
        "02",
        "03",
        "04",
        "07",
        "08",
        "09",
        "0A",
        "0B",
        "0C",
        "0D",
        "0F",
        "10",
        "11",
        "12",
        "13",
        "14",
        "16",
        "17",
        "18",
        "1A",
        "1B",
        "1C",
        "1D",
        "1E",
        "1F",
        "20",
        "21",
        "22",
        "23",
        "24",
        "25",
        "27",
        "28",
        "2A",
        "2E",
        "31",
        "32",
        "33",
        "34",
        "35",
        "36",
        "38",
        "39",
        "3D",
        "43",
        "47",
        "4C",
        "4D",
        "FF",
    }
    assert PRODUCT_CODE_LABELS["2E"] == "Storage heater"
