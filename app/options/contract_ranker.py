from datetime import (
    datetime,
    timezone
)

from app.utils.runtime_logging import debug_print
from app.config.settings import settings
from app.options.affordability_config import get_affordability_config
from app.options.option_affordability import add_affordability_metrics

def _number(value):

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return None if result != result else result


def prefer_tightest_qualified(ranked, symbol=None):
    """Move fully-qualified contracts to the front, tightest spread first.

    The ranker scores tenor, delta, quality and cost, and spread is one term
    among many. That is why AMD on 2026-08-14 chose its contract at rank #82
    with an 8.33% spread and then refused it for being wide, while eighteen
    contracts inside the 3% ceiling sat in the same chain. MU the same day
    reported `Short DTE Spread % 2.37` at quality 80 and bought nothing.

    Measured across the archive, on scans where the app bought **nothing**:

        a fully-qualified contract was available   323 of 1,764   18.3%
        that contract's spread, median             1.71%
        the spread the app reported instead        9.76%

    So roughly one refusal in five had a usable contract sitting beside the one
    it rejected.

    **Qualified means all four gates the app already enforces** -- spread inside
    the ceiling, cost inside the cap, and open interest and volume above their
    floors. Nothing is loosened and no new threshold is invented here. That
    matters: ranking on spread *alone* reaches for deep-ITM LEAPS which are tight
    precisely because they are expensive, and §14 measured that arm at $3,696 a
    contract. Requiring the cost cap first makes that impossible.

    It also cannot promote a dead contract. Three of AMD's eight tightest that
    day were rejected `LOW_VOLUME` -- tight because nobody trades them -- and the
    open-interest and volume floors exclude exactly those.

    When no contract qualifies, which is the other 81.7%, the order is untouched
    and behaviour is exactly as before.

    **This changes which candidates become tradeable, not how good the existing
    trades are.** On the 138 trades the app already takes, all six selection
    rules tested land within 0.8 points of each other. Expect more trades rather
    than better ones, and note that the 2026-08-15 validation replay showed more
    trades making the total worse. Watch the count.
    """

    from app.config.settings import get_bool_env, get_float_env, settings

    if not get_bool_env("OPTION_PREFER_TIGHTEST_QUALIFIED", True):
        return ranked

    max_spread = get_float_env("OPTION_MAX_SPREAD_PCT", 6.0)

    # Must read the same cap the ranker did. Reading the global one here
    # would let a per-symbol exception through scoring and then refuse it at
    # promotion, which is the step that actually picks the contract -- the
    # exception would look configured and do nothing.
    from app.options.affordability_config import get_affordability_config

    max_cost = get_affordability_config(symbol)["max_contract_cost"]

    try:
        min_oi = settings.option_min_open_interest
        min_volume = settings.option_min_volume
    except AttributeError:
        min_oi, min_volume = 0, 0

    def qualified(contract):

        spread = _number(contract.get("spread_pct"))
        cost = _number(contract.get("contract_cost"))
        oi = _number(contract.get("open_interest"))
        volume = _number(contract.get("volume"))

        if spread is None or not (0 < spread <= max_spread):
            return False
        if cost is None or cost > max_cost:
            return False
        if oi is None or oi < min_oi:
            return False
        if volume is None or volume < min_volume:
            return False

        return True

    passing = [c for c in ranked if qualified(c)]

    if not passing:
        return ranked

    passing.sort(key=lambda c: _number(c.get("spread_pct")) or 999.0)
    remainder = [c for c in ranked if not qualified(c)]

    debug_print(
        f"[TIGHTEST QUALIFIED] {len(passing)} of {len(ranked)} qualify; "
        f"promoted {passing[0].get('ticker')} at "
        f"{_number(passing[0].get('spread_pct')):.2f}%"
    )

    return passing + remainder


