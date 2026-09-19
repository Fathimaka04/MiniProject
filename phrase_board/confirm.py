"""
GazeAssist - Arm/Confirm selection state machine.

Shared by both the phrase board and pain scale.  Implements:
  IDLE → ARMED  (800 ms dwell OR short deliberate blink)
  ARMED → CONFIRMED  (long deliberate blink within 3 s)
  ARMED → IDLE  (gaze away or timeout)
"""

import time
import threading
import logging
from enum import Enum, auto
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class SelectionState(Enum):
    IDLE = auto()
    ARMED = auto()
    CONFIRMED = auto()


class SelectionConfirmer:
    """
    Two-step selection: dwell/blink to arm, then long-blink to confirm.

    Call update_gaze() every frame and on_blink() when a blink is detected.
    The confirmer manages its own state and fires callbacks on transitions.
    """

    def __init__(
        self,
        dwell_time_ms: int = 800,
        confirm_timeout_s: float = 3.0,
        on_arm: Optional[Callable[[str], None]] = None,
        on_confirm: Optional[Callable[[str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
    ):
        self._dwell_time = dwell_time_ms / 1000.0
        self._confirm_timeout = confirm_timeout_s
        self._on_arm = on_arm
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel

        self._state = SelectionState.IDLE
        self._armed_tile_id: Optional[str] = None
        self._armed_zone: int = -1
        self._armed_time: float = 0.0

        # Dwell tracking
        self._current_zone: int = -1
        self._zone_enter_time: float = 0.0
        self._current_tile_id: Optional[str] = None

        self._lock = threading.Lock()

    # ── Public API ────────────────────────────────────────────────────

    def update_gaze(self, zone_id: int, tile_id: Optional[str], timestamp: float):
        """
        Called each frame with the current gaze zone and tile.

        Args:
            zone_id:  numeric zone identifier from gaze predictor
            tile_id:  the tile ID in that zone (None if empty zone)
            timestamp: time.time()
        """
        with self._lock:
            # Zone changed?
            if zone_id != self._current_zone:
                self._current_zone = zone_id
                self._zone_enter_time = timestamp
                self._current_tile_id = tile_id

                # If we were armed and gaze moved, cancel the armed state
                if self._state == SelectionState.ARMED and zone_id != self._armed_zone:
                    self._cancel_arm()
                return

            self._current_tile_id = tile_id

            # Check dwell time for arming
            if (
                self._state == SelectionState.IDLE
                and tile_id is not None
                and (timestamp - self._zone_enter_time) >= self._dwell_time
            ):
                self._arm_tile(tile_id, zone_id, timestamp)

            # Check confirm timeout
            if (
                self._state == SelectionState.ARMED
                and (timestamp - self._armed_time) >= self._confirm_timeout
            ):
                self._cancel_arm()

    def on_blink(self, blink_type, timestamp: float):
        """
        Called when a blink is detected.

        Args:
            blink_type: BlinkType enum.  SHORT_DELIBERATE arms,
                        LONG_DELIBERATE confirms.
        """
        blink_name = str(blink_type)

        with self._lock:
            if "SHORT" in blink_name or blink_name == "BlinkType.SHORT_DELIBERATE":
                # Short blink → arm the tile we're looking at
                if (
                    self._state == SelectionState.IDLE
                    and self._current_tile_id is not None
                ):
                    self._arm_tile(
                        self._current_tile_id, self._current_zone, timestamp
                    )

            elif "LONG" in blink_name or blink_name == "BlinkType.LONG_DELIBERATE":
                # Long blink → confirm if armed
                if self._state == SelectionState.ARMED:
                    self._confirm()

    def get_state(self) -> tuple[SelectionState, Optional[str]]:
        """Return (current_state, armed_tile_id or None)."""
        with self._lock:
            return self._state, self._armed_tile_id

    def reset(self):
        """Force-reset to IDLE."""
        with self._lock:
            self._state = SelectionState.IDLE
            self._armed_tile_id = None
            self._armed_zone = -1

    # ── Callbacks property setters ────────────────────────────────────

    def set_on_arm(self, cb: Callable[[str], None]):
        self._on_arm = cb

    def set_on_confirm(self, cb: Callable[[str], None]):
        self._on_confirm = cb

    def set_on_cancel(self, cb: Callable[[], None]):
        self._on_cancel = cb

    # ── Internal ──────────────────────────────────────────────────────

    def _arm_tile(self, tile_id: str, zone_id: int, timestamp: float):
        self._state = SelectionState.ARMED
        self._armed_tile_id = tile_id
        self._armed_zone = zone_id
        self._armed_time = timestamp
        logger.info("Tile ARMED: %s", tile_id)

        if self._on_arm:
            try:
                self._on_arm(tile_id)
            except Exception as e:
                logger.error("on_arm callback error: %s", e)

    def _confirm(self):
        tile_id = self._armed_tile_id
        self._state = SelectionState.IDLE
        self._armed_tile_id = None
        self._armed_zone = -1
        logger.info("Tile CONFIRMED: %s", tile_id)

        if self._on_confirm and tile_id:
            try:
                self._on_confirm(tile_id)
            except Exception as e:
                logger.error("on_confirm callback error: %s", e)

    def _cancel_arm(self):
        old_tile = self._armed_tile_id
        self._state = SelectionState.IDLE
        self._armed_tile_id = None
        self._armed_zone = -1
        logger.debug("Arm cancelled (was %s)", old_tile)

        if self._on_cancel:
            try:
                self._on_cancel()
            except Exception as e:
                logger.error("on_cancel callback error: %s", e)
