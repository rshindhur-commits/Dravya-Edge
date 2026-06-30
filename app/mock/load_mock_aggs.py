import json


def load_mock_aggs(symbol):

    filepath = f"app/mock/{symbol}.json"

    with open(filepath, "r") as f:

        data = json.load(f)

    return data.get(
        "results",
        []
    )