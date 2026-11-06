from pathlib import Path

from .config import LocalProfile
from .local_pipeline import LocalBackend
from .whisper import WhisperBackend


def build_backends(config):
    result = {}
    for key, profile in config.profiles.items():
        if not profile.enabled:
            continue
        if not isinstance(profile, LocalProfile):
            result[key] = WhisperBackend(profile)
            continue
        backend = LocalBackend(Path(config.cache_root), profile)
        if key == config.default_profile_id and backend.missing:
            raise ValueError("default model files missing: " + ", ".join(str(p) for p in backend.missing))
        result[key] = backend
    return result
