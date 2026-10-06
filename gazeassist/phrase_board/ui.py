"""
GazeAssist - Tkinter Phrase Board UI (polished)

Card-style 3x2 tile grid with:
  * calm dark theme, large high-contrast text (Nirmala UI covers Indic scripts)
  * a live dwell-progress bar on the tile being looked at
  * clear ARMED state + "close your eyes to confirm" prompt
  * emergency tiles with a permanent red outline
  * breadcrumb header (Home > More > Emergency)

Public API is unchanged: update_gaze_zone(), set_language(), show(), hide(),
update_fps(). Arm/confirm logic still lives entirely in SelectionConfirmer.
"""

import logging
import tkinter as tk
from typing import Optional, Callable

from phrase_board.tiles import (
    Category, Tile, create_default_tiles, get_tile_text,
)
from phrase_board.confirm import SelectionConfirmer
from phrase_board.frequency import TileReorderer

logger = logging.getLogger(__name__)

GRID_COLS = 3
TILE_PAD = 12

# ── Theme ─────────────────────────────────────────────────────────────
BG = "#0F172A"          # app background (slate 900)
SURFACE = "#1E293B"     # card
SURFACE_HI = "#273449"  # armed card
BORDER = "#334155"      # idle card border
HEADER_BG = "#111C33"
TEXT = "#F1F5F9"
MUTED = "#94A3B8"
ACCENT = "#38BDF8"      # gaze hover
ARMED = "#FBBF24"       # armed / waiting for confirm
SUCCESS = "#34D399"
DANGER = "#EF4444"
DANGER_BG = "#2A1519"

UI_FONT = "Segoe UI"
SCRIPT_FONT = "Nirmala UI"   # Malayalam/Hindi/Tamil/Telugu on Windows
EMOJI_FONT = "Segoe UI Emoji"

# Layout — gaze/calibration.py imports these so calibration dots land
# exactly on the tile centres. Change them here only.
HEADER_H = 64
STATUS_H = 64
GRID_PAD_X = 24
GRID_PAD_TOP = 20

BORDER_W = 4             # fixed so highlighting never changes tile size
STRIP_H = 6              # coloured category strip on top of each card
DWELL_BAR_H = 6

PARENT = {
    Category.MORE: Category.CORE,
    Category.MEDICAL: Category.MORE,
    Category.SOCIAL: Category.MORE,
    Category.EMERGENCY: Category.MORE,
}
CRUMB = {
    Category.CORE: "Home",
    Category.MORE: "Home  ›  More",
    Category.MEDICAL: "Home  ›  More  ›  Medical",
    Category.SOCIAL: "Home  ›  More  ›  Social",
    Category.EMERGENCY: "Home  ›  More  ›  Emergency",
}


