"""Review-window ("leverage") limits shared by the UI and pipeline.

The user-facing setting is a single leverage value: processing pauses when
this many documents are waiting in the Review tab and refills when the queue
drops to ``REFILL_AT`` documents.
"""

LEVERAGE_MIN = 20
LEVERAGE_MAX = 100
LEVERAGE_DEFAULT = 50
REFILL_AT = 10


def clamp_leverage(value) -> int:
    """Return a valid leverage value (20-100), falling back to the default."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = LEVERAGE_DEFAULT
    return max(LEVERAGE_MIN, min(LEVERAGE_MAX, number))


def normalize_review_window(config: dict) -> int:
    """Clamp the saved leverage and force the fixed refill threshold."""
    high = clamp_leverage(config.get("review_window_high", LEVERAGE_DEFAULT))
    config["review_window_high"] = high
    config["review_window_low"] = REFILL_AT
    return high
