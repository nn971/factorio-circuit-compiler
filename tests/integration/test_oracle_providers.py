import pytest

from factorio_circuit import (
    Circuit,
    ExternalOracleProvider,
    OracleBindingError,
    ScalarConstantOracleProvider,
    SignalId,
    VectorConstantOracleProvider,
    lower_to_abstract_physical,
)
from factorio_circuit.ir.abstract_physical import ConstantCombinator


IRON = SignalId("item", "iron-plate")
COPPER = SignalId("item", "copper-plate")


def test_physical_lowering_requires_explicit_provider_for_every_oracle() -> None:
    c = Circuit("missing_provider")
    temperature = c.oracle("temperature")
    c.output("temperature", temperature)

    with pytest.raises(OracleBindingError, match="missing.*temperature"):
        lower_to_abstract_physical(c, optimize=False)


def test_external_provider_intentionally_preserves_manual_boundary_port() -> None:
    c = Circuit("external_provider")
    temperature = c.oracle("temperature")
    c.output("temperature", temperature)

    lowered = lower_to_abstract_physical(
        c,
        optimize=False,
        oracle_providers={"temperature": ExternalOracleProvider()},
    )

    assert [port.name for port in lowered.abstract_physical.inputs] == ["temperature"]


def test_scalar_constant_provider_is_inserted_before_joint_physical_synthesis() -> None:
    c = Circuit("constant_provider")
    temperature = c.oracle("temperature")
    c.output("temperature", temperature + 1)

    lowered = lower_to_abstract_physical(
        c,
        optimize=False,
        oracle_providers={"temperature": ScalarConstantOracleProvider(42)},
    )

    assert lowered.abstract_physical.inputs == []
    providers = [
        entity
        for entity in lowered.abstract_physical.entities
        if isinstance(entity, ConstantCombinator)
        and entity.description == "ORACLE temperature: constant 42"
    ]
    assert len(providers) == 1
    provider = providers[0]
    provider_net = next(
        net
        for net in lowered.abstract_physical.nets
        if any(endpoint.entity == provider.id for endpoint in net.endpoints)
    )
    assert len(provider_net.endpoints) >= 2
    assert any(endpoint.entity != provider.id for endpoint in provider_net.endpoints)


def test_vector_constant_provider_can_supply_world_stock_map() -> None:
    c = Circuit("stock_provider")
    stock = c.oracle_signals("stock")
    c.output("stock", stock)

    lowered = lower_to_abstract_physical(
        c,
        optimize=False,
        oracle_providers={
            "stock": VectorConstantOracleProvider({IRON: 120, COPPER: 80}),
        },
    )

    assert lowered.abstract_physical.inputs == []
    oracle_net = next(
        net
        for net in lowered.abstract_physical.nets
        if {IRON, COPPER}.issubset(set(net.fixed_signals))
    )
    assert oracle_net.carries_dynamic_vector


def test_provider_bindings_reject_unknown_oracle_names() -> None:
    c = Circuit("extra_provider")
    temperature = c.oracle("temperature")
    c.output("temperature", temperature)

    with pytest.raises(OracleBindingError, match="undeclared.*humidity"):
        lower_to_abstract_physical(
            c,
            optimize=False,
            oracle_providers={
                "temperature": ExternalOracleProvider(),
                "humidity": ExternalOracleProvider(),
            },
        )
