#!/usr/bin/env bash
set -e

# Resolve the directory this script lives in, then climb up to project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data/redis"

# Make sure the host directory exists
mkdir -p "$DATA_DIR"

# If Redis already bound locally, exit
if nc -z localhost 6379; then
  echo "✅ Redis already listening on port 6379"
  exit 0
fi

# If a stopped or running container by that name exists, remove it:
if docker ps -a --format '{{.Names}}' | grep -q '^financial-toolkit-redis$'; then
  echo "🗑 Removing old Redis container..."
  docker rm -f financial-toolkit-redis >/dev/null 2>&1
fi

echo "🚀 Starting Redis (data at $DATA_DIR)..."
docker run -d \
  --name financial-toolkit-redis \
  -p 6379:6379 \
  -v "$DATA_DIR":/data \
  redis:7-alpine \
  redis-server --appendonly yes
echo "✅ Redis started successfully!"