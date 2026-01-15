"""
GStreamer Pipeline Module.

Creates and manages GStreamer pipelines for RTSP stream processing.
Each pipeline connects to an RTSP source and splits output using a tee:
    - Branch 1: MPEG-TS segmentation to tmpfs storage
    - Branch 2: Motion detection via appsink

Pipeline Structure:
    ┌─────────────┐    ┌──────────┐    ┌─────────┐    ┌─────┐
    │ rtspsrc     │───▶│ rtph264  │───▶│ h264    │───▶│ tee │
    │ (RTSP in)   │    │ depay    │    │ parse   │    │     │
    └─────────────┘    └──────────┘    └─────────┘    └──┬──┘
                                                         │
                 ┌───────────────────────────────────────┼───────────────────────────────────────┐
                 │                                       │                                       │
                 ▼                                       ▼                                       │
    ┌────────────────────┐                  ┌────────────────────┐                              │
    │ Segmentation Branch│                  │ Motion Detection   │                              │
    │ queue → mpegtsmux  │                  │ queue → avdec_h264 │                              │
    │ → splitmuxsink     │                  │ → videoscale →     │                              │
    │ (writes .ts files) │                  │ videoconvert →     │                              │
    └────────────────────┘                  │ appsink            │                              │
                                            └────────────────────┘                              │

Notes:
- Video is NOT re-encoded for segmentation (passthrough)
- Motion detection branch decodes and downscales for efficiency
- splitmuxsink handles automatic segment rotation
"""

import os
import time
from typing import Optional, Callable
from pathlib import Path

import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib

from motion_detector import MotionDetector, MotionEvent, default_motion_handler
from config import config


