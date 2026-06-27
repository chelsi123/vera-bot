#!/usr/bin/env bash
# Run the Vera bot. Loads .env if present, then starts uvicorn on port 8080.
set -e
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec uvicorn bot:app --host 0.0.0.0 --port 8080
