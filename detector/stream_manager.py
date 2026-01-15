"""
Stream Manager Module.

Handles discovery, creation, and lifecycle management of multiple RTSP streams.
This module is responsible for:
- Discovering available streams via MediaMTX API
- Creating and managing StreamPipeline instances
- Monitoring stream health and reconnecting on failures
- Cleaning up old segment files
- Managing pre-roll/post-roll motion-triggered recordings
"""

import os
import shutil
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Callable, Set, Deque, Tuple
from pathlib import Path

import requests

from pipeline import StreamPipeline
from motion_detector import MotionEvent, default_motion_handler
from config import config
from db import init_db, insert_recording, close_connection, upsert_camera, mark_cameras_offline


@dataclass
class CameraInfo:
    """Represents camera info from MediaMTX API."""
    stream_id: str
    name: str
    ready: bool
    source_type: Optional[str] = None
    source_url: Optional[str] = None
    bytes_received: int = 0
    bytes_sent: int = 0


@dataclass
class ClosedSegment:
    """Represents a closed (finished writing) TS segment."""
    path: str
    end_ts: float  # wallclock time when segment was closed


@dataclass
class RecordingSession:
    """Represents an active motion-triggered recording state for a stream."""
    last_motion_ts: float  # wallclock time of last motion event
    copied_segments: Set[str] = field(default_factory=set)  # source paths already copied


