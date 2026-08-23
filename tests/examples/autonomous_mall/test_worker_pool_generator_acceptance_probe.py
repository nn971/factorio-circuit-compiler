from examples.autonomous_mall.worker_pool import build_worker_pool_probe_blueprint


def test_two_worker_generator_end_to_end() -> None:
    blueprint = build_worker_pool_probe_blueprint(2)
    entities = blueprint["entities"]
    assert sum(entity.get("name") == "assembling-machine-3" for entity in entities) == 2
