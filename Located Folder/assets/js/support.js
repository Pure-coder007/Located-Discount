(() => {
  const panel = document.querySelector('[data-support-panel]');
  if (!panel) return;
  const launcher = document.querySelector('[data-support-open]');
  const history = panel.querySelector('[data-support-history]');
  const form = panel.querySelector('[data-support-form]');
  const status = panel.querySelector('[data-support-status]');
  const whatsapp = panel.querySelector('[data-support-whatsapp]');
  const send = form.querySelector('button');
  const input = form.elements.question;
  let busy = false;
  const open = () => { panel.hidden = false; launcher.setAttribute('aria-expanded', 'true'); input.focus(); };
  const close = () => { panel.hidden = true; launcher.setAttribute('aria-expanded', 'false'); launcher.focus(); };
  launcher.addEventListener('click', () => panel.hidden ? open() : close());
  document.querySelectorAll('[data-support-trigger]').forEach(button => button.addEventListener('click', open));
  panel.querySelector('[data-support-close]').addEventListener('click', close);
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) close(); });
  const element = (tag, content, className) => { const node = document.createElement(tag); if (content) node.textContent = content; if (className) node.className = className; return node; };
  const link = (label, url) => {
    const node = element('a', label);
    const parsed = new URL(url, location.origin);
    if (parsed.origin !== location.origin && !['https://wa.me', 'https://www.google.com'].includes(parsed.origin) && parsed.protocol !== 'tel:') return node;
    node.href = parsed.href;
    if (parsed.origin !== location.origin && parsed.protocol !== 'tel:') { node.target = '_blank'; node.rel = 'noopener noreferrer'; }
    return node;
  };
  const money = kobo => new Intl.NumberFormat('en-NG', {style:'currency', currency:'NGN', maximumFractionDigits:0}).format(kobo / 100);
  const renderOffer = offer => {
    const card = element('article', '', 'support-offer');
    if (offer.image) {
      const image = element('img'); image.src = offer.image; image.alt = offer.title; image.loading = 'lazy'; card.append(image);
    }
    const body = element('div'); body.append(element('h3', offer.title), element('p', `${offer.business} · ${offer.category}`), element('strong', money(offer.price_kobo)), element('p', `${offer.address}, ${offer.city}`), element('p', `${offer.days_left} days to go · ${offer.vouchers_remaining} vouchers remaining`));
    const details = element('details'); details.append(element('summary', 'Terms, description & opening hours'), element('p', offer.description), element('p', offer.terms));
    if (offer.hours) details.append(element('p', offer.hours));
    body.append(details);
    const actions = element('div', '', 'support-offer-actions'); actions.append(link('View deal →', offer.url));
    if (offer.phone) actions.append(link(offer.phone, `tel:${offer.phone.replace(/[^+0-9]/g, '')}`));
    body.append(actions); card.append(body); return card;
  };
  const ask = async question => {
    question = question.trim();
    if (!question || busy) return;
    busy = true; send.disabled = true;
    history.querySelectorAll('[data-support-suggestions]').forEach(node => node.remove());
    history.append(element('div', question, 'support-bubble user'));
    const waiting = element('div', 'Checking the current catalogue…', 'support-bubble'); history.append(waiting); history.scrollTop = history.scrollHeight;
    status.textContent = '';
    const controller = new AbortController(); const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch('/support/ask', {method:'POST', headers:{'Content-Type':'application/x-www-form-urlencoded', Accept:'application/json'}, body:new URLSearchParams({question, area:panel.dataset.area || '', csrf_token:panel.dataset.csrf}), signal:controller.signal});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.error || (response.status === 400 ? 'Reload this page to refresh your session, then try again.' : 'The guide is temporarily unavailable. Please try again or contact admin on WhatsApp.'));
      waiting.textContent = result.message;
      result.offers.forEach(offer => history.append(renderOffer(offer)));
      (result.places || []).forEach(place => {
        const card = element('article', '', 'support-offer');
        const body = element('div'); body.append(element('h3', place.name), element('p', place.category), element('p', `${place.address}, ${place.city}`));
        if (place.hours) body.append(element('p', place.hours));
        const actions = element('div', '', 'support-offer-actions'); actions.append(link('View business →', place.url));
        if (place.phone) actions.append(link(place.phone, `tel:${place.phone.replace(/[^+0-9]/g, '')}`));
        body.append(actions); card.append(body); history.append(card);
      });
      const links = element('div', '', 'support-links'); result.links.forEach(item => links.append(link(item.label, item.url))); history.append(links);
      const suggestions = element('div', '', 'support-suggestions'); suggestions.dataset.supportSuggestions = '';
      result.suggestions.forEach(question => { const button = element('button', question); button.type = 'button'; suggestions.append(button); }); history.append(suggestions);
      whatsapp.href = result.whatsapp;
      status.textContent = `Live information checked ${new Date(result.updated_at).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}. ${result.total_live} live offers.`;
      input.value = '';
    } catch (error) {
      waiting.textContent = error.name === 'AbortError' ? 'The lookup took too long. Please retry, or chat with admin on WhatsApp.' : error.message;
      status.textContent = 'Your enquiry was not sent to WhatsApp. Use the link below to continue there.';
    } finally {
      clearTimeout(timeout); busy = false; send.disabled = false;
      while (history.children.length > 80) history.firstElementChild.remove();
      waiting.scrollIntoView({block:'start', behavior:'auto'});
    }
  };
  form.addEventListener('submit', event => { event.preventDefault(); ask(input.value); });
  history.addEventListener('click', event => { const button = event.target.closest('[data-support-suggestions] button'); if (button) ask(button.textContent); });
})();
