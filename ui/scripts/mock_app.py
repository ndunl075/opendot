"""Serve the contract mock with the production browser WebSocket handshake.

The daemon's fixture app accepts sockets without selecting a subprotocol, which
Chromium rejects when the typed browser client offers ``opendot``. Keep this
adapter in the UI harness: no production authentication or API behavior changes.
"""

import uvicorn

from opendot_core.api.mock_server import create_app

mock = create_app()


async def app(scope, receive, send):
    async def accept_protocol(message):
        if (
            message["type"] == "websocket.accept"
            and "opendot" in scope.get("subprotocols", [])
        ):
            message = {**message, "subprotocol": "opendot"}
        await send(message)

    await mock(scope, receive, accept_protocol)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8787)
