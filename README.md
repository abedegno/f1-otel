# F1 2025 - ELK on Track

A real-time telemetry collector for the EA Sports F1 2025 video game. Receives UDP telemetry packets from up to 4 racing rigs, parses them, and forwards metrics and logs via OpenTelemetry (OTLP) to any compatible backend.

## Overview

The F1 game broadcasts detailed car telemetry (speed, throttle, brake, temperatures, lap times, etc.) over UDP at 10 Hz. This collector captures that data, processes it, and ships it through an OpenTelemetry Collector for real-time observability dashboards -- turning a racing event into a live data engineering demo.

The application runs as Docker containers orchestrated by `docker-compose`:

- **f1-app** -- Streamlit web UI, Flask API, nginx reverse proxy, and collector subprocesses
- **redis** -- In-memory data store for real-time IPC between collectors and the UI
- **otel-collector** -- Elastic Agent (OTel distribution) that receives OTLP and exports to Elasticsearch

Two modes via Docker Compose profiles:

- **Default** -- connects to an existing Elastic Cloud, ECH, or Serverless cluster
- **Local** (`--profile local`) -- spins up Elasticsearch + Kibana containers for testing

## Quick Start

### Local testing (with Elasticsearch + Kibana)

```bash
./start-collector.sh local
```

This starts all six containers (including a one-off setup container that configures Elasticsearch and Kibana). Data views and the F1 Telemetry dashboard are created automatically. You can also run `docker compose --profile local up -d` directly; the setup container runs as part of the stack. Open Kibana at `http://localhost:5601` to explore the data.

### Connecting to Elastic Cloud / Serverless

Edit `.env` with your cluster details:

```env
ELASTIC_ENDPOINT=https://my-deploy.es.us-central1.gcp.cloud.es.io:443
ELASTIC_API_KEY=your-base64-api-key
KIBANA_URL=https://my-deploy.kb.us-central1.gcp.cloud.es.io:9243
```

Then start:

```bash
./start-collector.sh cloud
```

Setup scripts (Elasticsearch refresh interval, Kibana data views, and dashboard import) run automatically in the background. If you omit `KIBANA_URL`, data views and the dashboard are not created automatically; set `KIBANA_URL` in `.env` and run `./scripts/setup-kibana.sh` when needed.

### Stopping

```bash
./start-collector.sh stop
```

### Building from source

```bash
docker compose build
./start-collector.sh local
```

### Access the Web UI

Open `http://<host-ip>:8501` in your browser to configure endpoints and manage collectors.

## Ports

| Port | Protocol | Purpose |
|------|----------|---------|
| 8501 | TCP | Web UI (nginx reverse proxy) |
| 20777 | UDP | Rig 1 telemetry |
| 20778 | UDP | Rig 2 telemetry |
| 20779 | UDP | Rig 3 telemetry |
| 20780 | UDP | Rig 4 telemetry |

| 5601 | TCP | Kibana (local profile only) |
| 9200 | TCP | Elasticsearch (local profile only, internal) |

Redis (6379) and the OTEL Collector (4318) are internal to the Docker network and not exposed externally. Elasticsearch (9200) is also internal; Kibana (5601) is exposed only when using `--profile local`.

## F1 Game Configuration

On each racing rig, configure the in-game telemetry settings:

| Setting | Value |
|---------|-------|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | Collector host IP |
| UDP Port | 20777 (rig 1), 20778 (rig 2), etc. |
| UDP Send Rate | 10Hz |
| UDP Format | 2025 |

## Collector Configuration

The web UI sidebar provides configuration for:

- **Rigs** -- number of active racing rigs (1-4)
- **Event Name** -- custom label attached to all telemetry as a resource attribute
- **OTEL Endpoint** -- OTLP endpoint URL (default: `http://otel-collector:4318`), with toggles for metrics and logs export
- **Playback Mode** -- replay pre-recorded `.tlm` files instead of live UDP

## Playback Mode

The project includes pre-recorded telemetry files (`.tlm`) that allow you to run the full pipeline without an F1 game. This is useful for testing, demos, and dashboard development.

