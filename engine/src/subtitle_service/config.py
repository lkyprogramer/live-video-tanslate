import os
from pathlib import Path
import re
import secrets
import stat
import tomllib
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

ProfileId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,39}$")]
Revision = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ServerConfig(StrictModel):
    port: int = Field(default=18765, ge=1024, le=65535)
    allowed_origin: Annotated[str, StringConstraints(pattern=r"^chrome-extension://[a-p]{32}$")]


class AsrConfig(StrictModel):
    type: Literal["reazon_sherpa"]
    model_revision: Revision
    threads: int = Field(default=2, ge=1, le=10)
    vad_pause_ms: int = Field(default=600, ge=100, le=1500)


class MtConfig(StrictModel):
    type: Literal["hy_mt2_mlx"]
    model_revision: Revision


class LocalProfile(StrictModel):
    type: Literal["local_pipeline"]
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    asr: AsrConfig
    mt: MtConfig


class WhisperProfile(StrictModel):
    type: Literal["whisperlivekit_ws"]
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = False
    url: str
    token_env: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]


Profile = Annotated[LocalProfile | WhisperProfile, Field(discriminator="type")]


class Settings(StrictModel):
    schema_version: Literal[1]
    default_profile_id: ProfileId
    cache_root: str
    server: ServerConfig
    profiles: dict[ProfileId, Profile]

    @model_validator(mode="after")
    def check_profiles(self):
        default = self.profiles.get(self.default_profile_id)
        if not default or not default.enabled:
            raise ValueError("default profile must exist and be enabled")
        for profile in self.profiles.values():
            if isinstance(profile, WhisperProfile):
                url = urlsplit(profile.url)
                if (url.scheme != "ws" or not url.port or url.port == self.server.port
                        or url.netloc != f"127.0.0.1:{url.port}" or url.path != "/asr"
                        or url.query or url.fragment or profile.url != url.geturl()):
                    raise ValueError("WhisperLiveKit must use a separate IPv4 loopback port and /asr")
        return self


def load_settings(path: Path) -> Settings:
    with path.open("rb") as stream:
        config = Settings.model_validate(tomllib.load(stream))
    config.cache_root = str((path.resolve().parent / config.cache_root).resolve())
    return config


def load_token(path: Path, *, create: bool) -> str:
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as stream:
                stream.write(secrets.token_urlsafe(32) + "\n")
        except FileExistsError:
            pass
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW)) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("token file must be private and owned by the current user")
        token = stream.read(129).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("invalid token file")
    return token
