#!/bin/sh
set -eu

cd "$(dirname "$0")"
docker compose down
echo "Robot Doctor stopped."
