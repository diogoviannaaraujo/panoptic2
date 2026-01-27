"""
Database Module.

Handles PostgreSQL connection and operations for recording metadata.
Schema is managed by the db-migrate service.
"""

import time
from typing import Optional
from datetime import datetime

import psycopg2

from config import config


# Module-level connection (reused across calls)
_connection: Optional[psycopg2.extensions.connection] = None


def get_connection() -> psycopg2.extensions.connection:
    """Get or create a database connection."""
    global _connection
    
    if _connection is None or _connection.closed:
        _connection = psycopg2.connect(
            host=config.database.host,
            port=config.database.port,
            dbname=config.database.name,
            user=config.database.user,
            password=config.database.password
        )
        _connection.autocommit = True
    
    return _connection


def init_db(max_retries: int = 10, retry_delay: int = 3) -> bool:
    """
    Initialize database connection (schema is managed by db-migrate service).
    
    Args:
        max_retries: Maximum connection attempts
        retry_delay: Seconds between retries
        
    Returns:
        True if connection succeeded
    """
    for attempt in range(max_retries):
        try:
            conn = get_connection()
            # Just verify the connection works
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            print("[INFO] Database connection established", flush=True)
            return True
            
        except psycopg2.OperationalError as e:
            print(f"[WARN] Database connection failed (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print("[ERROR] Failed to connect to database after retries", flush=True)
                return False
        except Exception as e:
            print(f"[ERROR] Database connection failed: {e}", flush=True)
            return False
    
    return False


def insert_recording(stream_id: str, filename: str, filepath: str, recorded_at: datetime) -> bool:
    """
    Insert a new recording into the database.
    
    Args:
        stream_id: The stream identifier (e.g., "live_botafogo2_CAM4")
        filename: The segment filename (e.g., "live_botafogo2_CAM4_181453.ts")
        filepath: Full path to the file relative to recordings dir
        recorded_at: Timestamp when the segment was recorded
        
    Returns:
        True if insert succeeded
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO recordings (stream_id, filename, filepath, recorded_at)
                VALUES (%s, %s, %s, %s)
                """,
                (stream_id, filename, filepath, recorded_at)
            )
        return True
    except Exception as e:
        print(f"[WARN] Failed to insert recording: {e}", flush=True)
        return False


def upsert_stream(
    stream_id: str,
    name: Optional[str] = None,
    source_type: Optional[str] = None,
    source_url: Optional[str] = None,
    ready: bool = False,
    bytes_received: int = 0,
    bytes_sent: int = 0
) -> bool:
    """
    Insert or update a stream record.
    
    Args:
        stream_id: Unique identifier for the stream
        name: Optional display name for the stream
        source_type: Type of source (e.g., "rtspSource", "webrtcSource")
        source_url: URL of the source stream
        ready: Whether the stream is ready/active
        bytes_received: Total bytes received
        bytes_sent: Total bytes sent
        
    Returns:
        True if upsert succeeded
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO streams (stream_id, name, source_type, source_url, ready, bytes_received, bytes_sent, last_seen_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT (stream_id) DO UPDATE SET
                    name = COALESCE(EXCLUDED.name, streams.name),
                    source_type = COALESCE(EXCLUDED.source_type, streams.source_type),
                    source_url = COALESCE(EXCLUDED.source_url, streams.source_url),
                    ready = EXCLUDED.ready,
                    bytes_received = EXCLUDED.bytes_received,
                    bytes_sent = EXCLUDED.bytes_sent,
                    last_seen_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (stream_id, name, source_type, source_url, ready, bytes_received, bytes_sent)
            )
        return True
    except Exception as e:
        print(f"[WARN] Failed to upsert stream: {e}", flush=True)
        return False


def update_stream_status(stream_id: str, ready: bool) -> bool:
    """
    Update the ready status of a stream.
    
    Args:
        stream_id: The stream identifier
        ready: Whether the stream is ready/active
        
    Returns:
        True if update succeeded
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE streams 
                SET ready = %s, last_seen_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE stream_id = %s
                """,
                (ready, stream_id)
            )
        return True
    except Exception as e:
        print(f"[WARN] Failed to update stream status: {e}", flush=True)
        return False


def mark_streams_offline(active_stream_ids: list) -> bool:
    """
    Mark streams as offline (ready=false) if they're not in the active list.
    
    Args:
        active_stream_ids: List of currently active stream IDs
        
    Returns:
        True if update succeeded
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            if active_stream_ids:
                # Mark streams not in the list as offline
                cur.execute(
                    """
                    UPDATE streams 
                    SET ready = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE ready = TRUE AND stream_id != ALL(%s)
                    """,
                    (active_stream_ids,)
                )
            else:
                # No active streams, mark all as offline
                cur.execute(
                    """
                    UPDATE streams 
                    SET ready = FALSE, updated_at = CURRENT_TIMESTAMP
                    WHERE ready = TRUE
                    """
                )
        return True
    except Exception as e:
        print(f"[WARN] Failed to mark streams offline: {e}", flush=True)
        return False


def get_detector_config(stream_id: str) -> Optional[dict]:
    """
    Get detector configuration for a stream.
    
    Args:
        stream_id: The stream identifier
        
    Returns:
        Dictionary with config values or None if not found
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT enabled, crop_x1, crop_y1, crop_x2, crop_y2, sensitivity
                FROM detector_configs
                WHERE stream_id = %s
                """,
                (stream_id,)
            )
            row = cur.fetchone()
            if row:
                return {
                    "enabled": row[0],
                    "crop_rect": (row[1], row[2], row[3], row[4]),
                    "sensitivity": row[5]
                }
            return None
    except Exception as e:
        print(f"[WARN] Failed to get detector config: {e}", flush=True)
        return None



def close_connection():
    """Close the database connection."""
    global _connection
    if _connection and not _connection.closed:
        _connection.close()
        _connection = None
