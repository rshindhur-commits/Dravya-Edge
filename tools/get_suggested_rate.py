from app.utils.polygon_client import suggest_rate_from_history

s = suggest_rate_from_history()
print('Suggested POLYGON_RATE_LIMIT_PER_MINUTE =', s)
