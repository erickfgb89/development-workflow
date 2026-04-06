const socket = new WebSocket("ws://" + window.location.host + "/ws");

socket.onopen = function(event) {
    console.log("WebSocket connected.");
};

socket.onmessage = function(event) {
    try {
        const msg = JSON.parse(event.data);
        if (msg.type === "state_update") {
            handleStateUpdate(msg.payload.state);
        } else if (msg.type === "agent_log") {
            handleAgentLog(msg.payload.agent, msg.payload.text);
        } else if (msg.type === "user_prompt") {
            handleUserPrompt(msg.payload);
        }
    } catch (e) {
        console.error("Failed to parse message", e);
    }
};

let currentInteractionId = null;

function handleStateUpdate(state) {
    const wusMap = state.work_units || {};
    const wuIds = Object.keys(wusMap);
    const wus = wuIds.map(id => ({ id, ...wusMap[id] }));
    
    const total = wus.length;
    const completed = wus.filter(wu => wu.status === "complete").length;
    const progressEl = document.querySelector(".bg-surface-container-lowest .text-secondary");
    if (progressEl) {
        progressEl.textContent = `${completed} / ${total}`;
    }
    const barEl = document.querySelector(".bg-surface-container-high .bg-secondary.h-full");
    if (barEl) {
        const pct = total > 0 ? (completed / total) * 100 : 0;
        barEl.style.width = pct + "%";
    }

    const dagContainer = document.querySelector(".relative.grid.grid-cols-3");
    if (dagContainer) {
        dagContainer.innerHTML = "";
        wus.forEach((wu, i) => {
            let statusColorClass = "dag-node-pending opacity-60";
            let colorHex = "#adaaad";
            if (wu.status === "complete") { statusColorClass = "dag-node-complete"; colorHex = "#69f6b8" }
            else if (wu.status === "in_progress") { statusColorClass = "dag-node-active scale-105 border border-primary/30 z-20"; colorHex = "#85adff" }
            else if (wu.status === "failed") { statusColorClass = "dag-node-failed"; colorHex = "#ff716c" }
            else if (wu.status === "blocked") { statusColorClass = "dag-node-blocked"; colorHex = "#ffb148" }
            
            const div = document.createElement("div");
            div.className = `bg-surface-container-high p-4 rounded shadow-2xl ${statusColorClass} w-48 relative`;
            div.innerHTML = `
                <div class="text-[10px] font-mono mb-1" style="color: ${colorHex}">${wu.id} [${wu.status.toUpperCase()}]</div>
                <div class="text-xs font-bold truncate" title="${wu.title || wu.id}">${wu.title || "WORK_UNIT"}</div>
            `;
            dagContainer.appendChild(div);
        });
    }
}

function handleAgentLog(agent, text) {
    const feedContainer = document.querySelector(".h-64.bg-surface-container-low.grid-cols-2");
    if (!feedContainer) return;
    
    let agentId = "agent-" + agent.replace(/[^a-zA-Z0-9]/g, "");
    let agentBox = document.getElementById(agentId);
    if (!agentBox) {
        agentBox = document.createElement("div");
        agentBox.id = agentId;
        agentBox.className = "bg-surface-container-lowest rounded border border-outline-variant/10 flex flex-col overflow-hidden";
        agentBox.innerHTML = `
            <div class="bg-surface-container-high px-3 py-2 flex items-center justify-between">
                <div class="flex items-center gap-2">
                    <span class="w-1.5 h-1.5 rounded-full bg-primary animate-pulse"></span>
                    <span class="text-[10px] font-mono font-bold tracking-tight">AGENT: ${agent}</span>
                </div>
            </div>
            <div class="log-stream flex-1 p-3 font-mono text-[11px] text-primary/80 overflow-y-auto custom-scrollbar bg-black/40"></div>
        `;
        feedContainer.appendChild(agentBox);
    }
    
    const logStream = agentBox.querySelector(".log-stream");
    const div = document.createElement("div");
    div.className = "text-on-surface whitespace-pre-wrap mt-1";
    div.textContent = text;
    logStream.appendChild(div);
    logStream.scrollTop = logStream.scrollHeight;
}

function handleUserPrompt(payload) {
    const interactionPanel = document.querySelector(".flex-1.p-6.flex-col.bg-surface-container-highest");
    if (!interactionPanel) return;
    
    currentInteractionId = payload.id;
    interactionPanel.style.display = "flex";
    
    let titleEl = interactionPanel.querySelector("h3");
    if (titleEl) titleEl.textContent = payload.type === "escalate" ? "REVIEWER ESCALATION" : "AWAITING HUMAN CLARIFICATION";
    
    let textEl = interactionPanel.querySelector(".bg-surface-container-low.p-4");
    if (textEl) {
        if (payload.type === "question") {
            textEl.innerHTML = `<span class="text-tertiary font-bold">Question:</span><br/>${payload.question}`;
            if (payload.context) {
                textEl.innerHTML += `<br/><br/><span class="text-zinc-500">Context:</span><br/>${payload.context}`;
            }
        } else {
            textEl.innerHTML = `<span class="text-error font-bold">Escalate (${payload.args.wu_id}):</span><br/>${payload.args.reason}<br/><br/>${payload.args.summary}`;
        }
    }
}

