FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    supervisor \
    vim \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/log/supervisor \
    /var/log/streamlit \
    /var/log/api \
    /var/log/nginx

# Create and activate virtual environment
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Copy application code
COPY src/ /app/
COPY f1_telemetry/ /app/f1_telemetry/

# Copy VERSION file
COPY VERSION /app/VERSION

# Copy configuration files
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY config/nginx.conf /etc/nginx/nginx.conf

# Create telemetry data directory for replay files
RUN mkdir -p /app/telemetry_data

WORKDIR /app

# Expose ports
# 8501/tcp - Web UI (nginx -> streamlit)
# 20777-20780/udp - F1 telemetry (up to 4 rigs)
EXPOSE 8501/tcp
EXPOSE 20777/udp
EXPOSE 20778/udp
EXPOSE 20779/udp
EXPOSE 20780/udp

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
