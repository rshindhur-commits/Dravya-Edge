import math


def calculate_position_size(

    account_size,
    risk_percent,
    entry_price,
    stop_loss,
    option_price,
    projection=None

):

    """
    Position sizing engine

    Calculates:
    - max account risk
    - recommended contracts
    - estimated loss
    - estimated profit
    """

    try:

        # =========================
        # Account risk
        # =========================

        max_risk_amount = (

            account_size *

            (risk_percent / 100)

        )

        # =========================
        # Underlying risk distance
        # =========================

        stop_distance = abs(

            entry_price - stop_loss

        )

        if stop_distance <= 0:

            stop_distance = 1

        # =========================
        # Estimated option move
        # =========================

        estimated_option_risk = (

            option_price * 0.35

        )

        # =========================
        # Contracts
        # =========================

        contracts = math.floor(

            max_risk_amount /

            (estimated_option_risk * 100)

        )

        contracts = max(
            contracts,
            1
        )

        # =========================
        # Estimated max loss
        # =========================

        estimated_loss = round(

            contracts *

            estimated_option_risk *

            100,

            2

        )

        # =========================
        # Estimated profit
        # =========================

        projected_gain_pct = 25

        if projection:

            projected_gain_pct = projection.get(

                "projected_option_gain",
                25

            )

        estimated_profit = round(

            contracts *

            option_price *

            (
                projected_gain_pct / 100
            ) *

            100,

            2

        )

        # =========================
        # Trade aggressiveness
        # =========================

        aggressiveness = "Moderate"

        if contracts >= 5:

            aggressiveness = "Aggressive"

        elif contracts == 1:

            aggressiveness = "Conservative"

        return {

            "contracts":
                contracts,

            "max_risk":
                round(
                    max_risk_amount,
                    2
                ),

            "estimated_loss":
                estimated_loss,

            "estimated_profit":
                estimated_profit,

            "aggressiveness":
                aggressiveness

        }

    except Exception as e:

        print(
            f"[POSITION SIZE ERROR] {e}"
        )

        return {

            "contracts": 0,

            "max_risk": 0,

            "estimated_loss": 0,

            "estimated_profit": 0,

            "aggressiveness": "N/A"

        }