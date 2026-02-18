import json
import os
import time
from datetime import datetime
from typing import Any, Dict, List

import altair as alt
import pandas as pd
import redis
import streamlit as st

# Initialize session state
if 'data_history' not in st.session_state:
    st.session_state.data_history = {}  # Change to dict to store per-rig data
if 'running' not in st.session_state:
    st.session_state.running = False
if 'current_rig' not in st.session_state:
    st.session_state.current_rig = None

# O11y metrics configuration
O11Y_METRICS = {
    "throttle": {"unit": "%", "color": "#32CD32", "icon": "radio_button_checked"},
    "engine_rpm": {"unit": "RPM", "color": "#FF8C00", "icon": "settings"},
    "speed": {"unit": "mph", "color": "#FF8C00", "icon": "speed"},
    "current_lap_num": {"unit": "", "color": "#00D4FF", "icon": "flag"},
    "current_lap_time_in_ms": {"unit": "ms", "color": "#00D4FF", "icon": "timer"},
    "track_temperature": {"unit": "°C", "color": "#FF6B35", "icon": "thermostat"},
    "brake": {"unit": "%", "color": "#FF0000", "icon": "radio_button_checked"},
    "engine_temperature": {"unit": "°C", "color": "#FF8C00", "icon": "thermostat"},
    "gear": {"unit": "", "color": "#FF8C00", "icon": "tune"},
    "sector": {"unit": "", "color": "#00BFFF", "icon": "sports_score"},
    "car_position": {"unit": "", "color": "#00BFFF", "icon": "emoji_events"},
    "air_temperature": {"unit": "°C", "color": "#FF6B35", "icon": "thermostat"},
    "brakes_temperature1": {"unit": "°C", "color": "#FF4444", "icon": "thermostat"},
    "brakes_temperature2": {"unit": "°C", "color": "#FF4444", "icon": "thermostat"},
    "brakes_temperature3": {"unit": "°C", "color": "#FF4444", "icon": "thermostat"},
    "brakes_temperature4": {"unit": "°C", "color": "#FF4444", "icon": "thermostat"},
    "tyres_surface_temperature1": {"unit": "°C", "color": "#1E90FF", "icon": "thermostat"},
    "tyres_surface_temperature2": {"unit": "°C", "color": "#1E90FF", "icon": "thermostat"},
    "tyres_surface_temperature3": {"unit": "°C", "color": "#1E90FF", "icon": "thermostat"},
    "tyres_surface_temperature4": {"unit": "°C", "color": "#1E90FF", "icon": "thermostat"},
}

class RedisStreamer:
    def __init__(self, host=None, port=6379, db=0, password=None):
        if host is None:
            host = os.getenv("REDIS_HOST", "redis")
        try:
            self.redis_client = redis.Redis(
                host=host, 
                port=port, 
                db=db, 
                password=password,
                decode_responses=True
            )
            # Test connection
            self.redis_client.ping()
            self.connected = True
        except Exception as e:
            self.connected = False
            self.error = str(e)
    
    def get_key_value(self, key: str) -> Any:
        try:
            value = self.redis_client.get(key)
            if value:
                try:
                    # Try to parse as JSON
                    return json.loads(value)  # type: ignore
                except json.JSONDecodeError:
                    # Return as string if not JSON
                    return value
            return None
        except Exception as e:
            st.error(f"Error getting key {key}: {str(e)}")
            return None
    
    def get_hash_data(self, key: str) -> Dict:
        try:
            return self.redis_client.hgetall(key)  # type: ignore
        except Exception as e:
            st.error(f"Error getting hash {key}: {str(e)}")
            return {}
    
    def get_all_keys(self, pattern="*") -> List[str]:
        """Get all keys matching pattern"""
        try:
            return self.redis_client.keys(pattern)  # type: ignore
        except Exception as e:
            st.error(f"Error getting keys: {str(e)}")
            return []

