# Panoptic API Service

This is a NestJS-based API service for the Panoptic video surveillance system. It provides endpoints to discover cameras, list recorded events, and stream event playback via HLS.

## Overview

The API scans the recordings directory (mapped from the detector service) and exposes the file structure as a structured API. It dynamically generates HLS playlists (`.m3u8`) to stitch together individual MPEG-TS segments recorded during motion events.

## Getting Started

The service is intended to be run via Docker Compose as part of the main stack.

```bash
# Run via docker-compose from the project root
docker-compose up -d api
```

The API will be available at `http://localhost:3000`.

### Development

To run locally for development:

```bash
cd api
npm install
npm run start:dev
```

*Note: You will need to set the `RECORDINGS_DIR` environment variable or ensure the default `/recordings` path exists and contains data.*

## API Endpoints

### 1. List Cameras

Retrieves a list of all available cameras based on the subdirectories found in the recordings folder.

- **URL**: `/cameras`
- **Method**: `GET`
- **Response**: Array of strings (camera IDs)

**Example Request:**
```bash
curl http://localhost:3000/cameras
```

**Example Response:**
```json
[
  "live_botafogo2_CAM8",
  "live_camera-test-1"
]
```

### 2. List Events

Retrieves a list of recorded events for a specific camera. An "event" corresponds to a motion detection session.

- **URL**: `/cameras/:camera/events`
- **Method**: `GET`
- **Parameters**:
  - `camera`: The ID of the camera (e.g., `live_botafogo2_CAM8`)
- **Response**: Array of event objects sorted by timestamp (newest first).

**Example Request:**
```bash
curl http://localhost:3000/cameras/live_botafogo2_CAM8/events
```

**Example Response:**
```json
[
  {
    "id": "20260115/20260115_093335",
    "date": "20260115",
    "timestamp": "20260115_093335",
    "path": "/recordings/live_botafogo2_CAM8/20260115/20260115_093335"
  }
]
```

### 3. Stream Event (HLS)

Generates an HLS playlist for a specific event. This URL can be fed directly into a video player (VLC, hls.js, etc.).

- **URL**: `/cameras/:camera/events/:date/:timestamp/playlist.m3u8`
- **Method**: `GET`
- **Parameters**:
  - `camera`: Camera ID
  - `date`: Date string (e.g., `20260115`)
  - `timestamp`: Event timestamp string (e.g., `20260115_093335`)
- **Response**: `application/vnd.apple.mpegurl` (M3U8 playlist content)

**Example Usage:**
Open `http://localhost:3000/cameras/live_botafogo2_CAM8/events/20260115/20260115_093335/playlist.m3u8` in a video player.

### 4. Get Video Segment

Serves individual video segments. These are typically called automatically by the video player based on the playlist.

- **URL**: `/cameras/:camera/events/:date/:timestamp/:segment`
- **Method**: `GET`
- **Response**: `video/mp2t` (Binary video data)

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RECORDINGS_DIR` | Path to the root directory containing camera recordings | `/recordings` |

