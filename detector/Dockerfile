FROM debian:bookworm-slim

# Install Python, GStreamer, and all required dependencies via apt
# This avoids PyGObject build issues by using system packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-gi \
    python3-gi-cairo \
    python3-numpy \
    python3-requests \
    python3-psycopg2 \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    gstreamer1.0-rtsp \
    gir1.2-gstreamer-1.0 \
    gir1.2-gst-plugins-base-1.0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy application source
COPY . .

# Create tmpfs mount point for segments (will be overridden by docker tmpfs mount)
RUN mkdir -p /dev/shm/segments

# Entry point - use unbuffered output
CMD ["python3", "-u", "main.py"]