def get_running_rigs(streamer: RedisStreamer) -> List[str]:
    try:
        # Look for all f1:rig_*:metrics keys
        rig_keys = streamer.get_all_keys("f1:rig_*:metrics")
        running_rigs = []
        
        for key in rig_keys:
            # Extract rig name from key (e.g., "f1:rig_1:metrics" -> "rig_1")
            parts = key.split(":")
            if len(parts) >= 2:
                rig_name = parts[1]
                # Check if the rig has recent data
                metrics_data = streamer.get_hash_data(key)
                if metrics_data and len(metrics_data) > 0:
                    running_rigs.append(rig_name)
        
        return sorted(running_rigs)
    except Exception as e:
        st.error(f"Error getting running rigs: {str(e)}")
        return []

def get_temperature_color(metric_name: str, temperature: float) -> str:
    """Get color based on temperature range for brake and tyre temperatures"""
    # Temperature ranges and corresponding colors
    TEMP_RANGES = {
        'brakes_temperature': [
            (200, "#00FF00"),   # Green (cool)
            (400, "#90EE90"),   # Light green
            (600, "#FFFF00"),   # Yellow (warm)
            (800, "#FFA500"),   # Orange (hot)
            (float('inf'), "#FF0000")  # Red (very hot)
        ],
        'tyres_temperature': [
            (85, "#00FF00"),    # Green (cool)
            (95, "#90EE90"),    # Light green
            (105, "#FFFF00"),   # Yellow (warm)
            (115, "#FFA500"),   # Orange (hot)
            (float('inf'), "#FF0000")  # Red (too hot)
        ]
    }
    
    # Determine metric type
    if metric_name.startswith('brakes_temperature'):
        ranges = TEMP_RANGES['brakes_temperature']
    elif metric_name.startswith('tyres_') and 'temperature' in metric_name:
        ranges = TEMP_RANGES['tyres_temperature']
    else:
        return "#FFFFFF"  # Default color for other metrics
    
    # Find appropriate color based on temperature
    for threshold, color in ranges:
        if temperature <= threshold:
            return color
    
    return "#FFFFFF"  # Fallback

def format_metric_name(metric_name: str) -> str:
    """Format metric name for display"""
    # Wheel position mapping
    WHEEL_POSITIONS = {
        '1': 'Rear Left',
        '2': 'Rear Right', 
        '3': 'Front Left',
        '4': 'Front Right'
    }
    
    # Special metric name mappings
    SPECIAL_NAMES = {
        'current_lap_time_in_ms': 'Lap Time',
        'current_lap_num': 'Lap Number',
        'engine_rpm': 'Engine RPM',
        'engine_temperature': 'Engine Temp',
        'track_temperature': 'Track Temp',
        'air_temperature': 'Air Temp',
    }
    
    # Check for special names first
    if metric_name in SPECIAL_NAMES:
        return SPECIAL_NAMES[metric_name]
    
    # Handle wheel-based metrics
    if metric_name.startswith(('tyres_', 'brakes_')) and metric_name[-1].isdigit():
        wheel_num = metric_name[-1]
        return WHEEL_POSITIONS.get(wheel_num, wheel_num)
    
    # Default formatting
    return metric_name.replace('_', ' ').title()

