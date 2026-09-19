import abc
import asyncio
import logging
import os
import tempfile
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Optional imports
try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False

try:
    import pyttsx3
    PYTTSX3_AVAILABLE = True
except ImportError:
    PYTTSX3_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False


class TTSEngine(abc.ABC):
    """Abstract base class for Text-To-Speech engines."""
    
    @abc.abstractmethod
    def speak(self, text: str, language: str) -> None:
        """Speak the text aloud in the given language."""
        pass

    @abc.abstractmethod
    def speak_to_file(self, text: str, language: str, path: str) -> str:
        """Save the spoken text to an audio file and return the path."""
        pass

    @abc.abstractmethod
    def is_available(self) -> bool:
        """Check if the backend is available and working."""
        pass


class EdgeTTSBackend(TTSEngine):
    """TTS backend using edge-tts (requires internet connection)."""
    
    VOICE_MAPPING = {
        'hi': 'hi-IN-SwaraNeural',
        'ml': 'ml-IN-SobhanaNeural',
        'ta': 'ta-IN-PallaviNeural',
        'te': 'te-IN-ShrutiNeural'
    }
    
    def __init__(self):
        self._lock = threading.Lock()
        if PYGAME_AVAILABLE:
            if not pygame.mixer.get_init():
                pygame.mixer.init()

    def speak(self, text: str, language: str) -> None:
        """Speak the text aloud by first saving to a temporary file and playing it."""
        if not PYGAME_AVAILABLE:
            logger.error("pygame is not available for playing audio in EdgeTTSBackend.")
            return

        with self._lock:
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
            temp_path = temp_file.name
            temp_file.close()

            try:
                self.speak_to_file(text, language, temp_path)
                
                # Play using pygame
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
                
                # Wait for playback to finish
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                    
            except Exception as e:
                logger.error(f"Error speaking with EdgeTTS: {e}")
            finally:
                # Cleanup
                if os.path.exists(temp_path):
                    try:
                        # Ensure pygame releases the file before trying to remove
                        pygame.mixer.music.unload()
                        os.remove(temp_path)
                    except (OSError, AttributeError):
                        pass

    def speak_to_file(self, text: str, language: str, path: str) -> str:
        """Save the spoken text to an audio file."""
        voice = self.VOICE_MAPPING.get(language, self.VOICE_MAPPING['hi'])
        
        async def _save_audio():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(path)

        def _run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_save_audio())
            loop.close()

        thread = threading.Thread(target=_run_async)
        thread.start()
        thread.join()
        
        return path

    def is_available(self) -> bool:
        """Check if the backend is available."""
        return EDGE_TTS_AVAILABLE


class Pyttsx3Backend(TTSEngine):
    """Offline TTS backend using pyttsx3."""
    
    def __init__(self):
        self._lock = threading.Lock()
        if PYTTSX3_AVAILABLE:
            try:
                self.engine = pyttsx3.init()
            except Exception as e:
                logger.error(f"Failed to initialize pyttsx3: {e}")
                self.engine = None
        else:
            self.engine = None

    def speak(self, text: str, language: str) -> None:
        """Speak the text using pyttsx3."""
        if not self.engine:
            logger.error("pyttsx3 engine is not initialized.")
            return
            
        with self._lock:
            try:
                self.engine.say(text)
                self.engine.runAndWait()
            except Exception as e:
                logger.error(f"Error speaking with Pyttsx3: {e}")

    def speak_to_file(self, text: str, language: str, path: str) -> str:
        """Save the spoken text to an audio file using pyttsx3."""
        if not self.engine:
            logger.error("pyttsx3 engine is not initialized.")
            return path
            
        with self._lock:
            try:
                self.engine.save_to_file(text, path)
                self.engine.runAndWait()
            except Exception as e:
                logger.error(f"Error saving to file with Pyttsx3: {e}")
                
        return path

    def is_available(self) -> bool:
        """Check if the backend is available."""
        return PYTTSX3_AVAILABLE and self.engine is not None


class MockTTSBackend(TTSEngine):
    """Mock TTS backend that logs output (fallback)."""
    
    def speak(self, text: str, language: str) -> None:
        """Log the spoken text."""
        logger.info(f"Mock TTS speaking [{language}]: {text}")

    def speak_to_file(self, text: str, language: str, path: str) -> str:
        """Log the file save action."""
        logger.info(f"Mock TTS saving to {path} [{language}]: {text}")
        # Create an empty file to satisfy callers expecting a file
        with open(path, 'w') as f:
            f.write(f"Mock audio data for {text} in {language}")
        return path

    def is_available(self) -> bool:
        """Mock backend is always available."""
        return True


def create_tts_engine() -> TTSEngine:
    """
    Factory function to create the best available TTS engine.
    Tries EdgeTTS -> Pyttsx3 -> MockTTS.
    """
    edge_engine = EdgeTTSBackend()
    if edge_engine.is_available():
        logger.info("Using EdgeTTSBackend for TTS.")
        return edge_engine
        
    pyttsx3_engine = Pyttsx3Backend()
    if pyttsx3_engine.is_available():
        logger.info("Using Pyttsx3Backend for TTS.")
        return pyttsx3_engine
        
    logger.info("Using MockTTSBackend for TTS (all other backends failed).")
    return MockTTSBackend()
