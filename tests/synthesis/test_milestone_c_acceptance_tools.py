from __future__ import annotations

from benchmarks import milestone_c_acceptance as acceptance
from benchmarks import milestone_c_examples as examples


def _row(
    case: str,
    *,
    proposals: int,
    seed: int,
    objective: tuple[int, float, float],
) -> dict[str, object]:
    return {
        "case": case,
        "proposals": proposals,
        "seed": seed,
        "kind": "core",
        "objective": list(objective),
        "runtime_seconds": 1.0,
        "work": {field: 0 for field in acceptance._WORK_FIELDS},
    }


def test_milestone_c_core_request_is_unique_and_complete() -> None:
    request = acceptance._request(seeds=2, proposals=4096, full=False)

    assert len(request) == len(acceptance.CORE_CASES) * 2
    assert len({acceptance._key(row) for row in request}) == len(request)
    assert {row["case"] for row in request} == set(acceptance.CORE_CASES)
    assert {row["seed"] for row in request} == {0, 1}
    assert {row["proposals"] for row in request} == {4096}


def test_milestone_c_full_request_adds_curves_without_duplicates() -> None:
    request = acceptance._request(seeds=2, proposals=4096, full=True)

    assert len({acceptance._key(row) for row in request}) == len(request)
    scale = [row for row in request if row["kind"] == "scale"]
    assert scale == [
        {
            "case": "large-sparse-1200",
            "proposals": 4096,
            "seed": 0,
            "kind": "scale",
        }
    ]
    for case in acceptance.CURVE_CASES:
        keys = {
            (row["proposals"], row["seed"])
            for row in request
            if row["case"] == case
        }
        assert keys == {
            (budget, seed)
            for budget in acceptance.CURVE_BUDGETS
            for seed in (0, 1)
        }


def test_milestone_c_compare_reports_lexicographic_and_component_outcomes() -> None:
    baseline = [
        _row("a", proposals=1, seed=0, objective=(2, 10.0, 10.0)),
        _row("b", proposals=1, seed=0, objective=(2, 10.0, 10.0)),
    ]
    current = [
        _row("a", proposals=1, seed=0, objective=(1, 20.0, 20.0)),
        _row("b", proposals=1, seed=0, objective=(2, 10.0, 11.0)),
    ]

    passed, summary = acceptance._compare(baseline, current)  # type: ignore[arg-type]

    assert not passed
    assert summary["lexicographic"] == {"better": 1, "equal": 0, "worse": 1}
    assert summary["components"]["relay_count"] == {
        "better": 1,
        "equal": 1,
        "worse": 0,
    }
    assert summary["components"]["occupied_area"] == {
        "better": 0,
        "equal": 1,
        "worse": 1,
    }
    assert summary["components"]["wire_length"] == {
        "better": 0,
        "equal": 0,
        "worse": 2,
    }


def test_milestone_c_svg_renders_wire_and_fixed_entity() -> None:
    snapshot = {
        "objective": [0, 2.0, 1.0],
        "positions": {"1": [0.0, 0.0], "2": [1.0, 0.0]},
        "relay_ids": [],
        "footprints": {"1": [0.5, 0.5], "2": [0.5, 0.5]},
        "fixed_ids": [1],
        "wires": [{"source": 1, "target": 2, "color": "red"}],
    }

    svg = examples._svg(snapshot, title="probe")

    assert "<svg" in svg
    assert "#c62828" in svg
    assert "#1565c0" in svg
    assert "<title>probe</title>" in svg
