"""Generate complete tileable autonomous-mall worker cells and the standard five-worker row."""

from __future__ import annotations

from examples.autonomous_mall.device_tiles import build_complete_book
from examples.autonomous_mall.manual_controller import _compose_controller, compile_manual_tiles


def build_complete_blueprint_book() -> dict[str, object]:
    """Compile controllers once, attach physical devices, and return the complete blueprint book."""

    head, assembler, recycler = compile_manual_tiles()
    controller_only = _compose_controller(head, assembler, recycler)
    return build_complete_book(
        head.blueprint,
        assembler.blueprint,
        recycler.blueprint,
        controller_only,
    ).payload()


def main() -> None:
    from factorio_circuit.synthesis.interface import encode_blueprint_payload

    print(encode_blueprint_payload(build_complete_blueprint_book()))


if __name__ == "__main__":
    main()
