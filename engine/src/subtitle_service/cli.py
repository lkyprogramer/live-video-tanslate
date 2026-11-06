import argparse
import os
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from .app import FRAME_BYTES, create_app
from .config import load_settings, load_token


def token_path():
    return Path(os.environ.get(
        "SUBTITLE_TOKEN_FILE",
        Path.home() / "Library/Application Support/Local Video Subtitle/token",
    ))


def serve():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    from .factory import build_backends

    try:
        config = load_settings(args.config)
        token = load_token(token_path(), create=True)
        backends = build_backends(config)
    except ValidationError as exc:
        fields = [".".join(str(key) for key in row["loc"]) + " (" + row["type"] + ")"
                  for row in exc.errors(include_input=False, include_context=False)]
        parser.error("invalid configuration fields: " + ", ".join(fields))
    except (OSError, ValueError):
        parser.error("unable to start: check config, local model files and private token file")
    app = create_app(config, token, backends)
    print(f"Token file: {token_path()}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=config.server.port, workers=1,
                proxy_headers=False, access_log=False, ws="websockets-sansio",
                ws_max_size=FRAME_BYTES * 2)


if __name__ == "__main__":
    serve()