### How it works

1. Set **Playback Mode** to `True` in the web UI sidebar and click **Save Configuration**
2. Toggle **Master Control** to start collectors
3. Each rig replays its assigned `.tlm` file at the original recording speed -- a 10-minute recording takes 10 minutes to replay

Packets are replayed with the same inter-packet timing as the original session (the `.tlm` format stores each packet with a relative timestamp). However, all data sent to the backend is stamped with the **current wall-clock time**, so telemetry appears as live data in dashboards and you don't need to adjust time filters.

When a replay file finishes, it loops automatically with a new randomised driver name.

### Replay files

| File | Rig | Description |
|------|-----|-------------|
| `telemetry_replay_20250813_180706.tlm` | rig_1 | Austria session |
| `telemetry_replay_20250813_181707.tlm` | rig_2 | Austria session |
| `telemetry_replay_20251021_112709.tlm` | rig_3 | October session |
| `telemetry_replay_20251021_114336.tlm` | rig_4 | October session |

The replay files are bind-mounted into the container via `docker-compose.yml` (`./src/telemetry_data:/app/telemetry_data`), so no image rebuild is needed to add or replace recordings.

## Kibana Data Views and Dashboard

Kibana needs data views to explore the telemetry data. These are created automatically:

- **Local** — When you run `./start-collector.sh local` or `docker compose --profile local up -d`, a `setup` container runs once after Elasticsearch and Kibana are ready. It applies the Elasticsearch refresh interval, creates the data views, and imports the dashboard. `start-collector.sh local` also runs the same setup scripts from the host in the background for immediate feedback.
- **Cloud** — When you run `./start-collector.sh cloud` with `KIBANA_URL` set in `.env`, the setup scripts run in the background and create data views and import the dashboard. If `KIBANA_URL` is not set, run `./scripts/setup-kibana.sh` manually (with `KIBANA_URL` in the environment or in `.env`).

Three **pre-built Kibana dashboards** are imported automatically:

**F1 Telemetry** (`dashboards/f1-telemetry.ndjson`) — real-time car telemetry from the metrics index:

- Key stats: Gear, Speed (mph), RPM, Throttle %, Brake %, Sector, Current Lap Time
- Time series (RPM/Speed, Speed/Brake), Engine Temp, Air/Track Temp, Current Lap
- Vega car diagram: brake, tyre surface, and tyre inner temperatures for all four wheels with color-coded thresholds

**F1 Race Overview** (`dashboards/f1-race-overview.ndjson`) — session context and race status from the logs index:

- Session context: track name, weather, track length, total laps (Vega with ID-to-name lookups)
- Driver info: player name, driver, team, race number (Vega with team ID lookup)
- Race position: current position, grid position, last lap time, sector times, pit stops
- Car status: tyre compound and age, DRS, FIA flags (Vega with color-coded lookups), fuel remaining, ERS energy gauge, speed trap
- Time series charts: position over time, lap time trend

**F1 Leaderboard** (`dashboards/f1-leaderboard.ndjson`) — cross-session player leaderboard from the logs index:

- Event stats: event name, total unique players, total completed races, current leader with best lap time
- Leaderboard table: ranked by best lap time across all sessions, showing player, best lap (mm:ss.SSS), total race time, team, rig, and timestamp (Vega with terms aggregation and team ID lookups; top 3 highlighted in gold/silver/bronze)
- Supporting charts: recent finishes activity feed, best lap trend over time
- Designed for conference/event use -- attendees take turns racing and the leaderboard tracks the fastest player for a prize

The Telemetry and Race Overview dashboards have an **Options list control** for hostname (Rig filter), default **time range** Last 1 minute, and **auto-refresh** every 5 seconds. The Leaderboard dashboard adds an **Event** filter (by `resource.attributes.f1.event_name`), defaults to **Last 24 hours**, and refreshes every **10 seconds**.

> **Note:** The underlying data streams (`metrics-f1_telemetry.otel-default` and `logs-f1_telemetry.otel-default`) are created by Elasticsearch on first write. Until telemetry data is flowing -- either from a live game or via Playback Mode in the web UI -- the dashboards will show "No data" or Vega errors. This is expected; once a telemetry stream starts, all panels populate automatically.

