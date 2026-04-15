# ORS geocoding pipeline constants
DAILY_LIMIT = 900       # safely under ORS 1,000/day quota
WORKERS = 10            # parallel threads

# Rate limiter: allow at most 80 API calls per 60-second sliding window.
# Stays safely under ORS hard limit of 100 calls/minute.
RATE_LIMIT_CALLS = 80
RATE_WINDOW = 60.0
