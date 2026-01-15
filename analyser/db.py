"""
Database Module for Analyser.

Handles PostgreSQL connection and operations for analysis results.
Schema is managed by the db-migrate service.
"""

import os
import time
import logging
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)

# Database configuration from environment
DB_HOST = os.getenv("DB_HOST", "db")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "panoptic")
DB_USER = os.getenv("DB_USER", "user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

# Module-level connection (reused across calls)
_connection: Optional[psycopg2.extensions.connection] = None


def get_connection() -> psycopg2.extensions.connection:
    """Get or create a database connection."""
    global _connection
    
    if _connection is None or _connection.closed:
        _connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
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
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            logger.info("Database connection established")
            return True
            
        except psycopg2.OperationalError as e:
            logger.warning(f"Database connection failed (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                logger.error("Failed to connect to database after retries")
                return False
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            return False
    
    return False


def get_recording_id_by_filepath(filepath: str) -> Optional[int]:
    """
    Look up a recording ID by its filepath.
    
    Args:
        filepath: The filepath stored in the recordings table
        
    Returns:
        The recording ID if found, None otherwise
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM recordings WHERE filepath = %s",
                (filepath,)
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        logger.error(f"Failed to look up recording by filepath: {e}")
        return None


def analysis_exists_for_recording(recording_id: int) -> bool:
    """
    Check if an analysis already exists for a recording.
    
    Args:
        recording_id: The recording ID to check
        
    Returns:
        True if analysis exists, False otherwise
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM analysis WHERE recording_id = %s LIMIT 1",
                (recording_id,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.error(f"Failed to check if analysis exists: {e}")
        return False


def insert_analysis(
    recording_id: int,
    description: Optional[str] = None,
    danger: bool = False,
    danger_level: int = 0,
    danger_details: Optional[str] = None,
    raw_response: Optional[str] = None,
    error: Optional[str] = None
) -> bool:
    """
    Insert a new analysis result into the database.
    
    Args:
        recording_id: The recording ID this analysis is for
        description: A detailed description of the scene and events
        danger: True if there is any danger, threat, or suspicious activity
        danger_level: The level of danger from 0 to 10
        danger_details: Details about the danger if any
        raw_response: The raw response from the model (for debugging)
        error: Error message if analysis failed
        
    Returns:
        True if insert succeeded
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analysis (recording_id, description, danger, danger_level, danger_details, raw_response, error)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (recording_id, description, danger, danger_level, danger_details, raw_response, error)
            )
        return True
    except Exception as e:
        logger.error(f"Failed to insert analysis: {e}")
        return False


def get_pending_recordings() -> list[dict]:
    """
    Get recordings that haven't been analysed yet.
    
    Returns:
        List of dicts with recording info (id, stream_id, filename, filepath)
    """
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.stream_id, r.filename, r.filepath
                FROM recordings r
                LEFT JOIN analysis a ON r.id = a.recording_id
                WHERE a.id IS NULL
                ORDER BY r.stream_id, r.recorded_at
                """
            )
            rows = cur.fetchall()
            return [
                {"id": row[0], "stream_id": row[1], "filename": row[2], "filepath": row[3]}
                for row in rows
            ]
    except Exception as e:
        logger.error(f"Failed to get pending recordings: {e}")
        return []


def close_connection():
    """Close the database connection."""
    global _connection
    if _connection and not _connection.closed:
        _connection.close()
        _connection = None

