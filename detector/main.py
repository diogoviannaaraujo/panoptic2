#!/usr/bin/env python3
"""
RTSP Motion Detection Pipeline - Main Entry Point

This application connects to RTSP streams exposed by MediaMTX, segments them
into MPEG-TS files stored in tmpfs, and performs real-time motion detection.

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                         Stream Manager                              │
    │  - Discovers streams via MediaMTX API                               │
    │  - Creates/manages StreamPipeline instances                         │
    │  - Monitors health and handles reconnection                         │
    └─────────────────────────────────────────────────────────────────────┘
                                    │
           ┌────────────────────────┼────────────────────────┐
           ▼                        ▼                        ▼
    ┌─────────────┐          ┌─────────────┐          ┌─────────────┐
    │  Pipeline 1 │          │  Pipeline 2 │          │  Pipeline N │
    │  (cam01)    │          │  (cam02)    │          │  (camN)     │
    └─────────────┘          └─────────────┘          └─────────────┘
           │                        │                        │
           ▼                        ▼                        ▼
    ┌─────────────┐          ┌─────────────┐          ┌─────────────┐
    │ GStreamer   │          │ GStreamer   │          │ GStreamer   │
    │ tee branch: │          │ tee branch: │          │ tee branch: │
    │ - TS output │          │ - TS output │          │ - TS output │
    │ - Motion    │          │ - Motion    │          │ - Motion    │
    └─────────────┘          └─────────────┘          └─────────────┘

Configuration (via environment variables):
    MEDIAMTX_HOST        - MediaMTX hostname (default: mediamtx)
    MEDIAMTX_API_PORT    - MediaMTX API port (default: 9997)
    MEDIAMTX_RTSP_PORT   - MediaMTX RTSP port (default: 8554)
    RTSP_STREAMS         - Comma-separated stream list (overrides discovery)
    SEGMENT_OUTPUT_DIR   - Output directory for TS files (default: /dev/shm/segments)
    SEGMENT_DURATION     - Segment duration in seconds (default: 5)
    MAX_SEGMENTS         - Max segments per stream (default: 20)
    RECORDINGS_DIR       - Directory for persisted recordings (default: /recordings)
    PRE_ROLL_SECONDS     - Seconds of video before motion to include (default: 5)
    POST_ROLL_SECONDS    - Seconds after last motion before stopping (default: 5)
    MOTION_PIXEL_THRESHOLD   - Pixel change threshold 0-255 (default: 25)
    MOTION_AREA_THRESHOLD    - Minimum % of frame changed (default: 1.0)
    MOTION_COOLDOWN_FRAMES   - Frames between motion reports (default: 30)
    MOTION_DETECTION_WIDTH   - Frame width for detection (default: 320)
    MOTION_DETECTION_HEIGHT  - Frame height for detection (default: 240)
    DISCOVERY_INTERVAL   - Stream discovery interval in seconds (default: 30)
    VERBOSE              - Enable verbose logging (default: false)

Pre-roll/Post-roll Recording:
    When motion is detected, a recording session starts:
    - PRE_ROLL_SECONDS of closed segments before motion are copied
    - Recording continues while motion occurs
    - Recording stops POST_ROLL_SECONDS after the last motion event
    
    Output structure:
        RECORDINGS_DIR/<stream_key>/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/segment_files.ts
    
    Log messages:
        [MOTION]  stream=<stream_id> file=<current_segment>   - motion detected
        [SESSION] stream=<stream_id> Started recording ...    - session begins
        [SESSION] stream=<stream_id> Ended recording ...      - session ends

Usage:
    python main.py
    
    # Or with environment variables:
    MEDIAMTX_HOST=192.168.1.100 RTSP_STREAMS=cam01,cam02 python main.py
"""

import signal
import sys
import threading

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

from stream_manager import StreamManager
from config import config


# Global state for signal handling
_shutdown_event = threading.Event()
_main_loop: GLib.MainLoop = None
_stream_manager: StreamManager = None


def signal_handler(signum, frame):
    """
    Handle SIGINT/SIGTERM for graceful shutdown.
    
    This handler:
    1. Sets the shutdown event to notify threads
    2. Stops the GLib main loop
    3. Initiates stream manager shutdown
    """
    signal_name = signal.Signals(signum).name
    print(f"\n[INFO] Received {signal_name}, initiating graceful shutdown...")
    
    _shutdown_event.set()
    
    if _main_loop:
        _main_loop.quit()


def print_banner():
    """Print application startup banner."""
    print("=" * 60)
    print("  RTSP Motion Detection Pipeline")
    print("  GStreamer-based multi-stream processor")
    print("=" * 60)
    print()


def print_config():
    """Print current configuration."""
    print("[CONFIG] Current settings:")
    print(f"  MediaMTX API:     {config.mediamtx.api_url}")
    print(f"  RTSP Base URL:    {config.mediamtx.rtsp_base_url}")
    print(f"  Output Directory: {config.segment.output_dir}")
    print(f"  Segment Duration: {config.segment.segment_duration}s")
    print(f"  Max Segments:     {config.segment.max_segments}")
    print(f"  Recordings Dir:   {config.recording.recordings_dir}")
    print(f"  Pre-roll:         {config.recording.pre_roll_seconds}s")
    print(f"  Post-roll:        {config.recording.post_roll_seconds}s")
    print(f"  Motion Threshold: {config.motion.pixel_threshold} (pixel), {config.motion.area_threshold}% (area)")
    print(f"  Detection Size:   {config.motion.detection_width}x{config.motion.detection_height}")
    print(f"  Discovery Int.:   {config.discovery_interval}s")
    if config.manual_streams:
        print(f"  Manual Streams:   {', '.join(config.manual_streams)}")
    print()


def main():
    """Main entry point for the motion detection pipeline."""
    global _main_loop, _stream_manager
    
    print_banner()
    
    # Initialize GStreamer
    print("[INFO] Initializing GStreamer...")
    Gst.init(None)
    
    # Print configuration
    print_config()
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Create the GLib main loop
    _main_loop = GLib.MainLoop()
    
    # Create and start the stream manager
    print("[INFO] Initializing stream manager...")
    _stream_manager = StreamManager()
    _stream_manager.start()
    
    print("[INFO] Motion detection pipeline running. Press Ctrl+C to stop.")
    print("-" * 60)
    
    try:
        # Run the main loop
        _main_loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        # Graceful shutdown
        print("\n[INFO] Shutting down...")
        
        if _stream_manager:
            _stream_manager.stop()
        
        print("[INFO] Shutdown complete.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

