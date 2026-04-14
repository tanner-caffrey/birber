import logging
import sqlite3
from pathlib import Path

from .config import DatabaseConfig

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    species TEXT NOT NULL,
    species_confidence REAL NOT NULL,
    detection_confidence REAL NOT NULL,
    image_path TEXT,
    bbox_x INTEGER,
    bbox_y INTEGER,
    bbox_w INTEGER,
    bbox_h INTEGER
);

CREATE INDEX IF NOT EXISTS idx_sightings_timestamp ON sightings(timestamp);
CREATE INDEX IF NOT EXISTS idx_sightings_species ON sightings(species);
"""


class SightingsDB:
    """SQLite database for bird sightings."""

    def __init__(self, config: DatabaseConfig):
        db_path = Path(config.path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Opening database: %s", db_path)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def log_sighting(
        self,
        timestamp: str,
        species: str,
        species_confidence: float,
        detection_confidence: float,
        image_path: str | None = None,
        bbox: tuple[int, int, int, int] | None = None,
    ) -> int:
        """Insert a sighting record. Returns the row ID."""
        bx, by, bw, bh = bbox if bbox else (None, None, None, None)
        cursor = self._conn.execute(
            """INSERT INTO sightings
               (timestamp, species, species_confidence, detection_confidence,
                image_path, bbox_x, bbox_y, bbox_w, bbox_h)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, species, species_confidence, detection_confidence,
             image_path, bx, by, bw, bh),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_sightings(
        self,
        limit: int = 50,
        offset: int = 0,
        species: str | None = None,
    ) -> list[dict]:
        """Query sightings with optional filtering."""
        query = "SELECT * FROM sightings"
        params: list = []
        if species:
            query += " WHERE species = ?"
            params.append(species)
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        cursor = self._conn.execute(query, params)
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def get_summary(self) -> dict[str, int]:
        """Get a count of sightings per species."""
        cursor = self._conn.execute(
            "SELECT species, COUNT(*) FROM sightings GROUP BY species ORDER BY COUNT(*) DESC"
        )
        return dict(cursor.fetchall())

    def get_total_count(self) -> int:
        cursor = self._conn.execute("SELECT COUNT(*) FROM sightings")
        return cursor.fetchone()[0]

    def close(self):
        self._conn.close()
