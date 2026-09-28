#!/bin/sh
exec uvicorn --host 0.0.0.0 --port 8080 --workers 1 --log-level "${LOG_LEVEL}" cactus_orchestrator.main:app
