#!/usr/bin/env bash
set -e

# make sure docker (and any other binaries) can be found
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

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

# Check if a container already exists
CONTAINER_ID=$(docker ps -aq --filter "name=finance-toolkit-redis")
if [ "$CONTAINER_ID" ]; then
  echo "🔄 Starting existing Redis container..."
  docker start finance-toolkit-redis >/dev/null
else
  echo "🚀 Starting new Redis container (data at $DATA_DIR)..."
  docker run -d \
    --name finance-toolkit-redis \
    -p 6379:6379 \
    -v "$DATA_DIR":/data \
    redis:7-alpine \
    redis-server --appendonly yes
fi

# wait for Redis to actually accept connections
for i in {1..20}; do
  if echo PING | nc localhost 6379 | grep -q PONG; then
    echo "✅ Redis is up and ready!"
    exit 0
  fi
  sleep 0.2
done

echo "❌ Redis did not start in time" >&2
exit 1
