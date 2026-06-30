# Risk Engine Rejection Paths - Complete Analysis

**Date:** 2026-06-10

## Executive Summary

The user reported seeing trades rejected with `allowed=False` even though `rr=1.5` and `quality=HIGH`. This document maps all rejection paths in the code to identify where this could happen.

## Current Test Results

Latest run (2026-06-10 23:38):
- **QQQ**: `allowed=True`, `rr=1.5`, `quality=HIGH` ✓ PASSED
- **SPY**: `allowed=True`, `rr=1.5`, `quality=HIGH` ✓ PASSED

Both trades passed all filters with `rr=1.5` exactly. The comparison `rr < 1.5` evaluates to **False**, so both trades were allowed.

---

## Rejection Paths - Ordered by Execution

### Path 1: Risk Manager (`app/risk/risk_manager.py`)

#### 1A. Signal Check (Early Exit)
```python
if analysis["signal"] in ["NEUTRAL", "INVALID"]:
    return {"trade_allowed": False, ...}
```
**Rejection Condition:** Signal is NEUTRAL or INVALID
**Result:** `allowed=False`

#### 1B. Entry Setup Check (Early Exit)
```python
if entry_setup is None:
    return {"trade_allowed": False, ...}
```
**Rejection Condition:** No entry setup provided
**Result:** `allowed=False`

#### 1C. Risk/Reward Threshold (Line 217)
```python
if risk_reward < 1.5:
    trade_allowed = False
    if entry_setup["entry_quality"] == "LOW":
        reasons.append("Low quality entry with poor RR")
```
**Rejection Condition:** `risk_reward < 1.5`
**Current Behavior:** 
- `rr=1.5` → `rr < 1.5` is **False** → **NOT rejected** ✓
- `rr=1.49999` → `rr < 1.5` is **True** → **REJECTED** ✗

⚠️ **POTENTIAL BUG:** If floating-point math produces `rr=1.499999...` that rounds to 1.50 in display, the comparison uses the unrounded value and rejects it.

**Fix Needed:** Change threshold comparison:
```python
# Option 1: Use <= for explicit include of 1.5
if risk_reward <= 1.45:  # or similar threshold
    trade_allowed = False

# Option 2: Add epsilon for floating-point safety
if risk_reward < 1.5 - 1e-6:
    trade_allowed = False
```

#### 1D. Avoid Chasing Filter (Line 284)
```python
if entry_setup["avoid_chasing"]:
    trade_allowed = False
    reasons.append("Avoid chasing extended move")
```
**Rejection Condition:** `avoid_chasing=True`

**When This Triggers:**
- `vwap_distance > 1.5%` (from `app/strategies/entry_engine.py` line 55)
- `ema_distance > 1.2%` (from `app/strategies/entry_engine.py` line 59)

**Note:** For SHORT entries, these distances are typically NEGATIVE (price below moving averages), so they shouldn't trigger. However, for LONG entries that extend too far above, this would set `avoid_chasing=True`.

**Symptom:** Trade shows `quality=MEDIUM` (if HIGH quality, then `avoid_chasing=False`)

---

### Path 2: Main Scanner (`app/main.py`)

#### 2A. Liquidity Check (Line 365)
```python
if not liquidity_check["liquid"]:
    risk_setup["trade_allowed"] = False
    reasons.append(f"Liquidity failed: {liquidity_check['reason']}")
```
**Rejection Condition:** Option liquidity check fails

**Current Liquidity Criteria:**
- Open interest ≥ 50
- Volume ≥ 10
- Bid > 0 and Bid < Ask
- Bid-ask spread < 5% of mid

**Current Mock Data:** All checks pass (OI=1200, Vol=450)

#### 2B. RR < 1.2 Filter (Line 377)
```python
if risk_setup["risk_reward"] < 1.2:
    risk_setup["trade_allowed"] = False
    if entry_setup:
        entry_setup["entry_quality"] = "LOW"
```
**Rejection Condition:** `risk_reward < 1.2`

⚠️ **This is a SECOND RR filter** that blocks trades with RR < 1.2. The risk_manager already blocks at < 1.5, so this layer is redundant but more lenient.

#### 2C. No Entry Filter (Line 390)
```python
if entry_setup["entry_type"] == "NO_ENTRY":
    risk_setup["trade_allowed"] = False
    entry_setup["entry_quality"] = "NONE"
```
**Rejection Condition:** No entry type detected

#### 2D. Entry Timing Filter (Line 453)
```python
if (
    entry_setup["entry_type"] in ["BREAKOUT_LONG", "BREAKDOWN_SHORT"]
    and 
    not analysis_15m["entry_timing_ok"]
):
    risk_setup["trade_allowed"] = False
```
**Rejection Condition:** 
- Entry is BREAKOUT_LONG or BREAKDOWN_SHORT AND
- `entry_timing_ok=False`

**When `entry_timing_ok` is False:**
From `app/strategies/momentum_strategy.py` lines 1180-1201:
```python
# Extended Candle Filter
if latest["BODY_STRENGTH"] > 0.8:
    entry_timing_ok = False

# VWAP Extension Filter
if vwap_distance_pct > 1.0:
    entry_timing_ok = False

# Momentum Exhaustion Filter
if latest["RSI"] > 78 and score > 0:
    entry_timing_ok = False

if latest["RSI"] < 22 and score < 0:
    entry_timing_ok = False
```

