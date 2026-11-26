"""Review-window backpressure gate.

The pipeline must stop submitting documents once the Review tab holds
``high`` unreviewed documents and only resume after the reviewer drains the
queue down to ``low``.
"""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ui.widgets import BackpressureGate


def test_has_capacity_until_high_watermark():
    gate = BackpressureGate(high=5, low=2)
    assert gate.has_capacity(0)
    gate.set_backlog(4)
    assert gate.has_capacity(0)
    gate.set_backlog(5)
    assert gate.paused
    assert not gate.has_capacity(0)


def test_resume_only_at_low_watermark():
    gate = BackpressureGate(high=5, low=2)
    gate.set_backlog(5)
    assert gate.paused
    gate.set_backlog(3)  # drained, but not far enough
    assert gate.paused
    assert not gate.has_capacity(0)
    gate.set_backlog(2)
    assert not gate.paused
    assert gate.has_capacity(0)


def test_in_flight_docs_count_toward_window():
    gate = BackpressureGate(high=5, low=1)
    gate.set_backlog(3)
    assert gate.has_capacity(1)
    assert not gate.has_capacity(2)


def test_low_watermark_clamped_below_high():
    gate = BackpressureGate(high=10, low=99)
    assert gate.low == 9


def test_wait_for_capacity_blocks_then_wakes():
    gate = BackpressureGate(high=5, low=1)
    gate.set_backlog(5)
    result = []
    waiter = threading.Thread(target=lambda: result.append(gate.wait_for_capacity(0)))
    waiter.start()
    time.sleep(0.4)
    assert waiter.is_alive()
    gate.set_backlog(1)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert result == [True]


def test_cancel_wakes_waiter_without_deadlock():
    gate = BackpressureGate(high=5, low=1)
    gate.set_backlog(5)
    result = []
    waiter = threading.Thread(
        target=lambda: result.append(gate.wait_for_capacity(0, lambda: False))
    )
    waiter.start()
    time.sleep(0.2)
    gate.cancel()
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert result == [False]
    assert not gate.has_capacity(0)


def test_raising_leverage_unpauses_below_new_limit():
    gate = BackpressureGate(high=5, low=2)
    gate.set_backlog(5)
    assert gate.paused
    gate.set_high(10)
    assert gate.high == 10
    assert not gate.paused
    assert gate.has_capacity(0)


def test_lowering_leverage_pauses_when_backlog_exceeds_it():
    gate = BackpressureGate(high=10, low=2)
    gate.set_backlog(8)
    assert not gate.paused
    gate.set_high(5)
    assert gate.paused
    assert not gate.has_capacity(0)


def test_set_high_keeps_low_watermark_and_clamps_above_it():
    gate = BackpressureGate(high=10, low=3)
    gate.set_high(1)
    assert gate.high == 4
    assert gate.low == 3


def test_set_high_wakes_waiter():
    gate = BackpressureGate(high=5, low=1)
    gate.set_backlog(5)
    result = []
    waiter = threading.Thread(target=lambda: result.append(gate.wait_for_capacity(0)))
    waiter.start()
    time.sleep(0.2)
    gate.set_high(10)
    waiter.join(timeout=2)
    assert not waiter.is_alive()
    assert result == [True]
