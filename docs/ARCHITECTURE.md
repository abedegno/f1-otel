# Architecture

## System Overview

The F1 2025 ELK on Track collector is a three-container application that bridges the EA Sports F1 2025 game's UDP telemetry output with any OpenTelemetry-compatible backend. It captures real-time car data from up to 4 racing rigs simultaneously and forwards it as OTLP metrics and logs through an OpenTelemetry Collector.

```
                          +-----------------------------------------+
                          |         f1-app container                 |
                          |                                         |
 F1 Game (Rig 1) ------->| UDP:20777 --> collector.py (rig_1)      |
 F1 Game (Rig 2) ------->| UDP:20778 --> collector.py (rig_2)      |
 F1 Game (Rig 3) ------->| UDP:20779 --> collector.py (rig_3)      |
 F1 Game (Rig 4) ------->| UDP:20780 --> collector.py (rig_4)      |
                          |                   |                     |
                          |          OTLP HTTP|         Redis TCP   |
                          +---------|---------|---------|------------+
                                    |         |         |
                                    v         |         v
                          +------------------+|  +---------------+
                          | otel-collector   ||  | redis         |
                          | :4318            ||  | :6379         |
                          +------------------+|  +---------------+
                                    |         |         |
                                    v         +---------|
                          +------------------+          |
                          | Any OTLP Backend |          v
                          | (Splunk, Datadog,|   Streamlit UI (8502)
                          |  Grafana, etc.)  |   Flask API   (8503)
                          +------------------+          |
                                               Nginx (8501) <--- Browser
```

## Container Architecture

Services orchestrated by `docker-compose` with profile support:

| Service | Image | Profile | Role |
|---------|-------|---------|------|
| **f1-app** | Built from `Dockerfile` | (always) | Nginx, Streamlit, Flask API, collector subprocesses |
| **redis** | `redis:alpine` | (always) | In-memory data store for real-time IPC |
| **otel-collector** | `docker.elastic.co/elastic-agent/elastic-agent` | (always) | Elastic OTel Distribution -- receives OTLP, exports to Elasticsearch |
| **elasticsearch** | `docker.elastic.co/elasticsearch/elasticsearch` | `local` | Search and analytics engine (local testing only) |
| **kibana** | `docker.elastic.co/kibana/kibana` | `local` | Data visualization UI (local testing only) |

Use `docker compose up -d` for the three base services (connecting to a remote Elastic cluster), or `docker compose --profile local up -d` to also spin up Elasticsearch and Kibana locally.

## Process Model

The f1-app container uses **supervisord** to manage three long-running processes:

| Process | Port | Role | Priority |
|---------|------|------|----------|
| **streamlit** | 8502 | Web UI for configuration and monitoring (`app.py` + `streamer.py`) | 200 |
| **api** (Flask/waitress) | 8503 | REST API for endpoint config updates | 300 |
| **nginx** | 8501 | Reverse proxy -- routes `/` to Streamlit, `/update_endpoint` to Flask API | 400 |

Collector processes (`collector.py`) are **not** managed by supervisord. They are spawned as child processes by the Streamlit app (`app.py`) when the user clicks "Master Control" in the UI. Each rig gets its own collector process.

## Data Flow

### 1. UDP Packet Reception

The F1 2025 game sends binary UDP packets at 10 Hz. Each rig's collector binds to its assigned port:

```
TelemetryListener (listener.py)
  -> socket.recv(2048)
  -> PacketHeader.from_buffer_copy(packet)  -- identify packet type
  -> HEADER_FIELD_TO_PACKET_TYPE[key].unpack(packet)  -- full ctypes decode
```