Setup scripts support `ELASTIC_API_KEY` for authenticated clusters (Elastic Cloud, Serverless). Set it in `.env` alongside `ELASTIC_ENDPOINT` and `KIBANA_URL`.

### Creating data views via Kibana Dev Tools

If you prefer to create them manually, open **Dev Tools** in Kibana and run:

```
POST kbn:/api/data_views/data_view
{"data_view":{"id":"f1-telemetry-metrics","title":"metrics-f1_telemetry.otel-default","name":"F1 Telemetry Metrics","timeFieldName":"@timestamp"}}
```

```
POST kbn:/api/data_views/data_view
{"data_view":{"id":"f1-telemetry-logs","title":"logs-f1_telemetry.otel-default","name":"F1 Telemetry Logs","timeFieldName":"@timestamp"}}
```

Once created, the data views are available in Discover, Lens, and Dashboards:

| Data View | Data Stream | Contents |
|-----------|-------------|----------|
| **F1 Telemetry Metrics** | `metrics-f1_telemetry.otel-default` | OTLP gauge metrics (speed, RPM, temperatures, etc.) stored as TSDS |
| **F1 Telemetry Logs** | `logs-f1_telemetry.otel-default` | Full packet payloads with structured `f1.*` fields stored in LogsDB |

## OTEL Collector Configuration

The OTEL Collector runs the Elastic OTel Distribution (embedded in Elastic Agent). The pipeline config is at `config/otel-collector-config.yaml`.

The Elasticsearch exporter uses `mapping.mode: otel` (OTel-native mapping), which automatically creates:

- **Time Series Data Streams (TSDS)** for metrics -- enables the ES|QL `TS` command, automatic dimension detection, and optimised storage
- **LogsDB-backed data streams** for logs -- with synthetic `_source` for efficient log storage

Data streams follow the `.otel-` naming convention: `metrics-f1_telemetry.otel-default` and `logs-f1_telemetry.otel-default`.

The Elasticsearch connection is configured entirely through environment variables (`ELASTIC_ENDPOINT`, `ELASTIC_API_KEY` in `.env`) -- no need to edit the YAML for different environments.

## Near-Real-Time Latency Tuning

The pipeline is tuned for near-real-time dashboards (~200-300ms end-to-end). Three settings control the latency between a telemetry packet arriving and the data becoming searchable in Elasticsearch:

| Setting | Location | Default | Tuned | Impact |
|---------|----------|---------|-------|--------|
| `sending_queue.batch.flush_timeout` | `otel-collector-config.yaml` (ES exporter) | 10s | **200ms** | **Dominant** -- internal bulk buffer flush |
| `batch.timeout` | `otel-collector-config.yaml` (batch processor) | 5s | **50ms** | Secondary -- narrows 0-5s variance |
| `index.refresh_interval` | Elasticsearch index setting | 1s | **200ms** | Minor -- time until indexed docs are searchable |

The `sending_queue.batch.flush_timeout` is the most impactful setting. At its default of 10s, data sits in the Elasticsearch exporter's internal buffer before being sent as a bulk request, regardless of how fast the batch processor delivers it. Reducing it to 200ms cut observed lag from ~16s to under 1s.

### Refresh interval setup

The `index.refresh_interval` is applied via `scripts/setup-elasticsearch.sh`, which:

1. Creates `@custom` component templates that persist the setting across index rollovers
2. Applies the setting to current backing indices for immediate effect

For local deployments, this runs automatically (Docker setup container and/or `start-collector.sh`). For cloud, it runs automatically when you use `./start-collector.sh cloud` (scripts read `ELASTIC_ENDPOINT` and `ELASTIC_API_KEY` from `.env`). To run it manually:

```bash
./scripts/setup-elasticsearch.sh
```

Override the interval via the `ES_REFRESH_INTERVAL` variable in `.env` (default: `200ms`).

### Trade-offs

