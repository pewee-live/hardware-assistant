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
    const chatForm = document.getElementById('chat-form');
    const messageFeed = document.getElementById('message-feed');
    const agentStatus = document.getElementById('agent-status');
    const chatTitle = document.getElementById('chat-title');

    const passwordModal = document.getElementById('password-modal');
    const passwordForm = document.getElementById('password-form');
    const modalPassword = document.getElementById('modal-password');
    const passwordPromptText = document.getElementById('password-prompt-text');
    
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
            
            const nameEl = document.createElement('div');
            nameEl.className = 'session-name';
            nameEl.textContent = session.name;
            
            const dateEl = document.createElement('div');
            dateEl.className = 'session-date';
            dateEl.textContent = new Date(session.updated_at).toLocaleString();
            
            div.appendChild(nameEl);
            div.appendChild(dateEl);
            
            div.addEventListener('click', () => loadSession(session.session_id));
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

            if (data.connected && data.active_session_id === activeSessionId) {
                connStatus.textContent = "✔ " + data.message;
                connStatus.className = 'status-indicator connected';
                currentConnType = data.conn_type;
                initWebSocket();
            }
        } catch (e) {
            console.error("Status check failed:", e);
        }
    }
    checkExistingConnection();

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
                connStatus.textContent = "✔ " + data.message;
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
                connStatus.textContent = "❌ " + data.message;
                connStatus.className = 'status-indicator';
                connectBtn.disabled = false;
                connectBtn.textContent = originalText;
            }
        } catch (err) {
            connStatus.textContent = "❌ " + err.message;
            connectBtn.disabled = false;
            connectBtn.textContent = originalText;
        }
    });

    // Disconnect Action
    disconnectBtn.addEventListener('click', async () => {
        try {
            await fetch('/api/disconnect', { method: 'POST' });
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.close();
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
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat?session_id=${activeSessionId}`);
        
        ws.onopen = () => {
            chatInput.disabled = false;
            sendBtn.disabled = false;
            connectForm.querySelectorAll('input').forEach(i => i.disabled = true);
            connectBtn.style.display = 'none';
            disconnectBtn.style.display = 'block';
            chatInput.focus();
        };

        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            handleAgentMessage(data);
        };

        ws.onclose = () => {
            handleDisconnectUI();
            ws = null;
        };
    }

    // Agent Message Handler
    function handleAgentMessage(data) {
        if (data.type === 'status') {
            agentStatus.textContent = data.content;
            if(data.content.includes("Thinking")) {
                agentStatus.className = "badge thinking";
            } else {
                agentStatus.className = "badge idle";
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
            div.innerHTML = `⚙️ <b>Executing Tool:</b> ${data.name}<br><code>${JSON.stringify(data.args)}</code>`;
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
        else if (data.type === 'error') {
            const div = document.createElement('div');
            div.className = 'msg msg-agent';
            div.style.backgroundColor = 'rgba(239, 68, 68, 0.1)';
            div.style.borderColor = 'rgba(239, 68, 68, 0.4)';
            div.style.color = '#fff';
            div.textContent = `❌ ${data.content}`;
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
                body: JSON.stringify({ password: pwd })
            });
            passwordModal.classList.remove('active');
        } catch (err) {
            console.error('Password submit error:', err);
        } finally {
            submitBtn.disabled = false;
        }
    });

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
});
