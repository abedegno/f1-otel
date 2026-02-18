#!/usr/bin/env python3
import datetime
import logging
import os
import sqlite3
import subprocess
import sys
import time
from sqlite3 import Error

import requests  # type: ignore
import psutil  # type: ignore
import redis  # type: ignore
import streamlit as st  # type: ignore

# Read version from VERSION file
def get_version():
    """Read version from VERSION file"""
    version_file = os.path.join(os.path.dirname(__file__), "VERSION")
    try:
        with open(version_file, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "unknown"

VERSION = get_version()

DATABASE = os.getenv("DATABASE", "/app/data/players.sqlite")

drivers = [
    ["Lando Norris", "rig_1", 20777],
    ["Oscar Piastri", "rig_2", 20778],
    ["Charles Leclerc", "rig_3", 20779],
    ["Lewis Hamilton", "rig_4", 20780]
]

# TRACKS moved to .streamlit/secrets.toml

# Initialize Session State Efficiently
session_defaults = {
    "configured": False,
    "debug": False,
    "otlp_endpoint": "http://otel-collector:4318",
    "metrics_enabled": 1,
    "logs_enabled": 1,
    "custom_event": "",
    "listeners_running": False
}

for key, value in session_defaults.items():
    if key not in st.session_state:
        st.session_state[key] = value

st.set_page_config(
    page_title="F1 2025 - ELK on Track",
    page_icon=":material/sports_motorsports:",
    layout="wide",
    initial_sidebar_state="collapsed",
)

logger = logging.getLogger(__name__)
logging.basicConfig(
    encoding="utf-8", 
    format="%(asctime)s %(levelname)s %(message)s", 
    level=logging.INFO,
    handlers=[
        logging.FileHandler("collector.log"),
    ]
)

# Get external IP address
def get_external_ip():
    """Get external IP address of the host"""
    try:
        response = requests.get('https://api.ipify.org', timeout=5)
        return response.text
    except Exception as e:
        logger.warning(f"Failed to get external IP: {e}")
        return None


# Redis connection function
@st.cache_resource
def get_redis_connection():
    try:
        r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, db=0, decode_responses=True, socket_connect_timeout=2, socket_timeout=2)
        r.ping()
        return r
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        return None


def get_speed_lap_from_redis(hostname):
    """Get speed and current lap data from Redis for a specific hostname"""
    r = get_redis_connection()
    if not r:
        return None, None, None
    
    try:
        # Try to get speed and current lap from the metrics hash
        metrics_key = f"f1:{hostname}:metrics"
        hash_data = r.hgetall(metrics_key)
        
        # Ensure hash_data is a dict (not awaitable)
        if isinstance(hash_data, dict) and hash_data:
            speed = float(hash_data['speed']) if 'speed' in hash_data else None
            current_lap = int(hash_data['current_lap_num']) if 'current_lap_num' in hash_data else None
            track_id = int(hash_data['track_id']) if 'track_id' in hash_data else None
            return speed, current_lap, track_id

    except Exception as e:
        logger.debug(f"Error getting speed/current lap for {hostname}: {e}")
    
    return None, None, None


def get_race_completion_status(hostname):
    """Check if race is completed and ready for next player"""
    r = get_redis_connection()
    if not r:
        return False, None
    
    try:
        race_complete_key = f"f1:{hostname}:race_complete"
        race_data = r.hgetall(race_complete_key)

        if isinstance(race_data, dict) and race_data:
            race_completed_value = race_data.get('race_completed')
            
            is_complete = race_completed_value == 'True'
            completion_time = race_data.get('completion_time')
            
            return is_complete, completion_time
    except Exception as e:
        logger.error(f"Error getting race completion status for {hostname}: {e}")
    
    return False, None


def clear_race_completion_status(hostname):
    """Clear race completion status when new player is set"""
    r = get_redis_connection()
    if not r:
        return
    
    try:
        race_complete_key = f"f1:{hostname}:race_complete"
        r.delete(race_complete_key)
        logger.info(f"Cleared race completion status for {hostname}")
    except Exception as e:
        logger.error(f"Error clearing race completion status for {hostname}: {e}")


def execute_query(query, params=(), fetch=False):
    """Executes an SQLite query safely with optional fetching."""
    try:
        with sqlite3.connect(DATABASE, check_same_thread=False) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch:
                return cursor.fetchall()
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")


