"""Capture the PCM received from the isolated diagnostic Chrome extension."""
import os
from pathlib import Path

from p0.p2 import server


class Capture(server.PCMTrace):
    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await super().__call__(scope, receive, send)
        stream = None

        async def capture_frame():
            nonlocal stream
            event = await receive()
            if event.get("bytes"):
                if stream is None:
                    stream = Path(os.environ["D1_CAPTURE_PCM"]).open("xb")
                stream.write(event["bytes"])
            return event

        try:
            await super().__call__(scope, capture_frame, send)
        finally:
            if stream is not None:
                stream.close()


if __name__ == "__main__":
    server.PCMTrace = Capture
    server.main()
