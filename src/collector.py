#!/usr/bin/env python3
import argparse
import json
import logging
import os
import sqlite3
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import redis  # type: ignore
import requests  # type: ignore
import urllib3  # type: ignore
from faker import Faker
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from f1_telemetry.listener import TelemetryListener

DATABASE = os.getenv("DATABASE", "/app/data/players.sqlite")
MAX_WORKERS = 5
MAX_POOLSIZE = 10
MAX_NETWORK_CONCURRENCY = 10

processing_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
network_semaphore = threading.BoundedSemaphore(MAX_NETWORK_CONCURRENCY)

PACKET_TYPES = {
    0: "MotionData", 1: "SessionData", 2: "LapData", 3: "EventData",
    4: "ParticipantsData", 5: "CarSetupData", 6: "CarTelemetryData",
    7: "CarStatusData", 8: "FinalClassificationData", 9: "LobbyInfoData",
    10: "CarDamageData", 11: "SessionHistoryData", 12: "TyreSetsData",
    13: "MotionExData", 14: "TimeTrialData", 99: "ScriptStartup",
}

OLLY_METRICS = {
    "air_temperature", "brake",
    "brakes_temperature1", "brakes_temperature2",
    "brakes_temperature3", "brakes_temperature4",
    "current_lap_num", "current_lap_time_in_ms", "engine_rpm",
    "engine_temperature", "gear", "speed", "sector", "throttle",
    "track_temperature", "track_id", "car_position",
    "tyres_inner_temperature1", "tyres_inner_temperature2",
    "tyres_inner_temperature3", "tyres_inner_temperature4",
    "tyres_surface_temperature1", "tyres_surface_temperature2",
    "tyres_surface_temperature3", "tyres_surface_temperature4",
}

TELEMETRY_KEY_MAP = {
    0: "car_motion_data",  # MotionData
    # 1: "session_data",
    # 2: "lap_data",
    # 3: "event_details",
    # 4: "participants_data",
    5: "car_setups",
    6: "car_telemetry_data",  # CarTelemetryData
    7: "car_status_data",  # CarStatusData
    8: "classification_data",
    # 9: "lobby_players",
    10: "car_damage_data",  # CarDamageData
    # 11: "lap_history_data",
    12: "tyre_set_data",  # TyreSetsData
    # 13: "motion_ex_data",
    # 14: "time_trial_data",
    15: "lap_position_data",
}

mode = "solo"

fake = Faker()

lap_info = []

urllib3.disable_warnings()

parser = argparse.ArgumentParser(description="F1 2025 - ELK on Track")
parser.add_argument("--hostname", help="Hostname", default="rig_1")
parser.add_argument("--playback", help="Playback", default=False)
args = vars(parser.parse_args())

hostname = args["hostname"]
playback = args["playback"]

for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    encoding="utf-8", 
    format="%(asctime)s %(levelname)s %(message)s", 
    level=logging.INFO,
    handlers=[
        logging.FileHandler("collector.log"),
    ]
)

# Initialize Redis connection
try:
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"), port=6379, db=0, decode_responses=True, socket_connect_timeout=2, socket_timeout=2
    )
    logging.info(f"Collector: Connected to Redis")
except redis.ConnectionError:
    logging.error(f"Failed to connect to Redis")
    redis_client = None

with sqlite3.connect(DATABASE) as conn:
    conn.row_factory = sqlite3.Row
    endpoint_config = conn.execute("SELECT * FROM endpoints").fetchone()

if playback == "True":
    redis_client.hset(f"f1:player:{hostname}", "player_name", fake.name()) if redis_client else None

player_name = redis_client.hget(f"f1:player:{hostname}", "player_name") if redis_client else None
udp_port_raw = redis_client.hget(f"f1:player:{hostname}", "port") if redis_client else None
# Convert Redis value to integer, handling various types
if udp_port_raw:
    if isinstance(udp_port_raw, (str, bytes)):
        udp_port = int(udp_port_raw)
    elif isinstance(udp_port_raw, int):
        udp_port = udp_port_raw

