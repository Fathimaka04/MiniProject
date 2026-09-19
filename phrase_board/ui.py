"""
GazeAssist - Tkinter Phrase Board UI

Paged grid layout with gaze cursor overlay, armed-tile highlighting,
and category navigation.  Uses SelectionConfirmer for arm/confirm logic.
"""

import time
import logging
import tkinter as tk
from typing import Optional, Callable

from phrase_board.tiles import (
    Category, Tile, create_default_tiles, get_tile_text,
)
from phrase_board.confirm import SelectionConfirmer, SelectionState
from phrase_board.frequency import TileReorderer

logger = logging.getLogger(__name__)

GRID_COLS = 3
TILE_PAD = 8
CURSOR_RADIUS = 18


class PhraseBoardUI:
    """
    Tkinter phrase board — the core AAC communication interface.

    Paged grid of tiles with gaze cursor overlay, dwell/blink arming,
    and repeat-to-confirm before speaking.
    """

    def __init__(
        self,
        root: tk.Tk,
        confirmer: SelectionConfirmer,
        language: str = "hi",
        on_phrase_confirmed: Optional[Callable[[Tile], None]] = None,
        on_pain_scale: Optional[Callable[[], None]] = None,
        reorderer: Optional[TileReorderer] = None,
    ):
        self._root = root
        self._confirmer = confirmer
        self._language = language
        self._on_phrase_confirmed = on_phrase_confirmed
        self._on_pain_scale = on_pain_scale
        self._reorderer = reorderer

        # Tile data
        self._all_tiles = create_default_tiles()
        self._current_category = Category.CORE
        self._current_tiles: list[Tile] = []
        self._tile_widgets: dict[int, dict] = {}  # zone_id -> {frame, label, ...}
        self._zone_to_tile: dict[int, Tile] = {}

        # Gaze state
        self._cursor_zone: int = -1
        self._armed_tile_id: Optional[str] = None

        # Build UI
        self._frame = tk.Frame(root, bg="#1a1a2e")
        self._frame.pack(fill="both", expand=True)

        # Gaze cursor canvas (overlay) — created first so everything
        # below stacks on top of it automatically. Nothing currently
        # draws on this canvas.
        self._cursor_canvas = tk.Canvas(
            self._frame, bg="#1a1a2e", highlightthickness=0,
        )
        self._cursor_canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self._header = tk.Frame(self._frame, bg="#16213e", height=50)
        self._header.pack(fill="x")
        self._header.pack_propagate(False)

        self._title_label = tk.Label(
            self._header, text="GazeAssist", font=("Helvetica", 18, "bold"),
            fg="#e0e0e0", bg="#16213e",
        )
        self._title_label.pack(side="left", padx=15)

        self._page_label = tk.Label(
            self._header, text="", font=("Helvetica", 12),
            fg="#888888", bg="#16213e",
        )
        self._page_label.pack(side="right", padx=15)

        self._grid_frame = tk.Frame(self._frame, bg="#1a1a2e")
        self._grid_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # Status bar
        self._status_frame = tk.Frame(self._frame, bg="#0f0f23", height=35)
        self._status_frame.pack(fill="x", side="bottom")
        self._status_frame.pack_propagate(False)

        self._status_label = tk.Label(
            self._status_frame, text="Look at a tile to select",
            font=("Helvetica", 11), fg="#666666", bg="#0f0f23",
        )
        self._status_label.pack(side="left", padx=10)

        self._fps_label = tk.Label(
            self._status_frame, text="",
            font=("Helvetica", 10), fg="#444444", bg="#0f0f23",
        )
        self._fps_label.pack(side="right", padx=10)

        # Wire up confirmer callbacks
        self._confirmer.set_on_arm(self._on_tile_armed)
        self._confirmer.set_on_confirm(self._on_tile_confirmed)
        self._confirmer.set_on_cancel(self._on_arm_cancelled)

        # Show home page
        self._show_category(Category.CORE)

    # ── Page rendering ────────────────────────────────────────────────

    def _show_category(self, category: Category):
        """Display a category page."""
        self._current_category = category

        # Get tiles for this category
        if category == Category.CORE:
            # Home page: core tiles + nav tiles
            core = list(self._all_tiles.get(Category.CORE, []))
            self._current_tiles = core
        else:
            self._current_tiles = list(self._all_tiles.get(category, []))
            # Add a "BACK" tile
            back_tile = Tile(
                "nav_back", "← BACK", Category.NAVIGATION,
                {"hi": "← वापस", "ml": "← തിരികെ", "ta": "← பின்", "te": "← వెనుకకు"},
                "#607D8B", "🔙", is_navigation=True, target_category=Category.CORE,
            )
            self._current_tiles.insert(0, back_tile)

        # Apply frequency reordering (only for non-nav tiles)
        if self._reorderer and category != Category.CORE:
            non_nav = [t for t in self._current_tiles if not t.is_navigation]
            nav = [t for t in self._current_tiles if t.is_navigation]
            non_nav = self._reorderer.reorder_tiles(non_nav, GRID_COLS)
            self._current_tiles = nav + non_nav

        self._render_grid()

    def _render_grid(self):
        """Render the tile grid for the current page."""
        # Clear existing widgets
        for w in self._grid_frame.winfo_children():
            w.destroy()
        self._tile_widgets.clear()
        self._zone_to_tile.clear()

        tiles = self._current_tiles
        n = len(tiles)
        rows = (n + GRID_COLS - 1) // GRID_COLS

        self._page_label.config(
            text=f"{self._current_category.name} · {n} tiles"
        )

        for i, tile in enumerate(tiles):
            r = i // GRID_COLS
            c = i % GRID_COLS

            frame = tk.Frame(
                self._grid_frame, bg=tile.color,
                relief="flat", bd=0,
            )
            frame.grid(row=r, column=c, padx=TILE_PAD, pady=TILE_PAD, sticky="nsew")

            # Emoji
            emoji_label = tk.Label(
                frame, text=tile.emoji, font=("Segoe UI Emoji", 28),
                bg=tile.color, fg="white",
            )
            emoji_label.pack(pady=(12, 2))

            # Text in selected language
            display_text = get_tile_text(tile, self._language)
            text_label = tk.Label(
                frame, text=display_text, font=("Helvetica", 13, "bold"),
                bg=tile.color, fg="white", wraplength=140,
            )
            text_label.pack(pady=(0, 2))

            # English fallback if different
            if display_text != tile.text:
                en_label = tk.Label(
                    frame, text=tile.text, font=("Helvetica", 9),
                    bg=tile.color, fg="#cccccc",
                )
                en_label.pack(pady=(0, 8))
            else:
                # Spacer
                tk.Label(frame, text="", bg=tile.color, height=1).pack()

            self._tile_widgets[i] = {"frame": frame, "tile": tile, "original_color": tile.color}
            self._zone_to_tile[i] = tile

        # Configure grid weights for uniform sizing
        for c in range(GRID_COLS):
            self._grid_frame.columnconfigure(c, weight=1)
        for r in range(rows):
            self._grid_frame.rowconfigure(r, weight=1)

    # ── Gaze update (called from main loop) ───────────────────────────

    def update_gaze_zone(self, zone_id: int, timestamp: float):
        """
        Called each frame with the predicted gaze zone.
        Maps the zone to a tile and updates the confirmer.
        """
        # Map zone to tile index on current page
        tile_idx = self._zone_to_grid_index(zone_id)
        tile = self._zone_to_tile.get(tile_idx)
        tile_id = tile.id if tile else None
        logger.info("ZONE MAP: zone_id=%s -> tile_idx=%s tile_id=%s", zone_id, tile_idx, tile_id)
        self._confirmer.update_gaze(tile_idx, tile_id, timestamp)
        self._cursor_zone = tile_idx

        # Update cursor visual
        self._update_cursor(tile_idx)

    def _zone_to_grid_index(self, zone_id: int) -> int:
        """
        Map a gaze zone to a grid tile index on the current page.

        Calibration now uses the same 3-column x 2-row layout as the tile
        grid (6 zones, 0-5), assigned in the same row-major order as
        _render_grid() lays out tiles — so zone_id IS the tile index
        directly, no quadrant/modulo translation needed.
        """
        n = len(self._current_tiles)
        if n == 0 or zone_id < 0 or zone_id >= n:
            return -1
        return zone_id

        # zone_id = quadrant (0-3: TL,TR,BL,BR) * 4 + sub-zone (0-3, same
        # TL/TR/BL/BR pattern within that quadrant). Together these form
        # a 4x4 spatial grid over the screen (row/col each 0-3).
        quad, sub = divmod(zone_id, 4)
        quad_row, quad_col = divmod(quad, 2)
        sub_row, sub_col = divmod(sub, 2)
        row4 = quad_row * 2 + sub_row   # 0-3, top to bottom
        col4 = quad_col * 2 + sub_col   # 0-3, left to right

        rows = (n + GRID_COLS - 1) // GRID_COLS

        # Scale the 4x4 gaze grid down onto however many rows/columns
        # of tiles are actually on screen right now.
        tile_col = min(col4 * GRID_COLS // 4, GRID_COLS - 1)
        tile_row = min(row4 * rows // 4, rows - 1) if rows > 0 else 0

        idx = tile_row * GRID_COLS + tile_col
        return min(idx, n - 1)

    def _update_cursor(self, tile_idx: int):
        """Update the gaze cursor visual indicator."""
        # Highlight the gazed tile
        for idx, w in self._tile_widgets.items():
            if idx == tile_idx:
                w["frame"].config(
                    highlightbackground="#FFD700", highlightthickness=3,
                )
            else:
                armed_id = self._armed_tile_id
                if armed_id and w["tile"].id == armed_id:
                    # Keep armed highlight
                    pass
                else:
                    w["frame"].config(highlightthickness=0)

    # ── Confirmer callbacks ───────────────────────────────────────────

    def _on_tile_armed(self, tile_id: str):
        """Visual feedback when a tile is armed."""
        self._armed_tile_id = tile_id
        self._status_label.config(
            text=f"▶ {tile_id.upper()} armed — long blink to confirm",
            fg="#FFD700",
        )

        # Pulse the armed tile
        for w in self._tile_widgets.values():
            if w["tile"].id == tile_id:
                w["frame"].config(bg="#FFD700", highlightbackground="#FF5722",
                                  highlightthickness=4)
                # Also update child labels
                for child in w["frame"].winfo_children():
                    child.config(bg="#FFD700")

    def _on_tile_confirmed(self, tile_id: str):
        """Handle confirmed tile selection."""
        self._armed_tile_id = None
        self._reset_tile_colors()

        # Find the tile
        tile = None
        for t_list in self._all_tiles.values():
            for t in t_list:
                if t.id == tile_id:
                    tile = t
                    break

        if not tile:
            # Check nav_back
            if tile_id == "nav_back":
                self._show_category(Category.CORE)
                return
            return

        # Navigation tile?
        if tile.is_navigation:
            if tile.id == "nav_pain":
                # Trigger pain scale mode
                if self._on_pain_scale:
                    self._on_pain_scale()
            elif tile.target_category:
                self._show_category(tile.target_category)
            self._status_label.config(text="Look at a tile to select", fg="#666666")
            return

        # Phrase tile confirmed — fire callback
        self._status_label.config(
            text=f"✓ {get_tile_text(tile, self._language)} — spoken",
            fg="#4CAF50",
        )

        if self._on_phrase_confirmed:
            self._on_phrase_confirmed(tile)

        # Record usage for frequency learning
        if self._reorderer:
            self._reorderer.record_use(tile_id)

        logger.info("Phrase confirmed: %s (%s)", tile.text, tile_id)

    def _on_arm_cancelled(self):
        """Reset visuals when arm is cancelled."""
        self._armed_tile_id = None
        self._reset_tile_colors()
        self._status_label.config(text="Look at a tile to select", fg="#666666")

    def _reset_tile_colors(self):
        """Restore all tiles to their original colours."""
        for w in self._tile_widgets.values():
            color = w["original_color"]
            w["frame"].config(bg=color, highlightthickness=0)
            for child in w["frame"].winfo_children():
                child.config(bg=color)

    # ── External controls ─────────────────────────────────────────────

    def set_language(self, language: str):
        """Change the display language and re-render."""
        self._language = language
        self._show_category(self._current_category)

    def show(self):
        """Show the phrase board."""
        self._frame.pack(fill="both", expand=True)

    def hide(self):
        """Hide the phrase board."""
        self._frame.pack_forget()

    def update_fps(self, fps: float):
        """Update FPS display."""
        self._fps_label.config(text=f"{fps:.0f} FPS")
