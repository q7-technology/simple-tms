"""How full a run is, in decimals, with a plain message when it matters."""
from decimal import Decimal
from types import SimpleNamespace

from tms.services.load import Totals, consignment_totals, load, plain, total

D = Decimal


def pkg(kg=None, l=None, w=None, h=None, type="carton"):
    return SimpleNamespace(weight_kg=None if kg is None else D(kg),
                           length_cm=None if l is None else D(l),
                           width_cm=None if w is None else D(w),
                           height_cm=None if h is None else D(h), type=type)


def test_a_consignment_adds_up_its_packages_exactly():
    t = consignment_totals([pkg("9.5", "60", "40", "40"), pkg("0.1", "10", "10", "10")])
    assert t.weight_kg == D("9.6")
    assert t.volume_m3 == D("0.097")          # 0.096 + 0.001, no float drift
    assert (t.packages, t.cartons, t.unknown_weight, t.unknown_volume) == (2, 2, 0, 0)


def test_a_package_without_a_weight_or_size_adds_nothing_and_is_counted():
    t = consignment_totals([pkg(None, "60", "40", "40"),
                            pkg("2", "60", None, "40", type="satchel")])
    assert t.weight_kg == D("2") and t.volume_m3 == D("0.096")
    assert (t.unknown_weight, t.unknown_volume, t.cartons) == (1, 1, 1)


def test_totals_add_up():
    a = Totals(D("1.5"), D("0.2"), 1, 1, 0, 1)
    b = Totals(D("2"), D("0.05"), 3, 2, 1, 0)
    assert total([a, b]) == Totals(D("3.5"), D("0.25"), 4, 3, 1, 1)
    assert total([]) == Totals(D(0), D(0), 0, 0, 0, 0)


def test_well_under_the_limits_is_fine():
    out = load(Totals(D("2380"), D("11.2"), 5, 5, 0, 0), D("4500"), D("22"))
    assert out.state == "ok" and out.message is None


def test_ninety_per_cent_or_more_is_nearly_full():
    out = load(Totals(D("1120"), D("8.6"), 5, 5, 0, 0), D("1200"), D("9"))
    assert out.state == "near"
    assert out.message == "80 kg and 0.4 m³ left. Small drops only."
    exactly_full = load(Totals(D("1200"), D("1"), 1, 1, 0, 0), D("1200"), D("9"))
    assert exactly_full.state == "near"


def test_over_either_limit_is_over_and_says_by_how_much():
    heavy = load(Totals(D("4780"), D("19.4"), 4, 4, 0, 0), D("4500"), D("22"))
    assert heavy.state == "over"
    assert heavy.message == ("280 kg too heavy. Move a stop to another run before printing "
                             "the run sheet.")
    both = load(Totals(D("4500.2"), D("22.01"), 4, 4, 0, 0), D("4500"), D("22"))
    assert both.message.startswith("1 kg too heavy and 0.1 m³ too big.")


def test_left_rounds_down_and_over_rounds_up():
    out = load(Totals(D("1099.6"), D("8.15"), 1, 1, 0, 0), D("1200"), D("9"))
    assert out.message == "100 kg and 0.8 m³ left. Small drops only."


def test_plain_numbers_have_no_exponent_and_group_thousands():
    assert plain(D("280.000000")) == "280"
    assert plain(D("2380.5")) == "2,380.5"
    assert plain(D("0")) == "0"
    assert plain(D("-1200")) == "-1,200"
