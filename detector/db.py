"""
Database Module.

Handles PostgreSQL connection and operations for recording metadata.
"""

import time
from typing import Optional
from datetime import datetime

import psycopg2
from psycopg2 import sql

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
    Initialize database schema (create tables if they don't exist).
    
    Args:
        max_retries: Maximum connection attempts
        retry_delay: Seconds between retries
        
    Returns:
        True if initialization succeeded
    """
    for attempt in range(max_retries):
        try:
            conn = get_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS recordings (
                        id SERIAL PRIMARY KEY,
                        stream_id VARCHAR(255) NOT NULL,
                        filename VARCHAR(255) NOT NULL,
                        filepath VARCHAR(512) NOT NULL,
                        recorded_at TIMESTAMP NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                # Index for efficient queries by stream and date
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_recordings_stream_id 
                    ON recordings(stream_id)
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_recordings_recorded_at 
                    ON recordings(recorded_at)
                """)
            print("[INFO] Database initialized successfully", flush=True)
            return True
            
        except psycopg2.OperationalError as e:
            print(f"[WARN] Database connection failed (attempt {attempt + 1}/{max_retries}): {e}", flush=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print("[ERROR] Failed to connect to database after retries", flush=True)
                return False
        except Exception as e:
            print(f"[ERROR] Database initialization failed: {e}", flush=True)
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


def close_connection():
    """Close the database connection."""
    global _connection
    if _connection and not _connection.closed:
        _connection.close()
        _connection = None

