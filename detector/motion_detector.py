"""
Motion Detection Module.

Implements frame differencing-based motion detection for video streams.
This module processes raw video frames and detects motion by comparing
consecutive frames and measuring the percentage of changed pixels.

Algorithm:
1. Convert frame to grayscale (if needed)
2. Compare with previous frame using absolute difference
3. Apply threshold to identify changed pixels
4. Calculate percentage of frame with motion
5. Trigger event if motion exceeds area threshold
"""

import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass


@dataclass
class MotionEvent:
    """Represents a detected motion event."""
    stream_id: str
    segment_file: str
    motion_percentage: float
    timestamp: float


class MotionDetector:
    """
    Frame differencing-based motion detector.
    
    This class maintains state for a single video stream and processes
    incoming frames to detect motion events.
    
    Usage:
        detector = MotionDetector(
            stream_id="cam01",
            pixel_threshold=25,
            area_threshold=1.0,
            cooldown_frames=30
        )
        
        # Called for each frame from GStreamer pipeline
        detector.process_frame(frame_data, width, height, get_segment_fn)
    """
    
    def __init__(
        self,
        stream_id: str,
        pixel_threshold: int = 25,
        area_threshold: float = 1.0,
        cooldown_frames: int = 30,
        on_motion: Optional[Callable[[MotionEvent], None]] = None
    ):
        """
        Initialize the motion detector.
        
        Args:
            stream_id: Identifier for the stream being monitored
            pixel_threshold: Minimum pixel difference (0-255) to consider as changed
            area_threshold: Minimum percentage of frame that must change (0.0-100.0)
            cooldown_frames: Minimum frames between motion reports
            on_motion: Callback function when motion is detected
        """
        self.stream_id = stream_id
        self.pixel_threshold = pixel_threshold
        self.area_threshold = area_threshold
        self.cooldown_frames = cooldown_frames
        self.on_motion = on_motion
        
        # State
        self._previous_frame: Optional[np.ndarray] = None
        self._frames_since_motion: int = cooldown_frames  # Allow immediate first detection
        self._frame_count: int = 0
    
    def process_frame(
        self,
        frame_data: bytes,
        width: int,
        height: int,
        current_segment: str,
        timestamp: float = 0.0
    ) -> Optional[MotionEvent]:
        """
        Process a single video frame for motion detection.
        
        The frame is expected to be in grayscale format (1 byte per pixel).
        
        Args:
            frame_data: Raw frame bytes (grayscale, width * height bytes)
            width: Frame width in pixels
            height: Frame height in pixels
            current_segment: Path to the currently active TS segment file
            timestamp: Frame timestamp in seconds
            
        Returns:
            MotionEvent if motion was detected, None otherwise
        """
        self._frame_count += 1
        self._frames_since_motion += 1
        
        # Convert bytes to numpy array
        try:
            frame = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, width))
        except ValueError as e:
            # Frame size mismatch - skip this frame
            return None
        
        # First frame - store and return
        if self._previous_frame is None:
            self._previous_frame = frame.copy()
            return None
        
        # Ensure frames are same shape
        if frame.shape != self._previous_frame.shape:
            self._previous_frame = frame.copy()
            return None
        
        # Calculate absolute difference between frames
        diff = np.abs(frame.astype(np.int16) - self._previous_frame.astype(np.int16))
        
        # Count pixels that exceed threshold
        changed_pixels = np.sum(diff > self.pixel_threshold)
        total_pixels = width * height
        motion_percentage = (changed_pixels / total_pixels) * 100.0
        
        # Update previous frame for next comparison
        self._previous_frame = frame.copy()
        
        # Check if motion exceeds threshold and cooldown has passed
        if motion_percentage >= self.area_threshold and self._frames_since_motion >= self.cooldown_frames:
            self._frames_since_motion = 0
            
            event = MotionEvent(
                stream_id=self.stream_id,
                segment_file=current_segment,
                motion_percentage=motion_percentage,
                timestamp=timestamp
            )
            
            # Invoke callback if registered
            if self.on_motion:
                self.on_motion(event)
            
            return event
        
        return None
    
    def reset(self):
        """Reset the detector state (e.g., when stream reconnects)."""
        self._previous_frame = None
        self._frames_since_motion = self.cooldown_frames
        self._frame_count = 0
    
    @property
    def frame_count(self) -> int:
        """Total number of frames processed."""
        return self._frame_count


def default_motion_handler(event: MotionEvent):
    """
    Default motion event handler that prints to stdout.
    
    Output format:
        [MOTION] stream=<stream_id> file=<segment_path>
    """
    print(f"[MOTION] stream={event.stream_id} file={event.segment_file}", flush=True)

