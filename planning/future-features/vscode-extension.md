# VS Code Extension

## Overview

A VS Code extension could surface orchestrator session activity directly inside the editor, giving developers visibility into running work units without leaving their coding environment. Because VS Code extensions run in a Node.js context that has full access to the `net` module, an extension can open a Unix domain socket connection directly — no proxy or bridge required.

The extension speaks the same JSON-RPC protocol already defined for the socket interface. This is a natural fit: VS Code itself uses JSON-RPC as the Language Server Protocol transport, so the data structures are already familiar territory in the extension ecosystem.

## Architecture

```
VS Code Extension (Node.js)
        |
   net.connect() — Unix domain socket
        |
    Orchestrator
```

The extension uses `net.connect({ path: socketPath })` to establish a connection as a standard `SocketClient`. It subscribes to notifications and renders them in the editor UI. No changes to the orchestrator or protocol are needed.

Key UI surfaces:

- **WebviewPanel or Output Channel** — streams `log.message` and `status.update` events as they arrive, providing a live tail of agent output.
- **Tree View** — a sidebar panel driven by `status.update` events, showing each work unit and its current status (pending, running, complete, failed).
- **Input Box** — when an `input.request` notification arrives, `vscode.window.showInputBox` prompts the user inline. The response is sent back as an `input.respond` call on the socket.
- **Quick Pick** — `vscode.window.showQuickPick` populated from `session.list` responses lets the user select which active session to attach to.

History replay on attach works the same way as the TUI: the extension sends the standard replay request and populates its views with the backfilled event stream before switching to live mode.

## Key Considerations

**Unix socket support in Node.js**
Node.js has supported Unix domain sockets via `net.connect` since v0.1. VS Code extensions run in a Node.js worker, so this works without any native add-ons or additional dependencies.

**JSON-RPC alignment with LSP**
VS Code's `vscode-languageclient` and related packages already understand JSON-RPC message framing. The orchestrator protocol uses the same newline-delimited framing, so the extension can reuse existing JSON-RPC parsing utilities rather than writing a custom parser.

**Terminal integration**
Agent output that would normally appear in a terminal can be piped into a VS Code Terminal instance (`vscode.window.createTerminal`) for a familiar look. Alternatively, a dedicated Output Channel keeps agent output separate from user terminals.

**Activation triggers**
The extension can activate on workspace open (if a socket file is found in a known location) or on an explicit command. A status bar item showing the number of active sessions provides lightweight always-on visibility.

**Security**
The socket is a local filesystem resource protected by standard Unix permissions. The extension inherits those permissions automatically; no additional authentication mechanism is needed for local use.

## Status: Not Implemented
