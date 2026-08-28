from factorio_circuit.ir.physical import WireColor
from factorio_circuit.synthesis.multilevel import (
    CoarseningLevel,
    ImplementationHyperedge,
    PlacementMacro,
)
from factorio_circuit.synthesis.multilevel_anneal import (
    MacroAnnealOptions,
    anneal_macro_geometry,
    coarse_cut_congestion,
)
from factorio_circuit.synthesis.multilevel_zoom import MacroGeometry, validate_macro_placement


def _square_geometry(*, fixed_first: bool = False) -> MacroGeometry:
    level = CoarseningLevel(
        tuple(PlacementMacro((index + 1,), fixed=fixed_first and index == 0) for index in range(4))
    )
    return MacroGeometry(
        level=level,
        centers=((0.0, 0.0), (8.0, 0.0), (0.0, 8.0), (8.0, 8.0)),
        half_extents=((0.5, 0.5),) * 4,
        implementation_area=4.0,
    )


def _diagonal_edges() -> tuple[ImplementationHyperedge, ...]:
    return (
        ImplementationHyperedge((1, 4), WireColor.RED),
        ImplementationHyperedge((2, 3), WireColor.RED),
    )


def test_zero_budget_is_exact_pass_through() -> None:
    geometry = _square_geometry()

    result = anneal_macro_geometry(
        geometry,
        _diagonal_edges(),
        options=MacroAnnealOptions(proposals=0),
    )

    assert result.geometry == geometry
    assert result.before == result.after
    assert result.stats.proposals == 0
    assert result.stats.accepted_proposals == 0


def test_macro_anneal_is_deterministic_for_fixed_seed() -> None:
    geometry = _square_geometry()
    options = MacroAnnealOptions(proposals=512, random_seed=17)

    left = anneal_macro_geometry(geometry, _diagonal_edges(), options=options)
    right = anneal_macro_geometry(geometry, _diagonal_edges(), options=options)

    assert left == right


def test_fixed_macro_stays_exact_and_area_growth_is_bounded() -> None:
    geometry = _square_geometry(fixed_first=True)
    options = MacroAnnealOptions(
        proposals=1024,
        random_seed=7,
        max_area_factor=1.02,
    )

    result = anneal_macro_geometry(geometry, _diagonal_edges(), options=options)

    validate_macro_placement(result.geometry)
    assert result.geometry.centers[0] == geometry.centers[0]
    assert result.after.bounding_area <= result.before.bounding_area * 1.02 + 1e-9
    assert result.after_energy.value <= result.before_energy.value + 1e-9


def test_anneal_reduces_obvious_diagonal_hpwl_without_reopening_area() -> None:
    geometry = _square_geometry()
    options = MacroAnnealOptions(
        proposals=2048,
        random_seed=0,
        area_weight=0.25,
        hpwl_weight=1.0,
        congestion_weight=0.0,
        max_area_factor=1.0,
    )

    result = anneal_macro_geometry(geometry, _diagonal_edges(), options=options)

    validate_macro_placement(result.geometry)
    assert result.after.bounding_area <= result.before.bounding_area + 1e-9
    assert result.after.hypernet_hpwl < result.before.hypernet_hpwl


def test_cut_congestion_penalizes_parallel_nets_crossing_the_same_channel() -> None:
    geometry = _square_geometry()
    crossing = (
        ImplementationHyperedge((1, 2), WireColor.RED),
        ImplementationHyperedge((3, 4), WireColor.RED),
        ImplementationHyperedge((1, 4), WireColor.GREEN),
        ImplementationHyperedge((2, 3), WireColor.GREEN),
    )
    local = (
        ImplementationHyperedge((1, 3), WireColor.RED),
        ImplementationHyperedge((2, 4), WireColor.RED),
    )

    assert coarse_cut_congestion(geometry, crossing) > coarse_cut_congestion(geometry, local)