def _format_lap_time(ms_value: float) -> tuple[str, str]:
    """Convert milliseconds to appropriate time format"""
    if ms_value < 60000:  # Less than 1 minute
        seconds = ms_value / 1000
        return f"{seconds:.3f}", "s"
    else:  # 1 minute or more
        minutes = int(ms_value // 60000)
        remaining_ms = ms_value % 60000
        seconds = int(remaining_ms // 1000)
        milliseconds = int(remaining_ms % 1000)
        return f"{minutes}:{seconds:02d}.{milliseconds:03d}", ""

def _format_metric_value(numeric_value: float, unit: str) -> tuple[str, str]:
    """Format metric value based on unit type"""
    # Unit-specific formatting rules
    FORMATTERS = {
        "RPM": lambda v: (f"{v:,.0f}", unit),
        "°C": lambda v: (f"{v:.0f}", unit),
        "%": lambda v: (f"{v:.0f}", unit),
        "mph": lambda v: (f"{v * 0.621371:.0f}", unit),  # Convert km/h to mph
    }
    
    # Special case for lap times (always format ms as time)
    if unit == "ms":
        return _format_lap_time(numeric_value)
    
    # Use specific formatter or default
    formatter = FORMATTERS.get(unit, lambda v: (f"{v:.0f}", unit))
    return formatter(numeric_value)

def create_metric_card(metric_name: str, value: Any, config: Dict):
    """Create a styled metric card"""
    try:
        # Convert value to float for numeric display
        numeric_value = float(value)
        
        # Special handling for throttle and brake - multiply by 100 for percentage
        if metric_name in ["throttle", "brake"]:
            numeric_value = numeric_value * 100
        
        # Format the display value
        display_value, unit = _format_metric_value(numeric_value, config["unit"])
        
        # Create metric display
        formatted_name = format_metric_name(metric_name)
        
        # Get dynamic color for temperature metrics
        dynamic_color = get_temperature_color(metric_name, numeric_value)
        display_color = dynamic_color if dynamic_color != "#FFFFFF" else config["color"]
        
        return {
            "name": formatted_name,
            "value": display_value,
            "unit": unit,
            "icon": config["icon"],
            "color": display_color,
            "raw_value": numeric_value
        }
    
    except (ValueError, TypeError):
        # Handle non-numeric values
        return {
            "name": format_metric_name(metric_name),
            "value": str(value),
            "unit": "",
            "icon": config.get("icon", "bar_chart"),
            "color": config.get("color", "#FFFFFF"),
            "raw_value": 0
        }

def render_metric_card_html(card: Dict, font_size_icon: int = 20, font_size_value: int = 20, font_size_name: int = 12, unit_size: int = 14) -> str:
    """Generate HTML for a metric card with configurable font sizes"""
    return f"""
    <div style="
        background: linear-gradient(135deg, {card['color']}50, {card['color']}10);
        border: 1px solid {card['color']}40;
        border-radius: 10px;
        padding: 10px;
        text-align: center;
        margin: 3px 0;
    ">
        <div style="font-size: {font_size_icon}px; margin-bottom: 5px;">
            <span class="material-icons" style="color: {card['color']};">{card['icon']}</span>
        </div>
        <div style="font-size: {font_size_value}px; font-weight: bold; color: {card['color']};">
            {card['value']} <span style="font-size: {unit_size}px;">{card['unit']}</span>
        </div>
        <div style="font-size: {font_size_name}px; color: #888; margin-top: 5px;">
            {card['name']}
        </div>
    </div>
    """

def plot_time_series(data_history: List[Dict], metric: str):
    """Create time series plot for a specific metric using Altair"""
    if not data_history or len(data_history) < 2:
        return None
    
    try:
        df = pd.DataFrame(data_history)
        if 'timestamp' not in df.columns or metric not in df.columns:
            return None
            
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        config = O11Y_METRICS.get(metric, {"color": "#FFFFFF"})
        
        if metric in ['throttle', 'brake']:
            # Convert throttle/brake to percentage
            df[metric] = df[metric].astype(float) * 100
        
        # Configure y-axis based on metric type
        if metric in ['throttle', 'brake']:
            y_encoding = alt.Y(f'{metric}:Q',
                             title=f"{format_metric_name(metric)} ({config.get('unit', '')})",
                             scale=alt.Scale(domain=[0, 100]))
        else:
            y_encoding = alt.Y(f'{metric}:Q',
                             title=f"{format_metric_name(metric)} ({config.get('unit', '')})")
        
        # Create optimized chart
        chart = alt.Chart(df).mark_line(
            strokeWidth=2,
            color=config["color"],
            interpolate='monotone'
        ).encode(
            x=alt.X('timestamp:T', 
                   title=None,
                   axis=alt.Axis(labels=False, ticks=False, grid=False, domain=False)),
            y=y_encoding,
            tooltip=[
                alt.Tooltip('timestamp:T', title='Time', format='%H:%M:%S'),
                alt.Tooltip(f'{metric}:Q', title=format_metric_name(metric), format='.0f')
            ]
        ).properties(
            height=250,
            title=alt.TitleParams(
                text=format_metric_name(metric),
                fontSize=14,
                anchor='start'
            )
        ).configure_view(
            strokeWidth=0
        ).configure_axis(
            grid=False
        )
        
        return chart
        
    except Exception:
        return None


def main():
    # Add Material Icons CSS
    st.markdown("""
    <link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">
    <style>
    .material-icons {
        font-family: 'Material Icons';
        font-weight: normal;
        font-style: normal;
        font-size: 24px;
        line-height: 1;
        letter-spacing: normal;
        text-transform: none;
        display: inline-block;
        white-space: nowrap;
        word-wrap: normal;
        direction: ltr;
        -webkit-font-feature-settings: 'liga';
        -webkit-font-smoothing: antialiased;
        vertical-align: middle;
    }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('# <span class="material-icons">speed</span> F1 Telemetry Monitor', unsafe_allow_html=True)
    st.markdown("Real-time F1 telemetry monitoring from Redis")
    
    # Sidebar configuration
    st.sidebar.header("Configuration")
    
    # Create Redis connection
    streamer = RedisStreamer(port=6379, db=0)
    
    if not streamer.connected:
        st.error(f"Failed to connect to Redis: {streamer.error}", icon=":material/error:")
        st.stop()
    
    # Get running rigs
    running_rigs = get_running_rigs(streamer)
    
    # Rig selector
    st.sidebar.header("Rig Selection")
    if running_rigs:
        selected_rig = st.sidebar.selectbox(
            "Select Rig",
            options=running_rigs,
            index=0,
            help="Only rigs with active data are shown"
        )
        metrics_key = f"f1:{selected_rig}:metrics"
    else:
        st.sidebar.warning("No active rigs found")
        metrics_key = "f1:rig_1:metrics"  # Fallback
        selected_rig = "rig_1"
    
    # Streaming configuration
    st.sidebar.header("Streaming")
    refresh_rate = st.sidebar.slider("Refresh Rate (seconds)", 0.1, 2.0, 1.0, 0.1)
    max_data_points = st.sidebar.number_input("Max Data Points", 10, 1000, 100)
    
    # Metrics selection with grouped options
    st.sidebar.header("Metrics Display")
    show_charts = st.sidebar.checkbox("Show Time Series Charts", value=True)
    
    # Group metrics for selection
    general_metrics_list = [k for k in O11Y_METRICS.keys() if not k.startswith(('brakes_', 'tyres_'))]
    
    st.sidebar.markdown("**Select Metrics for Charts:**")
    selected_general = st.sidebar.multiselect(
        "General Metrics",
        options=general_metrics_list,
        default=["speed", "throttle"]
    )
    
    selected_metrics_for_charts = selected_general
    
    # Control buttons
    col1, col2 = st.sidebar.columns([1,1])
    
    with col1:
        if st.button("Start", icon=":material/play_arrow:"):
            st.session_state.running = True
    
    with col2:
        if st.button("Stop", icon=":material/stop:"):
            st.session_state.running = False
        
    # Main content area
    if st.session_state.running:
        placeholder = st.empty()
        
        # Check if rig has changed and reset data if needed
        if st.session_state.current_rig != selected_rig:
            st.session_state.current_rig = selected_rig
            # Initialize data history for this rig if it doesn't exist
            if selected_rig not in st.session_state.data_history:
                st.session_state.data_history[selected_rig] = []
        
        while st.session_state.running:
            # Get metrics data from Redis hash
            metrics_data = streamer.get_hash_data(metrics_key)
            
            if metrics_data:
                # Filter out metadata fields
                telemetry_data = {k: v for k, v in metrics_data.items() 
                                if k in O11Y_METRICS}
                
                if telemetry_data:
                    # Initialize rig data if it doesn't exist
                    if selected_rig not in st.session_state.data_history:
                        st.session_state.data_history[selected_rig] = []
                    
                    # Add timestamp
                    data_point = {
                        'timestamp': datetime.now().isoformat(),
                        **telemetry_data
                    }
                    
                    st.session_state.data_history[selected_rig].append(data_point)
                    
                    # Keep only recent data points for this rig
                    if len(st.session_state.data_history[selected_rig]) > max_data_points:
                        st.session_state.data_history[selected_rig] = st.session_state.data_history[selected_rig][-max_data_points:]
                    
                    with placeholder.container():
                        # Group metrics by category in O11Y_METRICS order
                        general_metrics = []
                        brake_metrics = []
                        tyre_metrics = []
                        
                        # Process metrics in the order they appear in O11Y_METRICS
                        for metric_name in O11Y_METRICS.keys():
                            if metric_name in telemetry_data:
                                value = telemetry_data[metric_name]
                                config = O11Y_METRICS[metric_name]
                                card = create_metric_card(metric_name, value, config)
                                card['metric_name'] = metric_name  # Keep original name for sorting
                                card['raw_redis_value'] = value  # Keep original Redis value
                                
                                if metric_name.startswith('brakes_'):
                                    brake_metrics.append(card)
                                elif metric_name.startswith('tyres_'):
                                    tyre_metrics.append(card)
                                else:
                                    general_metrics.append(card)
                        
                        # Display General Metrics
                        if general_metrics:
                            cols_per_row = 6
                            rows = [general_metrics[i:i + cols_per_row] for i in range(0, len(general_metrics), cols_per_row)]
                            
                            for row in rows:
                                cols = st.columns(len(row))
                                for i, card in enumerate(row):
                                    with cols[i]:
                                        # Render metric card using shared template
                                        st.markdown(render_metric_card_html(card), unsafe_allow_html=True)

                        def render_metric_card(card):
                            return render_metric_card_html(card, font_size_icon=18, font_size_value=20, font_size_name=10, unit_size=12)

                        def render_wheel_grid(metrics, title):
                            st.markdown(f'### {title}', unsafe_allow_html=True)
                            
                            positions = {}
                            for card in metrics:
                                wheel_num = card['metric_name'][-1]
                                positions[wheel_num] = card
                            
                            front_row = st.columns(2)
                            for idx, wheel_num in enumerate(['3', '4']):
                                if wheel_num in positions:
                                    with front_row[idx]:
                                        st.markdown(render_metric_card(positions[wheel_num]), unsafe_allow_html=True)
                            
                            rear_row = st.columns(2)
                            for idx, wheel_num in enumerate(['1', '2']):
                                if wheel_num in positions:
                                    with rear_row[idx]:
                                        st.markdown(render_metric_card(positions[wheel_num]), unsafe_allow_html=True)

                        # Display Brake and Tyre Metrics in 2x2 grid layout
                        if brake_metrics or tyre_metrics:
                            brake_col, tyre_col = st.columns(2)
                            
                            if brake_metrics:
                                with brake_col:
                                    render_wheel_grid(brake_metrics, "Brakes")
                            
                            if tyre_metrics:
                                with tyre_col:
                                    surface_temps = [card for card in tyre_metrics if 'surface' in card['metric_name']]
                                    display_tyres = surface_temps if surface_temps else tyre_metrics
                                    render_wheel_grid(display_tyres, "Tyres")
                        
                        # Time series charts
                        current_rig_data = st.session_state.data_history.get(selected_rig, [])
                        if show_charts and selected_metrics_for_charts and len(current_rig_data) > 1:
                            with st.container():
                                st.subheader("Time Series Analysis")
                                
                                # Create charts in a grid
                                charts_per_row = 3
                                chart_rows = [selected_metrics_for_charts[i:i + charts_per_row] 
                                            for i in range(0, len(selected_metrics_for_charts), charts_per_row)]
                                
                                for chart_row in chart_rows:
                                    chart_cols = st.columns(len(chart_row))
                                    for i, metric in enumerate(chart_row):
                                        with chart_cols[i]:
                                            if metric in telemetry_data:
                                                chart = plot_time_series(current_rig_data, metric)
                                                if chart is not None:
                                                    st.altair_chart(chart, use_container_width=True)
                                                else:
                                                    st.info(f"No data for {format_metric_name(metric)}")
            else:
                # Only show warning once, then just indicate no data status
                with placeholder.container():
                    st.markdown("### Waiting for F1 Data...")
                    st.info(f"Monitoring `{selected_rig}` - Redis hash: `{metrics_key}`")
            time.sleep(refresh_rate)
    
    else:
        st.info("Click the play button to begin monitoring F1 rig telemetry")
        
        # Show available Redis keys when not running
        all_keys = streamer.get_all_keys("f1:*")
        if not all_keys:
            st.info("No F1 keys found in Redis. Start the F1 data collection script first.")

if __name__ == "__main__":
    main()