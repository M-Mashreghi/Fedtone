#!/bin/sh
# Container entry point.
#   api        migrate the database, then serve the research API on :8000
#   ui         serve the Streamlit dashboard on :8501
#   scheduler  migrate, then run pipeline cycles (FEDTONE_SCHEDULER__ENABLED=true)
#   <other>    run as a command, e.g. `fedtone backup` or `fedtone ingest`
set -eu

case "${1:-api}" in
  api)
    alembic upgrade head
    exec uvicorn fedtone.api.app:create_default_app --factory --host 0.0.0.0 --port 8000
    ;;
  ui)
    exec streamlit run /app/src/fedtone/ui/dashboard.py \
      --server.address 0.0.0.0 --server.port 8501 --server.headless true \
      --browser.gatherUsageStats false
    ;;
  scheduler)
    alembic upgrade head
    exec fedtone schedule
    ;;
  *)
    exec "$@"
    ;;
esac
