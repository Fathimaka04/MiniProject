import re
import sqlite3
import json
import os
import threading
import logging
from typing import List, Dict, Optional, Union, Any

logger = logging.getLogger(__name__)


def _is_placeholder_phone(number: str) -> bool:
    """Return True if *number* looks like a placeholder, not a real phone.

    Catches patterns like +91XXXXXXXXXX, 0000000000, 1234567890, etc.
    Used as a safety net in create_user() — the UI wizard also validates,
    but this ensures bad data never reaches the DB regardless of caller.
    """
    if not number:
        return False  # empty is acceptable (contact is optional)
    if re.search(r'[Xx]', number):
        return True
    digits = re.sub(r'\D', '', number)
    if 0 < len(digits) < 7:
        return True
    if digits and len(set(digits)) == 1:
        return True
    if digits in '0123456789012345':
        return True
    return False


class Database:
    """
    Database access layer for GazeAssist.
    Provides methods to interact with the SQLite database.
    """
    def __init__(self, db_path: str = 'gazeassist.db'):
        """
        Initialize the database connection.
        
        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._lock = threading.Lock()
        
        # Ensure the directory exists
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            
        self.create_tables()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a configured thread-safe SQLite connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Return dict-like rows
        return conn

    def create_tables(self) -> None:
        """Create database tables from schema if they don't exist."""
        schema_path = os.path.join(os.path.dirname(__file__), 'schema.sql')
        
        try:
            with open(schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()
                
            with self._lock:
                with self._get_connection() as conn:
                    conn.executescript(schema_sql)
        except Exception as e:
            # Fallback if schema.sql is not found where expected
            # We will still try to create tables based on internal definitions if needed,
            # but we assume schema.sql exists as per requirement.
            print(f"Error creating tables: {e}")
            raise

    def create_user(self, name: str, language: str, emergency_contact: Optional[str] = None) -> int:
        """
        Create a new user.
        
        Args:
            name: User's name.
            language: User's preferred language code (e.g., 'hi', 'ml').
            emergency_contact: Emergency contact number or details.
            
        Returns:
            int: The ID of the newly created user.
            
        Raises:
            ValueError: If emergency_contact looks like a placeholder.
        """
        if _is_placeholder_phone(emergency_contact or ""):
            raise ValueError(
                f"Emergency contact '{emergency_contact}' looks like a "
                "placeholder — refusing to save. Please provide a real number."
            )

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO users (name, language, emergency_contact) VALUES (?, ?, ?)",
                    (name, language, emergency_contact)
                )
                return cursor.lastrowid

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Retrieve user details by ID."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Retrieve all users."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
                return [dict(row) for row in cursor.fetchall()]

    def create_session(self, user_id: int) -> int:
        """
        Start a new session for a user.
        
        Args:
            user_id: ID of the user.
            
        Returns:
            int: The ID of the new session.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO sessions (user_id) VALUES (?)",
                    (user_id,)
                )
                return cursor.lastrowid

    def end_session(self, session_id: int) -> None:
        """
        End an active session.
        
        Args:
            session_id: ID of the session to end.
        """
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET end_time = CURRENT_TIMESTAMP WHERE id = ?",
                    (session_id,)
                )

    def log_communication(self, session_id: int, phrase: str, category: Optional[str] = None) -> None:
        """
        Log a communication phrase.
        
        Args:
            session_id: The current session ID.
            phrase: The phrase spoken/communicated.
            category: Category of the phrase.
        """
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO communication_log (session_id, phrase, category) VALUES (?, ?, ?)",
                    (session_id, phrase, category)
                )

    def log_pain(self, session_id: int, pain_level: int) -> None:
        """
        Log a reported pain level.
        
        Args:
            session_id: The current session ID.
            pain_level: Pain level (1-10).
        """
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO pain_log (session_id, pain_level) VALUES (?, ?)",
                    (session_id, pain_level)
                )

    def log_sos(self, session_id: int, channels_fired: List[str]) -> None:
        """
        Log an SOS event.
        
        Args:
            session_id: The current session ID.
            channels_fired: List of channels alerted (e.g., ['speaker', 'whatsapp']).
        """
        channels_json = json.dumps(channels_fired)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO sos_events (session_id, alert_channels_fired) VALUES (?, ?)",
                    (session_id, channels_json)
                )

    def increment_tile_use(self, user_id: int, tile_id: str) -> None:
        """
        Increment the usage count for a specific communication tile.
        
        Args:
            user_id: ID of the user.
            tile_id: Identifier for the tile.
        """
        with self._lock:
            with self._get_connection() as conn:
                # Upsert query: insert if new, update if exists
                conn.execute(
                    '''
                    INSERT INTO tile_frequency (user_id, tile_id, use_count, last_used)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(user_id, tile_id) DO UPDATE SET
                    use_count = use_count + 1,
                    last_used = CURRENT_TIMESTAMP
                    ''',
                    (user_id, tile_id)
                )

    def get_tile_frequencies(self, user_id: int) -> Dict[str, int]:
        """
        Get the frequency of tile usage for a user.
        
        Args:
            user_id: ID of the user.
            
        Returns:
            Dict mapping tile_id to usage count.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT tile_id, use_count FROM tile_frequency WHERE user_id = ?",
                    (user_id,)
                )
                return {row['tile_id']: row['use_count'] for row in cursor.fetchall()}

    def get_session_log(self, session_id: int) -> List[Dict[str, Any]]:
        """
        Retrieve the communication log for a session.
        
        Args:
            session_id: The session ID.
            
        Returns:
            List of communication records.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM communication_log WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,)
                )
                return [dict(row) for row in cursor.fetchall()]

    def get_pain_history(self, session_id: int) -> List[Dict[str, Any]]:
        """
        Retrieve the pain log for a session.
        
        Args:
            session_id: The session ID.
            
        Returns:
            List of pain records.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM pain_log WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,)
                )
                return [dict(row) for row in cursor.fetchall()]

    def get_sos_events(self, session_id: int) -> List[Dict[str, Any]]:
        """
        Retrieve SOS events for a session.
        
        Args:
            session_id: The session ID.
            
        Returns:
            List of SOS event records. The 'alert_channels_fired' field is parsed from JSON.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT * FROM sos_events WHERE session_id = ? ORDER BY timestamp ASC",
                    (session_id,)
                )
                events = []
                for row in cursor.fetchall():
                    event = dict(row)
                    if event.get('alert_channels_fired'):
                        try:
                            event['alert_channels_fired'] = json.loads(event['alert_channels_fired'])
                        except json.JSONDecodeError:
                            event['alert_channels_fired'] = []
                    events.append(event)
                return events

    def get_most_used_phrases(self, session_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieve the most frequently used phrases in a session.
        
        Args:
            session_id: The session ID.
            limit: Maximum number of phrases to return.
            
        Returns:
            List of dictionaries with 'phrase' and 'count' keys.
        """
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    SELECT phrase, COUNT(*) as count 
                    FROM communication_log 
                    WHERE session_id = ? 
                    GROUP BY phrase 
                    ORDER BY count DESC 
                    LIMIT ?
                    ''',
                    (session_id, limit)
                )
                return [dict(row) for row in cursor.fetchall()]

    def get_session_stats(self, session_id: int) -> Dict[str, Any]:
        """
        Calculate and retrieve aggregate statistics for a session.
        
        Args:
            session_id: The session ID.
            
        Returns:
            Dictionary containing session statistics.
        """
        stats = {
            'total_communications': 0,
            'total_pain_reports': 0,
            'total_sos_events': 0,
            'average_pain_level': None,
            'duration_seconds': None
        }
        
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # Communications
                cursor.execute("SELECT COUNT(*) FROM communication_log WHERE session_id = ?", (session_id,))
                stats['total_communications'] = cursor.fetchone()[0]
                
                # Pain
                cursor.execute(
                    "SELECT COUNT(*), AVG(pain_level) FROM pain_log WHERE session_id = ?",
                    (session_id,)
                )
                pain_row = cursor.fetchone()
                stats['total_pain_reports'] = pain_row[0]
                stats['average_pain_level'] = float(pain_row[1]) if pain_row[1] is not None else None
                
                # SOS
                cursor.execute("SELECT COUNT(*) FROM sos_events WHERE session_id = ?", (session_id,))
                stats['total_sos_events'] = cursor.fetchone()[0]
                
                # Duration
                cursor.execute(
                    '''
                    SELECT start_time, end_time 
                    FROM sessions 
                    WHERE id = ?
                    ''', 
                    (session_id,)
                )
                session_row = cursor.fetchone()
                if session_row and session_row['end_time']:
                    # Assuming timestamp format 'YYYY-MM-DD HH:MM:SS'
                    try:
                        from datetime import datetime
                        start = datetime.strptime(session_row['start_time'], "%Y-%m-%d %H:%M:%S")
                        end = datetime.strptime(session_row['end_time'], "%Y-%m-%d %H:%M:%S")
                        stats['duration_seconds'] = (end - start).total_seconds()
                    except (ValueError, TypeError):
                        pass
                        
        return stats