class PhraseBoardUI:
    """Tkinter phrase board — the core AAC communication interface."""

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
        self._dwell_s = getattr(confirmer, "_dwell_time", 1.2) or 1.2

        self._all_tiles = create_default_tiles()
        self._current_category = Category.CORE
        self._current_tiles: list[Tile] = []
        self._tile_widgets: dict[int, dict] = {}
        self._zone_to_tile: dict[int, Tile] = {}

        self._armed_tile_id: Optional[str] = None
        self._hover_idx: Optional[int] = None
        self._hover_start = 0.0

        # ── layout ────────────────────────────────────────────────────
        self._frame = tk.Frame(root, bg=BG)
        self._frame.pack(fill="both", expand=True)

        header = tk.Frame(self._frame, bg=HEADER_BG, height=HEADER_H)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="●", font=(UI_FONT, 16), fg=ACCENT,
                 bg=HEADER_BG).pack(side="left", padx=(20, 6))
        tk.Label(header, text="GazeAssist", font=(UI_FONT, 18, "bold"),
                 fg=TEXT, bg=HEADER_BG).pack(side="left")

        self._crumb_label = tk.Label(header, text="", font=(UI_FONT, 14),
                                     fg=MUTED, bg=HEADER_BG)
        self._crumb_label.pack(side="left", padx=30)

        self._fps_label = tk.Label(header, text="", font=(UI_FONT, 11),
                                   fg=BORDER, bg=HEADER_BG)
        self._fps_label.pack(side="right", padx=20)

        # Status bar packed BEFORE the grid so it always keeps its space
        status = tk.Frame(self._frame, bg=BG, height=STATUS_H)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self._status_label = tk.Label(
            status, text="", font=(UI_FONT, 16, "bold"), fg=MUTED, bg=BG,
        )
        self._status_label.pack(expand=True)

        self._grid_frame = tk.Frame(self._frame, bg=BG)
        self._grid_frame.pack(fill="both", expand=True, padx=GRID_PAD_X, pady=(GRID_PAD_TOP, 0))

        self._set_status_idle()

        self._confirmer.set_on_arm(self._on_tile_armed)
        self._confirmer.set_on_confirm(self._on_tile_confirmed)
        self._confirmer.set_on_cancel(self._on_arm_cancelled)

        self._show_category(Category.CORE)

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _paint(widget, bg: str):
        """Recursively set background (labels/frames inside a card)."""
        if not getattr(widget, "_keep_bg", False):
            try:
                widget.config(bg=bg)
            except tk.TclError:
                pass
        for child in widget.winfo_children():
            PhraseBoardUI._paint(child, bg)

    def _set_status_idle(self):
        self._status_label.config(text="Look at a tile to choose it", fg=MUTED)

    @staticmethod
    def _card_colours(tile: Tile) -> tuple[str, str]:
        """(background, idle border) for a tile."""
        if getattr(tile, "is_emergency", False):
            return DANGER_BG, DANGER
        return SURFACE, BORDER

    # ── Page rendering ────────────────────────────────────────────────

    def _show_category(self, category: Category):
        self._current_category = category

        if category == Category.CORE:
            self._current_tiles = list(self._all_tiles.get(Category.CORE, []))
        else:
            self._current_tiles = list(self._all_tiles.get(category, []))
            parent = PARENT.get(category, Category.CORE)
            back_tile = Tile(
                "nav_back", "BACK", Category.NAVIGATION,
                {"hi": "वापस", "ml": "തിരികെ", "ta": "பின்", "te": "వెనుకకు"},
                "#64748B", "←", is_navigation=True, target_category=parent,
            )
            self._current_tiles.insert(0, back_tile)

        if self._reorderer and category != Category.CORE:
            non_nav = [t for t in self._current_tiles if not t.is_navigation]
            nav = [t for t in self._current_tiles if t.is_navigation]
            non_nav = self._reorderer.reorder_tiles(non_nav, GRID_COLS)
            self._current_tiles = nav + non_nav

        self._crumb_label.config(text=CRUMB.get(category, category.name.title()))
        self._render_grid()

    def _render_grid(self):
        for w in self._grid_frame.winfo_children():
            w.destroy()
        self._tile_widgets.clear()
        self._zone_to_tile.clear()
        self._hover_idx = None

        tiles = self._current_tiles
        # Always 2 rows so tiles never jump size/position between pages
        # (keeps them aligned with the 3x2 calibration zones).
        rows = max(2, (len(tiles) + GRID_COLS - 1) // GRID_COLS)

        for i, tile in enumerate(tiles):
            r, c = divmod(i, GRID_COLS)
            bg, border = self._card_colours(tile)

            card = tk.Frame(
                self._grid_frame, bg=bg,
                highlightthickness=BORDER_W,
                highlightbackground=border, highlightcolor=border, bd=0,
            )
            card.grid(row=r, column=c, padx=TILE_PAD, pady=TILE_PAD, sticky="nsew")

            strip = tk.Frame(card, bg=tile.color, height=STRIP_H)
            strip._keep_bg = True
            strip.pack(fill="x", side="top")

            bar = tk.Canvas(card, height=DWELL_BAR_H, bg=bg,
                            highlightthickness=0, bd=0)
            bar.pack(fill="x", side="bottom")
            bar_item = bar.create_rectangle(0, 0, 0, DWELL_BAR_H, fill=ACCENT, width=0)

            body = tk.Frame(card, bg=bg)
            body.pack(fill="both", expand=True)

            tk.Label(body, text=tile.emoji, font=(EMOJI_FONT, 40),
                     bg=bg, fg=TEXT).pack(pady=(10, 4), expand=True, anchor="s")

            main_text = get_tile_text(tile, self._language)
            main_lbl = tk.Label(body, text=main_text, font=(SCRIPT_FONT, 22, "bold"),
                                bg=bg, fg=TEXT, justify="center")
            main_lbl.pack(padx=12)

            if main_text != tile.text:
                tk.Label(body, text=tile.text, font=(UI_FONT, 12),
                         bg=bg, fg=MUTED).pack(pady=(2, 0))

            tk.Frame(body, bg=bg, height=10).pack(expand=True, anchor="n")

            # wrap long phrases to the card width
            card.bind("<Configure>",
                      lambda e, l=main_lbl: l.config(wraplength=max(120, e.width - 40)))

            self._tile_widgets[i] = {
                "frame": card, "tile": tile, "bg": bg, "border": border,
                "bar": bar, "bar_item": bar_item,
            }
            self._zone_to_tile[i] = tile

        for c in range(GRID_COLS):
            self._grid_frame.columnconfigure(c, weight=1, uniform="col")
        for r in range(rows):
            self._grid_frame.rowconfigure(r, weight=1, uniform="row")

    # ── Gaze update (called ~30 Hz from main loop) ────────────────────

    def update_gaze_zone(self, zone_id: int, timestamp: float):
        tile_idx = self._zone_to_grid_index(zone_id)
        tile = self._zone_to_tile.get(tile_idx)
        tile_id = tile.id if tile else None
        logger.debug("ZONE MAP: zone_id=%s -> tile_idx=%s tile_id=%s",
                     zone_id, tile_idx, tile_id)
        self._confirmer.update_gaze(tile_idx, tile_id, timestamp)
        self._update_hover(tile_idx, timestamp)

    def _zone_to_grid_index(self, zone_id: int) -> int:
        """Calibration zones (0-5) map 1:1 onto tile positions."""
        n = len(self._current_tiles)
        if n == 0 or zone_id < 0 or zone_id >= n:
            return -1
        return zone_id

    def _update_hover(self, tile_idx: int, now: float):
        """Border + dwell bar for the tile being looked at.

        Border colour only changes when the hovered tile changes (no
        per-frame reconfigure -> no flicker). The dwell bar is a single
        canvas coords() update, which is cheap.
        """
        if tile_idx != self._hover_idx:
            prev = self._tile_widgets.get(self._hover_idx)
            if prev and prev["tile"].id != self._armed_tile_id:
                prev["frame"].config(highlightbackground=prev["border"],
                                     highlightcolor=prev["border"])
                prev["bar"].coords(prev["bar_item"], 0, 0, 0, DWELL_BAR_H)

            cur = self._tile_widgets.get(tile_idx)
            if cur and cur["tile"].id != self._armed_tile_id:
                cur["frame"].config(highlightbackground=ACCENT, highlightcolor=ACCENT)
            self._hover_idx = tile_idx
            self._hover_start = now

        cur = self._tile_widgets.get(tile_idx)
        if cur and cur["tile"].id != self._armed_tile_id:
            frac = min(1.0, (now - self._hover_start) / self._dwell_s)
            width = cur["bar"].winfo_width()
            cur["bar"].coords(cur["bar_item"], 0, 0, int(width * frac), DWELL_BAR_H)

    # ── Confirmer callbacks ───────────────────────────────────────────

    def _on_tile_armed(self, tile_id: str):
        self._armed_tile_id = tile_id
        for w in self._tile_widgets.values():
            if w["tile"].id == tile_id:
                w["frame"].config(highlightbackground=ARMED, highlightcolor=ARMED)
                self._paint(w["frame"], SURFACE_HI)
                w["bar"].itemconfig(w["bar_item"], fill=ARMED)
                w["bar"].coords(w["bar_item"], 0, 0, w["bar"].winfo_width(), DWELL_BAR_H)
                name = get_tile_text(w["tile"], self._language)
                self._status_label.config(
                    text=f"{name}  —  close your eyes for a moment to confirm",
                    fg=ARMED,
                )
                break

    def _on_tile_confirmed(self, tile_id: str):
        self._armed_tile_id = None
        self._reset_tiles()

        if tile_id == "nav_back":
            self._show_category(PARENT.get(self._current_category, Category.CORE))
            self._set_status_idle()
            return

        tile = next((t for ts in self._all_tiles.values() for t in ts
                     if t.id == tile_id), None)
        if not tile:
            return

        if tile.is_navigation:
            if tile.id == "nav_pain":
                if self._on_pain_scale:
                    self._on_pain_scale()
            elif tile.target_category:
                self._show_category(tile.target_category)
            self._set_status_idle()
            return

        is_sos = getattr(tile, "is_emergency", False)
        self._status_label.config(
            text=("🚨  Alert sent: " if is_sos else "✓  ") + get_tile_text(tile, self._language),
            fg=DANGER if is_sos else SUCCESS,
        )

        if self._on_phrase_confirmed:
            self._on_phrase_confirmed(tile)
        if self._reorderer:
            self._reorderer.record_use(tile_id)
        logger.info("Phrase confirmed: %s (%s)", tile.text, tile_id)

    def _on_arm_cancelled(self):
        self._armed_tile_id = None
        self._reset_tiles()
        self._set_status_idle()

    def _reset_tiles(self):
        for w in self._tile_widgets.values():
            self._paint(w["frame"], w["bg"])
            w["frame"].config(highlightbackground=w["border"],
                              highlightcolor=w["border"])
            w["bar"].itemconfig(w["bar_item"], fill=ACCENT)
            w["bar"].coords(w["bar_item"], 0, 0, 0, DWELL_BAR_H)
        self._hover_idx = None   # hover re-applied on next gaze frame

    # ── External controls ─────────────────────────────────────────────

    def set_language(self, language: str):
        self._language = language
        self._show_category(self._current_category)

    def show(self):
        self._frame.pack(fill="both", expand=True)

    def hide(self):
        self._frame.pack_forget()

    def update_fps(self, fps: float):
        self._fps_label.config(text=f"{fps:.0f} fps")