These aggressive settings are tuned for live demo use with 1-4 rigs. For higher-volume production workloads, consider increasing `batch.timeout` and `sending_queue.batch.flush_timeout` to reduce bulk request frequency and Elasticsearch I/O pressure.

## Telemetry Metrics

The following metrics are sent as OTLP gauges under the `f1.*` namespace. In Elasticsearch (OTel-native mapping), metric values are stored at `metrics.f1.*` (e.g., `metrics.f1.speed`) and resource attributes at `resource.attributes.*`:

| Metric | Description |
|--------|-------------|
| `f1.speed` | Car speed (km/h) |
| `f1.throttle` | Throttle input (0.0-1.0) |
| `f1.brake` | Brake input (0.0-1.0) |
| `f1.gear` | Current gear |
| `f1.engine_rpm` | Engine RPM |
| `f1.engine_temperature` | Engine temperature (C) |
| `f1.current_lap_num` | Current lap number |
| `f1.current_lap_time_in_ms` | Current lap time (ms) |
| `f1.sector` | Current track sector |
| `f1.car_position` | Race position |
| `f1.air_temperature` | Ambient air temperature (C) |
| `f1.track_temperature` | Track surface temperature (C) |
| `f1.brakes_temperature1-4` | Per-wheel brake temperature (C) |
| `f1.tyres_surface_temperature1-4` | Per-wheel tyre surface temperature (C) |
| `f1.tyres_inner_temperature1-4` | Per-wheel tyre inner temperature (C) |

## Project Structure

```
├── src/
│   ├── collector.py              # Core UDP telemetry collector
│   ├── app.py                    # Streamlit web UI
│   ├── streamer.py               # Real-time telemetry monitor page
│   ├── api.py                    # Flask API for endpoint config updates
│   ├── .streamlit/               # Streamlit theme and secrets
│   ├── static/                   # Fonts and images
│   └── telemetry_data/           # Replay .tlm files (not in git)
├── f1_telemetry/                 # F1 2025 UDP packet parser library
│   ├── packets.py                # ctypes packet definitions
│   ├── listener.py               # UDP listener + replay support
│   ├── appendices.py             # Team/driver/track ID lookups
│   ├── compare.py                # F1 2024 packet spec (reference)
│   └── main.py                   # CLI packet dumper
├── config/
│   ├── supervisord.conf          # Process manager configuration
│   ├── nginx.conf                # Reverse proxy configuration
│   └── otel-collector-config.yaml # OpenTelemetry Collector pipeline
├── dashboards/
│   ├── f1-telemetry.ndjson       # Car Telemetry dashboard (metrics index)
│   ├── f1-race-overview.ndjson   # Race Overview dashboard (logs index)
│   └── f1-leaderboard.ndjson     # Player Leaderboard dashboard (logs index)
├── scripts/
│   ├── setup-elasticsearch.sh    # Configures ES refresh interval (run by setup container + start-collector.sh)
│   └── setup-kibana.sh           # Creates data views + imports all dashboards (run by setup container + start-collector.sh)
├── docker-compose.yml            # Multi-service orchestration (includes setup service for local profile)
├── Dockerfile
├── requirements.txt
├── VERSION
└── start-collector.sh
```

The Web UI theme is inspired by [Elastic EUI](https://eui.elastic.co/docs/getting-started/theming/tokens/colors) and can be tuned via `src/.streamlit/config.toml` and the optional CSS injection in `app.py` and `streamer.py`.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for detailed architecture documentation.

## Dependencies

- Python 3.13
- Redis (separate container, `redis:alpine`)
- Elastic Agent OTel Distribution (separate container, `docker.elastic.co/elastic-agent/elastic-agent`)
- Nginx (in f1-app container, reverse proxy)
- Streamlit, Flask, requests, redis-py, psutil, faker

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

## Acknowledgements

This project is a derivative of [splunk/f1-simulator](https://github.com/splunk/f1-simulator), originally created by Splunk Inc. and licensed under the Apache License 2.0. The original project provided the F1 telemetry collection foundation; this version replaces the Splunk backend with an OpenTelemetry pipeline and Elastic Stack.