logging.info(f"Collector: {hostname} - {player_name} - UDP Port: {udp_port}")

session = requests.Session()
retry = Retry(
    total=3,
    backoff_factor=0.3,
    status_forcelist=[500, 502, 503, 504]
)
adapter = HTTPAdapter(
    pool_connections=2,
    pool_maxsize=MAX_POOLSIZE,
    max_retries=retry
)
session.mount("https://", adapter)
session.mount("http://", adapter)


# Function to write UDP status to Redis
def write_udp_status_to_redis(packet_id, current_time=None):
    if not redis_client:
        return

    try:
        # Create a status key indicating UDP data is being received
        status_key = f"f1:{hostname}:udp_status"

        # Ensure all values are properly converted to Redis-compatible types
        safe_player_name = str(player_name.decode('utf-8')) if isinstance(player_name, bytes) else str(player_name) if player_name else "Unknown"
        safe_udp_port = int(udp_port) if isinstance(udp_port, (str, bytes)) else udp_port if udp_port else 20777

        # Use provided timestamp or get current time once
        if current_time is None:
            current_time = datetime.now()
            
        status_data = {
            "last_packet_received": current_time.isoformat(),
            "last_packet_id": int(packet_id),
            "last_packet_type": PACKET_TYPES.get(packet_id, "Unknown"),
            "hostname": str(hostname),
            "player_name": safe_player_name,
            "udp_port": safe_udp_port,
            "status": "active",
        }

        # Use Redis pipeline for batch operations
        pipe = redis_client.pipeline()
        
        # Write status as a hash to Redis
        pipe.hset(status_key, mapping=status_data)
        # Set expiration time (30 seconds - if no packets received, key will expire)
        pipe.expire(status_key, 30)

        # Also maintain a simple timestamp key for quick checks
        simple_status_key = f"f1:{hostname}:last_seen"
        pipe.set(simple_status_key, current_time.timestamp(), ex=30)

        # Special handling for FinalClassificationData (packet 8)
        if packet_id == 8:
            race_complete_key = f"f1:{hostname}:race_complete"
            race_complete_data = {
                "race_completed": "True",  # Must match app.py check for 'True'
                "completion_time": current_time.isoformat(),
                "player_name": safe_player_name,
                "hostname": str(hostname),
                "ready_for_next_player": "True"  # Must match app.py check for 'True'
            }

            pipe.hset(race_complete_key, mapping=race_complete_data)
            # Set longer expiration for race completion status (5 minutes)
            pipe.expire(race_complete_key, 300)
        
        # Execute all Redis operations at once
        pipe.execute()
        
        # Log race completion after pipeline execution
        if packet_id == 8:
            logging.info(f"Race completed for {hostname} - {player_name}. Ready for next player.")

        logging.debug(
            f"Updated UDP status in Redis for packet type: {PACKET_TYPES.get(packet_id, 'Unknown')}"
        )

    except Exception as e:
        logging.error(f"Error writing UDP status to Redis: {e}")
        logging.error(f"DEBUG - packet_id: {packet_id}, hostname: {hostname}, player_name: {player_name}, udp_port: {udp_port}")


# Function to write metrics to Redis
def write_o11y_metrics_to_redis_hash(f1_json, current_time=None):
    if not redis_client or not f1_json:
        return
        
    try:
        redis_key = f"f1:{hostname}:metrics"
        # Use provided timestamp or get current time once
        if current_time is None:
            current_time = datetime.now()
            
        base_data = {
            "timestamp": current_time.isoformat(),
            "hostname": hostname, 
            "player_name": player_name,
            "game_version": "2025"
        }
        
        # Process all cars and merge metrics
        all_metrics = {}
        for car_dict in f1_json:
            all_metrics.update({k: v for k, v in car_dict.items() if k in OLLY_METRICS and v is not None})
        
        if all_metrics:
            all_metrics.update(base_data)
            # Use Redis pipeline for batch operations
            pipe = redis_client.pipeline()
            pipe.hset(redis_key, mapping=all_metrics)
            pipe.expire(redis_key, 60)
            pipe.execute()
            
    except Exception as e:
        logging.error(f"Error writing to Redis: {e}")


