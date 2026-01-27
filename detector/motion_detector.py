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
from typing import Optional, Callable, Tuple
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
        crop_rect: Optional[Tuple[int, int, int, int]] = None,
        enabled: bool = True,
        sensitivity: int = 50,
        on_motion: Optional[Callable[[MotionEvent], None]] = None
    ):
        """
        Initialize the motion detector.
        
        Args:
            stream_id: Identifier for the stream being monitored
            pixel_threshold: Minimum pixel difference (0-255) to consider as changed (overridden if sensitivity changed)
            area_threshold: Minimum percentage of frame that must change (0.0-100.0)
            cooldown_frames: Minimum frames between motion reports
            crop_rect: Optional crop area (x1, y1, x2, y2)
            enabled: Whether detection is enabled
            sensitivity: Motion sensitivity (0-100), where 100 is most sensitive. Overrides pixel_threshold if used.
            on_motion: Callback function when motion is detected
        """
        self.stream_id = stream_id
        self.area_threshold = area_threshold
        self.cooldown_frames = cooldown_frames
        self.crop_rect = crop_rect
        self.enabled = enabled
        self.sensitivity = sensitivity
        self.on_motion = on_motion
        
        # Calculate pixel threshold from sensitivity
        # Sensitivity 50 -> threshold 25 (matches old default)
        # Sensitivity 100 -> threshold 0 (clamped to 5)
        # Sensitivity 0 -> threshold 50
        self.pixel_threshold = max(5, int(50 - (sensitivity * 0.5)))
        
        # State
        self._previous_frame: Optional[np.ndarray] = None
        self._frames_since_motion: int = cooldown_frames  # Allow immediate first detection
        self._frame_count: int = 0
    
    def update_config(self, config: dict):
        """
        Update detector configuration dynamically.
        
        Args:
            config: Dictionary containing config updates
        """
        if "sensitivity" in config:
            self.sensitivity = config["sensitivity"]
            self.pixel_threshold = max(5, int(50 - (self.sensitivity * 0.5)))
        elif "pixel_threshold" in config:
             # Fallback if sensitivity not provided but raw threshold is
            self.pixel_threshold = config["pixel_threshold"]
            
        if "area_threshold" in config:
            self.area_threshold = config["area_threshold"]
        
        # Update crop rect
        if "crop_rect" in config:
            # If crop changes, reset previous frame to avoid false positives
            if self.crop_rect != config["crop_rect"]:
                self.crop_rect = config["crop_rect"]
                self._previous_frame = None
        
        # Update enabled status
        if "enabled" in config:
            if self.enabled != config["enabled"]:
                self.enabled = config["enabled"]
                if not self.enabled:
                    self._previous_frame = None

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
        if not self.enabled:
            return None

        self._frame_count += 1
        self._frames_since_motion += 1
        
        # Convert bytes to numpy array
        try:
            frame = np.frombuffer(frame_data, dtype=np.uint8).reshape((height, width))
        except ValueError as e:
            # Frame size mismatch - skip this frame
            return None
        
        # Apply crop if defined
        if self.crop_rect:
            x1, y1, x2, y2 = self.crop_rect
            # Validate bounds
            h, w = frame.shape
            x1 = max(0, min(x1, w))
            y1 = max(0, min(y1, h))
            x2 = max(x1, min(x2, w))
            y2 = max(y1, min(y2, h))
            
            # Ensure valid crop area
            if x2 > x1 and y2 > y1:
                frame = frame[y1:y2, x1:x2]
            else:
                # Invalid crop or zero area, ignore frame
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
        # Total pixels is current frame size (cropped or original)
        total_pixels = frame.size
        motion_percentage = (changed_pixels / total_pixels) * 100.0 if total_pixels > 0 else 0.0
        
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

