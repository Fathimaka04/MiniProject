"""
GazeAssist - Cross-session frequency-based tile reordering.

High-frequency tiles migrate toward the screen/gaze centroid at each session
start, reducing average gaze travel distance over time.
"""

import logging
from typing import Optional

from phrase_board.tiles import Tile

logger = logging.getLogger(__name__)


class TileReorderer:
    """
    Reorders tiles so frequently-used ones appear closer to the grid center.

    Uses tile usage counts from SQLite, persisted across sessions.
    """

    def __init__(self, db, user_id: int):
        """
        Args:
            db: Database instance with get_tile_frequencies / increment_tile_use.
            user_id: Current user ID.
        """
        self._db = db
        self._user_id = user_id

    def reorder_tiles(self, tiles: list[Tile], grid_cols: int = 4) -> list[Tile]:
        """
        Reorder tiles so high-frequency ones occupy center positions.

        Args:
            tiles: list of Tile objects for one page/category.
            grid_cols: number of columns in the grid layout.

        Returns:
            Reordered list of tiles.
        """
        if len(tiles) <= 1:
            return tiles

        freqs = self._db.get_tile_frequencies(self._user_id)

        # Compute center-priority ordering for grid positions
        n = len(tiles)
        grid_rows = (n + grid_cols - 1) // grid_cols
        center_r = (grid_rows - 1) / 2.0
        center_c = (grid_cols - 1) / 2.0

        # Distance of each grid position from center (lower = more central)
        positions = []
        for i in range(n):
            r = i // grid_cols
            c = i % grid_cols
            dist = ((r - center_r) ** 2 + (c - center_c) ** 2) ** 0.5
            positions.append((i, dist))

        # Sort positions: closest to center first
        center_positions = [idx for idx, _ in sorted(positions, key=lambda x: x[1])]

        # Sort tiles: highest frequency first
        tiles_with_freq = [
            (tile, freqs.get(tile.id, 0)) for tile in tiles
        ]
        tiles_sorted = [t for t, _ in sorted(tiles_with_freq, key=lambda x: -x[1])]

        # Assign high-frequency tiles to center positions
        reordered = [None] * n
        for priority_rank, tile in enumerate(tiles_sorted):
            if priority_rank < len(center_positions):
                reordered[center_positions[priority_rank]] = tile

        # Fill any None slots (shouldn't happen, but safety)
        result = [t for t in reordered if t is not None]
        return result

    def record_use(self, tile_id: str):
        """Increment usage count for a tile."""
        try:
            self._db.increment_tile_use(self._user_id, tile_id)
        except Exception as e:
            logger.error("Failed to record tile use: %s", e)

    def get_frequencies(self) -> dict[str, int]:
        """Return tile_id → use_count map."""
        return self._db.get_tile_frequencies(self._user_id)
