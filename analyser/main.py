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

def process_video(video_path: Path):
    try:
        relative_path = video_path.relative_to(abs_recordings_path)
        # Use valid URL characters for path
        video_url = f"http://{HOST_IP}:{SERVER_PORT}/recordings/{relative_path}"
        
        logger.info(f"Processing {video_path.name}...")
        logger.debug(f"Video URL: {video_url}")
        
        prompt = """
        Analyze this video segment. Provide a structured analysis in JSON format.
        The JSON object must strictly adhere to this schema:
        {
            "description": "A detailed description of the scene and events",
            "danger": boolean, // true if there is any danger, threat, or suspicious activity
            "danger_details": "Details about the danger if any, otherwise null or empty string"
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
            "max_tokens": 1024,
            "temperature": 0.1
        }
        
        logger.info(f"Sending request to vLLM at {VLLM_API_URL}")
        
        # Use session with retries for resilience
        session = create_session_with_retries(retries=3)
        response = session.post(VLLM_API_URL, json=payload, timeout=300)  # Long timeout for video processing
        
        if response.status_code != 200:
            logger.error(f"vLLM API Error: {response.status_code} - {response.text}")
            return

        result = response.json()
        content = result['choices'][0]['message']['content']
        
        # Parse JSON
        cleaned_content = clean_json_string(content)
        try:
            data = json.loads(cleaned_content)
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {content}")
            # Save raw content just in case
            data = {"raw_content": content, "error": "json_parse_error"}

        # Save result to .json file
        json_path = video_path.with_suffix('.json')
        with open(json_path, 'w') as f:
            json.dump(data, f, indent=2)
            
        logger.info(f"Successfully processed {video_path.name}")
        logger.info(f"Result: {json.dumps(data, indent=2)}")
        
    except Exception as e:
        logger.error(f"Exception processing {video_path.name}: {e}")

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

def get_pending_files_by_camera() -> dict[str, list[Path]]:
    """Get pending .ts files grouped by camera directory (round-robin fair processing)."""
    pending_by_camera = {}
    
    # Find all camera directories (first-level subdirectories like live_botafogo2_CAM4, etc.)
    for camera_dir in abs_recordings_path.iterdir():
        if not camera_dir.is_dir():
            continue
        
        camera_name = camera_dir.name
        pending_files = []
        
        # Find all .ts files in this camera's directory tree
        for ts_file in camera_dir.rglob("*.ts"):
            json_file = ts_file.with_suffix('.json')
            if not json_file.exists():
                pending_files.append(ts_file)
        
        if pending_files:
            # Sort by modification time (oldest first) within each camera
            pending_files.sort(key=os.path.getmtime)
            pending_by_camera[camera_name] = pending_files
    
    return pending_by_camera

def monitor_loop():
    logger.info(f"Started monitoring {abs_recordings_path} for .ts files...")
    while True:
        try:
            # Get pending files grouped by camera
            pending_by_camera = get_pending_files_by_camera()
            
            if not pending_by_camera:
                logger.debug("No pending files to process")
                time.sleep(POLL_INTERVAL)
                continue
            
            # Log status
            for camera, files in pending_by_camera.items():
                logger.info(f"Camera {camera}: {len(files)} pending files")
            
            # Round-robin processing across all cameras
            # Process one file from each camera in turn until all are done
            camera_names = list(pending_by_camera.keys())
            camera_indices = {cam: 0 for cam in camera_names}
            
            while True:
                processed_any = False
                
                for camera in camera_names:
                    idx = camera_indices[camera]
                    files = pending_by_camera[camera]
                    
                    if idx < len(files):
                        ts_file = files[idx]
                        # Double-check it still needs processing (might have been processed by another loop)
                        json_file = ts_file.with_suffix('.json')
                        if not json_file.exists():
                            logger.info(f"[{camera}] Processing file {idx + 1}/{len(files)}")
                            process_video(ts_file)
                        camera_indices[camera] = idx + 1
                        processed_any = True
                
                if not processed_any:
                    # All cameras exhausted their queues
                    break
                    
        except Exception as e:
            logger.error(f"Error in monitor loop: {e}")
        
        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    # Wait for vLLM to be ready
    wait_for_vllm()
    
    # Start monitor in separate thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()
    
    # Start FastAPI server
    logger.info(f"Starting HTTP server on port {SERVER_PORT}")
    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)

