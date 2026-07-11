from datetime import (
    datetime,
    timezone
)

from app.utils.runtime_logging import debug_print
from app.config.settings import settings
from app.options.affordability_config import get_affordability_config
from app.options.option_affordability import add_affordability_metrics

def rank_option_contracts(

    contracts,
    underlying_price,
    direction="CALL",
    paper_mode=False

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
        affordability_config = get_affordability_config()

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

        return ranked

    except Exception as e:

        print(
            f"[RANK ERROR] {e}"
        )

        return []