from datetime import datetime, timedelta

from app.utils.json_store import (
    load_json_file,
    save_json_file
)
from app.utils.runtime_logging import debug_print

MEMORY_FILE = "app/state/signal_memory.json"


def load_memory():

    return load_json_file(
        MEMORY_FILE,
        {}
    )


def save_memory(memory):

    save_json_file(
        MEMORY_FILE,
        memory
    )


def should_call_ai(symbol, signal, score):

    memory = load_memory()

    now = datetime.now()

    # First time seeing symbol
    if symbol not in memory:

        print(f"[AI CALL] {symbol} → First signal detected")

        memory[symbol] = {
            "last_signal": signal,
            "last_score": score,
            "last_ai_call": now.isoformat()
        }

        save_memory(memory)

        return True

    previous = memory[symbol]

    previous_signal = previous.get("last_signal")
    previous_score = previous.get("last_score")
    previous_call = previous.get("last_ai_call")

    try:

        previous_call_time = datetime.fromisoformat(previous_call)

    except (TypeError, ValueError):

        previous_call_time = datetime.min

    if previous_score is None:

        previous_score = 0

    cooldown_passed = (
        now - previous_call_time
    ) > timedelta(minutes=20)

    signal_changed = signal != previous_signal

    score_improved = score > previous_score

    # Decision logging
    debug_print(f"\n[AI DEBUG] {symbol}")
    debug_print(f"Current Signal: {signal}")
    debug_print(f"Previous Signal: {previous_signal}")
    debug_print(f"Current Score: {score}")
    debug_print(f"Previous Score: {previous_score}")
    debug_print(f"Cooldown Passed: {cooldown_passed}")
    debug_print(f"Signal Changed: {signal_changed}")
    debug_print(f"Score Improved: {score_improved}")

    if (
        signal_changed
        or score_improved
        or cooldown_passed
    ):

        print(f"[AI CALL] {symbol} → Conditions met")

        memory[symbol] = {
            "last_signal": signal,
            "last_score": score,
            "last_ai_call": now.isoformat()
        }

        save_memory(memory)

        return True

    print(f"[AI SKIP] {symbol} → No meaningful change")

    return False