#!/bin/bash
# Exit immediately if a command exits with a non-zero status
set -e

echo "====================================="
echo "Building and starting LawGraphRAG..."
echo "====================================="

# 0. Setup Environment Variables
echo "[0/3] Checking environment configuration..."

if [ ! -f .env ]; then
    echo "  > .env file not found. Copying from .env.example..."
    cp .env.example .env
fi

if [ ! -f .env.ops ]; then
    echo "  > .env.ops file not found. Copying from .env.ops.example..."
    cp .env.ops.example .env.ops
fi

# Check for placeholder API keys and prompt if needed
if grep -q "LAW_OC_KEY=your_law_open_api_key_here" .env; then
    echo ""
    echo "🔑 [Required] Law Open API Key is missing."
    echo "  Get it from: https://open.law.go.kr"
    read -p "  Enter your LAW_OC_KEY: " user_law_key
    if [ ! -z "$user_law_key" ]; then
        # Use a temporary file for cross-platform sed compatibility
        sed "s/LAW_OC_KEY=your_law_open_api_key_here/LAW_OC_KEY=$user_law_key/" .env > .env.tmp && mv .env.tmp .env
        echo "  ✅ LAW_OC_KEY saved."
    else
        echo "  ⚠️ Warning: LAW_OC_KEY is empty. The application may not function correctly."
    fi
fi

if grep -q "GEMINI_API_KEY=your_gemini_api_key_here" .env; then
    echo ""
    echo "🔑 [Required] Gemini API Key is missing."
    echo "  Get it from: https://aistudio.google.com/apikey"
    read -p "  Enter your GEMINI_API_KEY: " user_gemini_key
    if [ ! -z "$user_gemini_key" ]; then
        sed "s/GEMINI_API_KEY=your_gemini_api_key_here/GEMINI_API_KEY=$user_gemini_key/" .env > .env.tmp && mv .env.tmp .env
        echo "  ✅ GEMINI_API_KEY saved."
    else
        echo "  ⚠️ Warning: GEMINI_API_KEY is empty. The application may not function correctly."
    fi
    echo ""
fi

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

