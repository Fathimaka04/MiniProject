"""
GazeAssist - Pain Scale Communicator UI (Tkinter — premium redesign)

1-10 visual scale with coloured circles, face icons, and matching dark theme.
Reuses SelectionConfirmer from phrase_board.confirm.
"""

import logging
import tkinter as tk
from typing import Optional, Callable

from phrase_board.confirm import SelectionConfirmer

logger = logging.getLogger(__name__)

# ── Theme (matches phrase board) ──────────────────────────────────────
BG       = "#0B0F1A"
SURFACE  = "#131B2E"
TEXT     = "#F0F4FC"
MUTED    = "#6B7FA0"
ACCENT   = "#4FC3F7"
ARMED    = "#FFB74D"
SUCCESS  = "#66BB6A"
FONT     = "Segoe UI"
EMOJI    = "Segoe UI Emoji"

# Colour gradient: green → yellow → orange → red
PAIN_COLORS = [
    "#43A047",  # 1  green
    "#66BB6A",  # 2
    "#9CCC65",  # 3
    "#D4E157",  # 4  yellow-green
    "#FFEE58",  # 5  yellow
    "#FFC107",  # 6  amber
    "#FF9800",  # 7  orange
    "#FF5722",  # 8  deep orange
    "#F44336",  # 9  red
    "#C62828",  # 10 dark red
]

PAIN_EMOJIS = ["😊", "🙂", "😐", "😕", "😟", "😣", "😖", "😫", "😩", "😭"]

PAIN_TRANSLATIONS = {
    "hi": "दर्द स्तर",
    "ml": "വേദന ലെവൽ",
    "ta": "வலி நிலை",
    "te": "నొప్పి స్థాయి",
}


