#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

ENV_FILE="$SCRIPT_DIR/../.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

ES_URL="${ELASTIC_ENDPOINT:-http://localhost:${ES_PORT:-9200}}"
REFRESH_INTERVAL="${ES_REFRESH_INTERVAL:-200ms}"

echo "Setting up Elasticsearch index settings..."
echo "Elasticsearch URL: $ES_URL"
echo "Refresh interval:  $REFRESH_INTERVAL"

echo -n "Waiting for Elasticsearch to be ready"
until curl -sf "${ES_URL}/_cluster/health" > /dev/null 2>&1; do
    echo -n "."
    sleep 5
done
echo " ready"

DATA_STREAMS=(
    "metrics-f1_telemetry.otel-default"
    "logs-f1_telemetry.otel-default"
)

echo ""
echo "Applying component templates (persist across rollover)..."
for ds in "${DATA_STREAMS[@]}"; do
    prefix="${ds%-default}"
    template_name="${prefix}@custom"

    body=$(curl -s -w "\n%{http_code}" -X PUT "${ES_URL}/_component_template/${template_name}" \
        -H 'Content-Type: application/json' \
        -d "{\"template\":{\"settings\":{\"index.refresh_interval\":\"${REFRESH_INTERVAL}\"}}}")

    http_code=$(echo "$body" | tail -1)

    if [[ "$http_code" == "200" ]]; then
        echo "  Template: ${template_name} (refresh_interval: ${REFRESH_INTERVAL})"
    else
        echo "  Warning:  ${template_name} returned HTTP $http_code"
    fi
done

echo ""
echo "Applying to current backing indices..."
for ds in "${DATA_STREAMS[@]}"; do
    body=$(curl -s -w "\n%{http_code}" -X PUT "${ES_URL}/.ds-${ds}-*/_settings" \
        -H 'Content-Type: application/json' \
        -d "{\"index.refresh_interval\":\"${REFRESH_INTERVAL}\"}")

    http_code=$(echo "$body" | tail -1)

    if [[ "$http_code" == "200" ]]; then
        echo "  Applied:  ${ds} (refresh_interval: ${REFRESH_INTERVAL})"
    elif [[ "$http_code" == "404" ]]; then
        echo "  Skipped:  ${ds} (no backing indices yet)"
    else
        echo "  Warning:  ${ds} returned HTTP $http_code"
    fi
done

echo ""
echo "Elasticsearch index settings configured."
