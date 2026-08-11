"""Reusable representative circuits shared by timing/integration tests."""

from factorio_circuit import Circuit


def n_tick_pulse_generator(n: int) -> Circuit:
    """Stretch one input pulse into exactly ``n`` physical output ticks.

    The logical expression is an n-sample lookahead window.  The compiler waits for those samples,
    so the realized circuit is causal: a one-tick trigger produces an n-tick physical pulse after
    the inferred pipeline latency.  This is a compact stress test for freshness and alignment.
    """

    if n <= 0:
        raise ValueError("pulse length must be positive")
    c = Circuit(f"pulse_{n}")
    trigger = c.input("trigger")
    pulse = trigger != 0
    for _ in range(1, n):
        c.tick(1)
        pulse = pulse | (trigger.sample() != 0)
    c.output("pulse", pulse)
    return c


def delayed_accumulator_window(*, offset: int = 3) -> Circuit:
    """Accumulator whose current update is bracketed by old/new state observations.

    The old read at ``offset`` forces the elastic transition to commit no earlier, while the new
    read one tick later forces it to commit exactly there.  Update operands deliberately remain the
    base streams: this cleanly tests compiler scheduling without introducing negative-time warm-up
    effects from future-sampled state updates.  The multi-stage clear predicate exercises physical
    latency inference independently from semantic commit time.
    """

    c = Circuit("delayed_accumulator_window")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")

    c.tick(offset)
    old = memory.value
    complex_clear = ((clear * 3) + 1) > 1
    memory.add(data)
    memory.clear(when=complex_clear)

    c.tick(1)
    new = memory.value
    c.output("old", old)
    c.output("new", new)
    return c


def switchable_fibonacci() -> Circuit:
    """Generate 1, 1, 2, 3, 5, ... while ``on`` is nonzero, otherwise hold.

    Two zero-initial vector registers store an affine form of the Fibonacci pair:

        A' = B
        B' = B + A + 1
        post-transition output = B' - A'

    ``A`` uses FreezeReg assignment while ``B`` uses two commutative conditional accumulator adds.
    Both registers store the same concrete signal lane, so state-to-state feeds stay whole-vector.
    """

    from factorio_circuit import SignalId

    fib_signal = SignalId("virtual", "signal-F")
    c = Circuit("switchable_fibonacci")
    on = c.input("on")
    one = c.constant_signals({fib_signal: 1})

    a = c.freeze("fib_a")
    b = c.accumulator("fib_b")

    old_a = a.value
    old_b = b.value

    a.set(old_b, when=on)
    b.add(old_a, when=on)
    b.add(one, when=on)

    # Observe the state after this invocation's transition.  For the affine pair above, B-A is the
    # ordinary Fibonacci sequence 1, 1, 2, 3, 5, ... .
    c.tick(1)
    new_a = a.value
    new_b = b.value
    c.output("fib", new_b.signal(fib_signal) - new_a.signal(fib_signal))
    return c
