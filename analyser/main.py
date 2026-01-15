import os
import time
import json
import logging
import threading
import socket
import requests
import re
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import uvicorn

import db

# Configuration
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", "../recordings")
VLLM_API_URL = os.getenv("VLLM_API_URL", "http://localhost:8000/v1/chat/completions")
VLLM_MODEL = os.getenv("VLLM_MODEL", "Qwen/Qwen3-VL-8B-Instruct-FP8")
SERVER_PORT = int(os.getenv("SERVER_PORT", 8080))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 10))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# FastAPI for serving files
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount recordings directory
# We need absolute path for StaticFiles
abs_recordings_path = Path(RECORDINGS_DIR).resolve()
if not abs_recordings_path.exists():
    logger.warning(f"Recordings directory {abs_recordings_path} does not exist. Creating it.")
    abs_recordings_path.mkdir(parents=True, exist_ok=True)

app.mount("/recordings", StaticFiles(directory=abs_recordings_path), name="recordings")

def get_host_ip():
    """Helper to get host IP reachable from docker or local network."""
    try:
        # Connect to a public DNS server to determine the outgoing interface IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

# Determine the IP address to use for URLs
# If running inside Docker, we might need a different strategy or env var
HOST_IP = os.getenv("HOST_IP", get_host_ip())

def create_session_with_retries(
    retries: int = 3,
    backoff_factor: float = 0.5,
    status_forcelist: tuple = (500, 502, 503, 504)
) -> requests.Session:
    """Create a requests session with retry logic."""
    session = requests.Session()
    retry_strategy = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["POST"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def clean_json_string(content: str) -> str:
    """Extract JSON from markdown code blocks or raw text."""
    # Remove markdown code blocks if present
    json_match = re.search(r'```(?:json)?\s*(.*?)```', content, re.DOTALL)
    if json_match:
        return json_match.group(1).strip()
    return content.strip()

def process_recording(recording: dict):
    """Process a recording from the database."""
    recording_id = recording["id"]
    filepath = recording["filepath"]
    filename = recording["filename"]
    
    try:
        # Build full path and URL
        video_path = abs_recordings_path / filepath
        video_url = f"http://{HOST_IP}:{SERVER_PORT}/recordings/{filepath}"
        
        logger.info(f"Processing recording {recording_id}: {filename}...")
        logger.debug(f"Video URL: {video_url}")
        
        prompt = """
        Analyze this video segment of a security camera.
        Provide a structured analysis in JSON format.
        The JSON object must strictly adhere to this schema:
        {
            "description": "A detailed description of the scene and events",
            "danger": boolean, // true if there is any danger, threat, or suspicious activity that may require attention
            "danger_level": number, // the level of the danger between 0 and 10
            "danger_details": "Details about the danger if any, otherwise empty string"
        }
        
        Ensure valid JSON output. Do not include any text outside the JSON object.
        """
        
        payload = {
            "model": VLLM_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "video_url", "video_url": {"url": video_url}}
                    ]
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.1
        }
        
        logger.info(f"Sending request to vLLM at {VLLM_API_URL}")
        
        # Use session with retries for resilience
        session = create_session_with_retries(retries=3)
        response = session.post(VLLM_API_URL, json=payload, timeout=300)
        
        if response.status_code != 200:
            logger.error(f"vLLM API Error: {response.status_code} - {response.text}")
            db.insert_analysis(
                recording_id=recording_id,
                error=f"vLLM API Error: {response.status_code}"
            )
            return

        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Parse JSON
        cleaned_content = clean_json_string(content)
        try:
            data = json.loads(cleaned_content)
            
            # Insert successful analysis
            db.insert_analysis(
                recording_id=recording_id,
                description=data.get("description"),
                danger=data.get("danger", False),
                danger_details=data.get("danger_details"),
                raw_response=content
            )
            
            logger.info(f"Successfully processed recording {recording_id}: {filename}")
            logger.info(f"Result: {json.dumps(data, indent=2)}")
            
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {content}")
            # Save with error flag
            db.insert_analysis(
                recording_id=recording_id,
                raw_response=content,
                error="json_parse_error"
            )
        
    except Exception as e:
        logger.error(f"Exception processing recording {recording_id}: {e}")
        db.insert_analysis(
            recording_id=recording_id,
            error=str(e)
        )

def wait_for_vllm(timeout: int = 300):
    """Wait for vLLM to be ready before starting."""
    start_time = time.time()
    logger.info(f"Waiting for vLLM to be ready at {VLLM_API_URL}...")
    
    while time.time() - start_time < timeout:
        try:
            response = requests.get(VLLM_API_URL.replace("/v1/chat/completions", "/v1/models"), timeout=5)
            if response.status_code == 200:
                logger.info("vLLM is ready!")
                return True
        except Exception as e:
            logger.debug(f"vLLM not ready yet: {e}")
        
        time.sleep(5)
    
    logger.warning(f"vLLM did not become ready after {timeout} seconds, proceeding anyway...")
    return False

def get_pending_by_camera() -> dict[str, list[dict]]:
    """Get pending recordings grouped by camera (stream_id) for round-robin processing."""
    pending = db.get_pending_recordings()
    
    by_camera = {}
    for rec in pending:
        stream_id = rec["stream_id"]
        if stream_id not in by_camera:
            by_camera[stream_id] = []
        by_camera[stream_id].append(rec)
    
    return by_camera

def monitor_loop():
    logger.info("Started monitoring database for pending recordings...")
    while True:
        try:
            # Get pending recordings grouped by camera
            pending_by_camera = get_pending_by_camera()
            
            if not pending_by_camera:
                logger.debug("No pending recordings to process")
                time.sleep(POLL_INTERVAL)
                continue
            
            # Log status
            for camera, recordings in pending_by_camera.items():
                logger.info(f"Camera {camera}: {len(recordings)} pending recordings")
            
            # Round-robin processing across all cameras
            camera_names = list(pending_by_camera.keys())
            camera_indices = {cam: 0 for cam in camera_names}
            
            while True:
                processed_any = False
                
                for camera in camera_names:
                    idx = camera_indices[camera]
                    recordings = pending_by_camera[camera]
                    
                    if idx < len(recordings):
                        recording = recordings[idx]
                        logger.info(f"[{camera}] Processing recording {idx + 1}/{len(recordings)}")
                        process_recording(recording)
                        camera_indices[camera] = idx + 1
                        processed_any = True
                
                if not processed_any:
                    # All cameras exhausted their queues
                    break
                    
        except Exception as e:
            logger.error(f"Error in monitor loop: {e}")
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    # Initialize database connection
    if not db.init_db():
        logger.error("Failed to connect to database, exiting")
        exit(1)
    
    # Wait for vLLM to be ready
    wait_for_vllm()
    
    # Start monitor in separate thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start FastAPI server
    logger.info(f"Starting HTTP server on port {SERVER_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)