def rank_option_contracts(

    contracts,
    underlying_price,
    direction="CALL",
    paper_mode=False,
    symbol=None

):

    debug_print(
        f"[CONTRACTS FETCHED] "
        f"{len(contracts)}"
    )

    """
    Greeks-aware contract scoring
    """

    try:

        ranked = []
        # Symbol only matters for the per-symbol cost cap exception; without it
        # this is the global config exactly, which is what every caller that
        # does not pass a symbol still gets.
        affordability_config = get_affordability_config(symbol)

        for c in contracts:

            gamma = c.get("gamma", 0)

            theta = c.get("theta", 0)

            spread_pct = c.get("spread_pct")

            option_quality_score = c.get(
                "option_quality_score",
                0
            )

            expiration_bucket = c.get(
                "expiration_bucket",
                "UNKNOWN"
            )

            volume = c.get("volume", 0)

            oi = c.get(
                "open_interest",
                0
            )


            # =================================
            # Days To Expiry
            # =================================

            dte = c.get("dte")

            if dte is None:

                dte = 999

            try:

                if dte != 999:

                    raise StopIteration

                # Try direct field first
                expiry_str = c.get(
                    "expiration_date"
                ) or c.get(
                    "expiration"
                )

                # Fallback: parse from ticker
                if not expiry_str:

                    ticker = c.get(
                        "ticker",
                        ""
                    )

                    import re

                    match = re.search(
                        r'(\d{6})[CP]',
                        ticker
                    )

                    if not match:

                        raise ValueError(
                            f"Could not parse expiry "
                            f"from {ticker}"
                        )

                    expiry_code = match.group(1)

                    expiry_date = datetime.strptime(
                        expiry_code,
                        "%y%m%d"
                    )

                else:

                    expiry_date = datetime.strptime(
                        expiry_str,
                        "%Y-%m-%d"
                    )

                expiry_date = expiry_date.replace(
                    tzinfo=timezone.utc
                )

                now = datetime.now(
                    timezone.utc
                )

                dte = (
                    expiry_date - now
                ).days

                dte = max(dte, 0)

            except StopIteration:

                pass

            except Exception as e:

                print(
                    f"[DTE PARSE ERROR] "
                    f"{c.get('ticker')} "
                    f"{e}"
                )          

            # =================================
            # Direction filtering
            # =================================

            if direction == "CALL":

                if c["type"] != "call":

                    continue

            else:

                if c["type"] != "put":

                    continue

            # =================================
            # Liquidity filters
            # =================================

            if volume < 5:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"LOW VOLUME"
                # )
                continue

            if oi < 1:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"LOW VOLUME"
                # )
                continue

            # print(
            #     f"{c['ticker']} "
            #     f"STRIKE={c['strike']} "
            #     f"DELTA={c['delta']}"
            # )

            # print(

            #     f"[CONTRACT DEBUG] "

            #     f"{c['ticker']} "

            #     f"STRIKE={c['strike']} "

            #     f"UNDERLYING={underlying_price}"

            # )

            # =================================
            # Strike proximity
            # =================================

            strike_distance_pct = abs(

                c["strike"]
                -
                underlying_price

            ) / underlying_price * 100

            if strike_distance_pct > 10:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"STRIKE TOO FAR"
                # )
                continue

            # =================================
            # Delta targeting
            # =================================

            delta = abs(c.get("delta", 0))

            if delta < 0.25:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"DELTA TOO LOW"
                # )
                continue

            if delta > 0.75:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"DELTA TOO HIGH"
                # )
                continue

            # =================================
            # IV sanity
            # =================================

            iv = c["iv"]

            if iv <= 0:
                # print(
                #     f"[REJECT] "
                #     f"{c['ticker']} "
                #     f"INVALID IV"
                # )
                continue

            # =================================
            # Scoring
            # =================================

            score = 0

            # Volume scoring

            if volume >= 1000:

                score += 15

            elif volume >= 250:

                score += 8

            # OI scoring

            if oi >= 5000:

                score += 10

            elif oi >= 1000:

                score += 5

            # Prefer ATM/slightly ITM
            score -= (
                strike_distance_pct * 8
            )

            # Prefer ideal delta
            delta_target = 0.55

            score -= abs(
                delta - delta_target
            ) * 100


            # =================================
            # Expiry Intelligence
            # =================================

            if dte <= 1:

                score -= 50

            elif dte < 7:

                score -= 25

            elif dte < settings.option_min_dte:

                score -= 12

            elif (
                settings.option_min_dte
                <= dte
                < settings.option_preferred_min_dte
            ):

                score += 4

            elif (
                settings.option_preferred_min_dte
                <= dte
                <= settings.option_preferred_max_dte
            ):

                score += 28

            elif dte <= settings.option_max_dte:

                score += 10

            else:

                score -= 10


            # Prefer lower theta decay
            score += (
                abs(theta) * -2
            )

            # Prefer gamma exposure
            if gamma >= 0.03:

                score += 20

            elif gamma >= 0.015:

                score += 10


            # =================================
            # Gamma vs Theta balance
            # =================================

            if (

                gamma >= 0.03

                and

                abs(theta) <= 0.12

            ):

                score += 10



            # Prefer efficient IV range

            if 18 <= iv <= 45:

                score += 20

            elif iv <= 60:

                score += 10            

            # Penalize insane IV
            if iv > 80:

                score -= 25

            # Directional alignment bonus

            if (
                direction == "CALL"
                and c["type"] == "call"
            ):

                score += 10

            elif (
                direction == "PUT"
                and c["type"] == "put"
            ):

                score += 10

            # =================================
            # Spread readiness
            # =================================

            if spread_pct is not None:

                if spread_pct <= 5:

                    score += 10

                elif spread_pct <= 10:

                    score += 4

                else:

                    score -= 20

            score += (
                option_quality_score / 10
            )

            c = add_affordability_metrics(
                c,
                config=affordability_config
            )

            affordability_adjusted_score = score

            if affordability_config.get("mode") != "OFF" and not paper_mode:

                if c.get("preferred_affordable"):

                    affordability_adjusted_score += 10

                elif c.get("affordable"):

                    affordability_adjusted_score += 5

                else:

                    affordability_adjusted_score -= 1000

            if expiration_bucket == "0DTE":

                score -= 50

            elif expiration_bucket == "1DTE":

                score -= 40

            elif expiration_bucket in [
                "PREFERRED_14_30"
            ]:

                score += 12

            elif expiration_bucket == "FALLBACK_31_45":

                score += 5

            elif expiration_bucket in [
                "SHORT_DTE_2_6",
                "SHORT_SWING_7_13"
            ]:

                score -= 8


            c["ranking_score"] = round(
                score,
                2
            )

            c["affordability_adjusted_score"] = round(
                affordability_adjusted_score,
                2
            )

            debug_print(
                f"[RANKED] "
                f"{c['ticker']} "
                f"SCORE={round(score,2)} "
                f"AFFORD={c.get('affordability_status')} "
                f"DELTA={c['delta']} "
                f"GAMMA={gamma} "
                f"THETA={theta} "
                f"IV={iv} "
                f"DTE={dte}"
            )

            ranked.append(c)

        ranked = sorted(

            ranked,

            key=lambda x: x[
                "ranking_score"
            ],

            reverse=True

        )

        ranked = prefer_tightest_qualified(ranked, symbol)

        return ranked

    except Exception as e:

        print(
            f"[RANK ERROR] {e}"
        )

        return []