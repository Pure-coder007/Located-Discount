(() => {
  const badge = document.querySelector('[data-chat-unread-total]');
  if (!badge) return;
  const select = document.querySelector('[data-chat-business-select]');
  select?.addEventListener('change', () => { if (select.value) select.form.requestSubmit(); });
  let loading = false;
  const refresh = async () => {
    if (loading || document.hidden) return;
    loading = true;
    try {
      const response = await fetch('/chat/unread', {headers:{Accept:'application/json'}, cache:'no-store'});
      if (!response.ok) return;
      const data = await response.json();
      badge.textContent = data.total;
      badge.hidden = !data.total;
      const notices = document.querySelector('[data-chat-notices]');
      if (notices) {
        notices.replaceChildren();
        data.businesses.filter(business => business.unread).forEach(business => {
          const notice = document.createElement('div'); notice.className = 'flash-message flash-info';
          const text = document.createElement('span'); text.textContent = `New message from ${business.name}.`;
          const link = document.createElement('a'); link.href = '#chat-business-picker'; link.textContent = 'View below ↓';
          link.addEventListener('click', () => select?.focus());
          notice.append(text, link); notices.append(notice);
        });
      }
      data.businesses.forEach(business => {
        const option = select && [...select.options].find(option => option.value === business.id);
        if (option) option.textContent = business.name + (business.unread ? ` · ${business.unread} new message${business.unread === 1 ? '' : 's'}` : '');
      });
    } catch (_) { /* Retry at the next interval without interrupting chat. */ }
    finally { loading = false; }
  };
  document.addEventListener('chat-read', refresh);
  document.addEventListener('visibilitychange', refresh);
  refresh();
  setInterval(refresh, 5000);
})();
