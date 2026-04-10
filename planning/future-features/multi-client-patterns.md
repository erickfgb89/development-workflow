# Multi-Client Patterns

## Overview

The Unix domain socket architecture allows any number of clients to connect to the orchestrator simultaneously. Each client receives the full event stream independently; the orchestrator does not distinguish between a human-facing TUI, an automated script, and a passive observer. This opens up a range of usage patterns that require no orchestrator changes — only a new client with a specific purpose.

The patterns below illustrate the extensibility surface. All of them connect via the standard `SocketClient` interface and consume the existing JSON-RPC protocol.

## Architecture

```
                    Orchestrator
                         |
          +--------------+--------------+
          |              |              |
       TUI client   Observer client   Script client
     (interactive)   (read-only)     (automated)
```

Every connected client receives the same notifications. Clients that choose not to respond to `input.request` events are naturally read-only — the orchestrator will route the request to whichever client does respond (or wait for a timeout).

## Key Considerations

**Read-only observers**
A client that connects, subscribes to notifications, and never calls `input.respond` acts as a pure observer. This is useful for logging, dashboards, and monitoring tools that should never interfere with an active session. No special mode is required on the orchestrator side — simply not responding to input requests is sufficient.

**Notification-only clients**
A stripped-down client can ignore most event types and act only on specific notifications (e.g., `status.update` with `status: "failed"`). Such a client could write a line to a log file, post a message to a Slack webhook, or trigger a CI system notification — all without the overhead of a full TUI.

**Programmatic clients via direct import**
Python scripts can import `SocketClient` directly and drive it programmatically. This is useful for integration tests that need to assert on the event stream, automation scripts that respond to input requests with pre-determined answers, and tooling that needs to inspect session state. Because `SocketClient` is a plain class with no UI dependencies, it can be used in any Python context.

**Session recording**
A recording client connects on session start and writes every received notification to a JSONL file (one JSON object per line). The resulting file is a complete, replayable record of the session. A companion replay tool could feed the JSONL back through the TUI for post-mortem review or demo purposes. The recording client never calls `input.respond`, so it never interferes with the live session.

**Mobile / push notifications**
A bridge client running on a server could forward `input.request` notifications to a mobile push notification service (e.g., via FCM or APNs). The human operator sees a push notification on their phone, taps to open a lightweight web view, submits a response, and the bridge relays it back to the socket as `input.respond`. This enables asynchronous human-in-the-loop approval workflows without requiring the operator to be at a terminal.

## Status: Not Implemented
