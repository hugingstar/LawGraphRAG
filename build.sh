#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "====================================="
echo "Building and starting LawGraphRAG..."
echo "====================================="

# Check for reset flag
RESET_DATA=false
if [ "$1" == "--reset-data" ]; then
    echo "⚠️  WARNING: --reset-data flag provided. ALL COLLECTED DATA WILL BE DELETED."
    read -p "Are you sure you want to delete all database and graph data? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        RESET_DATA=true
    else
        echo "Reset cancelled. Exiting."
        exit 1
    fi
fi

echo "[1/3] Stopping existing containers..."
if [ "$RESET_DATA" = true ]; then
    echo "🚨 Shutting down and REMOVING DATA VOLUMES (-v)..."
    docker compose -f docker-compose.yml -f docker-compose.ops.yml down -v || true
else
    echo "✅ Shutting down containers safely. (Data volumes are PRESERVED)"
    docker compose -f docker-compose.yml -f docker-compose.ops.yml down || true
fi

echo "[2/3] Building and starting main application services..."
# Build the main app detached
docker compose -f docker-compose.yml up --build -d

echo "[3/3] Building and starting ops/monitoring services..."
# Build the ops app detached (using its specific env file)
docker compose -f docker-compose.ops.yml --env-file .env.ops up --build -d

echo "====================================="
echo "Build complete! Services are running in the background."
if [ "$RESET_DATA" = false ]; then
    echo "💾 Your existing collected data and graphs have been preserved."
fi
echo "Use 'docker ps' to check their status."
echo "====================================="