The listener also supports **replay mode** -- see [Replay Mode](#replay-mode) below.

### 2. Packet Processing

`collector.py:massage_data()` is the central processing function:

```
massage_data(packet)
  -> packet.to_dict()                    -- ctypes struct to Python dict
  -> process_packet_data(packet_id, ...) -- per-type processing
       -> flatten_data()                 -- expand array fields (e.g., tyres[4] -> tyres1..tyres4)
       -> augment_packet()               -- merge header, player info, session data
       -> set_mode_data()                -- filter to player car only (solo mode)
  -> write_udp_status_to_redis()         -- heartbeat for UI status indicators
  -> write_o11y_metrics_to_redis_hash()  -- metrics for Streamlit streamer
  -> send_otlp_metrics()                 -- POST to OTEL Collector /v1/metrics (if enabled)
  -> send_otlp_logs()                    -- POST to OTEL Collector /v1/logs (if enabled)
```

### 3. Packet Types

The F1 2025 UDP spec defines 16 packet types. Key ones for telemetry:

| ID | Type | Key Data |
|----|------|----------|
| 0 | MotionData | World position, velocity, g-forces, yaw/pitch/roll |
| 1 | SessionData | Weather, track, temperatures, marshal zones |
| 2 | LapData | Lap times, sector times, car position, pit status |
| 4 | ParticipantsData | Driver names, teams, nationalities (updates player_info) |
| 6 | CarTelemetryData | Speed, throttle, brake, gear, RPM, temperatures |
| 7 | CarStatusData | Fuel, ERS, tyre compound, DRS |
| 8 | FinalClassificationData | End-of-race results (triggers race_complete flag) |
| 10 | CarDamageData | Wing/tyre/engine damage and wear |
| 11 | SessionHistoryData | Per-lap history with sector times |

All packet structs are defined in `f1_telemetry/packets.py` using `ctypes.LittleEndianStructure` for zero-copy binary parsing.

### 4. Output Destinations

#### OTLP Metrics

Metrics are sent as OTLP JSON to `{otlp_endpoint}/v1/metrics`:

```json
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "f1-2025"}},
        {"key": "f1.hostname", "value": {"stringValue": "rig_1"}},
        {"key": "f1.event_name", "value": {"stringValue": "Grand Prix Demo"}}
      ]
    },
    "scopeMetrics": [{
      "metrics": [{
        "name": "f1.speed",
        "gauge": {"dataPoints": [{"asDouble": 312.0, "timeUnixNano": "..."}]}
      }]
    }]
  }]
}
```

#### OTLP Logs

Full packet data is sent as OTLP log records to `{otlp_endpoint}/v1/logs`:

```json
{
  "resourceLogs": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "f1-2025"}},
        {"key": "f1.hostname", "value": {"stringValue": "rig_1"}}
      ]
    },
    "scopeLogs": [{
      "logRecords": [{
        "timeUnixNano": "...",
        "severityText": "INFO",
        "body": {"stringValue": "{...}"},
        "attributes": [
          {"key": "f1.packet_type", "value": {"stringValue": "CarTelemetryData"}}
        ]
      }]
    }]
  }]
}
```

The Elastic OTel Collector then exports these to Elasticsearch (local or cloud, configured via `ELASTIC_ENDPOINT` env var).

### 5. Elasticsearch Mapping -- OTel-Native Mode

The Elasticsearch exporter uses `mapping.mode: otel`, which preserves OpenTelemetry semantic conventions and enables specialised storage:

#### Data Streams

| Signal  | Data Stream Name                    | Index Mode   |
|---------|-------------------------------------|--------------|
| Metrics | `metrics-f1_telemetry.otel-default` | `time_series` (TSDS) |
| Logs    | `logs-f1_telemetry.otel-default`    | LogsDB       |

The `.otel-` suffix in the data stream name is automatically added by the exporter in OTel-native mapping mode.

#### Metrics Field Layout (TSDS)

Time Series Data Streams store metrics with automatic dimension detection and optimised storage:

| OTel Concept | Elasticsearch Field Path |
|-------------|-------------------------|
| Metric value (e.g., `f1.speed`) | `metrics.f1.speed` |
| Resource attribute (e.g., `service.name`) | `resource.attributes.service.name` (also queryable at top level via passthrough) |
| Resource attribute (e.g., `f1.hostname`) | `resource.attributes.f1.hostname` |

TSDS enables the ES|QL `TS` command for time-series-native queries and aggregations:

```
TS metrics-f1_telemetry.otel-default
| STATS avg_speed = AVG(metrics.f1.speed) BY resource.attributes.f1.hostname
```

#### Logs Field Layout (LogsDB)

LogsDB uses synthetic `_source` for efficient storage:

| OTel Concept | Elasticsearch Field Path |
|-------------|-------------------------|
| Log body (raw JSON string) | `body.text` |
| Severity | `severity_text` |
| Parsed body fields (from transform processor) | `attributes.f1.speed`, `attributes.f1.car_position`, etc. |
| Resource attribute | `resource.attributes.f1.hostname`, etc. |

The `transform` processor in the OTel Collector pipeline parses the JSON log body and merges its contents into the `attributes` map, making individual fields queryable:

```
FROM logs-f1_telemetry.otel-default
| WHERE attributes.f1.car_position == 1
| KEEP @timestamp, attributes.f1.speed, attributes.f1.current_lap_num
```

#### Latency Pipeline

The end-to-end latency from a telemetry packet to a searchable document in Elasticsearch passes through three tuneable stages:

```
collector.py          OTel Collector              Elasticsearch
  datetime.now() -->  batch processor (50ms) -->  sending_queue (200ms) -->  bulk API -->  refresh (200ms) -->  searchable
                      config/otel-collector-      config/otel-collector-     scripts/setup-
                      config.yaml                 config.yaml                elasticsearch.sh
```

| Stage | Config Key | Location | Default | Tuned | Role |
|-------|-----------|----------|---------|-------|------|
| Batch processor | `batch.timeout` | `otel-collector-config.yaml` | 5s | 50ms | Buffers incoming OTLP data before passing to exporters |
| ES exporter queue | `sending_queue.batch.flush_timeout` | `otel-collector-config.yaml` | 10s | 200ms | Internal bulk buffer inside the Elasticsearch exporter |
| Index refresh | `index.refresh_interval` | Elasticsearch index setting | 1s | 200ms | Time between index refreshes that make new documents searchable |

The `sending_queue.batch.flush_timeout` is the dominant factor. At its default of 10 seconds, it dwarfs the other two stages regardless of their values. The batch processor adds up to its timeout as variance (you may catch it right after a flush or right before the next one). The refresh interval adds a final sub-second delay.

The refresh interval is applied by `scripts/setup-elasticsearch.sh` using two mechanisms: `@custom` component templates (for persistence across rollover) and direct `_settings` API calls (for immediate effect on current backing indices). The value is configurable via `ES_REFRESH_INTERVAL` in `.env`.

## Replay Mode

Replay mode allows the full telemetry pipeline to run from pre-recorded `.tlm` files instead of a live F1 game. This is controlled by the **Playback Mode** toggle in the web UI.

### `.tlm` file format

Each packet is stored with a 12-byte header followed by the raw UDP payload:

```
[relative_timestamp: float64 (8 bytes)][packet_length: uint32 (4 bytes)][packet_data: N bytes]
```

The `relative_timestamp` is seconds elapsed since the recording started. When saving, `TelemetryListener` writes `time.time() - start_time` for each received packet.

### Replay timing

`TelemetryListener._get_from_replay()` maintains real-time pacing:

```
replay_start_time = time.time()           # set once at listener creation

for each packet in file:
    current_replay_time = time.time() - replay_start_time
    if packet.timestamp > current_replay_time:
        sleep(packet.timestamp - current_replay_time)
    yield packet
```

This preserves the original inter-packet gaps, so a 10 Hz telemetry stream replays at 10 Hz.

### Timestamps sent to the backend

Replay timing only controls packet *pacing*. Both `send_otlp_metrics()` and `send_otlp_logs()` stamp `datetime.now()` at the point of export:

```python
time_unix_nano = str(int(datetime.now().timestamp() * 1e9))
```

This means data always appears as "live" in the backend, regardless of when the recording was originally captured. No time-range adjustments are needed in dashboards.

Each log record includes a `data_mode` field set to `"playback"` or `"live"`, so replayed data can be distinguished from real game sessions if needed.

### Rig-to-file mapping

Each rig hostname maps to a specific replay file:

| Hostname | File |
|----------|------|
| `rig_1` | `telemetry_replay_20250813_180706.tlm` |
| `rig_2` | `telemetry_replay_20250813_181707.tlm` |
| `rig_3` | `telemetry_replay_20251021_112709.tlm` |
| `rig_4` | `telemetry_replay_20251021_114336.tlm` |

### Looping

When a replay file reaches EOF, the collector automatically:

1. Generates a new random driver name (via `Faker`)
2. Sleeps briefly (100ms) to avoid CPU spinning
3. Creates a new `TelemetryListener` instance pointing to the same file
4. Continues the packet loop

This provides an indefinite stream of telemetry data for long-running demos.

## Storage

### Redis (separate container)

Used for ephemeral, high-frequency state shared between processes:

| Key Pattern | Type | Purpose | TTL |
|-------------|------|---------|-----|
| `f1:player:{hostname}` | Hash | Player name, port, listener PID | None |
| `f1:{hostname}:metrics` | Hash | Latest telemetry metrics for Streamlit | 60s |
| `f1:{hostname}:udp_status` | Hash | Last packet info, active status | 30s |
| `f1:{hostname}:last_seen` | String | Timestamp for quick liveness check | 30s |
| `f1:{hostname}:race_complete` | Hash | Race completion flag for player rotation | 300s |

### SQLite (`players.sqlite`)

Used for persistent configuration that survives container restarts:

| Table | Columns | Purpose |
|-------|---------|---------|
| `players` | id, player_name, hostname, port, listener_pid | Rig-to-player mapping and process tracking |
| `endpoints` | id, otlp_endpoint, otlp_protocol, metrics_enabled, logs_enabled, custom_event | OTEL endpoint configuration |

The database lives inside a Docker named volume (`db-data:/app/data`) to persist across container recreation.

## Web UI

### Collector Page (`app.py`)

The main page provides:

- **Sidebar** -- Configuration form for rigs, OTEL endpoint, playback mode, debug toggle
- **Master Control** -- Toggle to start/stop all collector processes
- **Rig Cards** -- Per-rig status showing UDP activity, process state, memory usage, current speed/lap/track, race completion, and a player name input form
- **Status Badges** -- Redis connection, active collector count

### Streamer Page (`streamer.py`)

A real-time telemetry dashboard that reads from Redis and displays:

- Metric cards with formatted values and color-coded temperatures
- Brake and tyre temperature grids in wheel-position layout (FL/FR/RL/RR)
- Time-series charts using Altair for selected metrics

### API (`api.py`)

A minimal Flask endpoint served by waitress:

```
POST /update_endpoint
  Body: {"otlp_endpoint": "...", "metrics_enabled": true, "logs_enabled": true}
  -> Updates the endpoints table in SQLite
```

Accessible via nginx at `http://<host>:8501/update_endpoint`.

## Concurrency Model

Each collector process (`collector.py`) uses:

- **Main thread** -- Blocking `socket.recv()` loop, submits packets to executor
- **ThreadPoolExecutor** (5 workers) -- Parallel `massage_data()` processing
- **ThreadPoolExecutor** (5 workers) -- Network I/O for OTLP API calls
- **BoundedSemaphore** (10) -- Caps concurrent outbound HTTP connections
- **Stats thread** -- Logs success/failure counts every 10 seconds

The Streamlit app runs in its own process and communicates with collectors only through Redis and SQLite (no direct IPC).

## Networking

Nginx on port 8501 acts as the single external entry point:

| Path | Upstream | Notes |
|------|----------|-------|
| `/` | Streamlit (8502) | Main web UI |
| `/_stcore/*` | Streamlit (8502) | Streamlit internal (health, WebSocket stream) |
| `/static/*` | Streamlit (8502) | Static assets (fonts, images) |
| `/update_endpoint` | Flask API (8503) | Endpoint configuration API |

WebSocket upgrade is handled for `/_stcore/stream` to support Streamlit's real-time updates.

Redis (6379) and the OTEL Collector (4318) are only accessible within the Docker Compose network. When using `--profile local`, Elasticsearch (9200) is internal and Kibana is exposed on port 5601 (configurable via `KIBANA_PORT` in `.env`).
