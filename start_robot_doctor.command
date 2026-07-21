#!/bin/sh
set -eu

cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is required. Install and start it, then run this launcher again."
  read -r _
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is installed but not running. Start it, then run this launcher again."
  read -r _
  exit 1
fi

docker compose up --build -d
echo "Robot Doctor is starting at http://127.0.0.1:8765"

attempt=0
while [ "$attempt" -lt 30 ]; do
  if curl -fsS http://127.0.0.1:8765/healthz >/dev/null 2>&1; then
    open http://127.0.0.1:8765
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 1
done

echo "Robot Doctor did not become ready. Run: docker compose logs"
read -r _
exit 1
