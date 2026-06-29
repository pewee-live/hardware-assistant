document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const tabBtns = document.querySelectorAll('.tab-btn');
    const sshFields = document.getElementById('ssh-fields');
    const serialFields = document.getElementById('serial-fields');
    const connectForm = document.getElementById('connect-form');
    const connectBtn = document.getElementById('connect-btn');
    const disconnectBtn = document.getElementById('disconnect-btn');
    const connStatus = document.getElementById('conn-status');
    
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const stopBtn = document.getElementById('stop-btn');
    const chatForm = document.getElementById('chat-form');
    const messageFeed = document.getElementById('message-feed');
    const agentStatus = document.getElementById('agent-status');
    const chatTitle = document.getElementById('chat-title');

    const passwordModal = document.getElementById('password-modal');
    const passwordForm = document.getElementById('password-form');
    const modalPassword = document.getElementById('modal-password');
    const passwordPromptText = document.getElementById('password-prompt-text');

    const interventionModal = document.getElementById('intervention-modal');
    const interventionContext = document.getElementById('intervention-context');
    const interventionInput = document.getElementById('intervention-input');
    const interventionForm = document.getElementById('intervention-form');
    const interventionAbort = document.getElementById('intervention-abort');
    const interventionWait = document.getElementById('intervention-wait');

    const costBadge = document.getElementById('cost-badge');
    const exportBtn = document.getElementById('export-btn');

    const sessionList = document.getElementById('session-list');
    const newChatBtn = document.getElementById('new-chat-btn');

    const connToggleHeader = document.getElementById('conn-toggle-header');
    const connectionWrapper = document.getElementById('connection-wrapper');
    const connToggleIcon = document.getElementById('conn-toggle-icon');

    // Toggle connection settings
    connToggleHeader.addEventListener('click', () => {
        connectionWrapper.classList.toggle('collapsed');
        if (connectionWrapper.classList.contains('collapsed')) {
            connToggleIcon.style.transform = 'rotate(-90deg)';
        } else {
            connToggleIcon.style.transform = 'rotate(0deg)';
        }
    });

    let currentConnType = 'ssh';
    let ws = null;
    let currentTerminalBlock = null;
    let activeSessionId = null;
    let intentionalClose = false;     // distinguishes user disconnect from network drop
    let reconnectAttempts = 0;
    let reconnectTimer = null;
    let currentSessionUsage = null;   // last-known token usage for the active session

    // Fetch initial sessions
    async function loadSessions() {
        try {
            const res = await fetch('/api/sessions');
            const data = await res.json();
            if (data.status === 'success') {
                renderSessionList(data.sessions);
                return data.sessions;
            }
        } catch (e) {
            console.error("Failed to load sessions:", e);
        }
        return [];
    }

    function renderSessionList(sessions) {
        sessionList.innerHTML = '';
        sessions.forEach(session => {
            const div = document.createElement('div');
            div.className = `session-item ${session.session_id === activeSessionId ? 'active' : ''}`;
            div.addEventListener('click', () => loadSession(session.session_id));

            const nameEl = document.createElement('div');
            nameEl.className = 'session-name';
            nameEl.textContent = session.name;
            nameEl.title = session.name;

            const dot = document.createElement('span');
            dot.className = 'running-dot' + (session.running ? ' active' : '');
            dot.title = session.running ? 'Working in background' : '';

            const actions = document.createElement('div');
            actions.className = 'session-actions';
            const renameBtn = document.createElement('button');
            renameBtn.className = 'session-action-btn';
            renameBtn.title = 'Rename';
            renameBtn.textContent = 'Rename';
            renameBtn.addEventListener('click', (ev) => { ev.stopPropagation(); renameSession(session.session_id, session.name); });
            const delBtn = document.createElement('button');
            delBtn.className = 'session-action-btn danger';
            delBtn.title = 'Delete';
            delBtn.textContent = 'Delete';
            delBtn.addEventListener('click', (ev) => { ev.stopPropagation(); deleteSession(session.session_id); });
            actions.appendChild(renameBtn);
            actions.appendChild(delBtn);

            const row = document.createElement('div');
            row.className = 'session-row';
            row.appendChild(nameEl);
            row.appendChild(dot);
            row.appendChild(actions);

            const dateEl = document.createElement('div');
            dateEl.className = 'session-date';
            dateEl.textContent = new Date(session.updated_at).toLocaleString();

            div.appendChild(dateEl);
            div.insertBefore(row, div.firstChild);
            sessionList.appendChild(div);
        });
    }

    async function createNewSession() {
        try {
            const res = await fetch('/api/sessions', { method: 'POST' });
            const data = await res.json();
            if (data.status === 'success') {
                activeSessionId = data.session_id;
                clearChat();
                document.getElementById('ssh-password').value = '';
                chatTitle.textContent = "New Hardware Agent Console";
                loadSessions();
            }
        } catch (e) {
            console.error("Failed to create new session:", e);
        }
    }

    async function loadSession(sessionId) {
        try {
            const res = await fetch(`/api/sessions/${sessionId}`);
            const data = await res.json();
            if (data.status === 'success') {
                activeSessionId = sessionId;
                clearChat();
                chatTitle.textContent = data.session.name;

                // Cost badge + export button
                updateCostBadge(data.session.usage);
                exportBtn.style.display = 'inline-flex';

                // Device profile memory card (if a profile exists for this device)
                renderDeviceProfile(data.session.device_profile);

                // Pre-fill connection params
                const params = data.session.connection_params || {};
                const connType = data.session.conn_type || 'ssh';
                
                tabBtns.forEach(btn => {
                    if (btn.dataset.type === connType) {
                        btn.click();
                    }
                });
                
                if (connType === 'ssh' && params.host) {
                    document.getElementById('ssh-host').value = params.host;
                    if (params.username) document.getElementById('ssh-username').value = params.username;
                    document.getElementById('ssh-password').value = ''; // Ensure password is empty
                } else if (connType === 'serial' && params.serial_port) {
                    document.getElementById('serial-port').value = params.serial_port;
                    if (params.baudrate) document.getElementById('serial-baudrate').value = params.baudrate;
                }
                
                // Load history
                if (data.session.history && data.session.history.length > 0) {
                    const existingWelcome = document.querySelector('.welcome-message');
                    if (existingWelcome) existingWelcome.remove();
                    data.session.history.forEach(msg => handleAgentMessage(msg));
                }

                // Close old websocket if open
                if (ws) {
                    closeSocketIntentionally();
                }

                // Check status and initialize websocket/UI
                try {
                    const statusRes = await fetch(`/api/status?session_id=${sessionId}`, { cache: 'no-store' });
                    const statusData = await statusRes.json();
                    if (statusData.connected) {
                        connStatus.textContent = "鉁?" + statusData.message;
                        connStatus.className = 'status-indicator connected';
                        currentConnType = statusData.conn_type;
                        initWebSocket();
                    } else {
                        handleDisconnectUI();
                    }
                } catch (statusErr) {
                    console.error("Failed to fetch connection status for session:", statusErr);
                    handleDisconnectUI();
                }
                
                // re-render to update active class
                loadSessions();
            }
        } catch (e) {
            console.error("Failed to load session details:", e);
        }
    }

    newChatBtn.addEventListener('click', createNewSession);

    function clearChat() {
        messageFeed.innerHTML = '<div class="welcome-message">Welcome. Connect to a device to begin debugging.</div>';
    }

    // Tabs toggle
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentConnType = btn.dataset.type;
            
            if (currentConnType === 'ssh') {
                sshFields.style.display = 'flex';
                serialFields.style.display = 'none';
                
                sshFields.querySelectorAll('input:not(#ssh-password)').forEach(el => el.required = true);
                serialFields.querySelectorAll('input').forEach(el => el.required = false);
            } else {
                sshFields.style.display = 'none';
                serialFields.style.display = 'flex';
                
                sshFields.querySelectorAll('input').forEach(el => el.required = false);
                serialFields.querySelectorAll('input').forEach(el => el.required = true);
            }
        });
    });

    // --- Check if connection already exists ---
    async function checkExistingConnection() {
        try {
            const sessions = await loadSessions();
            const res = await fetch('/api/status', { cache: 'no-store' });
            const data = await res.json();
            
            if (data.active_session_id) {
                activeSessionId = data.active_session_id;
                await loadSession(activeSessionId);
            } else if (sessions.length > 0) {
                // If not connected, load the most recent session locally without connecting
                await loadSession(sessions[0].session_id);
            } else {
                await createNewSession();
            }
        } catch (e) {
            console.error("Status check failed:", e);
        }
    }
    checkExistingConnection();

    // Refresh the session list periodically so background-run status stays live,
    // without disturbing the active chat view.
    setInterval(async () => {
        try {
            const res = await fetch('/api/sessions', { cache: 'no-store' });
            const data = await res.json();
            if (data.status === 'success') renderSessionList(data.sessions);
        } catch (e) { /* ignore transient poll failures */ }
    }, 5000);

    // Connection Form
    connectForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        if (!activeSessionId) {
            await createNewSession();
        }

        connectBtn.disabled = true;
        const originalText = connectBtn.textContent;
        connectBtn.textContent = 'Connecting...';
        
        const payload = { conn_type: currentConnType, session_id: activeSessionId };
        
        if (currentConnType === 'ssh') {
            payload.host = document.getElementById('ssh-host').value;
            payload.username = document.getElementById('ssh-username').value;
            payload.password = document.getElementById('ssh-password').value || null;
            payload.port = parseInt(document.getElementById('ssh-port').value) || 22;
        } else {
            payload.serial_port = document.getElementById('serial-port').value;
            payload.baudrate = parseInt(document.getElementById('serial-baudrate').value) || 115200;
        }

        try {
            const res = await fetch('/api/connect', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            
            if (data.status === 'success') {
                connStatus.textContent = "鉁?" + data.message;
                connStatus.className = 'status-indicator connected';
                // Refresh title
                const sRes = await fetch(`/api/sessions/${activeSessionId}`);
                const sData = await sRes.json();
                if(sData.status === 'success') {
                    chatTitle.textContent = sData.session.name;
                    loadSessions(); // update sidebar
                }
                
                // Auto collapse connection wrapper to save space
                if (!connectionWrapper.classList.contains('collapsed')) {
                    connToggleHeader.click();
                }
                
                initWebSocket();
            } else {
                connStatus.textContent = "鉂?" + data.message;
                connStatus.className = 'status-indicator';
                connectBtn.disabled = false;
                connectBtn.textContent = originalText;
            }
        } catch (err) {
            connStatus.textContent = "鉂?" + err.message;
            connectBtn.disabled = false;
            connectBtn.textContent = originalText;
        }
    });

    // Disconnect Action
    disconnectBtn.addEventListener('click', async () => {
        try {
            await fetch(`/api/disconnect?session_id=${activeSessionId}`, { method: 'POST' });
            if (ws && ws.readyState === WebSocket.OPEN) {
                closeSocketIntentionally();
                handleDisconnectUI();
            } else {
                handleDisconnectUI();
            }
        } catch (e) {
            console.error("Disconnect API failed", e);
        }
    });

    function handleDisconnectUI() {
        connStatus.textContent = 'Disconnected';
        connStatus.className = 'status-indicator';
        connectForm.querySelectorAll('input').forEach(i => i.disabled = false);
        connectBtn.style.display = 'block';
        connectBtn.disabled = false;
        connectBtn.textContent = 'Connect';
        disconnectBtn.style.display = 'none';
        chatInput.disabled = true;
        sendBtn.disabled = true;
    }

    // Chat WebSocket
    function initWebSocket() {
        if (!activeSessionId) return;
        // Reset reconnect bookkeeping on a fresh (re)connect.
        intentionalClose = false;
        reconnectAttempts = 0;
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat?session_id=${activeSessionId}`);
        
        ws.onopen = () => {
            chatInput.disabled = false;
            sendBtn.disabled = false;
            connectForm.querySelectorAll('input').forEach(i => i.disabled = true);
            connectBtn.style.display = 'none';
            disconnectBtn.style.display = 'block';
            agentStatus.title = '';
            chatInput.focus();
        };

        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            handleAgentMessage(data);
        };

        ws.onclose = () => {
            ws = null;
            // If the user intentionally disconnected, just update the UI.
            // Otherwise the network dropped -- try to reconnect so a background
            // agent task stays visible (the task itself is never cancelled by a
            // viewer disconnect).
            if (intentionalClose) {
                handleDisconnectUI();
            } else {
                scheduleReconnect();
            }
        };
        ws.onerror = () => { /* onclose handles reconnect */ };
    }

    function scheduleReconnect() {
        if (reconnectTimer) return;
        reconnectAttempts++;
        const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);
        agentStatus.textContent = reconnectAttempts <= 1 ? 'Reconnecting...' : `Reconnecting (${reconnectAttempts})...`;
        agentStatus.className = 'badge reconnecting';
        agentStatus.title = `Lost connection. Retrying in ${Math.round(delay/1000)}s.`;
        reconnectTimer = setTimeout(() => {
            reconnectTimer = null;
            if (!activeSessionId) return;
            initWebSocket();
        }, delay);
    }

    function closeSocketIntentionally() {
        intentionalClose = true;
        if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
        if (ws) {
            ws.onclose = null;
            try { ws.close(); } catch (e) {}
            ws = null;
        }
    }

    // Agent Message Handler
    function handleAgentMessage(data) {
        if (data.type === 'status') {
            agentStatus.textContent = data.content;
            if (data.content.includes("Thinking") || data.content.includes("Executing") || data.content.includes("processing")) {
                agentStatus.className = "badge thinking";
                chatInput.disabled = true;
                sendBtn.style.display = 'none';
                stopBtn.style.display = 'block';
            } else {
                agentStatus.className = "badge idle";
                stopBtn.style.display = 'none';
                sendBtn.style.display = 'block';
                // A run just finished (status -> Ready). Refresh token usage.
                if (data.content === 'Ready' && activeSessionId) refreshUsage();
                if (ws && ws.readyState === WebSocket.OPEN) {
                    chatInput.disabled = false;
                    sendBtn.disabled = false;
                } else {
                    chatInput.disabled = true;
                    sendBtn.disabled = true;
                }
            }
        } 
        else if (data.type === 'user_message') {
            const existingWelcome = document.querySelector('.welcome-message');
            if (existingWelcome) existingWelcome.remove();
            
            appendMessage(data.content, 'msg-user');
            currentTerminalBlock = null;
        }
        else if (data.type === 'agent_message') {
            appendMessage(data.content, 'msg-agent');
            currentTerminalBlock = null;
        }
        else if (data.type === 'tool_call') {
            const div = document.createElement('div');
            div.className = 'tool-call msg';
            div.innerHTML = `鈿欙笍 <b>Executing Tool:</b> ${data.name}<br><code>${JSON.stringify(data.args)}</code>`;
            messageFeed.appendChild(div);
            scrollToBottom();
            currentTerminalBlock = null;
        }
        else if (data.type === 'log') {
            // Append continuous terminal logs
            if (!currentTerminalBlock) {
                currentTerminalBlock = document.createElement('div');
                currentTerminalBlock.className = 'terminal-log msg';
                messageFeed.appendChild(currentTerminalBlock);
            }
            currentTerminalBlock.textContent += data.content;
            scrollToBottom();
        }
        else if (data.type === 'password_request') {
            passwordPromptText.textContent = data.prompt;
            modalPassword.value = '';
            passwordModal.classList.add('active');
            setTimeout(() => modalPassword.focus(), 100);
        }
        else if (data.type === 'intervention_request') {
            interventionContext.textContent = data.context || '(no output yet)';
            interventionInput.value = '';
            interventionModal.classList.add('active');
            setTimeout(() => interventionInput.focus(), 100);
        }
        else if (data.type === 'error') {
            const div = document.createElement('div');
            div.className = 'msg msg-agent';
            div.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
            div.style.borderColor = 'rgba(239, 68, 68, 0.4)';
            div.style.color = '#fff';
            div.textContent = `鉂?${data.content}`;
            messageFeed.appendChild(div);
            scrollToBottom();
        }
    }

    // Chat form submit
    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const msg = chatInput.value.trim();
        if (msg && ws && ws.readyState === WebSocket.OPEN) {
            ws.send(msg);
            chatInput.value = '';
            chatInput.disabled = true;
            sendBtn.disabled = true;
        }
    });

    // Stop execution button
    stopBtn.addEventListener('click', async () => {
        if (!activeSessionId) return;
        stopBtn.disabled = true;
        const originalText = stopBtn.textContent;
        stopBtn.textContent = 'Stopping...';
        try {
            await fetch('/api/interrupt', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ session_id: activeSessionId })
            });
        } catch (e) {
            console.error("Failed to stop execution:", e);
        } finally {
            stopBtn.disabled = false;
            stopBtn.textContent = originalText;
        }
    });

    // Password form submit
    passwordForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const pwd = modalPassword.value;
        const submitBtn = passwordForm.querySelector('button');
        submitBtn.disabled = true;
        
        try {
            await fetch('/api/password', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ password: pwd, session_id: activeSessionId })
            });
            passwordModal.classList.remove('active');
        } catch (err) {
            console.error('Password submit error:', err);
        } finally {
            submitBtn.disabled = false;
        }
    });

    // --- Manual intervention ---
    function submitIntervention(action, input) {
        fetch('/api/intervention', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ action, input: input || '', session_id: activeSessionId })
        }).catch(err => console.error('Intervention submit error:', err));
        interventionModal.classList.remove('active');
    }

    interventionForm.addEventListener('submit', (e) => {
        e.preventDefault();
        submitIntervention('send', interventionInput.value);
    });
    interventionAbort.addEventListener('click', () => submitIntervention('abort', ''));
    interventionWait.addEventListener('click', () => submitIntervention('wait', ''));

    // --- Cost badge / device profile / export ---
    function fmtTokens(n) {
        if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
        return String(n);
    }

    function updateCostBadge(usage) {
        currentSessionUsage = usage;
        if (!usage) { costBadge.textContent = '0 tokens'; return; }
        const total = usage.total_tokens || 0;
        const cost = usage.estimated_cost || 0;
        const cur = usage.currency || 'USD';
        costBadge.textContent = `${fmtTokens(total)} tokens`;
        costBadge.title = `Input: ${usage.input_tokens || 0} | Output: ${usage.output_tokens || 0}\nEst. cost: $${cost} ${cur}`;
    }

    function renderDeviceProfile(profile) {
        // Remove any existing card first.
        const existing = document.querySelector('.device-profile-card');
        if (existing) existing.remove();
        if (!profile) return;
        const fields = ['hostname', 'os', 'kernel', 'architecture', 'cpu', 'memory', 'storage', 'network', 'notes'];
        const parts = [];
        for (const f of fields) {
            if (profile[f]) parts.push(`<b>${f}</b>: ${String(profile[f]).replace(/</g,'&lt;')}`);
        }
        if (!parts.length) return;
        const card = document.createElement('div');
        card.className = 'device-profile-card';
        card.innerHTML = '<b>Device memory</b> &mdash; ' + parts.join(' &middot; ');
        const welcome = document.querySelector('.welcome-message');
        messageFeed.insertBefore(card, welcome ? welcome.nextSibling : messageFeed.firstChild);
    }

    exportBtn.addEventListener('click', () => {
        if (!activeSessionId) return;
        // Trigger a download via the export endpoint.
        window.location.href = `/api/sessions/${activeSessionId}/export?format=markdown`;
    });

    async function renameSession(sessionId, currentName) {
        const name = window.prompt('Rename session:', currentName);
        if (name === null || !name.trim()) return;
        try {
            const res = await fetch(`/api/sessions/${sessionId}/rename`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name.trim() })
            });
            const data = await res.json();
            if (data.status === 'success' && sessionId === activeSessionId) {
                chatTitle.textContent = data.name;
            }
            loadSessions();
        } catch (e) { console.error('Rename failed:', e); }
    }

    async function deleteSession(sessionId) {
        if (!window.confirm('Delete this session permanently? This cannot be undone.')) return;
        try {
            await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
            if (sessionId === activeSessionId) {
                closeSocketIntentionally();
                handleDisconnectUI();
                const list = await loadSessions();
                if (list && list.length > 0) {
                    loadSession(list[0].session_id);
                } else {
                    await createNewSession();
                }
            } else {
                loadSessions();
            }
        } catch (e) { console.error('Delete failed:', e); }
    }

    function appendMessage(text, className) {
        const div = document.createElement('div');
        div.className = `msg ${className}`;
        
        if (className === 'msg-agent') {
            let html = text.replace(/\*\*(.*?)\*\*/g, '<b>$1</b>');
            html = html.replace(/`(.*?)`/g, '<code style="background: rgba(0,0,0,0.5); padding: 2px 4px; border-radius: 4px; color: #a5d6ff;">$1</code>');
            html = html.replace(/\n\n/g, '</p><p>');
            html = html.replace(/\n/g, '<br>');
            div.innerHTML = `<p>${html}</p>`;
        } else {
            div.textContent = text;
        }
        
        messageFeed.appendChild(div);
        scrollToBottom();
    }

    function scrollToBottom() {
        messageFeed.scrollTop = messageFeed.scrollHeight;
    }

    async function refreshUsage() {
        if (!activeSessionId) return;
        try {
            const res = await fetch(`/api/sessions/${activeSessionId}`, { cache: "no-store" });
            const data = await res.json();
            if (data.status === "success") updateCostBadge(data.session.usage);
        } catch (e) { /* non-critical */ }
    }
});