# Global counters (consider using a class to encapsulate this)
request_stats = defaultdict(int)
stats_lock = threading.Lock()


def log_stats_periodically():
    """Log stats every second in a separate thread"""
    while True:
        time.sleep(10)
        with stats_lock:
            if request_stats["success"] > 0 or request_stats["failed"] > 0:
                logging.info(
                    f"{hostname}: Requests/10 seconds - Success: {request_stats['success']}, Failed: {request_stats['failed']}"
                )
                request_stats.clear()

# Start the logging thread once
stats_thread = threading.Thread(target=log_stats_periodically, daemon=True)
stats_thread.start()


def send_otlp_metrics(f1_json):
    custom_event = endpoint_config["custom_event"]
    time_unix_nano = str(int(datetime.now().timestamp() * 1e9))

    metrics = []
    for car_dict in f1_json:
        for key in OLLY_METRICS:
            if key in car_dict and car_dict[key] is not None:
                metrics.append({
                    "name": f"f1.{key}",
                    "gauge": {
                        "dataPoints": [{
                            "asDouble": float(car_dict[key]),
                            "timeUnixNano": time_unix_nano,
                        }]
                    }
                })

    if not metrics:
        return

    payload = {
        "resourceMetrics": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "f1-2025"}},
                    {"key": "f1.hostname", "value": {"stringValue": hostname}},
                    {"key": "f1.event_name", "value": {"stringValue": custom_event}},
                    {"key": "f1.game_version", "value": {"stringValue": "2025"}},
                ]
            },
            "scopeMetrics": [{
                "metrics": metrics
            }]
        }]
    }

    endpoint = f"{endpoint_config['otlp_endpoint']}/v1/metrics"

    def send_request(data):
        with network_semaphore:
            try:
                response = session.post(
                    endpoint,
                    headers={"Content-Type": "application/json"},
                    json=data,
                )
                if response.status_code not in (200, 202):
                    logging.error(f"OTLP metrics error {response.status_code}: {response.text}")
                    with stats_lock:
                        request_stats["failed"] += 1
                else:
                    with stats_lock:
                        request_stats["success"] += 1
            except requests.exceptions.RequestException as e:
                logging.error(f"Network error: {e}")
                with stats_lock:
                    request_stats["failed"] += 1

    processing_executor.submit(send_request, payload)


def _coerce(v):
    if isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def send_otlp_logs(event_rows, packet_id):
    time_unix_nano = str(int(datetime.now().timestamp() * 1e9))
    sourcetype = PACKET_TYPES.get(packet_id, "Unknown")
    custom_event = endpoint_config["custom_event"]

    log_records = []
    for row in event_rows:
        body = {f"f1.{key}": _coerce(row[key]) for key in row.keys()}
        body["f1.custom_event"] = custom_event
        body["f1.data_mode"] = "playback" if playback == "True" else "live"

        log_records.append({
            "timeUnixNano": time_unix_nano,
            "severityNumber": 9,
            "severityText": "INFO",
            "body": {"stringValue": json.dumps(body)},
            "attributes": [
                {"key": "f1.packet_type", "value": {"stringValue": sourcetype}},
                {"key": "f1.hostname", "value": {"stringValue": hostname}},
            ],
        })

    if not log_records:
        return

    payload = {
        "resourceLogs": [{
            "resource": {
                "attributes": [
                    {"key": "service.name", "value": {"stringValue": "f1-2025"}},
                    {"key": "f1.hostname", "value": {"stringValue": hostname}},
                    {"key": "f1.event_name", "value": {"stringValue": custom_event}},
                    {"key": "f1.game_version", "value": {"stringValue": "2025"}},
                ]
            },
            "scopeLogs": [{
                "logRecords": log_records
            }]
        }]
    }

    endpoint = f"{endpoint_config['otlp_endpoint']}/v1/logs"

    def send_request(data):
        with network_semaphore:
            try:
                response = session.post(
                    endpoint,
                    headers={"Content-Type": "application/json"},
                    json=data,
                )
                if response.status_code not in (200, 202):
                    logging.error(f"OTLP logs error {response.status_code}: {response.text}")
                    with stats_lock:
                        request_stats["failed"] += 1
                else:
                    with stats_lock:
                        request_stats["success"] += 1
            except requests.exceptions.RequestException as e:
                logging.error(f"Network error: {e}")
                with stats_lock:
                    request_stats["failed"] += 1

    processing_executor.submit(send_request, payload)