def create_db_tables():
    logging.info("APP: Creating database tables")

    execute_query("DROP TABLE IF EXISTS players")
    logging.info("APP: Cleared existing players from database")

    execute_query("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            hostname TEXT NOT NULL,
            port INTEGER NOT NULL,
            listener_pid INTEGER NOT NULL DEFAULT 0
        );
    """)

    logging.info("APP: Created players table")
    
    execute_query("""
        CREATE TABLE IF NOT EXISTS endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            otlp_endpoint TEXT NOT NULL DEFAULT 'http://otel-collector:4318',
            otlp_protocol TEXT NOT NULL DEFAULT 'http/protobuf',
            metrics_enabled BOOLEAN NOT NULL DEFAULT 1,
            logs_enabled BOOLEAN NOT NULL DEFAULT 1,
            custom_event TEXT NOT NULL DEFAULT ''
        );
    """)
    
    logging.info("APP: Created endpoints table")


def init_db(num_rigs, otlp_endpoint, metrics_enabled, logs_enabled, custom_event):
    redis_client = get_redis_connection()
    if redis_client:
        try:
            redis_client.flushdb()
            logging.info("APP: Flushed Redis database successfully")
            pipe = redis_client.pipeline()
            
            for player_name, hostname, port in drivers[:num_rigs]:
                pipe.hset(f"f1:player:{hostname}", mapping={
                    "player_name": player_name,
                    "port": port,
                    "listener_pid": 0,
                    "last_updated": datetime.datetime.now().isoformat()
                })

            pipe.execute()
            logging.info(f"APP: Stored {num_rigs} players in Redis successfully")
        except redis.RedisError as e:
            logging.error(f"Failed to store players in Redis: {e}")
        except Exception as e:
            logging.error(f"Unexpected error storing players in Redis: {e}")

    with sqlite3.connect(DATABASE, check_same_thread=False) as conn:
        cursor = conn.cursor()

        players_data = [(drivers[i][0], drivers[i][1], drivers[i][2], 0) for i in range(num_rigs)]
        cursor.executemany("""
            INSERT INTO players (player_name, hostname, port, listener_pid)
            VALUES (?, ?, ?, ?);
        """, players_data)

        cursor.execute("""
            INSERT INTO endpoints (id, otlp_endpoint, metrics_enabled, logs_enabled, custom_event)
            VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET 
                otlp_endpoint = excluded.otlp_endpoint,
                metrics_enabled = excluded.metrics_enabled,
                logs_enabled = excluded.logs_enabled,
                custom_event = excluded.custom_event;
        """, (otlp_endpoint, metrics_enabled, logs_enabled, custom_event))

        conn.commit()

        st.session_state.configured = True

def get_f1_player(hostname):
    r = get_redis_connection()
    
    name = r.hget(f"f1:player:{hostname}", "player_name") if r else None
    port = r.hget(f"f1:player:{hostname}", "port") if r else None
    pid = r.hget(f"f1:player:{hostname}", "listener_pid") if r else None

    return name, port, pid


def update_player(player_name, hostname):
    player_name = player_name.rstrip()
    r = get_redis_connection()
    if r:
        r.hset(f"f1:player:{hostname}", "player_name", player_name)
        r.hset(f"f1:player:{hostname}", "last_updated", datetime.datetime.now().isoformat())

    execute_query(
        "UPDATE players SET player_name = ? WHERE hostname = ?",
        (player_name, hostname),
    )

    # Clear race completion status when new player is entered
    clear_race_completion_status(hostname)

    logging.info("APP: Player updated: " + player_name + " on " + hostname)
    
    if st.session_state.debug:
        players = execute_query("SELECT * FROM players", fetch=True)
        st.session_state.current_players = str(players)


def check_process_alive(pid):
    """Check if a process with given PID is still running."""
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def get_process_memory(pid):
    """Get memory usage of a process in MB."""
    if pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        memory_info = process.memory_info()
        return memory_info.rss / (1024 * 1024)  # Convert bytes to MB
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def cleanup_dead_processes():
    """Remove PIDs from database for processes that are no longer running."""
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT hostname, listener_pid FROM players WHERE listener_pid > 0")
            players_with_pids = cursor.fetchall()

            dead_processes = []
            for hostname, pid in players_with_pids:
                if not check_process_alive(pid):
                    dead_processes.append(hostname)
                    logging.info(f"APP: Process {pid} for {hostname} is no longer running. Cleaning up.")

            if dead_processes:
                cursor.executemany(
                    "UPDATE players SET listener_pid = 0 WHERE hostname = ?",
                    [(hostname,) for hostname in dead_processes]
                )
                conn.commit()
                logging.info(f"APP: Cleaned up {len(dead_processes)} dead processes from database.")

    except Exception as e:
        logger.error(f"Error cleaning up dead processes: {e}")


def get_listeners_status():
    """Get the actual status of listeners from the database and verify they're running."""
    cleanup_dead_processes()  # Clean up any dead processes first

    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM players WHERE listener_pid > 0")
            active_count = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM players")
            total_count = cursor.fetchone()[0]
            
            return active_count > 0, active_count, total_count
    except Exception as e:
        logger.error(f"Error getting listeners status: {e}")
        return False, 0, 0


