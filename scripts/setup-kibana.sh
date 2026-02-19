#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Source .env from project root if it exists
ENV_FILE="$SCRIPT_DIR/../.env"
if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
fi

KIBANA_URL="${KIBANA_URL:-http://localhost:${KIBANA_PORT:-5601}}"

CURL_AUTH=()
if [[ -n "$ELASTIC_API_KEY" ]]; then
    CURL_AUTH=(-H "Authorization: ApiKey ${ELASTIC_API_KEY}")
fi

echo "Setting up Kibana data views..."
echo "Kibana URL: $KIBANA_URL"

echo -n "Waiting for Kibana to be ready"
until curl -sf "${CURL_AUTH[@]}" "${KIBANA_URL}/api/status" > /dev/null 2>&1; do
    echo -n "."
    sleep 5
done
echo " ready"

DATA_VIEWS=(
    "f1-telemetry-metrics|metrics-f1_telemetry.otel-default|F1 Telemetry Metrics"
    "f1-telemetry-logs|logs-f1_telemetry.otel-default|F1 Telemetry Logs"
)

for dv in "${DATA_VIEWS[@]}"; do
    IFS='|' read -r id title name <<< "$dv"

    body=$(curl -s -w "\n%{http_code}" -X POST "${KIBANA_URL}/api/data_views/data_view" \
        "${CURL_AUTH[@]}" \
        -H 'kbn-xsrf: true' \
        -H 'Content-Type: application/json' \
        -H 'elastic-api-version: 2023-10-31' \
        -d "{\"data_view\":{\"id\":\"${id}\",\"title\":\"${title}\",\"name\":\"${name}\",\"timeFieldName\":\"@timestamp\"}}")

    http_code=$(echo "$body" | tail -1)
    response_body=$(echo "$body" | sed '$d')

    if [[ "$http_code" == "200" ]]; then
        echo "  Created: $name ($title)"
    elif echo "$response_body" | grep -qi "duplicate\|already exists\|conflict"; then
        echo "  Exists:  $name ($title)"
    else
        echo "  Warning: $name returned HTTP $http_code"
    fi
done

DASHBOARDS_DIR="${DASHBOARDS_DIR:-$SCRIPT_DIR/../dashboards}"

echo ""
echo "Importing dashboards from $DASHBOARDS_DIR ..."
for ndjson in "$DASHBOARDS_DIR"/*.ndjson; do
    [[ -f "$ndjson" ]] || continue
    fname=$(basename "$ndjson")
    import_result=$(curl -s -w "\n%{http_code}" -X POST "${KIBANA_URL}/api/saved_objects/_import?overwrite=true" \
        "${CURL_AUTH[@]}" \
        -H 'kbn-xsrf: true' \
        --form "file=@${ndjson}")
    import_code=$(echo "$import_result" | tail -1)
    if [[ "$import_code" == "200" ]]; then
        echo "  Imported: $fname"
    else
        echo "  Warning: $fname returned HTTP $import_code"
    fi
done

echo ""
echo "Data views ready. Open Kibana at ${KIBANA_URL} to explore your data."
