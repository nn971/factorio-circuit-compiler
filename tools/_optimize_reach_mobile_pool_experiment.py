from pathlib import Path

path = Path("src/factorio_circuit/synthesis/incremental_joint_layout.py")
text = path.read_text()
text = text.replace(
    "from collections import Counter, defaultdict\n",
    "from bisect import bisect_left, bisect_right\nfrom collections import Counter, defaultdict\n",
    1,
)
old = '''    current = state.object_position(object_id)\n    if object_id in state.relay_positions:\n        candidates = grid.unit_slots\n    else:\n        candidates = base_placement._candidate_positions(\n            state.circuit.entity_by_id(object_id),\n            grid,\n        )\n\n    neighbors: list[Position] = []\n'''
new = '''    current = state.object_position(object_id)\n    if object_id in state.relay_positions:\n        x_positions = grid.unit_x_positions\n    else:\n        entity = state.circuit.entity_by_id(object_id)\n        if isinstance(entity, ConstantCombinator):\n            x_positions = grid.unit_x_positions\n        else:\n            x_positions = grid.x_positions\n    y_positions = grid.y_positions\n\n    neighbors: list[Position] = []\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected one candidate block, found {text.count(old)}")
text = text.replace(old, new, 1)
old = '''    for candidate in candidates:\n        if candidate == current:\n            continue\n        x, y = candidate\n        if (\n            x < left - _EPSILON\n            or x > right + _EPSILON\n            or y < top - _EPSILON\n            or y > bottom + _EPSILON\n        ):\n            continue\n        if all(_distance(candidate, neighbor) <= safe_span + _EPSILON for neighbor in neighbors):\n            return True\n'''
new = '''    x_start = bisect_left(x_positions, left - _EPSILON)\n    x_end = bisect_right(x_positions, right + _EPSILON)\n    y_start = bisect_left(y_positions, top - _EPSILON)\n    y_end = bisect_right(y_positions, bottom + _EPSILON)\n    for x in x_positions[x_start:x_end]:\n        for y in y_positions[y_start:y_end]:\n            candidate = (x, y)\n            if candidate == current:\n                continue\n            if all(\n                _distance(candidate, neighbor) <= safe_span + _EPSILON\n                for neighbor in neighbors\n            ):\n                return True\n'''
if text.count(old) != 1:
    raise SystemExit(f"expected one scan block, found {text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text)
