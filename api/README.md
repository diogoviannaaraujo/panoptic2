# Panoptic API Service

NestJS API for the Panoptic video surveillance system. Provides endpoints to list cameras, query recordings with AI analysis results, stream video segments, and configure motion detection settings.

## Getting Started

Run via Docker Compose from the project root:

```bash
docker-compose up -d api
```

The API will be available at `http://localhost:3000`.

### Development

```bash
cd api
npm install
npm run start:dev
```

### Available Scripts

| Script | Description |
|--------|-------------|
| `npm run start` | Start the application |
| `npm run start:dev` | Start with hot-reload for development |
| `npm run start:debug` | Start with debugger attached |
| `npm run start:prod` | Start production build |
| `npm run build` | Build the application |
| `npm run lint` | Run ESLint with auto-fix |
| `npm run format` | Format code with Prettier |

## API Endpoints

### List Cameras

Returns all cameras with their current state, ordered by name.

```
GET /cameras
```

**Response:**

```json
[
  {
    "id": 1,
    "stream_id": "live_botafogo2_CAM8",
    "name": "Botafogo Camera 8",
    "source_type": "rtspSource",
    "source_url": "rtsp://...",
    "ready": true,
    "last_seen_at": "2026-01-15T12:30:00.000Z"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | number | Unique camera identifier |
| `stream_id` | string | Stream identifier used for recordings |
| `name` | string \| null | Human-readable camera name |
| `source_type` | string \| null | Source type (e.g., `rtspSource`) |
| `source_url` | string \| null | RTSP or source URL |
| `ready` | boolean | Whether the camera is currently active |
| `last_seen_at` | string \| null | ISO timestamp of last activity |

---

### List Recordings

Returns paginated recordings for a camera, ordered by date (newest first). Each recording includes its associated AI analysis results if available.

```
GET /cameras/:streamId/recordings?page=1&limit=50
```

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `streamId` | string | The camera's stream identifier |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `page` | number | 1 | Page number (1-indexed) |
| `limit` | number | 50 | Items per page |

**Response:**

```json
{
  "data": [
    {
      "id": 123,
      "stream_id": "live_botafogo2_CAM8",
      "filename": "live_botafogo2_CAM8_093335.ts",
      "filepath": "live_botafogo2_CAM8/20260115/20260115_093335/live_botafogo2_CAM8_093335.ts",
      "recorded_at": "2026-01-15T09:33:35.000Z",
      "analysis": {
        "id": 456,
        "description": "Person walking near entrance",
        "danger": false,
        "danger_level": 0,
        "danger_details": null,
        "created_at": "2026-01-15T09:34:00.000Z"
      }
    }
  ],
  "total": 150,
  "page": 1,
  "limit": 50
}
```

**Recording Object:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | number | Unique recording identifier |
| `stream_id` | string | Associated camera stream ID |
| `filename` | string | Recording filename |
| `filepath` | string | Relative path to the recording file |
| `recorded_at` | string | ISO timestamp when recorded |
| `analysis` | object \| null | AI analysis results (see below) |

**Analysis Object** (nullable):

| Field | Type | Description |
|-------|------|-------------|
| `id` | number | Analysis record identifier |
| `description` | string \| null | AI-generated description of the scene |
| `danger` | boolean | Whether a danger was detected |
| `danger_level` | number | Severity level (0 = none, higher = more severe) |
| `danger_details` | string \| null | Details about the detected danger |
| `created_at` | string | ISO timestamp of analysis completion |

---

### Get Recording

Streams a recording segment by ID. Returns the raw video file as a binary stream.

```
GET /cameras/:streamId/recordings/:id
```

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `streamId` | string | The camera's stream identifier |
| `id` | number | Recording ID |

**Response:**

- Content-Type: `video/mp2t`
- Body: Binary video stream

**Errors:**

| Status | Description |
|--------|-------------|
| 404 | Recording not found or file missing |

---

### Get Detector Config

Returns the motion detector configuration for a stream. If no config exists in the database, returns default values (full frame detection, enabled).

```
GET /streams/:streamId/config
```

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `streamId` | string | The camera's stream identifier |

**Response:**

```json
{
  "stream_id": "live_botafogo2_CAM8",
  "enabled": true,
  "crop_x1": 0,
  "crop_y1": 0,
  "crop_x2": 100,
  "crop_y2": 100
}
```

| Field | Type | Description |
|-------|------|-------------|
| `stream_id` | string | Stream identifier |
| `enabled` | boolean | Whether motion detection is active |
| `crop_x1` | number | Left edge of detection area (0-100%) |
| `crop_y1` | number | Top edge of detection area (0-100%) |
| `crop_x2` | number | Right edge of detection area (0-100%) |
| `crop_y2` | number | Bottom edge of detection area (0-100%) |

---

### Update Detector Config

Updates the motion detector configuration for a stream. Creates the config if it doesn't exist (upsert). Changes are picked up by the detector service within a few seconds.

```
PUT /streams/:streamId/config
```

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `streamId` | string | The camera's stream identifier |

**Request Body:**

All fields are optional. Only provided fields will be updated.

```json
{
  "enabled": true,
  "crop_x1": 10,
  "crop_y1": 20,
  "crop_x2": 90,
  "crop_y2": 80
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | boolean | true | Enable/disable motion detection |
| `crop_x1` | number | 0 | Left edge of detection area (0-100%) |
| `crop_y1` | number | 0 | Top edge of detection area (0-100%) |
| `crop_x2` | number | 100 | Right edge of detection area (0-100%) |
| `crop_y2` | number | 100 | Bottom edge of detection area (0-100%) |

**Response:**

Returns the updated config object (same format as GET).

**Validation:**

- All crop values must be between 0 and 100
- `crop_x2` must be greater than `crop_x1`
- `crop_y2` must be greater than `crop_y1`

**Errors:**

| Status | Description |
|--------|-------------|
| 400 | Invalid crop values or validation failed |

**Examples:**

```bash
# Disable motion detection for a camera
curl -X PUT http://localhost:3000/streams/live_cam1/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Set detection area to bottom-right quadrant only
curl -X PUT http://localhost:3000/streams/live_cam1/config \
  -H "Content-Type: application/json" \
  -d '{"crop_x1": 50, "crop_y1": 50, "crop_x2": 100, "crop_y2": 100}'

# Reset to full frame detection
curl -X PUT http://localhost:3000/streams/live_cam1/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "crop_x1": 0, "crop_y1": 0, "crop_x2": 100, "crop_y2": 100}'
```

## Architecture

```
src/
├── main.ts              # Application entry point, CORS enabled
├── app.module.ts        # Root module definition
├── app.controller.ts    # REST endpoints
├── app.service.ts       # Business logic & data access
└── database.service.ts  # PostgreSQL connection pool
```

The service uses a connection pool to PostgreSQL with automatic retry logic on startup (up to 10 attempts with 3-second delays).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RECORDINGS_DIR` | Path to recordings directory | `/recordings` |
| `DB_HOST` | PostgreSQL host | `localhost` |
| `DB_PORT` | PostgreSQL port | `5432` |
| `DB_NAME` | Database name | `panoptic` |
| `DB_USER` | Database user | `user` |
| `DB_PASSWORD` | Database password | `password` |

## Docker

The API runs in a Node.js 18 Alpine container. The Dockerfile:

1. Installs dependencies
2. Builds the TypeScript source
3. Runs the production build on port 3000

```bash
# Build manually
docker build -t panoptic-api .

# Run standalone
docker run -p 3000:3000 \
  -e DB_HOST=host.docker.internal \
  -e RECORDINGS_DIR=/recordings \
  -v /path/to/recordings:/recordings \
  panoptic-api
```

## Tech Stack

- **Runtime:** Node.js 18
- **Framework:** NestJS 10
- **Database:** PostgreSQL (via `pg` driver)
- **Language:** TypeScript 5.1
