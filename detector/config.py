"""
Configuration module for the RTSP Motion Detection Pipeline.

This module centralizes all configuration parameters including:
- MediaMTX API connection settings
- RTSP stream defaults
- Segment storage paths
- Motion detection thresholds
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MediaMTXConfig:
    """Configuration for MediaMTX server connection."""
    
    # MediaMTX API endpoint for stream discovery
    api_host: str = field(default_factory=lambda: os.getenv("MEDIAMTX_HOST", "mediamtx"))
    api_port: int = field(default_factory=lambda: int(os.getenv("MEDIAMTX_API_PORT", "9997")))
    
    # RTSP connection settings
    rtsp_port: int = field(default_factory=lambda: int(os.getenv("MEDIAMTX_RTSP_PORT", "8554")))
    
    @property
    def api_url(self) -> str:
        """Full URL for MediaMTX API."""
        return f"http://{self.api_host}:{self.api_port}"
    
    @property
    def rtsp_base_url(self) -> str:
        """Base URL for RTSP streams."""
        return f"rtsp://{self.api_host}:{self.rtsp_port}"


@dataclass
class SegmentConfig:
    """Configuration for MPEG-TS segment output."""
    
    # Output directory for TS segments (should be tmpfs mounted)
    output_dir: str = field(default_factory=lambda: os.getenv("SEGMENT_OUTPUT_DIR", "/dev/shm/segments"))
    
    # Segment duration in seconds
    segment_duration: int = field(default_factory=lambda: int(os.getenv("SEGMENT_DURATION", "5")))
    
    # Maximum number of segments to keep per stream (0 = unlimited)
    max_segments: int = field(default_factory=lambda: int(os.getenv("MAX_SEGMENTS", "20")))


@dataclass
class MotionConfig:
    """Configuration for motion detection parameters."""
    
    # Pixel difference threshold (0-255) for considering a pixel as "changed"
    pixel_threshold: int = field(default_factory=lambda: int(os.getenv("MOTION_PIXEL_THRESHOLD", "25")))
    
    # Percentage of frame that must change to trigger motion event (0.0-100.0)
    area_threshold: float = field(default_factory=lambda: float(os.getenv("MOTION_AREA_THRESHOLD", "1.0")))
    
    # Minimum frames between motion reports to avoid spam
    cooldown_frames: int = field(default_factory=lambda: int(os.getenv("MOTION_COOLDOWN_FRAMES", "30")))
    
    # Resolution to scale frames for motion detection (reduces CPU usage)
    detection_width: int = field(default_factory=lambda: int(os.getenv("MOTION_DETECTION_WIDTH", "320")))
    detection_height: int = field(default_factory=lambda: int(os.getenv("MOTION_DETECTION_HEIGHT", "240")))


@dataclass
class RecordingConfig:
    """Configuration for motion-triggered recording with pre/post roll."""
    
    # Directory for persisted recordings (should be a host-mounted volume in Docker)
    recordings_dir: str = field(default_factory=lambda: os.getenv("RECORDINGS_DIR", "/recordings"))
    
    # Pre-roll: seconds of video to include before motion starts
    pre_roll_seconds: int = field(default_factory=lambda: int(os.getenv("PRE_ROLL_SECONDS", "5")))
    
    # Post-roll: seconds of no motion before stopping recording
    post_roll_seconds: int = field(default_factory=lambda: int(os.getenv("POST_ROLL_SECONDS", "5")))


@dataclass
class DatabaseConfig:
    """Configuration for PostgreSQL database connection."""
    
    host: str = field(default_factory=lambda: os.getenv("DB_HOST", "db"))
    port: int = field(default_factory=lambda: int(os.getenv("DB_PORT", "5432")))
    name: str = field(default_factory=lambda: os.getenv("DB_NAME", "panoptic"))
    user: str = field(default_factory=lambda: os.getenv("DB_USER", "user"))
    password: str = field(default_factory=lambda: os.getenv("DB_PASSWORD", "password"))


@dataclass
class AppConfig:
    """Main application configuration."""
    
    mediamtx: MediaMTXConfig = field(default_factory=MediaMTXConfig)
    segment: SegmentConfig = field(default_factory=SegmentConfig)
    motion: MotionConfig = field(default_factory=MotionConfig)
    recording: RecordingConfig = field(default_factory=RecordingConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    
    # Manual stream list (comma-separated stream names, overrides API discovery)
    manual_streams: Optional[List[str]] = field(default_factory=lambda: _parse_streams())
    
    # Polling interval for stream discovery (seconds)
    discovery_interval: int = field(default_factory=lambda: int(os.getenv("DISCOVERY_INTERVAL", "30")))
    
    # Enable verbose logging
    verbose: bool = field(default_factory=lambda: os.getenv("VERBOSE", "false").lower() == "true")


def _parse_streams() -> Optional[List[str]]:
    """Parse comma-separated stream names from environment variable."""
    streams_env = os.getenv("RTSP_STREAMS", "")
    if streams_env:
        return [s.strip() for s in streams_env.split(",") if s.strip()]
    return None


# Global configuration instance
config = AppConfig()