class StreamPipeline:
    """
    GStreamer pipeline for a single RTSP stream.
    
    Manages the complete lifecycle of an RTSP connection including:
    - Pipeline construction and linking
    - Segment file management
    - Motion detection frame processing
    - Error handling and recovery
    """
    
    def __init__(
        self,
        stream_id: str,
        rtsp_url: str,
        output_dir: str,
        segment_duration: int = 5,
        motion_callback: Optional[Callable[[MotionEvent], None]] = None,
        on_segment_closed: Optional[Callable[[str, str, float], None]] = None
    ):
        """
        Initialize the stream pipeline.
        
        Args:
            stream_id: Unique identifier for this stream
            rtsp_url: Full RTSP URL to connect to
            output_dir: Directory for TS segment output
            segment_duration: Duration of each segment in seconds
            motion_callback: Function to call when motion is detected
            on_segment_closed: Callback(stream_id, segment_path, end_timestamp) when a segment finishes
        """
        self.stream_id = stream_id
        # Stream IDs from MediaMTX are path-like (e.g. "live/cam1") and may contain slashes.
        # Use a filesystem-safe key for pipeline element names and file output.
        self.stream_key = stream_id.replace("/", "_")
        self.rtsp_url = rtsp_url
        self.output_dir = output_dir
        self.segment_duration = segment_duration
        self.motion_callback = motion_callback or default_motion_handler
        self.on_segment_closed = on_segment_closed
        
        # Pipeline state
        self.pipeline: Optional[Gst.Pipeline] = None
        self._current_segment: str = ""
        self._segment_index: int = 0
        self._running: bool = False
        self._error_count: int = 0
        
        # Create output directory for this stream
        self._stream_output_dir = os.path.join(output_dir, self.stream_key)
        Path(self._stream_output_dir).mkdir(parents=True, exist_ok=True)
        
        # Initialize motion detector
        self.motion_detector = MotionDetector(
            stream_id=stream_id,
            pixel_threshold=config.motion.pixel_threshold,
            area_threshold=config.motion.area_threshold,
            cooldown_frames=config.motion.cooldown_frames,
            on_motion=self.motion_callback
        )
    
    def build_pipeline(self) -> bool:
        """
        Construct the GStreamer pipeline.
        
        Returns:
            True if pipeline was built successfully, False otherwise.
        """
        try:
            # Create pipeline
            self.pipeline = Gst.Pipeline.new(f"pipeline_{self.stream_key}")
            
            # === Source Elements ===
            # RTSP source with TCP transport for reliability
            rtspsrc = Gst.ElementFactory.make("rtspsrc", "rtspsrc")
            rtspsrc.set_property("location", self.rtsp_url)
            rtspsrc.set_property("protocols", "tcp")
            rtspsrc.set_property("latency", 200)
            rtspsrc.set_property("buffer-mode", 0)  # Auto buffering
            
            # RTP depayloader (connected dynamically when pad appears)
            rtph264depay = Gst.ElementFactory.make("rtph264depay", "rtph264depay")
            
            # H264 parser for clean frame boundaries
            h264parse = Gst.ElementFactory.make("h264parse", "h264parse")
            
            # Tee to split the stream
            tee = Gst.ElementFactory.make("tee", "tee")
            
            # === Segmentation Branch ===
            # Queue for segment branch
            seg_queue = Gst.ElementFactory.make("queue", "seg_queue")
            seg_queue.set_property("max-size-buffers", 100)
            seg_queue.set_property("max-size-time", 2 * Gst.SECOND)

            # Splitmux sink for segment files
            splitmuxsink = Gst.ElementFactory.make("splitmuxsink", "splitmuxsink")
            splitmuxsink.set_property("max-size-time", self.segment_duration * Gst.SECOND)
            splitmuxsink.set_property("muxer-factory", "mpegtsmux")
            
            # Create segment filename pattern
            segment_pattern = os.path.join(self._stream_output_dir, f"{self.stream_key}_%06d.ts")
            splitmuxsink.set_property("location", segment_pattern)
            
            # === Motion Detection Branch ===
            # Queue for motion detection branch
            motion_queue = Gst.ElementFactory.make("queue", "motion_queue")
            motion_queue.set_property("max-size-buffers", 5)
            motion_queue.set_property("leaky", 2)  # Leak downstream (drop old frames)
            
            # H264 decoder
            avdec_h264 = Gst.ElementFactory.make("avdec_h264", "avdec_h264")
            
            # Video scale for downsampling
            videoscale = Gst.ElementFactory.make("videoscale", "videoscale")
            
            # Scale filter caps
            scale_caps = Gst.Caps.from_string(
                f"video/x-raw,width={config.motion.detection_width},"
                f"height={config.motion.detection_height}"
            )
            scale_filter = Gst.ElementFactory.make("capsfilter", "scale_filter")
            scale_filter.set_property("caps", scale_caps)
            
            # Convert to grayscale for motion detection
            videoconvert = Gst.ElementFactory.make("videoconvert", "videoconvert")
            
            # Grayscale caps filter
            gray_caps = Gst.Caps.from_string("video/x-raw,format=GRAY8")
            gray_filter = Gst.ElementFactory.make("capsfilter", "gray_filter")
            gray_filter.set_property("caps", gray_caps)
            
            # App sink for frame access
            appsink = Gst.ElementFactory.make("appsink", "appsink")
            appsink.set_property("emit-signals", True)
            appsink.set_property("max-buffers", 2)
            appsink.set_property("drop", True)
            appsink.set_property("sync", False)
            appsink.connect("new-sample", self._on_new_sample)
            
            # === Add all elements to pipeline ===
            elements = [
                rtspsrc, rtph264depay, h264parse, tee,
                seg_queue, splitmuxsink,
                motion_queue, avdec_h264, videoscale, scale_filter,
                videoconvert, gray_filter, appsink
            ]
            
            for element in elements:
                if element is None:
                    print(f"[ERROR] stream={self.stream_id} Failed to create GStreamer element")
                    return False
                self.pipeline.add(element)
            
            # === Link elements ===
            # Connect rtspsrc pad-added signal for dynamic linking
            rtspsrc.connect("pad-added", self._on_pad_added, rtph264depay)
            
            # Link main chain: depay -> parse -> tee
            if not rtph264depay.link(h264parse):
                print(f"[ERROR] stream={self.stream_id} Failed to link rtph264depay -> h264parse")
                return False
            if not h264parse.link(tee):
                print(f"[ERROR] stream={self.stream_id} Failed to link h264parse -> tee")
                return False
            
            # Link segmentation branch
            tee_seg_pad = tee.get_request_pad("src_%u")
            seg_queue_pad = seg_queue.get_static_pad("sink")
            if tee_seg_pad.link(seg_queue_pad) != Gst.PadLinkReturn.OK:
                print(f"[ERROR] stream={self.stream_id} Failed to link tee -> seg_queue")
                return False

            # splitmuxsink is a sink bin that owns the muxer; link H264 directly to its "video" request pad.
            seg_queue_src_pad = seg_queue.get_static_pad("src")
            splitmuxsink_video_pad = splitmuxsink.get_request_pad("video")
            if splitmuxsink_video_pad is None:
                print(f"[ERROR] stream={self.stream_id} Failed to get splitmuxsink 'video' request pad")
                return False
            if seg_queue_src_pad.link(splitmuxsink_video_pad) != Gst.PadLinkReturn.OK:
                print(f"[ERROR] stream={self.stream_id} Failed to link seg_queue -> splitmuxsink.video")
                return False
            
            # Link motion detection branch
            tee_motion_pad = tee.get_request_pad("src_%u")
            motion_queue_pad = motion_queue.get_static_pad("sink")
            if tee_motion_pad.link(motion_queue_pad) != Gst.PadLinkReturn.OK:
                print(f"[ERROR] stream={self.stream_id} Failed to link tee -> motion_queue")
                return False
            
            if not motion_queue.link(avdec_h264):
                print(f"[ERROR] stream={self.stream_id} Failed to link motion_queue -> avdec_h264")
                return False
            if not avdec_h264.link(videoscale):
                print(f"[ERROR] stream={self.stream_id} Failed to link avdec_h264 -> videoscale")
                return False
            if not videoscale.link(scale_filter):
                print(f"[ERROR] stream={self.stream_id} Failed to link videoscale -> scale_filter")
                return False
            if not scale_filter.link(videoconvert):
                print(f"[ERROR] stream={self.stream_id} Failed to link scale_filter -> videoconvert")
                return False
            if not videoconvert.link(gray_filter):
                print(f"[ERROR] stream={self.stream_id} Failed to link videoconvert -> gray_filter")
                return False
            if not gray_filter.link(appsink):
                print(f"[ERROR] stream={self.stream_id} Failed to link gray_filter -> appsink")
                return False
            
            # Connect to splitmuxsink signals for segment tracking
            splitmuxsink.connect("format-location-full", self._on_format_location)
            
            # Add bus message handler
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self._on_bus_message)
            
            print(f"[INFO] stream={self.stream_id} Pipeline built successfully")
            return True
            
        except Exception as e:
            print(f"[ERROR] stream={self.stream_id} Failed to build pipeline: {e}")
            return False
    
    def _on_pad_added(self, element, pad, depay):
        """Handle dynamic pad creation from rtspsrc."""
        pad_name = pad.get_current_caps().to_string() if pad.get_current_caps() else "unknown"
        
        if "video" in pad_name.lower() or "h264" in pad_name.lower():
            sink_pad = depay.get_static_pad("sink")
            if not sink_pad.is_linked():
                result = pad.link(sink_pad)
                if result == Gst.PadLinkReturn.OK:
                    print(f"[INFO] stream={self.stream_id} Video pad linked successfully")
                else:
                    print(f"[WARN] stream={self.stream_id} Failed to link video pad: {result}")
    
    def _on_format_location(self, splitmux, fragment_id, first_sample):
        """
        Called when splitmuxsink creates a new segment file.
        Updates the current segment path for motion event reporting.
        
        Before switching to the new segment, we emit a "segment closed" callback
        for the previous segment so the manager can persist it if needed.
        """
        # Emit closed callback for the previous segment (if any)
        previous_segment = self._current_segment
        if previous_segment and self.on_segment_closed:
            # Use wallclock time as approximate end timestamp
            end_ts = time.time()
            try:
                # Only emit if file actually exists (sanity check)
                if os.path.isfile(previous_segment):
                    self.on_segment_closed(self.stream_id, previous_segment, end_ts)
            except Exception as e:
                print(f"[WARN] stream={self.stream_id} segment_closed callback error: {e}")

        # Now update to the new segment
        self._segment_index = fragment_id
        self._current_segment = os.path.join(
            self._stream_output_dir,
            f"{self.stream_key}_{fragment_id:06d}.ts"
        )
        if config.verbose:
            print(f"[DEBUG] stream={self.stream_id} New segment: {self._current_segment}")
        return self._current_segment
    
    def _on_new_sample(self, appsink) -> Gst.FlowReturn:
        """
        Process incoming frame from appsink for motion detection.
        
        This is the motion detection hook - each frame from the decoded
        video stream is passed to the motion detector for analysis.
        """
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.OK
        
        buffer = sample.get_buffer()
        caps = sample.get_caps()
        
        # Extract frame dimensions from caps
        structure = caps.get_structure(0)
        width = structure.get_int("width")[1]
        height = structure.get_int("height")[1]
        
        # Map buffer to get frame data
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.OK
        
        try:
            # Get current segment (fallback to placeholder if not yet created)
            current_segment = self._current_segment or f"{self._stream_output_dir}/{self.stream_key}_000000.ts"
            
            # Get timestamp
            timestamp = buffer.pts / Gst.SECOND if buffer.pts != Gst.CLOCK_TIME_NONE else 0.0
            
            # Process frame through motion detector
            self.motion_detector.process_frame(
                frame_data=map_info.data,
                width=width,
                height=height,
                current_segment=current_segment,
                timestamp=timestamp
            )
        finally:
            buffer.unmap(map_info)
        
        return Gst.FlowReturn.OK
    
    def _on_bus_message(self, bus, message):
        """Handle GStreamer bus messages."""
        msg_type = message.type
        
        if msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"[ERROR] stream={self.stream_id} {err.message}")
            if config.verbose and debug:
                print(f"[DEBUG] stream={self.stream_id} {debug}")
            self._error_count += 1
            self._running = False
            
        elif msg_type == Gst.MessageType.EOS:
            print(f"[INFO] stream={self.stream_id} End of stream")
            self._running = False
            
        elif msg_type == Gst.MessageType.STATE_CHANGED:
            if message.src == self.pipeline:
                old, new, pending = message.parse_state_changed()
                if config.verbose:
                    print(f"[DEBUG] stream={self.stream_id} State: {old.value_nick} -> {new.value_nick}")
                    
        elif msg_type == Gst.MessageType.WARNING:
            warn, debug = message.parse_warning()
            print(f"[WARN] stream={self.stream_id} {warn.message}")
    
    def start(self) -> bool:
        """
        Start the pipeline.
        
        Returns:
            True if pipeline started successfully.
        """
        if self.pipeline is None:
            if not self.build_pipeline():
                return False
        
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print(f"[ERROR] stream={self.stream_id} Failed to start pipeline")
            return False
        
        self._running = True
        self._error_count = 0
        print(f"[INFO] stream={self.stream_id} Pipeline started, connecting to {self.rtsp_url}")
        return True
    
    def stop(self):
        """Stop the pipeline gracefully."""
        if self.pipeline:
            print(f"[INFO] stream={self.stream_id} Stopping pipeline...")
            self.pipeline.set_state(Gst.State.NULL)
            self._running = False
    
    def is_running(self) -> bool:
        """Check if pipeline is currently running."""
        return self._running
    
    @property
    def current_segment(self) -> str:
        """Get the current segment file path."""
        return self._current_segment
    
    @property
    def error_count(self) -> int:
        """Get the number of errors encountered."""
        return self._error_count

