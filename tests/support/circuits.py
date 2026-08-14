"""Reusable representative circuits shared by timing/integration tests."""

from factorio_circuit import Circuit


def n_tick_pulse_generator(n: int) -> Circuit:
    """Stretch one input pulse across ``n`` logical samples in the default P=1 domain.

    This case is deliberately stateless, so logical steps map one-to-one to physical ticks.  It
    remains useful as a freshness/alignment regression while stateful circuits may infer P > 1.
    """

    if n <= 0:
        raise ValueError("pulse length must be positive")
    c = Circuit(f"pulse_{n}")
    trigger = c.input("trigger")
    pulse = trigger != 0
    for _ in range(1, n):
        c.step(1)
        pulse = pulse | (trigger.sample() != 0)
    c.output("pulse", pulse)
    return c


def delayed_accumulator_window(*, offset: int = 3) -> Circuit:
    """Accumulator whose transition is bracketed by old/new logical observations."""

    c = Circuit("delayed_accumulator_window")
    data = c.signals("data")
    clear = c.input("clear")
    memory = c.accumulator("memory")

    c.step(offset)
    old = memory.sample()
    complex_clear = ((clear * 3) + 1) > 1
    memory.add(data)
    memory.clear(when=complex_clear)

    c.step(1)
    new = memory.sample()
    c.output("old", old)
    c.output("new", new)
    return c


def switchable_fibonacci() -> Circuit:
    """Generate 1, 1, 2, 3, 5, ... while ``on`` is nonzero, otherwise hold."""

    from factorio_circuit import SignalId

    fib_signal = SignalId("virtual", "signal-F")
    c = Circuit("switchable_fibonacci")
    on = c.input("on")
    one = c.constant_signals({fib_signal: 1})

    a = c.freeze("fib_a")
    b = c.accumulator("fib_b")

    old_a = a.sample()
    old_b = b.sample()

    a.set(old_b, when=on)
    b.add(old_a, when=on)
    b.add(one, when=on)

    c.step(1)
    new_a = a.sample()
    new_b = b.sample()
    c.output("fib", new_b.signal(fib_signal) - new_a.signal(fib_signal))
    return c
