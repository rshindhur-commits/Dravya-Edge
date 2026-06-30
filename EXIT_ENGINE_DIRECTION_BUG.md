# Exit Engine Bug - Direction-Agnostic Exits Killing Shorts

**Date:** 2026-06-10  
**Severity:** CRITICAL 🔴

---

## The Problem

The Exit Engine (`app/exit/exit_engine.py`) has directional-agnostic exit conditions that trigger for BOTH long and short entries when they should only trigger for LONGS.

**Current Exit Conditions:**
```python
if latest["Close"] < latest["VWAP"]:
    exit_signal = True
    exit_reason = "Lost VWAP support"

elif latest["Close"] < latest["EMA20"]:
    exit_signal = True
    exit_reason = "Lost EMA20 support"
```

**The Problem:**
- For LONG entries: `Close < VWAP` means **exit** ✓ (correct)
- For SHORT entries: `Close < VWAP` means **in profit** ✗ (should NOT exit)

---

## Evidence from Latest Run

**QQQ (SHORT Entry: EMA_REJECTION_SHORT):**
```
[ENTRY SELECTED] type=EMA_REJECTION_SHORT quality=HIGH
[ENTRY OPENED] QQQ → opened trade
[TABLE ROW DEBUG] symbol=QQQ exit_signal=True replay_result=False active_trade=True
```

**Price Data at Time of Exit:**
```
Close = 689.73
VWAP = 700.06
EMA20 = 692.05
```

**Analysis:**
- Entry is SHORT ✓
- Price is below VWAP (689.73 < 700.06) ✓ - This is PROFITABLE for shorts
- BUT exit_signal = True ✗ - Exit engine triggered incorrectly

**What Happened:**
1. Trade opened with EMA_REJECTION_SHORT
2. evaluate_exit() was called
3. The condition `latest["Close"] < latest["VWAP"]` evaluated to True
4. exit_signal set to True with reason "Lost VWAP support"
5. Trade immediately closed/exited
6. **Result:** Profitable trade was killed immediately after entry

---

## Root Cause Analysis

The exit_engine doesn't know the entry direction. It needs to be aware of:
- Was this a LONG entry or SHORT entry?
- What are the appropriate exit conditions for each?

**For LONG entries:**
- `Close < VWAP` → Exit (price rejected from above)
- `Close < EMA20` → Exit (lost key support)

**For SHORT entries:**
- `Close > VWAP` → Exit (price rejected from below - price goes UP)
- `Close > EMA20` → Exit (lost key resistance)

---

## Solution

The exit_engine needs to know the entry type or direction. Options:

### Option 1: Pass Entry Type to exit_engine (Recommended)

```python
def evaluate_exit(df, analysis, risk_setup, entry_setup):
    entry_type = entry_setup.get("entry_type", "UNKNOWN")
    is_short = "SHORT" in entry_type.upper() or "BEARISH" in entry_type.upper()
    
    # VWAP exit logic
    if is_short:
        # For shorts, exit if price goes UP through VWAP
        if latest["Close"] > latest["VWAP"]:
            exit_signal = True
            exit_reason = "Lost VWAP resistance"
    else:
        # For longs, exit if price goes DOWN through VWAP
        if latest["Close"] < latest["VWAP"]:
            exit_signal = True
            exit_reason = "Lost VWAP support"
```

### Option 2: Infer Direction from Risk Setup

```python
# Check if stop_loss is above entry (short) or below entry (long)
entry_price = risk_setup.get("entry_price")
stop_loss = risk_setup.get("stop_loss")

is_short = stop_loss > entry_price
```

### Option 3: Pass Direction Explicitly

```python
def evaluate_exit(df, analysis, risk_setup, is_short=False):
    ...
```

---

## Code Changes Required

### Current Code (BROKEN)

```python
def evaluate_exit(df, analysis, risk_setup):
    latest = df.iloc[-1]
    exit_signal = False
    
    # ... other conditions ...
    
    elif latest["Close"] < latest["VWAP"]:
        exit_signal = True
        exit_reason = "Lost VWAP support"
    
    elif latest["Close"] < latest["EMA20"]:
        exit_signal = True
        exit_reason = "Lost EMA20 support"
```

### Fixed Code (Option 1)

```python
def evaluate_exit(df, analysis, risk_setup, entry_setup=None):
    latest = df.iloc[-1]
    exit_signal = False
    exit_reason = "Hold"
    
    # Determine if this is a short entry
    is_short = False
    if entry_setup:
        entry_type = entry_setup.get("entry_type", "").upper()
        is_short = "SHORT" in entry_type or "BEARISH" in entry_type
    
    # ... Stop Loss and other conditions ...
    
    elif is_short:
        # SHORT exit conditions: price goes UP through key levels
        if latest["Close"] > latest["VWAP"]:
            exit_signal = True
            exit_reason = "Lost VWAP resistance (short)"
        elif latest["Close"] > latest["EMA20"]:
            exit_signal = True
            exit_reason = "Lost EMA20 resistance (short)"
    else:
        # LONG exit conditions: price goes DOWN through key levels
        if latest["Close"] < latest["VWAP"]:
            exit_signal = True
            exit_reason = "Lost VWAP support (long)"
        elif latest["Close"] < latest["EMA20"]:
            exit_signal = True
            exit_reason = "Lost EMA20 support (long)"
```

---

## Impact

### Current Behavior (Broken)
- LONG trades: Working correctly (exit when price drops below VWAP/EMA20)
- SHORT trades: **IMMEDIATELY EXITED** after entry because price is below VWAP ✗

### After Fix
- LONG trades: Exit when price drops below key levels ✓
- SHORT trades: Exit when price rises above key levels ✓

---

## Call Sites to Update

**File:** `app/main.py` (Line ~553)

Current:
```python
exit_setup = evaluate_exit(
    df_15m,
    analysis_15m,
    {
        "stop_loss": active_trade["stop_loss"],
        "take_profit": active_trade["take_profit"]
    }
)
```

Fixed:
```python
exit_setup = evaluate_exit(
    df_15m,
    analysis_15m,
    {
        "stop_loss": active_trade["stop_loss"],
        "take_profit": active_trade["take_profit"],
        "entry_price": active_trade.get("entry_price")
    },
    entry_setup  # Pass entry type
)
```

---

## Testing Strategy

**Test Case 1: SHORT Entry (EMA_REJECTION_SHORT)**
- Entry: Close < EMA9, EMA9 < EMA20 (bearish)
- Expected: Exit when Close > VWAP or Close > EMA20 (price goes UP)
- Current: ✗ Exits immediately because Close < VWAP
- After Fix: ✓ Holds position while price is down

**Test Case 2: LONG Entry (EMA_PULLBACK)**
- Entry: Close > EMA9, EMA9 > EMA20 (bullish)
- Expected: Exit when Close < VWAP or Close < EMA20 (price goes DOWN)
- Current: ✓ Works correctly
- After Fix: ✓ Still works correctly

---

## Priority

🔴 **CRITICAL** - Blocks all SHORT entries from holding positions

---

## Related Issues

This bug explains why:
- Exit = True even though replay never ran
- Active trades immediately show exit signals
- SHORT positions are killed on first tick

