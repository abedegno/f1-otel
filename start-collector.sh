#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

usage() {
    echo "Usage: $0 [local|cloud|stop]"
    echo ""
    echo "  local  - Start with local Elasticsearch + Kibana (default)"
    echo "  cloud  - Connect to remote Elastic Cloud / Serverless (configure .env first)"
    echo "  stop   - Stop all containers"
    echo ""
}

case "${1:-local}" in
    local)
        echo "Starting F1 ELK on Track with local Elasticsearch + Kibana..."
        docker compose --profile local up -d
        echo ""
        echo "Web UI:  http://localhost:${WEB_UI_PORT:-8501}"
        echo "Kibana:  http://localhost:${KIBANA_PORT:-5601}"
        echo ""
        echo "Setting up Elasticsearch index settings (runs in background)..."
        nohup "$SCRIPT_DIR/scripts/setup-elasticsearch.sh" > /dev/null 2>&1 &
        echo "Setting up Kibana data views (runs in background)..."
        nohup "$SCRIPT_DIR/scripts/setup-kibana.sh" > /dev/null 2>&1 &
        ;;
    cloud)
        echo "Starting F1 ELK on Track (connecting to remote Elastic cluster)..."
        docker compose up -d
        echo ""
        echo "Web UI:  http://localhost:${WEB_UI_PORT:-8501}"
        echo ""
        echo "Setting up Elasticsearch index settings (runs in background)..."
        nohup "$SCRIPT_DIR/scripts/setup-elasticsearch.sh" > /dev/null 2>&1 &
        if [[ -n "$KIBANA_URL" ]]; then
            echo "Setting up Kibana data views (runs in background)..."
            nohup "$SCRIPT_DIR/scripts/setup-kibana.sh" > /dev/null 2>&1 &
        else
            echo "To create Kibana data views, set KIBANA_URL in .env and run:"
            echo "  ./scripts/setup-kibana.sh"
        fi
        ;;
    stop)
        echo "Stopping F1 ELK on Track..."
        docker compose --profile local down
        ;;
    *)
        usage
        exit 1
        ;;
esac
