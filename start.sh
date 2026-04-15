#!/bin/bash
# Auto-restart Flask app on crash
cd "$(dirname "$0")"
source venv/bin/activate

echo "Starting PDF Parser app..."
while true; do
    python3 app.py
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 0 ]; then
        echo "App exited cleanly."
        break
    fi
    echo "App crashed (exit $EXIT_CODE). Restarting in 2 seconds..."
    sleep 2
done
