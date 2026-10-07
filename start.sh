#!/bin/sh
set -e
# Launch the trading bot in the background (stdout -> desk.log for the dashboard)
python -u memecoin_desk.py > /app/desk.log 2>&1 &
# Launch the read-only web dashboard in the foreground
python -u desk_web.py &
# Keep the container alive as long as the web server runs.
# If the bot crashes the dashboard still serves its last log output.
wait