def update_player_info(data):
    global player_info, lap_info
    player_info = data["participants"]
    # Initialize lap_info with dictionaries for each car to track lap events
    # This prevents memory leaks by resetting on each participant update
    lap_info = [{"lap_event": "", "current_sector": 0, "current_lap": 0} for _ in player_info]


def flatten_data(data):
    telemetry_list = []

    for car_index, entry in enumerate(data):
        # Start with car_index to avoid separate assignment
        flat_entry = {"car_index": car_index}
        
        # Process all items in a single pass to avoid multiple iterations
        for key, value in entry.items():
            if isinstance(value, list):
                # Flatten list values directly into flat_entry
                for i, val in enumerate(value):
                    flat_entry[f"{key}{i + 1}"] = val
            else:
                # Add non-list values directly
                flat_entry[key] = value

        telemetry_list.append(flat_entry)

    return telemetry_list


def set_mode_data(telemetry, playerCarIndex):
    player_name = redis_client.hget(f"f1:player:{hostname}", "player_name") if redis_client else None
    # If not in spectator mode, get rid of the non-player cars
    if mode == "solo":
        # Get only player car from flattened data
        data = [telemetry[playerCarIndex]]
        data[0].update({"player_name": player_name})
    else:
        data = telemetry

    return data


def augment_packet(header, telemetry, data, *args):
    # Prepare a list of dictionaries to merge for each entry
    augmented_data = []

    for entry, player in zip(telemetry, player_info):
        # Merge the header, player, and additional data fields in one go
        combined_data = {**entry, **player, **header}  # Merge entry, player, and header

        # Add additional fields from args (data), if any
        for a in args:
            combined_data[a] = data[a]

        augmented_data.append(combined_data)

    return augmented_data


def merge_car_lap(data, header, playerCarIndex):
    telemetry = flatten_data(data["lap_data"])

    # Augment with header and player info data
    for entry, player in zip(telemetry, player_info):
        entry.update(player)
        entry.update(header)
    # # check for events such as lap or sector completion
    for entry, info_buffer in zip(telemetry, lap_info):

        info_buffer.update(
            {
                "current_sector": entry["sector"],
                "current_lap": entry["current_lap_num"],
            }
        )
        entry.update({"lap_event": info_buffer["lap_event"]})

    return set_mode_data(telemetry, playerCarIndex)


def process_packet_data(packet_id, data, header, playerCarIndex):
    # Check if the packet_id needs to be processed in the common way
    if packet_id in TELEMETRY_KEY_MAP:
        telemetry_key = TELEMETRY_KEY_MAP[packet_id]
        telemetry = flatten_data(data[telemetry_key])
        augmented_telemetry = augment_packet(header, telemetry, data)
        merged_data = set_mode_data(augmented_telemetry, playerCarIndex)

        return merged_data

    # Specific packet processing logic for PacketSessionData (ID 1)
    if packet_id == 1:
        telemetry = flatten_data(data["marshal_zones"])
        augmented_telemetry = augment_packet(
            header, telemetry, data,
            "air_temperature", "track_id", "weather", "total_laps", "track_temperature", "track_length",
        )
        merged_data = set_mode_data(augmented_telemetry, playerCarIndex)

        return merged_data

    # Specific packet processing logic for PacketLapData (ID 2)
    if packet_id == 2:
        merged_data = merge_car_lap(data, header, playerCarIndex)

        return merged_data

    # PacketParticipantsData (ID 4) - No data handling needed here except for update
    if packet_id == 4:
        update_player_info(data)
        return None

    if packet_id == 11:
        if data["car_idx"] == playerCarIndex:
            telemetry = flatten_data(data["lap_history_data"])
            augmented_telemetry = augment_packet(
                header, telemetry, data,
                "best_lap_time_lap_num", "best_sector1_lap_num", "best_sector2_lap_num", "best_sector3_lap_num", "num_laps", "num_tyre_stints",
            )
            merged_data = set_mode_data(augmented_telemetry, playerCarIndex)

            return merged_data