class PainScaleUI:
    """
    1-10 pain scale UI with same arm/confirm mechanism as phrase board.
    """

    def __init__(
        self,
        root: tk.Tk,
        confirmer: SelectionConfirmer,
        language: str = "hi",
        on_pain_confirmed: Optional[Callable[[int], None]] = None,
        on_back: Optional[Callable[[], None]] = None,
    ):
        self._root = root
        self._confirmer = confirmer
        self._language = language
        self._on_pain_confirmed = on_pain_confirmed
        self._on_back = on_back

        self._frame = tk.Frame(root, bg=BG)
        self._level_widgets: dict[int, dict] = {}
        self._zone_to_level: dict[int, int] = {}
        self._armed_level: Optional[int] = None

        self._build_ui()

        # Wire confirmer
        self._confirmer.set_on_arm(self._on_level_armed)
        self._confirmer.set_on_confirm(self._on_level_confirmed)
        self._confirmer.set_on_cancel(self._on_arm_cancelled)

    def _build_ui(self):
        """Build the pain scale UI."""
        # Header
        header = tk.Frame(self._frame, bg="#0D1220", height=56)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="😣", font=(EMOJI, 18), fg=ACCENT,
                 bg="#0D1220").pack(side="left", padx=(24, 8))

        title_text = PAIN_TRANSLATIONS.get(self._language, "Pain Level")
        tk.Label(
            header, text=title_text,
            font=(FONT, 18, "bold"), fg=TEXT, bg="#0D1220",
        ).pack(side="left")

        tk.Label(
            header, text="Select your pain level (1 = low, 10 = high)",
            font=(FONT, 11), fg=MUTED, bg="#0D1220",
        ).pack(side="left", padx=30)

        # Accent line
        tk.Frame(self._frame, bg=ACCENT, height=1).pack(fill="x")

        # Grid: 2 rows × 5 columns
        grid = tk.Frame(self._frame, bg=BG)
        grid.pack(fill="both", expand=True, padx=24, pady=18)

        for i in range(10):
            level = i + 1
            r = i // 5
            c = i % 5

            color = PAIN_COLORS[i]
            emoji = PAIN_EMOJIS[i]

            cell = tk.Frame(grid, bg=SURFACE,
                            highlightthickness=2,
                            highlightbackground="#1E2A45",
                            highlightcolor="#1E2A45")
            cell.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")

            # Colour strip at top
            strip = tk.Frame(cell, bg=color, height=4)
            strip._keep_bg = True
            strip.pack(fill="x", side="top")

            tk.Label(
                cell, text=emoji, font=(EMOJI, 30),
                bg=SURFACE, fg=TEXT,
            ).pack(pady=(12, 2))

            tk.Label(
                cell, text=str(level), font=(FONT, 22, "bold"),
                bg=SURFACE, fg=TEXT,
            ).pack(pady=(0, 12))

            self._level_widgets[i] = {"frame": cell, "color": color,
                                       "level": level, "strip": strip}
            self._zone_to_level[i] = level

        for c in range(5):
            grid.columnconfigure(c, weight=1, uniform="pcol")
        for r in range(2):
            grid.rowconfigure(r, weight=1, uniform="prow")

        # Status bar
        status = tk.Frame(self._frame, bg=BG, height=56)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self._status_label = tk.Label(
            status, text="👁  Look at a number to select",
            font=(FONT, 14), fg=MUTED, bg=BG,
        )
        self._status_label.pack(expand=True)

        # Back button (also gaze-selectable as zone 10)
        back_frame = tk.Frame(self._frame, bg="#1E2A45",
                              highlightthickness=2,
                              highlightbackground="#2A3A55",
                              highlightcolor="#2A3A55")
        back_frame.pack(pady=(0, 12))
        tk.Label(
            back_frame, text="←  BACK", font=(FONT, 13, "bold"),
            bg="#1E2A45", fg=MUTED, padx=28, pady=8,
        ).pack()
        self._level_widgets[10] = {"frame": back_frame, "color": "#1E2A45",
                                    "level": -1, "strip": None}
        self._zone_to_level[10] = -1  # -1 = back

    # ── Gaze updates ──────────────────────────────────────────────────

    def update_gaze_zone(self, zone_id: int, timestamp: float):
        """Called each frame with predicted gaze zone."""
        idx = zone_id % 11  # 10 pain levels + 1 back
        level = self._zone_to_level.get(idx, -1)
        tile_id = f"pain_{level}" if level > 0 else "pain_back"

        self._confirmer.update_gaze(idx, tile_id, timestamp)

        # Visual highlight
        for i, w in self._level_widgets.items():
            if i == idx:
                w["frame"].config(highlightbackground=ACCENT,
                                  highlightcolor=ACCENT)
            elif self._armed_level is not None and w["level"] == self._armed_level:
                pass  # keep armed highlight
            else:
                w["frame"].config(highlightbackground="#1E2A45",
                                  highlightcolor="#1E2A45")

    # ── Confirmer callbacks ───────────────────────────────────────────

    def _on_level_armed(self, tile_id: str):
        if tile_id == "pain_back":
            self._status_label.config(
                text="⚡ BACK armed — close eyes to confirm", fg=ARMED)
            return
        try:
            level = int(tile_id.split("_")[1])
            self._armed_level = level
            self._status_label.config(
                text=f"⚡ Pain level {level} armed — close eyes to confirm",
                fg=ARMED,
            )
            # Highlight armed level
            for w in self._level_widgets.values():
                if w["level"] == level:
                    w["frame"].config(highlightbackground=ARMED,
                                      highlightcolor=ARMED,
                                      highlightthickness=3)
        except (ValueError, IndexError):
            pass

    def _on_level_confirmed(self, tile_id: str):
        self._armed_level = None
        self._reset_colors()

        if tile_id == "pain_back":
            if self._on_back:
                self._on_back()
            return

        try:
            level = int(tile_id.split("_")[1])
            self._status_label.config(
                text=f"✓  Pain level {level} recorded", fg=SUCCESS,
            )
            if self._on_pain_confirmed:
                self._on_pain_confirmed(level)
            logger.info("Pain level confirmed: %d", level)
        except (ValueError, IndexError):
            pass

    def _on_arm_cancelled(self):
        self._armed_level = None
        self._reset_colors()
        self._status_label.config(
            text="👁  Look at a number to select", fg=MUTED)

    def _reset_colors(self):
        for w in self._level_widgets.values():
            w["frame"].config(highlightbackground="#1E2A45",
                              highlightcolor="#1E2A45",
                              highlightthickness=2)

    # ── Visibility ────────────────────────────────────────────────────

    def show(self):
        self._frame.pack(fill="both", expand=True)

    def hide(self):
        self._frame.pack_forget()