def start_all_collectors():
    """Starts collector processes for all players."""
    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM players")
            players = cursor.fetchall()
            
        started_count = 0
        for player in players:
            hostname = player["hostname"]
            
            # Check if process is actually running
            if player["listener_pid"] > 0 and check_process_alive(player["listener_pid"]):
                logger.warning(f"Listener already running for: {hostname} (PID: {player['listener_pid']})")
                continue
            
            # If PID exists but process is dead, or no PID, start new process
            if player["listener_pid"] > 0:
                logging.info(f"APP: Previous process {player['listener_pid']} for {hostname} is dead, starting new one")
            
            try:
                process = subprocess.Popen(
                    [sys.executable, "collector.py", "--hostname", hostname, "--playback", str(st.session_state.playback)],
                     stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE
                )

                # Update PID in database
                with sqlite3.connect(DATABASE) as update_conn:
                    update_cursor = update_conn.cursor()
                    update_cursor.execute(
                        "UPDATE players SET listener_pid = ? WHERE hostname = ?",
                        (process.pid, hostname)
                    )
                    update_conn.commit()

                logging.info(f"APP: Started PID: {process.pid} for: {hostname}")
                started_count += 1
                
            except Exception as e:
                logger.error(f"Error starting listener for {hostname}: {e}")
                
        logging.info(f"APP: Started {started_count} new listeners.")
        return started_count
        
    except Exception as e:
        logger.error(f"Error starting all listeners: {e}")
        return 0


def stop_all_collectors():
    """Stops all running collector processes."""
    try:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT hostname, listener_pid FROM players WHERE listener_pid > 0")
            active_players = cursor.fetchall()
            
        stopped_count = 0
        hostnames_to_reset = []
        
        for hostname, pid in active_players:
            # Stop the process
            if check_process_alive(pid):
                try:
                    process = psutil.Process(pid)
                    process.terminate()  # Graceful termination
                    logging.info(f"APP: Stopped PID: {pid} for {hostname}")
                except psutil.NoSuchProcess:
                    logger.warning(f"Process {pid} for {hostname} already terminated.")
                except Exception as e:
                    logger.error(f"Error stopping process {pid} for {hostname}: {e}")
            else:
                logging.info(f"APP: Process {pid} for {hostname} was already stopped.")

            hostnames_to_reset.append(hostname)
            logging.info(f"APP: Stopped listener for {hostname}")
            stopped_count += 1
        
        # Batch update all PIDs to 0 in a single transaction
        if hostnames_to_reset:
            with sqlite3.connect(DATABASE) as update_conn:
                update_cursor = update_conn.cursor()
                update_cursor.executemany(
                    "UPDATE players SET listener_pid = 0 WHERE hostname = ?", 
                    [(hostname,) for hostname in hostnames_to_reset]
                )
                update_conn.commit()
                
        logging.info(f"APP: Stopped {stopped_count} listeners.")
        return stopped_count
        
    except Exception as e:
        logger.error(f"Error stopping all listeners: {e}")
        return 0


def total_rigs():
    if st.session_state.configured is True:
        try:
            with sqlite3.connect(DATABASE) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM players")
                result = cursor.fetchone()
                count = result[0] if result else 0
                return max(count, 1)
                #return total_rigs[0] if total_rigs else 1
        except sqlite3.Error as e:
            logger.error(f"Database error: {e}")
            return 1  # Return 1 in case of error
    else:
        # Default to 1 rig if not configured
        return 1

@st.fragment(run_every=1)
def current_speed(rig, is_running):
    redis_client = get_redis_connection()
    if redis_client and is_running:
        current_speed, current_lap_num, track_id = get_speed_lap_from_redis(rig)

        if current_speed is not None:
            speed_mph = current_speed * 0.621371
            speed = f"{speed_mph:.0f}"
            col1, col2, col3 = st.columns([0.7, 0.7, 2], gap="small")
            with col1:
                st.metric(
                    label="Speed (mph)",
                    value=f"{speed if current_speed is not None else '--'}",
                )
            with col2:
                st.metric(
                    label="Current Lap",
                    value=f"{current_lap_num if current_lap_num is not None else '--'}",
                )
            with col3:
                st.metric(
                    label="Track",
                    value=f"{st.secrets['TRACKS'].get(str(track_id), 'Unknown') if track_id is not None else '--'}",
                )

