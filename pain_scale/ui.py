"""
GazeAssist - Pain Scale Communicator UI (Tkinter)

1-10 visual scale with coloured circles and face icons.
Reuses SelectionConfirmer from phrase_board.confirm.
"""

import logging
import tkinter as tk
from typing import Optional, Callable

from phrase_board.confirm import SelectionConfirmer

logger = logging.getLogger(__name__)

# Colour gradient: green → yellow → orange → red
PAIN_COLORS = [
    "#4CAF50",  # 1  green
    "#66BB6A",  # 2
    "#8BC34A",  # 3
    "#CDDC39",  # 4  yellow-green
    "#FFEB3B",  # 5  yellow
    "#FFC107",  # 6  amber
    "#FF9800",  # 7  orange
    "#FF5722",  # 8  deep orange
    "#F44336",  # 9  red
    "#D32F2F",  # 10 dark red
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

        self._frame = tk.Frame(root, bg="#1a1a2e")
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
        # Title
        title_text = PAIN_TRANSLATIONS.get(self._language, "Pain Level")
        tk.Label(
            self._frame, text=title_text,
            font=("Helvetica", 22, "bold"), fg="#e0e0e0", bg="#1a1a2e",
        ).pack(pady=(20, 5))

        tk.Label(
            self._frame, text="Select your pain level (1 = low, 10 = high)",
            font=("Helvetica", 12), fg="#888888", bg="#1a1a2e",
        ).pack(pady=(0, 15))

        # Grid: 2 rows × 5 columns
        grid = tk.Frame(self._frame, bg="#1a1a2e")
        grid.pack(fill="both", expand=True, padx=20, pady=10)

        for i in range(10):
            level = i + 1
            r = i // 5
            c = i % 5

            color = PAIN_COLORS[i]
            emoji = PAIN_EMOJIS[i]

            cell = tk.Frame(grid, bg=color, relief="flat")
            cell.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")

            tk.Label(
                cell, text=emoji, font=("Segoe UI Emoji", 32),
                bg=color, fg="white",
            ).pack(pady=(15, 0))

            tk.Label(
                cell, text=str(level), font=("Helvetica", 24, "bold"),
                bg=color, fg="white",
            ).pack(pady=(0, 15))

            self._level_widgets[i] = {"frame": cell, "color": color, "level": level}
            self._zone_to_level[i] = level

        for c in range(5):
            grid.columnconfigure(c, weight=1)
        for r in range(2):
            grid.rowconfigure(r, weight=1)

        # Status
        self._status_label = tk.Label(
            self._frame, text="Look at a number to select",
            font=("Helvetica", 13), fg="#888888", bg="#1a1a2e",
        )
        self._status_label.pack(pady=10)

        # Back button (also gaze-selectable as zone 10)
        back_frame = tk.Frame(self._frame, bg="#607D8B")
        back_frame.pack(pady=(5, 15))
        tk.Label(
            back_frame, text="🔙 BACK", font=("Helvetica", 14, "bold"),
            bg="#607D8B", fg="white", padx=30, pady=8,
        ).pack()
        self._level_widgets[10] = {"frame": back_frame, "color": "#607D8B", "level": -1}
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
                w["frame"].config(highlightbackground="#FFD700", highlightthickness=3)
            elif self._armed_level is not None and w["level"] == self._armed_level:
                pass  # keep armed highlight
            else:
                w["frame"].config(highlightthickness=0)

    # ── Confirmer callbacks ───────────────────────────────────────────

    def _on_level_armed(self, tile_id: str):
        if tile_id == "pain_back":
            self._status_label.config(text="▶ BACK armed — long blink to confirm", fg="#FFD700")
            return
        try:
            level = int(tile_id.split("_")[1])
            self._armed_level = level
            self._status_label.config(
                text=f"▶ Pain level {level} armed — long blink to confirm",
                fg="#FFD700",
            )
            # Highlight armed level
            for w in self._level_widgets.values():
                if w["level"] == level:
                    w["frame"].config(bg="#FFD700", highlightthickness=4)
                    for child in w["frame"].winfo_children():
                        child.config(bg="#FFD700")
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
                text=f"✓ Pain level {level} recorded", fg="#4CAF50",
            )
            if self._on_pain_confirmed:
                self._on_pain_confirmed(level)
            logger.info("Pain level confirmed: %d", level)
        except (ValueError, IndexError):
            pass

    def _on_arm_cancelled(self):
        self._armed_level = None
        self._reset_colors()
        self._status_label.config(text="Look at a number to select", fg="#888888")

    def _reset_colors(self):
        for w in self._level_widgets.values():
            color = w["color"]
            w["frame"].config(bg=color, highlightthickness=0)
            for child in w["frame"].winfo_children():
                child.config(bg=color)

    # ── Visibility ────────────────────────────────────────────────────

    def show(self):
        self._frame.pack(fill="both", expand=True)

    def hide(self):
        self._frame.pack_forget()