**Current QQQ Status:**
- Entry type: `EMA_REJECTION_SHORT` (NOT in blocked list)
- `entry_timing_ok=False` (but doesn't matter for this entry type)
- Result: **NOT rejected** ✓

---

## Trade Allowed Decision Tree

```
┌─ Signal NEUTRAL/INVALID? ─→ YES → REJECT (Path 1A)
│
├─ Entry Setup = None? ─────→ YES → REJECT (Path 1B)
│
├─ Risk Reward < 1.5? ───────→ YES → REJECT (Path 1C) ⚠️
│
├─ Avoid Chasing = True? ────→ YES → REJECT (Path 1D)
│
├─ Liquidity = False? ───────→ YES → REJECT (Path 2A)
│
├─ Risk Reward < 1.2? ───────→ YES → REJECT (Path 2B)
│
├─ Entry Type = NO_ENTRY? ──→ YES → REJECT (Path 2C)
│
├─ Entry Type in [BREAKOUT_LONG, BREAKDOWN_SHORT]
│  AND entry_timing_ok = False? → YES → REJECT (Path 2D)
│
└─ ALLOWED = True ✓
```

---

## Current QQQ/SPY Status Trace

### QQQ Trace
```
[ENTRY TRIGGERED] EMA_REJECTION_SHORT
[ENTRY SELECTED] type=EMA_REJECTION_SHORT score=4 quality=HIGH
   → avoid_chasing=False (implied by quality=HIGH)

[RR DEBUG] reward=3.081200 risk_per_share=2.054133 rr_exact=1.500000
[RR THRESHOLD] rr=1.500000 < 1.5? False
   → Path 1C: NOT rejected (1.5 is NOT < 1.5)

[RISK ENGINE] allowed=True rr=1.5
   → risk_manager passes

[LIQUIDITY] liquid=True reason=Healthy liquidity
   → Path 2A: NOT rejected

[RR FILTER BLOCKED] not triggered
   → Path 2B: NOT rejected (1.5 is NOT < 1.2)

[ENTRY FILTER BLOCKED] not triggered
   → Path 2C: NOT rejected (entry_type != NO_ENTRY)

[ENTRY TIMING BLOCKED] not triggered
   → Path 2D: NOT rejected (entry_type not in blocked list)

[ALLOW DEBUG] allowed=True rr=1.5 entry=HIGH
   → FINAL: ALLOWED ✓
```

---

## Potential Issue: Floating-Point Precision

If somewhere in the calculation chain, `rr` ends up as `1.499999999` due to floating-point arithmetic:

```python
reward = 3.08119 - 0.0 = 3.08119
risk_per_share = 2.05413
rr = 3.08119 / 2.05413 = 1.500000000... (THEORETICALLY 1.5)
```

But if the division introduces floating-point error:
```python
rr = 1.4999999999999998  (actual float representation)
```

Then:
```python
if rr < 1.5:  # 1.4999... < 1.5 → TRUE
    trade_allowed = False  # REJECTED!
```

But then:
```python
round(rr, 2) = 1.50  # Rounds UP in display
```

**This would explain:** `allowed=False`, `rr=1.5 (displayed)`, `quality=HIGH`

---

## Recommended Fixes

### 1. Add Epsilon Safety to RR Comparison

**File:** `app/risk/risk_manager.py` (Line 217)

```python
# Add epsilon for floating-point safety
RR_MIN_THRESHOLD = 1.5
RR_EPSILON = 1e-9  # Small epsilon for floating-point math

if risk_reward < RR_MIN_THRESHOLD - RR_EPSILON:
    trade_allowed = False
    if entry_setup["entry_quality"] == "LOW":
        reasons.append("Low quality entry with poor RR")
```

### 2. Consolidate RR Filters

Remove the redundant `< 1.2` check in `main.py` (Line 377) since `risk_manager` already filters at `< 1.5`.

### 3. Add Comprehensive Debug Output

**Already Added:**
```python
[RR DEBUG] reward=X risk_per_share=Y rr_exact=Z rr_rounded=W
[RR THRESHOLD] rr=Z < 1.5? [True|False] entry_quality=Q
```

This clearly shows:
- The exact pre-rounding RR value
- Whether it passes the threshold
- The quality level

---

## Testing Recommendations

### Test Case 1: Exact RR=1.5
**Current Status:** ✓ PASSES (QQQ, SPY)

### Test Case 2: Floating-Point Edge Cases
Run scanner with mock data that produces `rr=1.4999...`:
- Monitor `[RR DEBUG]` output for exact value
- Confirm `allowed=True` (if epsilon fix applied)
- Verify `[RR THRESHOLD]` shows `False`

### Test Case 3: Avoid Chasing Rejection
Create scenario where:
- `quality=HIGH` but `avoid_chasing=True` (contradictory)
- Monitor if trade is rejected despite quality

---

## Summary

**Current Finding:** Trades with `rr=1.5` and `quality=HIGH` are **PASSING** through all filters and being **ALLOWED**. ✓

**Potential Bug Identified:** Floating-point precision issue at RR threshold could cause `rr=1.4999...` to be rejected while displaying as `rr=1.5`. ⚠️

**Recommended Action:** Apply epsilon safety fix to RR comparison to handle floating-point edge cases.

