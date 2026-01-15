"""
RTSP Motion Detection Pipeline.

A GStreamer-based Python application for:
- Connecting to multiple RTSP streams from MediaMTX
- Segmenting streams into MPEG-TS files on tmpfs
- Real-time motion detection using frame differencing
"""

__version__ = "1.0.0"