@st.fragment(run_every=1.0)
def rig_status(rig, port, is_running, pid=None):
    r = get_redis_connection()
    if not r:
        st.error("Redis client not initialized")
        return
    # if redis_client is None:
    #     st.error("Redis client not initialized")
    #     return
    # Single Redis pipeline for efficiency
    else:
        pipe = r.pipeline()
        pipe.hgetall(f"f1:{rig}:udp_status")
        pipe.exists(f"f1:{rig}:last_seen")
        status_data, udp_active = pipe.execute()
    
    # Check race completion status
    race_complete, completion_time = get_race_completion_status(rig)
    
    # Get memory usage if process is running
    memory_mb = get_process_memory(pid) if is_running and pid else None
    
    # Show last packet info if available
    # if status_data:
    #     st.caption(f"Last packet: {status_data['last_packet_type']} at {status_data['last_packet_received']}")
    
    # Determine status colors and icons
    udp_status_color = "green" if udp_active else "grey"
    run_status_color = "green" if is_running else "red"
    run_icon = ":green[:material/play_circle:]" if is_running else ":red[:material/stop_circle:]"
    run_text = "**RUNNING**" if is_running else "**STOPPED**"
    
    status_line = (f":blue-badge[:material/search_hands_free: **{rig.upper()}**]&emsp;"
                   f":{udp_status_color}-badge[:material/lan: **{port}**]&emsp;"
                   f":{run_status_color}-badge[{run_icon} {run_text}]")
    
    # Add memory usage if available
    if memory_mb is not None:
        status_line += f"&emsp;:orange-badge[:material/memory: **{memory_mb:.1f} MB**]"
    
    # Add race completion indicator if race is complete
    if race_complete:
        status_line += "&emsp;:green-badge[:material/flag: **RACE COMPLETE**]"
    
    st.markdown(status_line)


