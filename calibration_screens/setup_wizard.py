"""
GazeAssist - First-run Setup Wizard (Tkinter — premium redesign)

Collects language preference, emergency contact, then launches
gaze calibration and blink enrollment.
"""

import re
import logging
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

logger = logging.getLogger(__name__)

LANGUAGES = {
    "hi": "हिन्दी (Hindi)",
    "ml": "മലയാളം (Malayalam)",
    "ta": "தமிழ் (Tamil)",
    "te": "తెలుగు (Telugu)",
}

# ── Theme ─────────────────────────────────────────────────────────────
BG       = "#0B0F1A"
SURFACE  = "#131B2E"
TEXT     = "#F0F4FC"
MUTED    = "#6B7FA0"
ACCENT   = "#4FC3F7"
SUCCESS  = "#66BB6A"
DANGER   = "#EF5350"
INPUT_BG = "#101828"
FONT     = "Segoe UI"


class SetupWizard:
    """
    First-run wizard: language selection → emergency contact → calibration → enrollment.
    """

    def __init__(
        self,
        on_complete: Callable[[str, str, str], None],
        parent: Optional[tk.Tk] = None,
    ):
        """
        Args:
            on_complete: callback(user_name, language, emergency_contact)
            parent: optional parent Tk window
        """
        self._on_complete = on_complete
        self._parent = parent
        self._window: Optional[tk.Toplevel] = None
        self._selected_language = tk.StringVar(value="hi")
        self._user_name = tk.StringVar(value="Patient")
        self._emergency_contact = tk.StringVar(value="")
        self._step = 0

    def start(self):
        """Open the setup wizard."""
        if self._parent:
            self._window = tk.Toplevel(self._parent)
        else:
            self._window = tk.Tk()

        self._window.title("GazeAssist — Setup")
        self._window.configure(bg=BG)
        self._window.resizable(False, False)

        # Center the wizard window on screen
        self._window.update_idletasks()
        ww, wh = 560, 640
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        x = (screen_w - ww) // 2
        y = (screen_h - wh) // 2
        self._window.geometry(f"{ww}x{wh}+{x}+{y}")

        # Bring the wizard to the front
        self._window.lift()
        self._window.attributes("-topmost", True)
        self._window.after(200, lambda: self._window.attributes("-topmost", False))

        # Main container
        self._container = tk.Frame(self._window, bg=BG)
        self._container.pack(fill="both", expand=True, padx=48, pady=36)

        self._show_welcome()

    def _clear(self):
        for w in self._container.winfo_children():
            w.destroy()

    # ── Step 1: Welcome ──────────────────────────────────────────────

    def _show_welcome(self):
        self._clear()

        # Logo
        tk.Label(
            self._container, text="👁", font=("Segoe UI Emoji", 48),
            fg=ACCENT, bg=BG,
        ).pack(pady=(24, 4))

        tk.Label(
            self._container, text="GazeAssist", font=(FONT, 32, "bold"),
            fg=TEXT, bg=BG,
        ).pack(pady=(0, 4))

        tk.Label(
            self._container,
            text="Assistive Communication System\nfor Non-verbal & Motor-impaired Users",
            font=(FONT, 13), fg=MUTED, bg=BG, justify="center",
        ).pack(pady=(0, 28))

        # Divider
        tk.Frame(self._container, bg="#1E2A45", height=1).pack(fill="x", pady=(0, 20))

        tk.Label(
            self._container, text="Quick Setup", font=(FONT, 14, "bold"),
            fg=TEXT, bg=BG,
        ).pack(anchor="w", pady=(0, 10))

        steps = [
            ("1", "Choose your language", ACCENT),
            ("2", "Enter emergency contact", ACCENT),
            ("3", "Eye calibration (~15 seconds)", SUCCESS),
            ("4", "Blink baseline (~2 seconds)", SUCCESS),
        ]
        for num, label, color in steps:
            row = tk.Frame(self._container, bg=BG)
            row.pack(fill="x", pady=4)
            # Number badge
            badge = tk.Label(row, text=num, font=(FONT, 10, "bold"),
                             fg=BG, bg=color, width=3)
            badge.pack(side="left", padx=(0, 12))
            tk.Label(row, text=label, font=(FONT, 13), fg=TEXT, bg=BG).pack(
                side="left")

        # Begin button
        btn = tk.Label(
            self._container, text="   Begin Setup  →   ",
            font=(FONT, 15, "bold"), fg=BG, bg=ACCENT,
            cursor="hand2", padx=28, pady=12,
        )
        btn.pack(pady=(32, 0))
        btn.bind("<Button-1>", lambda e: self._show_user_info())
        btn.bind("<Enter>", lambda e: btn.config(bg="#29B6F6"))
        btn.bind("<Leave>", lambda e: btn.config(bg=ACCENT))

    # ── Step 2: User info + Language ─────────────────────────────────

    def _show_user_info(self):
        self._clear()

        tk.Label(
            self._container, text="User Information",
            font=(FONT, 24, "bold"), fg=TEXT, bg=BG,
        ).pack(pady=(8, 24))

        # Name
        tk.Label(
            self._container, text="PATIENT / USER NAME",
            font=(FONT, 10, "bold"), fg=MUTED, bg=BG,
        ).pack(anchor="w", pady=(0, 4))
        name_entry = tk.Entry(
            self._container, textvariable=self._user_name,
            font=(FONT, 14), bg=INPUT_BG, fg=TEXT,
            insertbackground=ACCENT, relief="flat",
            highlightthickness=1, highlightbackground="#1E2A45",
            highlightcolor=ACCENT,
        )
        name_entry.pack(fill="x", pady=(0, 18), ipady=10)

        # Language selection
        tk.Label(
            self._container, text="PREFERRED LANGUAGE",
            font=(FONT, 10, "bold"), fg=MUTED, bg=BG,
        ).pack(anchor="w", pady=(0, 6))

        lang_frame = tk.Frame(self._container, bg=BG)
        lang_frame.pack(fill="x", pady=(0, 18))

        for code, label in LANGUAGES.items():
            rb = tk.Radiobutton(
                lang_frame, text=label, variable=self._selected_language,
                value=code, font=(FONT, 13), fg=TEXT, bg=BG,
                selectcolor=INPUT_BG, activebackground=BG,
                activeforeground=ACCENT, indicatoron=True,
            )
            rb.pack(anchor="w", padx=8, pady=3)

        # Emergency contact
        tk.Label(
            self._container, text="EMERGENCY CONTACT (PHONE)",
            font=(FONT, 10, "bold"), fg=MUTED, bg=BG,
        ).pack(anchor="w", pady=(4, 4))
        contact_entry = tk.Entry(
            self._container, textvariable=self._emergency_contact,
            font=(FONT, 14), bg=INPUT_BG, fg=TEXT,
            insertbackground=ACCENT, relief="flat",
            highlightthickness=1, highlightbackground="#1E2A45",
            highlightcolor=ACCENT,
        )
        contact_entry.pack(fill="x", pady=(0, 4), ipady=10)
        tk.Label(
            self._container, text="Optional — for SOS WhatsApp/SMS alerts",
            font=(FONT, 9), fg="#3A5070", bg=BG,
        ).pack(anchor="w")

        # Inline validation error label (hidden initially)
        self._contact_error_label = tk.Label(
            self._container, text="",
            font=(FONT, 11, "bold"), fg=DANGER, bg=BG,
        )
        self._contact_error_label.pack(anchor="w", pady=(4, 0))

        # Continue button
        btn = tk.Label(
            self._container, text="   Continue  →   ",
            font=(FONT, 15, "bold"), fg=BG, bg=SUCCESS,
            cursor="hand2", padx=28, pady=12,
        )
        btn.pack(pady=(20, 0))
        btn.bind("<Button-1>", lambda e: self._finish())
        btn.bind("<Enter>", lambda e: btn.config(bg="#81C784"))
        btn.bind("<Leave>", lambda e: btn.config(bg=SUCCESS))

    # ── Finish ────────────────────────────────────────────────────────

    @staticmethod
    def _is_placeholder_phone(number: str) -> bool:
        """Return True if the number looks like a placeholder, not a real phone.

        Catches patterns like +91XXXXXXXXXX, 0000000000, 1234567890, etc.
        """
        if not number:
            return False  # empty is fine (contact is optional)

        # Contains 'X' / 'x' characters (e.g. +91XXXXXXXXXX)
        if re.search(r'[Xx]', number):
            return True

        digits = re.sub(r'\D', '', number)

        # Too few digits to be a real phone number
        if 0 < len(digits) < 7:
            return True

        # All same digit repeated (e.g. 0000000000)
        if digits and len(set(digits)) == 1:
            return True

        # Sequential digits (1234567890)
        if digits in '0123456789012345':
            return True

        return False

    def _finish(self):
        name = self._user_name.get().strip() or "Patient"
        language = self._selected_language.get()
        contact = self._emergency_contact.get().strip()

        # Validate emergency contact — reject obvious placeholders
        if self._is_placeholder_phone(contact):
            self._contact_error_label.config(
                text="⚠ Please enter a real phone number (not a placeholder)."
            )
            logger.warning("Rejected placeholder emergency contact: %s", contact)
            return

        logger.info("Setup complete: name=%s language=%s contact=%s", name, language, contact)

        if self._window:
            self._window.destroy()
            self._window = None

        self._on_complete(name, language, contact)
