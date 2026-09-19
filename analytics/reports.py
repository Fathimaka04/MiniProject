"""
GazeAssist - Analytics & Report Generation

Matplotlib-based session reports with PDF export.
"""

import logging
import os
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for PDF generation
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

logger = logging.getLogger(__name__)

# Colour palette matching the app theme
BLUE = "#4A90D9"
RED = "#F44336"
GREEN = "#4CAF50"
ORANGE = "#FF9800"
PAIN_COLORS = [
    "#4CAF50", "#66BB6A", "#8BC34A", "#CDDC39", "#FFEB3B",
    "#FFC107", "#FF9800", "#FF5722", "#F44336", "#D32F2F",
]


def generate_session_report(db, session_id: int, output_dir: str = ".") -> str:
    """
    Generate a multi-page PDF report for a session.

    Returns the path to the generated PDF.
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pdf_path = os.path.join(output_dir, f"gazeassist_report_{timestamp}.pdf")

    log = db.get_session_log(session_id)
    pain = db.get_pain_history(session_id)
    phrases = db.get_most_used_phrases(session_id, limit=10)
    sos = db.get_sos_events(session_id)
    stats = db.get_session_stats(session_id)

    with PdfPages(pdf_path) as pdf:
        # ── Page 1: Summary ──────────────────────────────────────────
        fig, ax = plt.subplots(figsize=(8.5, 6))
        ax.axis("off")
        ax.set_title("GazeAssist — Session Report", fontsize=20, fontweight="bold", pad=20)

        summary_text = (
            f"Session Duration: {stats.get('duration', 'N/A')}\n"
            f"Total Messages: {stats.get('total_messages', 0)}\n"
            f"Pain Readings: {len(pain)}\n"
            f"SOS Events: {len(sos)}\n"
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        ax.text(0.5, 0.5, summary_text, transform=ax.transAxes,
                fontsize=14, verticalalignment="center", horizontalalignment="center",
                fontfamily="monospace",
                bbox=dict(boxstyle="round,pad=1", facecolor="#f0f0f0"))
        pdf.savefig(fig)
        plt.close(fig)

        # ── Page 2: Communication frequency over time ────────────────
        if log:
            fig, ax = plt.subplots(figsize=(10, 5))
            times = [entry.get("timestamp", "") for entry in log]
            # Simple: plot message index over time
            ax.plot(range(len(log)), [1] * len(log), "o", color=BLUE, markersize=4)
            ax.set_xlabel("Message #")
            ax.set_title("Communication Activity")
            ax.set_yticks([])
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # ── Page 3: Pain level trend ─────────────────────────────────
        if pain:
            fig, ax = plt.subplots(figsize=(10, 5))
            levels = [p["pain_level"] for p in pain]
            timestamps = list(range(len(levels)))
            colors = [PAIN_COLORS[min(l - 1, 9)] for l in levels]

            ax.bar(timestamps, levels, color=colors, edgecolor="white", linewidth=0.5)
            ax.plot(timestamps, levels, "o-", color=RED, markersize=6, linewidth=2)
            ax.set_ylim(0, 11)
            ax.set_ylabel("Pain Level")
            ax.set_xlabel("Reading #")
            ax.set_title("Pain Level Trend")
            ax.axhline(y=7, color="#FF5722", linestyle="--", alpha=0.5, label="High pain threshold")
            ax.legend()
            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

        # ── Page 4: Most used phrases ────────────────────────────────
        if phrases:
            fig, ax = plt.subplots(figsize=(10, 5))
            labels = [p["phrase"] for p in phrases]
            counts = [p["count"] for p in phrases]

            bars = ax.barh(labels, counts, color=BLUE, edgecolor="white")
            ax.set_xlabel("Usage Count")
            ax.set_title("Most Used Phrases")
            ax.invert_yaxis()

            for bar, count in zip(bars, counts):
                ax.text(bar.get_width() + 0.3, bar.get_y() + bar.get_height() / 2,
                        str(count), va="center", fontsize=10, fontweight="bold")

            fig.tight_layout()
            pdf.savefig(fig)
            plt.close(fig)

    logger.info("Report generated: %s", pdf_path)
    return pdf_path