def main():
    st.image("static/images/f1-2025.png", width=380)
    external_ip = get_external_ip()

    caption_parts = [f"**F1 2025 ELK on Track** Version: {VERSION}"]
    if external_ip:
        caption_parts.append(f"External IP: **{external_ip}**")

    st.caption(" | ".join(caption_parts))

    try:
        with sqlite3.connect(DATABASE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM endpoints")
            endpoint_config = cursor.fetchone()

        if endpoint_config is not None and (endpoint_config["metrics_enabled"] or endpoint_config["logs_enabled"]):
            st.session_state.configured = True
            st.session_state.otlp_endpoint = endpoint_config["otlp_endpoint"]
            st.session_state.metrics_enabled = endpoint_config["metrics_enabled"]
            st.session_state.logs_enabled = endpoint_config["logs_enabled"]
            st.session_state.custom_event = endpoint_config["custom_event"]
            if not st.session_state.custom_event or st.session_state.custom_event.strip() == "":
                st.warning("Custom event is not set!", icon=":material/warning:")
        else:
            st.warning("No endpoints enabled!", icon=":material/warning:")
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        st.warning("No endpoints enabled!", icon=":material/warning:")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        st.warning("An unexpected error occurred!", icon=":material/warning:")
    
    with st.sidebar.form("o11y", border=False):
        with st.expander("**Configuration**", expanded=False):
            num_rigs = st.number_input("Rigs", min_value=1, max_value=4, value=total_rigs())
            custom_event = st.text_input("Event Name", value=st.session_state.custom_event, help="The Event Name is attached to all telemetry as a resource attribute")

        with st.expander("**OTEL Endpoint**"):
            otlp_endpoint = st.text_input("OTLP Endpoint URL", value=st.session_state.otlp_endpoint)
            metrics_enabled = st.checkbox("Enable Metrics Export", value=st.session_state.metrics_enabled)
            logs_enabled = st.checkbox("Enable Logs Export", value=st.session_state.logs_enabled)

        st.session_state.playback = st.selectbox("Playback Mode", options=["False", "True"], index=0)
        st.session_state.debug = st.checkbox("Enable Debug Mode", value=st.session_state.debug)
        submit_button = st.form_submit_button("Save Configuration", type="primary", use_container_width=True)

        if st.session_state.configured:
            status_col1, status_col2 = st.columns([1, 2], gap=None)
            listeners_running, active_count, total_count = get_listeners_status()
            
            with status_col1:
                redis_client = get_redis_connection()
                if redis_client:
                    st.badge("Redis", icon=":material/check_circle:", color="green", width="stretch")
                else:
                    st.badge("Redis", icon=":material/error:", color="red", width="stretch")

            with status_col2:
                if active_count > 0:
                    st.badge(f"**{active_count}/{total_count}** collectors running", icon=":material/check_circle:", color="green", width="stretch")
                else:
                    st.badge("No collectors running", icon=":material/info:", color="orange", width="stretch")

        if submit_button:
            stopped = stop_all_collectors()
            if stopped > 0:
                st.toast(f"Stopped {stopped} collectors for reconfiguration", icon=":material/stop_circle:")
                time.sleep(1)

            st.session_state.configured = True
            if "master_toggle" in st.session_state:
                st.session_state.master_toggle = False
            create_db_tables()
            init_db(num_rigs, otlp_endpoint, metrics_enabled, logs_enabled, custom_event)
            st.rerun()


    
# Show status information and global controls
    col1, col2 = st.columns([1, 1], gap=None)
    
    # Get current listener status
    listeners_running, active_count, total_count = get_listeners_status()
    
    with col1:
        # Master toggle for all collectors
        master_toggle = st.toggle(
            "Master Control",
            value=listeners_running,
            key="master_toggle",
            help="Start/Stop all collectors"
        )
        
        # Handle master toggle state changes
        if master_toggle and not listeners_running:
            # Toggle turned on but collectors not running - start all
            started = start_all_collectors()
            st.rerun()
        elif not master_toggle and listeners_running:
            # Toggle turned off but collectors still running - stop all
            stopped = stop_all_collectors()
            st.rerun()

    if st.session_state.configured is True:
        with sqlite3.connect(DATABASE) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM players")
            players = cursor.fetchall()
    else:
        players = []

    if len(players) <= 2:
        # Single column layout for 1-2 players
        n_cols = 1
        driver_placeholder = st.empty()
        
        # For 1-2 players, create rows equal to number of players
        n_rows = len(players)
        rows = [st.container() for _ in range(n_rows)]
        cols_per_row = [r.columns(n_cols) for r in rows]
        cols = [column for row in cols_per_row for column in row]
    else:
        # Original logic for 3+ players (2 columns)
        n_cols = 2
        driver_placeholder = st.empty()
        
        n_rows = 1 + len(players) // int(n_cols)
        rows = [st.container() for _ in range(n_rows)]
        cols_per_row = [r.columns(n_cols) for r in rows]
        cols = [column for row in cols_per_row for column in row]

    with driver_placeholder.container():
        for i, r in enumerate(players):
            with cols[i]:
                # Create a bordered container for each driver
                with st.container(border=True):
                    # Check if process is actually running
                    is_running = r[4] > 0 and check_process_alive(r[4])
                    rig_status(r[2], r[3], is_running, r[4])

                    if listeners_running:
                        current_speed(r[2], is_running)

                    # Driver name form
                    with st.form(f"driver_form_{r[2]}", clear_on_submit=True, border=False):
                        driver_col1, driver_col2 = st.columns([4, 1])
                        player = get_f1_player(f"{r[2]}")
                        with driver_col1:
                            player = st.text_input("Current Driver:", placeholder=r[1], key=f"driver_{r[2]}", label_visibility="collapsed")
                        with driver_col2:
                            form_submit = st.form_submit_button(f"Update", type="primary", use_container_width=True)
                    if form_submit and player:
                        update_player(player, r[2])
                        st.rerun()

    col1, col2 = st.columns([1, 1], gap="small")
    with col1:
        if st.session_state.debug is True:
            st.write("**Session State:**")
            st.write(st.session_state)
    with col2:
    # Show current process status
        if st.session_state.configured and st.session_state.debug is True:
            st.write("**Current Process Status:**")
            try:
                with sqlite3.connect(DATABASE) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT hostname, listener_pid FROM players")
                    db_players = cursor.fetchall()
                    
                status_info = []
                for hostname, pid in db_players:
                    is_alive = check_process_alive(pid) if pid > 0 else False
                    status_info.append({
                        "hostname": hostname,
                        "pid": pid,
                        "running": is_alive
                    })
                st.write(status_info)
            except Exception as e:
                st.error(f"Error checking process status: {e}")

pages = {
    "F1 2025 - ELK on Track": [
    st.Page(main, title="Collector"),
    st.Page("streamer.py", title="Streamer"),
    ]
}
pg = st.navigation(pages, position="top")
pg.run()
