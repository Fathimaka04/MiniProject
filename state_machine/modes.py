"""
GazeAssist - State Machine

Explicit mode-based state machine. SOS monitoring is NOT a mode —
it runs always in the background independent of current state.
"""

import threading
import logging
from enum import Enum, auto
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class AppMode(Enum):
    SETUP = auto()
    ENROLLMENT = auto()
    CALIBRATING = auto()
    NAVIGATE = auto()
    PAIN = auto()


# Valid transitions: (from_mode, to_mode)
VALID_TRANSITIONS = {
    (AppMode.SETUP, AppMode.ENROLLMENT),
    (AppMode.SETUP, AppMode.CALIBRATING),
    (AppMode.SETUP, AppMode.NAVIGATE),  
    (AppMode.ENROLLMENT, AppMode.CALIBRATING),
    (AppMode.CALIBRATING, AppMode.NAVIGATE),
    (AppMode.NAVIGATE, AppMode.PAIN),
    (AppMode.PAIN, AppMode.NAVIGATE),
    (AppMode.NAVIGATE, AppMode.CALIBRATING),
}


class StateMachine:
    """
    Mode-based state machine for GazeAssist.

    Manages transitions between SETUP → ENROLLMENT → CALIBRATING → NAVIGATE ↔ PAIN.
    SOS monitoring is orthogonal — always active regardless of current mode.
    """

    def __init__(self, initial_mode: AppMode = AppMode.SETUP):
        self._mode = initial_mode
        self._lock = threading.Lock()
        self._on_enter: dict[AppMode, list[Callable]] = {m: [] for m in AppMode}
        self._on_exit: dict[AppMode, list[Callable]] = {m: [] for m in AppMode}
        self._transition_listeners: list[Callable[[AppMode, AppMode], None]] = []

    @property
    def current_mode(self) -> AppMode:
        with self._lock:
            return self._mode

    def transition_to(self, new_mode: AppMode) -> bool:
        """
        Attempt a state transition.  Returns True on success.

        Calls on_exit hooks for the old mode and on_enter hooks for the new mode.
        """
        with self._lock:
            old = self._mode
            if old == new_mode:
                return True
            if (old, new_mode) not in VALID_TRANSITIONS:
                logger.warning("Invalid transition %s → %s", old.name, new_mode.name)
                return False
            self._mode = new_mode

        logger.info("Mode: %s → %s", old.name, new_mode.name)

        # Fire hooks outside the lock
        for cb in self._on_exit.get(old, []):
            try:
                cb()
            except Exception as e:
                logger.error("on_exit(%s) error: %s", old.name, e)

        for cb in self._on_enter.get(new_mode, []):
            try:
                cb()
            except Exception as e:
                logger.error("on_enter(%s) error: %s", new_mode.name, e)

        for cb in self._transition_listeners:
            try:
                cb(old, new_mode)
            except Exception as e:
                logger.error("transition_listener error: %s", e)

        return True

    def register_on_enter(self, mode: AppMode, callback: Callable):
        self._on_enter[mode].append(callback)

    def register_on_exit(self, mode: AppMode, callback: Callable):
        self._on_exit[mode].append(callback)

    def add_transition_listener(self, callback: Callable[[AppMode, AppMode], None]):
        self._transition_listeners.append(callback)