def massage_data(data):
    # Cache timestamp for all operations in this packet processing
    current_time = datetime.now()
    
    data = data.to_dict()
    packet_id = data["header"]["packet_id"]
    header = data["header"]
    playerCarIndex = data["header"]["player_car_index"]

    # Write UDP status to Redis - indicating data is being received
    write_udp_status_to_redis(packet_id, current_time)

    # Process the packet data using the helper function
    merged_data = process_packet_data(packet_id, data, header, playerCarIndex)

    # If merged_data is None, return early (for specific packet cases like ID 3 or 4)
    if merged_data is None:
        return

    # Write speed metrics to Redis
    write_o11y_metrics_to_redis_hash(merged_data, current_time)

    if endpoint_config['logs_enabled']:
        send_otlp_logs(merged_data, packet_id)

    if endpoint_config['metrics_enabled']:
        send_otlp_metrics(merged_data)


startup_payload = [
    {
        "message": f"F1 2025 collector starting for {hostname}",
        "description": "Start collector",
        "otlp_endpoint": endpoint_config["otlp_endpoint"],
        "checkpoint_1": datetime.now().timestamp(),
    }
]

if endpoint_config['logs_enabled']:
    send_otlp_logs(startup_payload, 99)

replay_files = {
    "rig_1": "telemetry_data/telemetry_replay_20250813_180706.tlm",
    "rig_2": "telemetry_data/telemetry_replay_20250813_181707.tlm",
    "rig_3": "telemetry_data/telemetry_replay_20251021_112709.tlm",
    "rig_4": "telemetry_data/telemetry_replay_20251021_114336.tlm",
}


def create_listener():
    """Helper function to create a new listener"""
    if playback == "True":
        logging.info(f"Collector: Using replay file: {replay_files[hostname]} for hostname: {hostname}")

        return TelemetryListener(replay_file=replay_files[hostname])
    else:
        return TelemetryListener(save_to_file=False, port=udp_port)
        #return TelemetryListener(save_to_file=True)


listener = create_listener()
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

# Track replay loops for debugging
replay_count = 0

try:
    while True:
        try:
            packet = listener.get()

            # Handle end of replay file
            if packet is None and playback == "True":
                replay_count += 1
                logging.info(f"Collector: Replay loop #{replay_count} completed, restarting...")
                redis_client.hset(f"f1:player:{hostname}", "player_name", fake.name()) if redis_client else None
                # Small delay to prevent spinning too fast
                time.sleep(0.1)

                # Recreate listener for next loop
                listener = create_listener()
                continue

            # Handle empty packet in live mode
            if packet is None:
                time.sleep(0.01)  # Small delay in live mode
                continue

            executor.submit(massage_data, packet)
            del packet

            # Small yield to prevent CPU spinning
            time.sleep(0.0001)

        except Exception as e:
            logging.error(f"Error processing packet: {e}")
            if playback == "True":
                # In playback mode, try to restart
                logging.error("Attempting to restart playback...")
                redis_client.hset(f"f1:player:{hostname}", "player_name", fake.name()) if redis_client else None
                listener = create_listener()
            else:
                # In live mode, continue
                time.sleep(1)
            continue
except KeyboardInterrupt:
    conn.execute(
        "UPDATE players SET listener_pid = ? WHERE hostname = ?",
        (0, hostname),
    )
    conn.commit()
    conn.close()
    pass
