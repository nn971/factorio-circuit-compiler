"""First autonomous-market controller composed only from primitive registers.

This is deliberately *not* a compiler FSM feature.  Four FreezeRegs form a depth-four task stack,
one FreezeReg stores the controller mode, and one FreezeReg remembers the currently selected item.

The environment uses held level handshakes because arbitrary one-tick pulses are not yet reliable for
multicycle domains:

* ``root_target`` is a persistent desired-stock threshold vector while ``root_enabled`` is high.
* while ``reader_request`` is high, the reader keeps ``reader_ingredients`` and ``reader_ready``
  stable until the request is withdrawn;
* while ``worker_request`` is high, the worker keeps ``worker_done`` asserted once the craft has
  completed until the request is withdrawn.

A stack entry is an arbitrary threshold vector T.  It is satisfied exactly when
``(T - stock).positive()`` is empty.  Missing prerequisites are pushed above their parent, giving a
depth-first recursive resolution order.  Product quantity is never stored or predicted: after each
craft the same top target is checked against observed stock again.
"""

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4
MODE_QUERY = SignalId("virtual", "signal-Q")
MODE_CRAFT = SignalId("virtual", "signal-C")


def build_controller() -> Circuit:
    circuit = Circuit("autonomous_market_controller")

    # Persistent/live environment observations.
    stock = circuit.signals("stock")
    root_target = circuit.signals("root_target")
    root_enabled = circuit.input("root_enabled") != 0
    reader_ingredients = circuit.signals("reader_ingredients")
    reader_ready = circuit.input("reader_ready") != 0
    worker_done = circuit.input("worker_done") != 0

    query_mode = circuit.constant_signals({MODE_QUERY: 1})
    craft_mode = circuit.constant_signals({MODE_CRAFT: 1})

    mode = circuit.freeze("mode")
    selected_item = circuit.freeze("selected_item")
    slots = [circuit.freeze(f"slot{index}") for index in range(DEPTH)]

    old_mode = mode.sample()
    old_selected = selected_item.sample()
    old_slots = [slot.sample() for slot in slots]

    top = old_slots[0]
    stack_nonempty = top.any()
    stack_empty = stack_nonempty.logical_not()
    stack_full = old_slots[-1].any()
    stack_not_full = stack_full.logical_not()

    querying = old_mode.signal(MODE_QUERY) != 0
    crafting = old_mode.signal(MODE_CRAFT) != 0
    checking = (querying | crafting).logical_not()

    # When the stack is empty, the persistent root target behaves like the bottom recursive call.
    # It is pushed only while currently unsatisfied, so an already-satisfied persistent target does
    # not cause a pop/push busy loop.
    root_missing = (root_target - stock).positive()
    root_needs_work = root_missing.any()
    push_root = checking * stack_empty * root_enabled * root_needs_work

    # Normal top-of-stack processing.
    target_missing = (top - stock).positive()
    target_has_missing = target_missing.any()
    check_top = checking * stack_nonempty
    pop_satisfied = check_top * target_has_missing.logical_not()
    start_query = check_top * target_has_missing
    selected_candidate = target_missing.max()

    # Reader response.  If any ingredient threshold is missing, push the entire ingredient vector
    # above the parent task.  Keeping the whole vector avoids needing to reconstruct the selected
    # lane's target count.  A full stack leaves the controller in QUERY until space is available.
    ingredient_missing = (reader_ingredients - stock).positive()
    ingredients_have_missing = ingredient_missing.any()
    push_prerequisite = querying * reader_ready * ingredients_have_missing * stack_not_full
    start_craft = querying * reader_ready * ingredients_have_missing.logical_not()
    blocked_on_full_stack = querying * reader_ready * ingredients_have_missing * stack_full

    # A completed craft does not mutate the task stack.  Returning to CHECK re-evaluates observed
    # stock; this feedback removes any need to know product quantity per recipe.
    finish_craft = crafting * worker_done

    # Mode is ordinary FreezeReg state.  Zero is CHECK/IDLE, signal-Q is QUERY, signal-C is CRAFT.
    mode_change = start_query | push_prerequisite | start_craft | finish_craft
    next_mode = query_mode.gate(start_query) + craft_mode.gate(start_craft)
    mode.set(next_mode, when=mode_change)

    # The selected one-lane item is captured when entering QUERY and survives through CRAFT.
    selected_item.set(selected_candidate, when=start_query)

    # Stack mutation, again using only one .set(...) call per FreezeReg.  The controller's conditions
    # make push and pop mutually exclusive by construction.
    push_stack = push_root | push_prerequisite
    push_data = root_target.gate(push_root) + reader_ingredients.gate(push_prerequisite)
    stack_change = push_stack | pop_satisfied

    for index, slot in enumerate(slots):
        if index == 0:
            pushed = push_data.gate(push_stack)
        else:
            pushed = old_slots[index - 1].gate(push_stack)
        if index + 1 < DEPTH:
            next_value = pushed + old_slots[index + 1].gate(pop_satisfied)
        else:
            next_value = pushed
        slot.set(next_value, when=stack_change)

    # External protocol and useful probe outputs.
    circuit.output("top_target", top)
    circuit.output("root_missing", root_missing)
    circuit.output("stack_empty", stack_empty)
    circuit.output("stack_full", stack_full)
    circuit.output("querying", querying)
    circuit.output("crafting", crafting)
    circuit.output("blocked_on_full_stack", blocked_on_full_stack)
    circuit.output("reader_request", querying)
    circuit.output("reader_item", old_selected.gate(querying))
    circuit.output("worker_request", crafting)
    circuit.output("worker_item", old_selected.gate(crafting))
    return circuit


if __name__ == "__main__":
    result = compile_circuit(build_controller())
    print(result.blueprint_string)
