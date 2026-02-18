# TODO

## High Priority

- [x] **Build Kibana dashboards** -- F1 Telemetry dashboard built and exported as `dashboards/f1-telemetry.ndjson` (imported by setup-kibana.sh)
- [ ] **Pin dependency versions** -- `requirements.txt` has unpinned packages; pin to specific versions for reproducible builds
- [ ] **Add health check endpoint** -- Expose a `/health` endpoint through the Flask API for container orchestration readiness/liveness probes
- [ ] **Dockerfile multi-stage build** -- Use a multi-stage build to reduce final image size (build deps in one stage, runtime in another)

## Medium Priority

- [ ] **Extract configuration to environment variables** -- Move OTEL endpoint and rig config from SQLite to environment variables or a config file for 12-factor app compliance
- [ ] **Replace SQLite with Redis-only storage** -- The app already uses Redis heavily; consolidating to Redis would remove the SQLite dependency and simplify the architecture
- [ ] **Add structured logging** -- Replace ad-hoc `logging.info` calls with structured JSON logging; ship the collector's own logs through the OTel pipeline for self-telemetry
- [ ] **Add unit tests** -- No test suite exists; add tests for packet parsing (`packets.py`), data flattening (`collector.py:flatten_data`), and metric filtering
- [ ] **Type hints and linting** -- Add comprehensive type annotations and integrate ruff or mypy into CI
- [ ] **Graceful shutdown handling** -- Improve the `KeyboardInterrupt` handler in `collector.py` to properly drain the ThreadPoolExecutor and close Redis connections

## Low Priority

- [ ] **Add Prometheus metrics endpoint** -- Expose collector internal metrics (packets/sec, errors, send latency) as Prometheus gauges
- [ ] **WebSocket streaming** -- Replace the Streamlit polling loop in `streamer.py` with Redis pub/sub or WebSocket push for lower-latency UI updates
- [ ] **Add replay file management UI** -- Allow uploading and selecting `.tlm` replay files through the Streamlit interface
- [ ] **Dashboard version management** -- Track dashboard JSON versions alongside the collector version in `dashboards/`
- [ ] **Remove `compare.py` or formalize it** -- The F1 2024 packet spec in `compare.py` is useful as reference but should be either moved to a `legacy/` folder or removed if not actively used
- [ ] **CI/CD pipeline** -- Add GitHub Actions for linting, testing, building, and pushing the Docker image to GHCR

## Completed

- [x] Build Kibana dashboards (F1 Telemetry dashboard in dashboards/f1-telemetry.ndjson)
- [x] Rebuild project source tree from container extraction
- [x] Create Dockerfile from container evidence
- [x] Add project documentation (README, ARCHITECTURE, TODO)
- [x] Debrand Splunk references from all source files and UI
- [x] Replace Splunk O11y and HEC export with OTLP metrics and logs
- [x] Add OpenTelemetry Collector as a sidecar container
- [x] Split into three-container architecture (f1-app, redis, otel-collector) with docker-compose
- [x] Create OTEL Collector config with OTLP receiver and debug exporter
- [x] Docker Compose profiles for local ELK stack vs remote Elastic Cloud
- [x] Elastic Agent OTel Distribution as the collector image
- [x] `.env` file support for port and Elastic stack configuration
- [x] Resource processor for standardised data stream naming (`metrics-f1_telemetry.otel-default`, `logs-f1_telemetry.otel-default`)
- [x] Telemetry replay setup -- `.tlm` files mounted into container for testing without a live game
- [x] Document playback mode in README and ARCHITECTURE
- [x] Structured log parsing via OTel Collector `transform` processor
- [x] Kibana data view setup script (`scripts/setup-kibana.sh`)
- [x] Migrate to OTel-native mapping mode (`mapping.mode: otel`) -- TSDS for metrics, LogsDB for logs
