import os
from collections.abc import Mapping

from clientops.node import app


def serve_options(env: Mapping[str, str]) -> dict:
    return {"host": env.get("CLIENTOPS_AGENT_HOST", "127.0.0.1"), "port": int(env.get("CLIENTOPS_AGENT_PORT", "8001"))}


if __name__ == "__main__":
    app.serve(**serve_options(os.environ))
