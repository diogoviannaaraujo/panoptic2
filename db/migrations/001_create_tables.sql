-- Create cameras table
CREATE TABLE IF NOT EXISTS cameras (
    id SERIAL PRIMARY KEY,
    stream_id VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    source_type VARCHAR(100),
    source_url VARCHAR(512),
    ready BOOLEAN DEFAULT FALSE,
    bytes_received BIGINT DEFAULT 0,
    bytes_sent BIGINT DEFAULT 0,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Camera indexes
CREATE INDEX IF NOT EXISTS idx_cameras_stream_id ON cameras(stream_id);
CREATE INDEX IF NOT EXISTS idx_cameras_ready ON cameras(ready);

-- Create recordings table
CREATE TABLE IF NOT EXISTS recordings (
    id SERIAL PRIMARY KEY,
    stream_id VARCHAR(255) NOT NULL,
    filename VARCHAR(255) NOT NULL,
    filepath VARCHAR(512) NOT NULL,
    recorded_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Recording indexes
CREATE INDEX IF NOT EXISTS idx_recordings_stream_id ON recordings(stream_id);
CREATE INDEX IF NOT EXISTS idx_recordings_recorded_at ON recordings(recorded_at);

-- Create analysis table (references recordings)
CREATE TABLE IF NOT EXISTS analysis (
    id SERIAL PRIMARY KEY,
    recording_id INTEGER NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
    description TEXT,
    danger BOOLEAN DEFAULT FALSE,
    danger_level INTEGER DEFAULT 0,
    danger_details TEXT,
    raw_response TEXT,
    error VARCHAR(255),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Analysis indexes
CREATE INDEX IF NOT EXISTS idx_analysis_recording_id ON analysis(recording_id);
CREATE INDEX IF NOT EXISTS idx_analysis_danger ON analysis(danger);
CREATE INDEX IF NOT EXISTS idx_analysis_danger_level ON analysis(danger_level);
CREATE INDEX IF NOT EXISTS idx_analysis_created_at ON analysis(created_at);