document.addEventListener("DOMContentLoaded", () => {
    const interactionPanel = document.querySelector(".flex-1.p-6.flex-col.bg-surface-container-highest");
    if (interactionPanel) interactionPanel.style.display = "none";
    
    const dagContainer = document.querySelector(".relative.grid.grid-cols-3");
    if (dagContainer) dagContainer.innerHTML = "";
    
    const feedContainer = document.querySelector(".h-64.bg-surface-container-low.grid-cols-2");
    if (feedContainer) feedContainer.innerHTML = "";

    const sendBtn = document.querySelector(".bg-primary.text-on-primary");
    if (sendBtn) {
        sendBtn.addEventListener("click", () => {
            const textarea = document.querySelector("textarea");
            if (currentInteractionId && textarea) {
                const answerValue = textarea.value;
                if (!answerValue.trim()) return;
                
                socket.send(JSON.stringify({
                    type: "answer",
                    payload: {
                        id: currentInteractionId,
                        result: answerValue
                    }
                }));
                if (interactionPanel) interactionPanel.style.display = "none";
                currentInteractionId = null;
                textarea.value = "";
            }
        });
    }

    // Workspace Navigation logic
    const navOrchestrator = document.getElementById("nav-orchestrator");
    const navWorkspace = document.getElementById("nav-workspace");
    const workspaceView = document.getElementById("workspace-view");
    
    if (navOrchestrator && navWorkspace && workspaceView) {
        navWorkspace.addEventListener("click", (e) => {
            e.preventDefault();
            workspaceView.style.display = "flex";
            navOrchestrator.classList.remove("text-blue-400", "bg-blue-400/10", "border-blue-400");
            navOrchestrator.classList.add("text-zinc-500");
            navWorkspace.classList.remove("text-zinc-500");
            navWorkspace.classList.add("text-blue-400", "bg-blue-400/10", "border-blue-400", "border-r-2");
            loadWorkspace();
        });

        navOrchestrator.addEventListener("click", (e) => {
            e.preventDefault();
            workspaceView.style.display = "none";
            navWorkspace.classList.remove("text-blue-400", "bg-blue-400/10", "border-blue-400");
            navWorkspace.classList.add("text-zinc-500");
            navOrchestrator.classList.remove("text-zinc-500");
            navOrchestrator.classList.add("text-blue-400", "bg-blue-400/10", "border-blue-400");
        });
    }

    const refreshBtn = document.getElementById("refresh-workspace");
    if (refreshBtn) refreshBtn.addEventListener("click", loadWorkspace);
});

async function loadWorkspace() {
    try {
        const fileTree = document.getElementById("file-tree");
        if (fileTree) fileTree.innerHTML = '<div class="text-zinc-500 animate-pulse">Loading...</div>';
        
        const res = await fetch("/api/workspace");
        const data = await res.json();
        
        if (data.error) {
            if (fileTree) fileTree.innerHTML = `<div class="text-error">${data.error}</div>`;
            return;
        }

        if (fileTree) {
            fileTree.innerHTML = "";
            renderTree(data.tree, fileTree, 0);
        }
    } catch (e) {
        console.error(e);
    }
}

function renderTree(node, container, depth) {
    if (!node) return;
    const item = document.createElement("div");
    item.className = "flex items-center gap-1.5 py-1 px-1 cursor-pointer hover:bg-white/5 rounded text-zinc-300";
    item.style.paddingLeft = (depth * 1) + "rem";
    
    const icon = document.createElement("span");
    icon.className = "material-symbols-outlined text-[14px]";
    icon.textContent = node.type === "dir" ? "folder" : "description";
    if (node.type === "dir") icon.classList.add("text-primary");
    else icon.classList.add("text-zinc-500");

    const label = document.createElement("span");
    label.textContent = node.name;
    
    item.appendChild(icon);
    item.appendChild(label);
    container.appendChild(item);

    if (node.type === "file") {
        item.addEventListener("click", () => loadFile(node.path));
    }

    if (node.type === "dir" && node.children) {
        // default all open for now
        node.children.forEach(child => renderTree(child, container, depth + 1));
    }
}

async function loadFile(path) {
    const hdr = document.getElementById("file-path-header");
    const viewer = document.getElementById("file-content-view");
    if (hdr) hdr.textContent = path;
    if (viewer) viewer.textContent = "Loading...";

    try {
        const res = await fetch("/api/workspace/file?path=" + encodeURIComponent(path));
        const ext = path.split(".").pop();
        if (res.ok) {
            const text = await res.text();
            if (viewer) viewer.textContent = text;
        } else {
            if (viewer) viewer.textContent = "Error loading file content: " + res.status;
        }
    } catch (e) {
        if (viewer) viewer.textContent = "Error: " + e;
    }
}
