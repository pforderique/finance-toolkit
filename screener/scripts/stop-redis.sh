#!/usr/bin/env bash

# Look up any container publishing port 6379 (local → container)
CONTAINER_ID=$(docker ps --filter "publish=6379" --format "{{.ID}}")

if [ -z "$CONTAINER_ID" ]; then
  echo "⚠️  No Redis container found listening on port 6379"
  exit 0
fi

echo "🛑 Stopping Redis container $CONTAINER_ID..."
docker stop "$CONTAINER_ID"