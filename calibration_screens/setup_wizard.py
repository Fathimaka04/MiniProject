"""
GazeAssist - First-run Setup Wizard (Tkinter)

Collects language preference, emergency contact, then launches
gaze calibration and blink enrollment.
"""

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
        self._window.geometry("620x520")
        self._window.configure(bg="#1a1a2e")
        self._window.resizable(False, False)
# NEW
        # Center the wizard window on screen
        self._window.update_idletasks()
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        x = (screen_w - 520) // 2
        y = (screen_h - 620) // 2
        self._window.geometry(f"520x620+{x}+{y}")

        # Bring the wizard to the front
        self._window.lift()
        self._window.attributes("-topmost", True)
        self._window.after(200, lambda: self._window.attributes("-topmost", False))
# NEW

        # Main container
        self._container = tk.Frame(self._window, bg="#1a1a2e")
        self._container.pack(fill="both", expand=True, padx=40, pady=30)

        self._show_welcome()

    def _clear(self):
        for w in self._container.winfo_children():
            w.destroy()

    # ── Step 1: Welcome ──────────────────────────────────────────────

    def _show_welcome(self):
        self._clear()

        tk.Label(
            self._container, text="👁️ GazeAssist", font=("Helvetica", 28, "bold"),
            fg="#4A90D9", bg="#1a1a2e",
        ).pack(pady=(20, 10))

        tk.Label(
            self._container,
            text="Assistive Communication System\nfor Non-verbal & Motor-impaired Users",
            font=("Helvetica", 14), fg="#aaaaaa", bg="#1a1a2e", justify="center",
        ).pack(pady=(0, 25))

        tk.Label(
            self._container, text="This setup takes about 3 minutes:",
            font=("Helvetica", 12), fg="#888888", bg="#1a1a2e",
        ).pack(anchor="w", pady=(10, 5))

        steps = [
            "1. Choose your language",
            "2. Enter emergency contact",
            "3. Gaze calibration (90 seconds)",
            "4. Blink enrollment (2 minutes)",
        ]
        for step in steps:
            tk.Label(
                self._container, text=f"  {step}",
                font=("Helvetica", 12), fg="#cccccc", bg="#1a1a2e",
            ).pack(anchor="w", pady=2)

        tk.Button(
            self._container, text="Begin Setup →", font=("Helvetica", 14, "bold"),
            bg="#4A90D9", fg="white", command=self._show_user_info,
            padx=25, pady=10, relief="flat",
        ).pack(pady=30)

    # ── Step 2: User info + Language ─────────────────────────────────

    def _show_user_info(self):
        self._clear()

        tk.Label(
            self._container, text="User Information",
            font=("Helvetica", 22, "bold"), fg="#e0e0e0", bg="#1a1a2e",
        ).pack(pady=(10, 20))

        # Name
        tk.Label(
            self._container, text="Patient / User Name:",
            font=("Helvetica", 13), fg="#aaaaaa", bg="#1a1a2e",
        ).pack(anchor="w", pady=(10, 3))
        name_entry = tk.Entry(
            self._container, textvariable=self._user_name,
            font=("Helvetica", 14), bg="#16213e", fg="white",
            insertbackground="white", relief="flat",
        )
        name_entry.pack(fill="x", pady=(0, 15), ipady=8)

        # Language selection
        tk.Label(
            self._container, text="Preferred Language:",
            font=("Helvetica", 13), fg="#aaaaaa", bg="#1a1a2e",
        ).pack(anchor="w", pady=(10, 5))

        for code, label in LANGUAGES.items():
            rb = tk.Radiobutton(
                self._container, text=label, variable=self._selected_language,
                value=code, font=("Helvetica", 14), fg="#e0e0e0", bg="#1a1a2e",
                selectcolor="#16213e", activebackground="#1a1a2e",
                activeforeground="#4A90D9",
            )
            rb.pack(anchor="w", padx=20, pady=2)

        # Emergency contact
        tk.Label(
            self._container, text="Emergency Contact (phone):",
            font=("Helvetica", 13), fg="#aaaaaa", bg="#1a1a2e",
        ).pack(anchor="w", pady=(20, 3))
        contact_entry = tk.Entry(
            self._container, textvariable=self._emergency_contact,
            font=("Helvetica", 14), bg="#16213e", fg="white",
            insertbackground="white", relief="flat",
        )
        contact_entry.pack(fill="x", pady=(0, 10), ipady=8)
        tk.Label(
            self._container, text="(Optional — for SOS WhatsApp/SMS alerts)",
            font=("Helvetica", 10), fg="#666666", bg="#1a1a2e",
        ).pack(anchor="w")

        tk.Button(
            self._container, text="Continue →", font=("Helvetica", 14, "bold"),
            bg="#4CAF50", fg="white", command=self._finish,
            padx=25, pady=10, relief="flat",
        ).pack(pady=25)

    # ── Finish ────────────────────────────────────────────────────────

    def _finish(self):
        name = self._user_name.get().strip() or "Patient"
        language = self._selected_language.get()
        contact = self._emergency_contact.get().strip()

        logger.info("Setup complete: name=%s language=%s contact=%s", name, language, contact)

        if self._window:
            self._window.destroy()
            self._window = None

        self._on_complete(name, language, contact)
