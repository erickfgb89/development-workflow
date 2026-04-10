# Web Dashboard

## Overview

A web dashboard could provide a browser-based interface for monitoring and interacting with orchestrator sessions. Rather than requiring a dedicated terminal client, any browser on the local network could attach to a running session, observe events in real time, and respond to input requests through a web form.

The dashboard is enabled by the socket architecture without any changes to the orchestrator or protocol layer. A lightweight bridge process connects to the Unix domain socket as a regular `SocketClient` and translates the JSON-RPC event stream into WebSocket frames that browsers can consume natively.

## Architecture

```
Browser (React/vanilla JS)
        |
     WebSocket
        |
  HTTP/WS Bridge (aiohttp / starlette)
        |
  Unix domain socket (JSON-RPC)
        |
    Orchestrator
```

The bridge is a small Python HTTP server (e.g., `aiohttp` or `starlette`) that:

1. Listens on a local TCP port (e.g., `localhost:8765`) for WebSocket connections from browsers.
2. Connects to the Unix domain socket as a standard `SocketClient`, using the same JSON-RPC protocol already defined.
3. Forwards every incoming notification from the socket to all connected WebSocket clients.
4. Forwards input responses from the browser back to the socket as `input.respond` calls.

Because the bridge is a plain `SocketClient`, the orchestrator sees it as just another attached client. No orchestrator changes are required.

On page load, the browser requests history replay (same mechanism used by the TUI on attach), so a late-joining dashboard sees the full session context immediately.

## Key Considerations

**Read-only vs. interactive mode**
The bridge can be started in read-only mode, where it never forwards input responses to the socket. This is useful for observers who want to monitor a session without risk of accidentally responding to a prompt meant for a human operator. Interactive mode enables full participation, including responding to `input.request` events via a web form.

**Multi-session dashboard**
The bridge can maintain connections to multiple Unix sockets simultaneously and present a unified view — a single page listing all active sessions, each expandable into its own event timeline. Session discovery would rely on `session.list` responses from each connected socket.

**Authentication**
The bridge binds to `localhost` by default and requires no credentials (it inherits the socket's filesystem permission model). If exposed beyond localhost, standard HTTP authentication middleware should be layered in front of the WebSocket endpoint.

**Browser UI**
The UI can be implemented in vanilla JavaScript with no build step (suitable for a dev-tool dashboard) or in React for a richer experience. The JSON-RPC notification structure maps naturally to a chronological event list with status indicators per work unit.

## Status: Not Implemented
