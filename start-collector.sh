#!/bin/bash
set -e

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
        SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
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
        echo "To configure Elasticsearch index settings, run: ./scripts/setup-elasticsearch.sh"
        echo "To create Kibana data views, run: ./scripts/setup-kibana.sh"
        echo "  Set KIBANA_URL in .env to point to your Kibana instance."
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
