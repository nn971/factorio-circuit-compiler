from factorio_circuit.devices import DISPLAY_VIRTUAL_SIGNAL_POOL
from factorio_circuit.target.factorio.signals import (
    DEFAULT_VIRTUAL_SIGNAL_POOL,
    SIGNAL_ANYTHING,
    SIGNAL_EACH,
    SIGNAL_EVERYTHING,
)


def test_default_scratch_signal_pool_is_large_unique_and_base_virtual() -> None:
    assert len(DEFAULT_VIRTUAL_SIGNAL_POOL) == 65
    assert len(DEFAULT_VIRTUAL_SIGNAL_POOL) == len(set(DEFAULT_VIRTUAL_SIGNAL_POOL))
    assert all(signal.kind == "virtual" for signal in DEFAULT_VIRTUAL_SIGNAL_POOL)


def test_default_scratch_signal_pool_stays_disjoint_from_fixed_display_abi() -> None:
    assert set(DEFAULT_VIRTUAL_SIGNAL_POOL).isdisjoint(DISPLAY_VIRTUAL_SIGNAL_POOL)


def test_default_scratch_signal_pool_excludes_combinator_meta_signals() -> None:
    meta = {SIGNAL_EACH, SIGNAL_ANYTHING, SIGNAL_EVERYTHING}
    assert meta.isdisjoint(DEFAULT_VIRTUAL_SIGNAL_POOL)