class StreamManager:
    """
    Manages multiple RTSP stream pipelines.
    
    Responsibilities:
    - Discover streams from MediaMTX API
    - Create/destroy pipelines as streams appear/disappear
    - Monitor pipeline health and restart failed pipelines
    - Clean up old segment files
    - Handle pre-roll/post-roll recording sessions
    
    Usage:
        manager = StreamManager()
        manager.start()  # Begins discovery and processing
        # ... run until shutdown ...
        manager.stop()   # Graceful shutdown
    """
    
    def __init__(
        self,
        motion_callback: Optional[Callable[[MotionEvent], None]] = None
    ):
        """
        Initialize the stream manager.
        
        Args:
            motion_callback: Function to call when motion is detected in any stream
        """
        self._user_motion_callback = motion_callback or default_motion_handler
        
        # Stream pipelines indexed by stream ID
        self._pipelines: Dict[str, StreamPipeline] = {}
        
        # Management state
        self._running = False
        self._discovery_thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._session_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Per-stream segment history: recent closed segments for pre-roll
        # Key: stream_id, Value: deque of ClosedSegment
        self._segment_history: Dict[str, Deque[ClosedSegment]] = {}
        
        # Per-stream recording sessions
        # Key: stream_id, Value: RecordingSession or None
        self._sessions: Dict[str, Optional[RecordingSession]] = {}
        self._session_lock = threading.Lock()
        
        # Ensure base output directory exists
        Path(config.segment.output_dir).mkdir(parents=True, exist_ok=True)
        Path(config.recording.recordings_dir).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _stream_key(stream_id: str) -> str:
        """Convert stream ID to filesystem-safe key."""
        return stream_id.replace("/", "_")

    def _get_history_max_size(self) -> int:
        """Calculate how many segments to keep in history for pre-roll."""
        # Keep enough segments to cover pre_roll + 2 segment durations as buffer
        pre_roll = config.recording.pre_roll_seconds
        seg_dur = config.segment.segment_duration
        return max(5, (pre_roll // seg_dur) + 3)

    def _handle_motion(self, event: MotionEvent):
        """
        Internal motion handler used by all pipelines.
        
        On motion:
        - If no active session: start one, copy pre-roll segments
        - If session active: extend it (update last_motion_ts)
        """
        stream_id = event.stream_id
        now = time.time()
        
        with self._session_lock:
            session = self._sessions.get(stream_id)
            
            if session is None:
                # Start a new recording session
                session = self._start_session(stream_id, now)
                if session:
                    self._sessions[stream_id] = session
                    # Copy pre-roll segments from history
                    self._copy_preroll_segments(stream_id, session, now)
            else:
                # Extend existing session
                session.last_motion_ts = now

        # Call user callback (stdout logging by default)
        try:
            self._user_motion_callback(event)
        except Exception as e:
            print(f"[WARN] stream={stream_id} Motion callback failed: {e}", flush=True)

    def _start_session(self, stream_id: str, start_ts: float) -> RecordingSession:
        """Create a new recording session state for a stream."""
        print(f"[SESSION] stream={stream_id} Started recording", flush=True)
        
        return RecordingSession(
            last_motion_ts=start_ts,
            copied_segments=set()
        )

    def _copy_preroll_segments(self, stream_id: str, session: RecordingSession, now: float):
        """Copy segments from history that fall within pre-roll window."""
        history = self._segment_history.get(stream_id, deque())
        pre_roll_seconds = config.recording.pre_roll_seconds
        cutoff_ts = now - pre_roll_seconds
        
        segments_to_copy = []
        for seg in history:
            # Include segments that ended after the cutoff (within pre-roll window)
            if seg.end_ts >= cutoff_ts:
                segments_to_copy.append(seg)
        
        for seg in segments_to_copy:
            self._copy_segment_to_recording(stream_id, seg.path, session)

    def _copy_segment_to_recording(self, stream_id: str, segment_path: str, session: RecordingSession) -> bool:
        """Copy a segment file into the day's recording folder."""
        if segment_path in session.copied_segments:
            return True  # Already copied
        
        src = Path(segment_path)
        if not src.exists() or not src.is_file():
            return False
        
        stream_key = self._stream_key(stream_id)
        
        # Get segment's modification time for naming and date folder
        seg_mtime = src.stat().st_mtime
        
        # Create day directory: recordings/<stream_key>/<YYYYMMDD>/
        date_str = time.strftime("%Y%m%d", time.localtime(seg_mtime))
        day_folder = Path(config.recording.recordings_dir) / stream_key / date_str
        day_folder.mkdir(parents=True, exist_ok=True)
        
        # Filename: <stream_key>_<HHMMSS>.ts
        time_str = time.strftime("%H%M%S", time.localtime(seg_mtime))
        dest = day_folder / f"{stream_key}_{time_str}.ts"
        
        # Handle potential filename collision (same second)
        if dest.exists():
            counter = 1
            while dest.exists():
                dest = day_folder / f"{stream_key}_{time_str}_{counter}.ts"
                counter += 1
        
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        
        try:
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
            session.copied_segments.add(segment_path)
            
            # Insert recording into database
            recorded_at = datetime.fromtimestamp(seg_mtime)
            filepath = f"{stream_key}/{date_str}/{dest.name}"
            insert_recording(stream_id, dest.name, filepath, recorded_at)
            
            if config.verbose:
                print(f"[DEBUG] stream={stream_id} Copied segment {src.name} -> {dest.name}", flush=True)
            return True
        except Exception as e:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            print(f"[WARN] stream={stream_id} Failed to copy segment: {e}", flush=True)
            return False

    def _handle_segment_closed(self, stream_id: str, segment_path: str, end_ts: float):
        """
        Called when a segment finishes writing (pipeline rolls to next segment).
        
        - Add to segment history
        - If recording session is active, copy this segment
        """
        closed_seg = ClosedSegment(path=segment_path, end_ts=end_ts)
        
        with self._session_lock:
            # Add to history (create deque if needed)
            if stream_id not in self._segment_history:
                self._segment_history[stream_id] = deque(maxlen=self._get_history_max_size())
            self._segment_history[stream_id].append(closed_seg)
            
            # If session is active, copy this segment
            session = self._sessions.get(stream_id)
            if session is not None:
                self._copy_segment_to_recording(stream_id, segment_path, session)

    def _check_session_timeouts(self):
        """Check all active sessions and end those that have timed out (post-roll expired)."""
        now = time.time()
        post_roll_seconds = config.recording.post_roll_seconds
        
        with self._session_lock:
            for stream_id, session in list(self._sessions.items()):
                if session is None:
                    continue
                
                elapsed = now - session.last_motion_ts
                if elapsed >= post_roll_seconds:
                    # Session timed out - end it
                    self._end_session(stream_id, session)
                    self._sessions[stream_id] = None

    def _end_session(self, stream_id: str, session: RecordingSession):
        """End a recording session."""
        segment_count = len(session.copied_segments)
        print(
            f"[SESSION] stream={stream_id} Ended recording ({segment_count} segments)",
            flush=True
        )

    def discover_streams(self) -> List[CameraInfo]:
        """
        Discover available RTSP streams from MediaMTX API.
        
        Returns:
            List of CameraInfo objects for streams available on MediaMTX
        """
        # If manual streams are configured, use those instead
        if config.manual_streams:
            return [
                CameraInfo(stream_id=s, name=s, ready=True)
                for s in config.manual_streams
            ]
        
        try:
            api_url = f"{config.mediamtx.api_url}/v3/paths/list"
            response = requests.get(api_url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                cameras = []
                
                # Extract stream info from the API response
                items = data.get("items", [])
                for item in items:
                    name = item.get("name", "")
                    ready = item.get("ready", False)
                    
                    if not name:
                        continue
                    
                    # Extract source info
                    source = item.get("source", {})
                    source_type = source.get("type") if source else None
                    source_id = source.get("id", "") if source else ""
                    
                    # Get traffic stats
                    bytes_received = item.get("bytesReceived", 0)
                    bytes_sent = item.get("bytesSent", 0)
                    
                    camera = CameraInfo(
                        stream_id=name,
                        name=name,
                        ready=ready,
                        source_type=source_type,
                        source_url=source_id,
                        bytes_received=bytes_received,
                        bytes_sent=bytes_sent
                    )
                    cameras.append(camera)
                
                if config.verbose:
                    ready_streams = [c.stream_id for c in cameras if c.ready]
                    print(f"[DEBUG] Discovered {len(cameras)} cameras, {len(ready_streams)} ready: {ready_streams}")
                
                return cameras
            else:
                print(f"[WARN] MediaMTX API returned status {response.status_code}")
                return []
                
        except requests.exceptions.ConnectionError:
            print(f"[WARN] Cannot connect to MediaMTX API at {config.mediamtx.api_url}")
            return []
        except requests.exceptions.Timeout:
            print(f"[WARN] MediaMTX API request timed out")
            return []
        except Exception as e:
            print(f"[ERROR] Failed to discover streams: {e}")
            return []
    
    def _create_pipeline(self, stream_id: str) -> Optional[StreamPipeline]:
        """
        Create a new pipeline for a stream.
        
        Args:
            stream_id: The stream identifier
            
        Returns:
            StreamPipeline instance or None on failure
        """
        rtsp_url = f"{config.mediamtx.rtsp_base_url}/{stream_id}"
        
        pipeline = StreamPipeline(
            stream_id=stream_id,
            rtsp_url=rtsp_url,
            output_dir=config.segment.output_dir,
            segment_duration=config.segment.segment_duration,
            motion_callback=self._handle_motion,
            on_segment_closed=self._handle_segment_closed
        )
        
        return pipeline
    
    def _update_streams(self, discovered_cameras: List[CameraInfo]):
        """
        Update pipelines based on discovered cameras.
        
        - Upsert all discovered cameras to database
        - Start pipelines for new ready streams
        - Remove pipelines for streams that are no longer ready
        """
        # Build lookup of camera info by stream_id
        camera_lookup = {c.stream_id: c for c in discovered_cameras}
        ready_stream_ids = {c.stream_id for c in discovered_cameras if c.ready}
        all_stream_ids = [c.stream_id for c in discovered_cameras]
        
        # Update all cameras in database (including non-ready ones)
        for camera in discovered_cameras:
            upsert_camera(
                stream_id=camera.stream_id,
                name=camera.name,
                source_type=camera.source_type,
                source_url=camera.source_url,
                ready=camera.ready,
                bytes_received=camera.bytes_received,
                bytes_sent=camera.bytes_sent
            )
        
        # Mark cameras not in the discovered list as offline
        mark_cameras_offline(all_stream_ids)
        
        with self._lock:
            current_streams = set(self._pipelines.keys())
            
            # Stop and remove pipelines for streams that are no longer ready
            for stream_id in current_streams - ready_stream_ids:
                print(f"[INFO] Stream {stream_id} no longer ready, stopping pipeline")
                self._pipelines[stream_id].stop()
                del self._pipelines[stream_id]
                # Clean up session state for removed streams
                with self._session_lock:
                    if stream_id in self._sessions:
                        del self._sessions[stream_id]
                    if stream_id in self._segment_history:
                        del self._segment_history[stream_id]
            
            # Create pipelines for new ready streams
            for stream_id in ready_stream_ids - current_streams:
                print(f"[INFO] New stream discovered: {stream_id}")
                pipeline = self._create_pipeline(stream_id)
                if pipeline:
                    if pipeline.start():
                        self._pipelines[stream_id] = pipeline
                    else:
                        print(f"[ERROR] Failed to start pipeline for {stream_id}")
    
    def _check_pipeline_health(self):
        """Check and restart failed pipelines."""
        with self._lock:
            for stream_id, pipeline in list(self._pipelines.items()):
                if not pipeline.is_running():
                    # Pipeline died - attempt restart
                    if pipeline.error_count < 5:
                        print(f"[WARN] stream={stream_id} Pipeline not running, attempting restart...")
                        pipeline.stop()
                        time.sleep(1)
                        
                        # Recreate and restart
                        new_pipeline = self._create_pipeline(stream_id)
                        if new_pipeline and new_pipeline.start():
                            self._pipelines[stream_id] = new_pipeline
                        else:
                            print(f"[ERROR] stream={stream_id} Failed to restart pipeline")
                    else:
                        print(f"[ERROR] stream={stream_id} Too many errors, removing pipeline")
                        del self._pipelines[stream_id]
    
    def _discovery_loop(self):
        """Background thread for stream discovery and health checks."""
        print("[INFO] Stream discovery thread started")
        
        while self._running:
            try:
                # Discover cameras and update DB/pipelines
                cameras = self.discover_streams()
                if cameras:
                    self._update_streams(cameras)
                
                # Check pipeline health
                self._check_pipeline_health()
                
            except Exception as e:
                print(f"[ERROR] Discovery loop error: {e}")
            
            # Wait before next discovery cycle
            for _ in range(config.discovery_interval):
                if not self._running:
                    break
                time.sleep(1)
        
        print("[INFO] Stream discovery thread stopped")
    
    def _cleanup_old_segments(self):
        """Remove old segment files to prevent tmpfs from filling up."""
        if config.segment.max_segments <= 0:
            return  # Cleanup disabled
        
        try:
            base_dir = Path(config.segment.output_dir)
            
            for stream_dir in base_dir.iterdir():
                if not stream_dir.is_dir():
                    continue
                
                # Get all .ts files sorted by modification time
                ts_files = sorted(
                    stream_dir.glob("*.ts"),
                    key=lambda f: f.stat().st_mtime,
                    reverse=True
                )
                
                # Remove files beyond the limit
                for old_file in ts_files[config.segment.max_segments:]:
                    try:
                        old_file.unlink()
                        if config.verbose:
                            print(f"[DEBUG] Cleaned up old segment: {old_file}")
                    except Exception as e:
                        print(f"[WARN] Failed to remove {old_file}: {e}")
                        
        except Exception as e:
            print(f"[ERROR] Segment cleanup error: {e}")
    
    def _cleanup_loop(self):
        """Background thread for cleaning up old segments."""
        print("[INFO] Segment cleanup thread started")
        
        while self._running:
            try:
                self._cleanup_old_segments()
            except Exception as e:
                print(f"[ERROR] Cleanup loop error: {e}")
            
            # Run cleanup every 30 seconds
            for _ in range(30):
                if not self._running:
                    break
                time.sleep(1)
        
        print("[INFO] Segment cleanup thread stopped")

    def _session_loop(self):
        """Background thread for checking session timeouts (post-roll)."""
        print("[INFO] Session monitor thread started")
        
        while self._running:
            try:
                self._check_session_timeouts()
            except Exception as e:
                print(f"[ERROR] Session loop error: {e}")
            
            # Check every second for responsive post-roll
            time.sleep(1)
        
        print("[INFO] Session monitor thread stopped")
    
    def start(self):
        """Start the stream manager and all background threads."""
        if self._running:
            print("[WARN] Stream manager already running")
            return
        
        print("[INFO] Starting stream manager...")
        
        # Initialize database connection and schema
        if not init_db():
            print("[WARN] Database initialization failed, recordings will not be saved to DB")
        
        self._running = True
        
        # Start discovery thread
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop,
            name="StreamDiscovery",
            daemon=True
        )
        self._discovery_thread.start()
        
        # Start cleanup thread
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="SegmentCleanup",
            daemon=True
        )
        self._cleanup_thread.start()

        # Start session monitor thread (handles post-roll timeout)
        self._session_thread = threading.Thread(
            target=self._session_loop,
            name="SessionMonitor",
            daemon=True
        )
        self._session_thread.start()
        
        print("[INFO] Stream manager started")
    
    def stop(self):
        """Stop the stream manager and all pipelines gracefully."""
        if not self._running:
            return
        
        print("[INFO] Stopping stream manager...")
        self._running = False
        
        # Wait for threads to finish
        if self._discovery_thread:
            self._discovery_thread.join(timeout=5)
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)
        if self._session_thread:
            self._session_thread.join(timeout=5)
        
        # End any active sessions
        with self._session_lock:
            for stream_id, session in self._sessions.items():
                if session is not None:
                    self._end_session(stream_id, session)
            self._sessions.clear()
        
        # Stop all pipelines
        with self._lock:
            for stream_id, pipeline in self._pipelines.items():
                print(f"[INFO] Stopping pipeline for {stream_id}")
                pipeline.stop()
            self._pipelines.clear()
        
        # Mark all cameras as offline before shutting down
        mark_cameras_offline([])
        
        # Close database connection
        close_connection()
        
        print("[INFO] Stream manager stopped")
    
    def get_active_streams(self) -> List[str]:
        """Get list of currently active stream IDs."""
        with self._lock:
            return list(self._pipelines.keys())
    
    def get_pipeline(self, stream_id: str) -> Optional[StreamPipeline]:
        """Get pipeline for a specific stream."""
        with self._lock:
            return self._pipelines.get(stream_id)
