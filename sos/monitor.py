"""
GazeAssist - Emergency SOS Background Monitor

Watches for the triple-long-blink pattern continuously, independent of
the current UI mode. Fires all alert channels simultaneously on trigger.
"""

import time
import threading
import logging
from typing import Callable, Optional
from collections import deque

logger = logging.getLogger(__name__)


class SOSMonitor:
    """
    Background blink-pattern watcher for emergency SOS.

    Watches for 3 LONG_DELIBERATE blinks within a configurable time window.
    Runs in its own thread, consuming blink events from the classifier.
    Independent of the current UI mode — never gated behind phrase board focus.

    Usage:
        monitor = SOSMonitor(on_trigger=fire_all_alerts)
        monitor.start()
        ...
        # From the blink classifier callback:
        monitor.on_blink(BlinkType.LONG_DELIBERATE)
        ...
        monitor.stop()
    """

    def __init__(
        self,
        on_trigger: Callable[[], None],
        required_blinks: int = 3,
        window_seconds: float = 10.0,
        cooldown_seconds: float = 30.0,
    ):
        """
        Args:
            on_trigger: Callback fired when SOS pattern detected.
            required_blinks: Number of long blinks needed (default: 3).
            window_seconds: Time window for the blink pattern (default: 10s).
            cooldown_seconds: Minimum time between SOS triggers (default: 30s).
        """
        self._on_trigger = on_trigger
        self._required_blinks = required_blinks
        self._window_seconds = window_seconds
        self._cooldown_seconds = cooldown_seconds

        self._long_blink_times: deque = deque(maxlen=required_blinks * 2)
        self._lock = threading.Lock()
        self._running = False
        self._last_trigger_time = 0.0
        self._triggered = threading.Event()

    def start(self):
        """Start the SOS monitor."""
        self._running = True
        logger.info(
            f"SOS monitor active: {self._required_blinks} long blinks "
            f"in {self._window_seconds}s window"
        )

    def stop(self):
        """Stop the SOS monitor."""
        self._running = False
        logger.info("SOS monitor stopped")

    def on_blink(self, blink_type) -> bool:
        """
        Called by the blink classifier on each detected blink.
        Returns True if SOS was triggered.

        Args:
            blink_type: BlinkType enum value. Only LONG_DELIBERATE counts.
        """
        if not self._running:
            return False

        # Import here to avoid circular dependency
        try:
            from blink.classifier import BlinkType
            if blink_type != BlinkType.LONG_DELIBERATE:
                return False
        except ImportError:
            # Fallback: check string representation
            if "LONG" not in str(blink_type):
                return False

        now = time.time()

        with self._lock:
            self._long_blink_times.append(now)

            # Prune blinks outside the window
            cutoff = now - self._window_seconds
            while self._long_blink_times and self._long_blink_times[0] < cutoff:
                self._long_blink_times.popleft()

            # Check if pattern matches
            if len(self._long_blink_times) >= self._required_blinks:
                # Check cooldown
                if now - self._last_trigger_time < self._cooldown_seconds:
                    logger.warning("SOS pattern detected but in cooldown period")
                    return False

                # TRIGGER SOS
                self._last_trigger_time = now
                self._long_blink_times.clear()
                self._triggered.set()

                logger.critical("🚨 SOS TRIGGERED — firing all alert channels")

                # Fire trigger in a separate thread so it doesn't block
                trigger_thread = threading.Thread(
                    target=self._safe_trigger, daemon=True
                )
                trigger_thread.start()
                return True

        return False

    def _safe_trigger(self):
        """Fire the trigger callback safely."""
        try:
            self._on_trigger()
        except Exception as e:
            logger.error(f"SOS trigger callback error: {e}")
        finally:
            self._triggered.clear()

    @property
    def is_armed(self) -> bool:
        """True if we have some long blinks in the buffer (building toward SOS)."""
        with self._lock:
            now = time.time()
            cutoff = now - self._window_seconds
            recent = sum(1 for t in self._long_blink_times if t >= cutoff)
            return recent > 0

    @property
    def blinks_toward_sos(self) -> int:
        """Number of qualifying long blinks in the current window."""
        with self._lock:
            now = time.time()
            cutoff = now - self._window_seconds
            return sum(1 for t in self._long_blink_times if t >= cutoff)

    def reset(self):
        """Clear the blink buffer (e.g., after false alarm dismissal)."""
        with self._lock:
            self._long_blink_times.clear()
