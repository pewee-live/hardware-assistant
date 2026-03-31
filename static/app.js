document.addEventListener('DOMContentLoaded', () => {
    // UI Elements
    const tabBtns = document.querySelectorAll('.tab-btn');
    const sshFields = document.getElementById('ssh-fields');
    const serialFields = document.getElementById('serial-fields');
    const connectForm = document.getElementById('connect-form');
    const connectBtn = document.getElementById('connect-btn');
    const connStatus = document.getElementById('conn-status');
    
    const chatInput = document.getElementById('chat-input');
    const sendBtn = document.getElementById('send-btn');
    const chatForm = document.getElementById('chat-form');
    const messageFeed = document.getElementById('message-feed');
    const agentStatus = document.getElementById('agent-status');

    const passwordModal = document.getElementById('password-modal');
    const passwordForm = document.getElementById('password-form');
    const modalPassword = document.getElementById('modal-password');
    const passwordPromptText = document.getElementById('password-prompt-text');

    let currentConnType = 'ssh';
    let ws = null;
    let currentTerminalBlock = null;

    // Tabs toggle
    tabBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            tabBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentConnType = btn.dataset.type;
            
            if (currentConnType === 'ssh') {
                sshFields.style.display = 'flex';
                serialFields.style.display = 'none';
                
                // toggle required attributes
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

    // Connection Form
    connectForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        connectBtn.disabled = true;
        const originalText = connectBtn.textContent;
        connectBtn.textContent = 'Connecting...';
        
        const payload = { conn_type: currentConnType };
        
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

    // Chat WebSocket
    function initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        ws = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);
        
        ws.onopen = () => {
            chatInput.disabled = false;
            sendBtn.disabled = false;
            connectForm.querySelectorAll('input').forEach(i => i.disabled = true);
            connectBtn.style.display = 'none';
            chatInput.focus();
        };

        ws.onmessage = (e) => {
            const data = JSON.parse(e.data);
            handleAgentMessage(data);
        };

        ws.onclose = () => {
            connStatus.textContent = 'Disconnected from Server';
            connStatus.className = 'status-indicator';
            chatInput.disabled = true;
            sendBtn.disabled = true;
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
        
        // Simple markdown parsing for the agent
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
