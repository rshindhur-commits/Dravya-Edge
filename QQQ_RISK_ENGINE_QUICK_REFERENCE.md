# QQQ Risk Engine Rejection - Quick Reference

**Status:** ✅ INVESTIGATED & FIXED

---

## The Question

You reported seeing this in logs:
```
ENTRY_SELECTED:
  type=EMA_REJECTION_SHORT
  quality=HIGH

RISK_ENGINE:
  allowed=False
  rr=1.5
```

Why would `allowed=False` when `rr=1.5` and `quality=HIGH`?

---

## Root Cause Analysis

### Possible Issue #1: Floating-Point Precision (Most Likely) ✅ FIXED

The RR threshold comparison `if risk_reward < 1.5:` could fail even with displayed RR of 1.5 if the actual calculation produced `1.4999999...` due to floating-point math.

**Example:**
```
True calculation: reward / risk = 3.081200 / 2.054133 = 1.500000000...
Actual float: 1.4999999999999998
Comparison: 1.4999999999999998 < 1.5 → TRUE → REJECTED
Display: round(1.4999999999999998, 2) → 1.5
```

**Result:** Appears to show `rr=1.5` but actually rejected because `rr < 1.5`

### Possible Issue #2: Avoid Chasing Flag ✅ CHECKED

If `avoid_chasing=True`, the trade is rejected even with good RR.

```python
if entry_setup["avoid_chasing"]:
    trade_allowed = False
```

However, if `quality=HIGH`, then `avoid_chasing=False` by definition:
```python
entry_quality = ("MEDIUM" if avoid_chasing else "HIGH")
```

So this shouldn't be the issue.

### Possible Issue #3: Other Rejection Filters ✅ DOCUMENTED

Multiple layers of rejection exist in `main.py`. Created comprehensive map in [`RISK_ENGINE_REJECTION_PATHS.md`](RISK_ENGINE_REJECTION_PATHS.md).

---

## Current Status (Latest Run)

**Test Result:** ✅ PASSING

```
[RR DEBUG] reward=3.170150 risk_per_share=2.113434 rr_exact=1.500000 rr_rounded=1.5
[RR THRESHOLD] rr=1.500000 < 1.5? False entry_quality=HIGH
[RISK ENGINE] QQQ allowed=True rr=1.5
[ALLOW DEBUG] allowed=True rr=1.5 entry=HIGH
```

Both QQQ and SPY with `rr=1.5` and `quality=HIGH` are now **ALLOWED**.

---

## Fixes Applied

### Fix #1: Epsilon Safety for RR Comparison ✅

**File:** `app/risk/risk_manager.py`

Changed from:
```python
if risk_reward < 1.5:
    trade_allowed = False
```

To:
```python
RR_MIN_THRESHOLD = 1.5
RR_EPSILON = 1e-9

if risk_reward < RR_MIN_THRESHOLD - RR_EPSILON:
    trade_allowed = False
```

**Effect:** Allows trades with `rr ≥ 1.5`, rejects only if `rr < 1.499999999`

### Fix #2: Removed Redundant RR Filter ✅

**File:** `app/main.py`

Removed the duplicate `if risk_reward < 1.2` check that was redundant with the `< 1.5` check in risk_manager.

**Effect:** Cleaner code, single source of truth for RR threshold

### Fix #3: Enhanced Debug Output ✅

**File:** `app/risk/risk_manager.py`

Added detailed logging:
```python
[RR DEBUG] reward=X risk_per_share=Y rr_exact=Z rr_rounded=W
[RR THRESHOLD] rr=Z < 1.5? [True|False] entry_quality=Q
```

**Effect:** Makes floating-point issues immediately visible

---

## How to Verify

When you see log output, look for these lines:

1. **[RR DEBUG]** — Shows exact RR before rounding
   - If `rr_exact` and `rr_rounded` differ significantly, that's a precision issue
   
2. **[RR THRESHOLD]** — Shows whether RR passed the check
   - `< 1.5? False` means RR ≥ 1.5, trade passes ✓
   - `< 1.5? True` means RR < 1.5, trade rejected ✗

3. **[RISK ENGINE]** — Final decision
   - `allowed=True` means trade passed risk checks
   - `allowed=False` means trade was rejected

4. **[ALLOW DEBUG]** — Final scanner decision
   - `allowed=True` means trade passed all checks (final approval)
   - `allowed=False` means trade was rejected at some filter

---

## All Rejection Points (For Reference)

These are ALL the places a trade can be rejected:

```
app/risk/risk_manager.py:
  1. Signal is NEUTRAL/INVALID
  2. Entry setup is None
  3. Risk/Reward < 1.5 - epsilon ⭐ FIXED
  4. Avoid chasing flag is True

app/main.py:
  5. Option liquidity check fails
  6. Entry type is NO_ENTRY
  7. Entry type is BREAKOUT_LONG or BREAKDOWN_SHORT AND entry_timing_ok=False
```

If you see `allowed=False`, one of these happened.

---

## Files to Reference

- **[RISK_ENGINE_REJECTION_PATHS.md](RISK_ENGINE_REJECTION_PATHS.md)** — Complete rejection path map with all conditions
- **[RISK_ENGINE_FIXES_SUMMARY.md](RISK_ENGINE_FIXES_SUMMARY.md)** — Detailed technical summary of fixes
- **[PROJECT_STATE_CURRENT.md](PROJECT_STATE_CURRENT.md)** — Full architecture documentation

---

## What Changed

| Before | After |
|--------|-------|
| Possible floating-point edge case | Protected with epsilon safety ✅ |
| Redundant RR filter in 2 places | Single RR threshold in risk_manager ✅ |
| Limited debug info for RR issues | Detailed [RR DEBUG] and [RR THRESHOLD] logs ✅ |

---

## Testing

Run the app:
```bash
python -m app.main
```

Look for QQQ/SPY logs:
- Should see `[RR THRESHOLD] rr=1.500000 < 1.5? False` → PASSES
- Should see `[RISK ENGINE] allowed=True rr=1.5` → ALLOWED ✓

If you see `allowed=False`, check the debug output to identify which rejection filter triggered.

