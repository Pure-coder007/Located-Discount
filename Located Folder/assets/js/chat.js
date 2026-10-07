(() => {
  const root = document.querySelector('[data-chat]');
  if (!root) return;
  const history = root.querySelector('[data-chat-history]');
  const form = root.querySelector('form');
  const status = root.querySelector('[data-chat-status]');
  const button = form.querySelector('button');
  const seen = new Set();
  let socket, retry = 1000, sending = false, sendTimeout, lastSequence = 0;
  const finishSending = () => { clearTimeout(sendTimeout); sending = false; button.disabled = socket?.readyState !== WebSocket.OPEN; };
  form.elements.body.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!sending) form.requestSubmit();
    }
  });
  const markRead = () => {
    if (!document.hidden && lastSequence && socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({type:'read', sequence:lastSequence}));
      setTimeout(() => document.dispatchEvent(new Event('chat-read')), 250);
    }
  };
  document.addEventListener('visibilitychange', markRead);
  const connect = () => {
    socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/chat/${encodeURIComponent(root.dataset.businessId)}`);
    socket.onopen = () => { socket.send(JSON.stringify({csrf_token: root.dataset.csrf})); status.textContent = 'Connected'; button.disabled = false; retry = 1000; };
    socket.onmessage = ({data}) => {
      let event;
      try { event = JSON.parse(data); } catch (_) { status.textContent = 'Invalid server response. Please reconnect.'; return; }
      if (event.type === 'error') { status.textContent = event.error; finishSending(); return; }
      if (event.type === 'sent') { form.elements.body.value = ''; finishSending(); status.textContent = 'Connected'; return; }
      const msg = event.message;
      if (!msg) return;
      lastSequence = Math.max(lastSequence, msg.sequence);
      markRead();
      if (seen.has(msg.id)) return;
      seen.add(msg.id);
      const nearBottom = history.scrollHeight - history.scrollTop - history.clientHeight < 80;
      const article = document.createElement('article');
      article.className = 'chat-message' + (msg.sender_id === root.dataset.userId ? ' mine' : '');
      const label = document.createElement('small');
      label.textContent = `${msg.sender_name} (${msg.sender_role}) · ${new Date(msg.created_at).toLocaleString()}`;
      const body = document.createElement('p'); body.textContent = msg.body;
      article.append(label, body); history.append(article);
      if (nearBottom) history.scrollTop = history.scrollHeight;
    };
    socket.onclose = () => { finishSending(); button.disabled = true; status.textContent = 'Disconnected. Reconnecting…'; setTimeout(connect, retry); retry = Math.min(30000, retry * 2); };
    socket.onerror = () => { status.textContent = 'Connection interrupted. Your message history is saved.'; };
  };
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    const body = form.elements.body.value.trim();
    if (!body || sending) return;
    if (socket?.readyState !== WebSocket.OPEN) { status.textContent = 'Reconnecting. Your draft is saved here; send it once connected.'; return; }
    sending = true; button.disabled = true; status.textContent = 'Sending…';
    try { socket.send(JSON.stringify({body})); } catch (_) { finishSending(); status.textContent = 'Message could not be sent. Your draft is retained.'; return; }
    sendTimeout = setTimeout(() => { finishSending(); status.textContent = 'Delivery has not been confirmed. Check the conversation before retrying; your draft is retained.'; }, 10000);
  });
  connect();
})();
