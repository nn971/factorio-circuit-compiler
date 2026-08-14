"""First autonomous-market controller composed only from primitive registers.

This is deliberately *not* a compiler FSM feature. Four FreezeRegs form a depth-four task stack,
one FreezeReg stores the controller mode, and one FreezeReg remembers the currently selected item.

The external assembler interface follows the real circuit behavior validated by the prototype:

* ``root_target`` is a persistent desired-stock threshold vector while ``root_enabled`` is high;
* ``reader_item`` directly drives the recipe-reader assembler. The controller keeps the recipe
  asserted through a complete QUERY_WAIT logical interval before QUERY_EVAL inspects ingredients;
* ``worker_item`` directly drives the worker assembler while the controller waits for Read working;
* once ``worker_working`` is observed high, ``worker_item`` is withdrawn. Factorio applies a
  circuit-set recipe change/removal only after the current craft finishes, so the active craft can
  finish without another craft being started. When working later becomes low, stock is rechecked.

A stack entry is an arbitrary threshold vector T. It is satisfied exactly when
``(T - stock).positive()`` is empty. Missing prerequisites are pushed above their parent, giving a
depth-first recursive resolution order. Product quantity is never stored or predicted: after each
observed craft the same top target is checked against stock again.
"""

from factorio_circuit import SignalId, compile_circuit
from factorio_circuit.frontend import Circuit

DEPTH = 4
MODE_QUERY_WAIT = SignalId("virtual", "signal-R")
MODE_QUERY_EVAL = SignalId("virtual", "signal-Q")
MODE_START_WORKER = SignalId("virtual", "signal-C")
MODE_WAIT_WORKER = SignalId("virtual", "signal-W")


def build_controller() -> Circuit:
    circuit = Circuit("autonomous_market_controller")

    # Persistent/live environment observations.
    stock = circuit.signals("stock")
    root_target = circuit.signals("root_target")
    root_enabled = circuit.input("root_enabled") != 0
    reader_ingredients = circuit.signals("reader_ingredients")
    worker_working = circuit.input("worker_working") != 0

    query_wait_mode = circuit.constant_signals({MODE_QUERY_WAIT: 1})
    query_eval_mode = circuit.constant_signals({MODE_QUERY_EVAL: 1})
    start_worker_mode = circuit.constant_signals({MODE_START_WORKER: 1})
    wait_worker_mode = circuit.constant_signals({MODE_WAIT_WORKER: 1})

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

    query_waiting = old_mode.signal(MODE_QUERY_WAIT) != 0
    query_evaluating = old_mode.signal(MODE_QUERY_EVAL) != 0
    starting_worker = old_mode.signal(MODE_START_WORKER) != 0
    waiting_worker = old_mode.signal(MODE_WAIT_WORKER) != 0
    checking = (
        query_waiting | query_evaluating | starting_worker | waiting_worker
    ).logical_not()

    # When the stack is empty, the persistent root target behaves like the bottom recursive call.
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

    # Reader protocol: QUERY_WAIT asserts the selected recipe but deliberately ignores the reader
    # response. The next logical transition enters QUERY_EVAL while keeping reader_item asserted.
    # Only QUERY_EVAL may inspect ingredients and choose prerequisite-vs-worker.
    advance_query = query_waiting
    ingredient_missing = (reader_ingredients - stock).positive()
    ingredients_have_missing = ingredient_missing.any()
    push_prerequisite = query_evaluating * ingredients_have_missing * stack_not_full
    start_worker = query_evaluating * ingredients_have_missing.logical_not()
    blocked_on_full_stack = query_evaluating * ingredients_have_missing * stack_full

    # START_WORKER keeps the selected recipe asserted until Read working becomes high. On the next
    # state transition the recipe signal is withdrawn and WAIT_WORKER begins. Factorio lets the
    # already-started craft finish even though its circuit recipe signal has disappeared.
    worker_started = starting_worker * worker_working
    worker_finished = waiting_worker * worker_working.logical_not()

    # Mode is ordinary FreezeReg state. Zero is CHECK/IDLE; R is QUERY_WAIT; Q is QUERY_EVAL; C
    # waits for working=1; W waits for that observed craft to finish and working to return to zero.
    mode_change = (
        start_query
        | advance_query
        | push_prerequisite
        | start_worker
        | worker_started
        | worker_finished
    )
    next_mode = (
        query_wait_mode.gate(start_query)
        + query_eval_mode.gate(advance_query)
        + start_worker_mode.gate(start_worker)
        + wait_worker_mode.gate(worker_started)
    )
    mode.set(next_mode, when=mode_change)

    # The selected one-lane item is captured when entering QUERY_WAIT and survives through worker
    # control. QUERY_WAIT and QUERY_EVAL therefore request exactly the same reader recipe.
    selected_item.set(selected_candidate, when=start_query)

    # Stack mutation uses exactly one .set(...) call per FreezeReg. Push and pop are mutually
    # exclusive because only CHECK can pop and only CHECK/QUERY_EVAL can push.
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

    # Recipe vectors are requests by themselves: an empty vector means no recipe request. The
    # reader request stays asserted throughout both query phases.
    querying_reader = query_waiting | query_evaluating
    circuit.output("reader_item", old_selected.gate(querying_reader))
    circuit.output("worker_item", old_selected.gate(starting_worker))

    # Compact probes retained for the first physical prototype.
    circuit.output("mode", old_mode)
    circuit.output("top_target", top)
    circuit.output("blocked_on_full_stack", blocked_on_full_stack)
    return circuit


if __name__ == "__main__":
    result = compile_circuit(build_controller())
    print(result.blueprint_string)
