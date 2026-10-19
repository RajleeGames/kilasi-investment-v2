(() => {
  const q = (selector, root = document) => root.querySelector(selector);
  const qa = (selector, root = document) => [...root.querySelectorAll(selector)];

  const composer = q('[data-sms-compose]');
  if (!composer) return;

  const tabs = qa('[data-sms-source]', composer);
  const rows = qa('[data-sms-recipient]', composer);
  const checks = qa('[data-sms-recipient-check]', composer);
  const search = q('[data-sms-recipient-search]', composer);
  const message = q('[data-sms-message]', composer);
  const templateSelect = q('[data-sms-template-select]', composer);
  const clientError = q('[data-sms-client-error]', composer);
  let activeSource = 'customer';

  const smsSegments = text => {
    if (!text) return 0;
    const unicode = [...text].some(char => char.charCodeAt(0) > 127);
    const single = unicode ? 70 : 160;
    const multipart = unicode ? 67 : 153;
    return text.length <= single ? 1 : Math.ceil(text.length / multipart);
  };

  const filteredRows = () => rows.filter(row => {
    const sourceOk = row.dataset.source === activeSource;
    const term = (search?.value || '').trim().toLowerCase();
    const searchOk = !term || (row.dataset.search || '').includes(term);
    return sourceOk && searchOk && !row.classList.contains('is-disabled');
  });

  const syncRows = () => {
    const term = (search?.value || '').trim().toLowerCase();
    rows.forEach(row => {
      const show = row.dataset.source === activeSource && (!term || (row.dataset.search || '').includes(term));
      row.classList.toggle('is-hidden-source', !show);
    });
  };

  const syncSummary = () => {
    const selected = checks.filter(input => input.checked && !input.disabled).length;
    const text = message?.value || '';
    const segments = smsSegments(text);
    const preview = text
      .replaceAll('{name}', 'Customer')
      .replaceAll('{first_name}', 'Customer')
      .replaceAll('{phone}', '255712345678');

    const selectedCount = q('[data-sms-selected-count]', composer);
    const summaryRecipients = q('[data-sms-summary-recipients]', composer);
    const charCount = q('[data-sms-char-count]', composer);
    const segmentCount = q('[data-sms-segment-count]', composer);
    const summaryChars = q('[data-sms-summary-chars]', composer);
    const summarySegments = q('[data-sms-summary-segments]', composer);
    const units = q('[data-sms-estimated-units]', composer);
    const previewEl = q('[data-sms-preview]', composer);

    if (selectedCount) selectedCount.textContent = selected;
    if (summaryRecipients) summaryRecipients.textContent = selected;
    if (charCount) charCount.textContent = text.length;
    if (segmentCount) segmentCount.textContent = segments;
    if (summaryChars) summaryChars.textContent = text.length;
    if (summarySegments) summarySegments.textContent = segments;
    if (units) units.textContent = selected * segments;
    if (previewEl) previewEl.textContent = preview || 'Your message preview will appear here.';
  };

  tabs.forEach(tab => tab.addEventListener('click', () => {
    activeSource = tab.dataset.smsSource || 'customer';
    tabs.forEach(item => item.classList.toggle('active', item === tab));
    syncRows();
  }));

  search?.addEventListener('input', syncRows);
  checks.forEach(input => input.addEventListener('change', syncSummary));

  q('[data-sms-select-visible]', composer)?.addEventListener('click', () => {
    filteredRows().forEach(row => {
      const input = q('[data-sms-recipient-check]', row);
      if (input && !input.disabled) input.checked = true;
    });
    syncSummary();
  });

  q('[data-sms-clear-selection]', composer)?.addEventListener('click', () => {
    checks.forEach(input => { input.checked = false; });
    syncSummary();
  });

  message?.addEventListener('input', syncSummary);

  templateSelect?.addEventListener('change', () => {
    const option = templateSelect.options[templateSelect.selectedIndex];
    const body = option?.dataset.body || '';
    if (message && body) {
      message.value = body;
      message.dispatchEvent(new Event('input', {bubbles:true}));
    }
  });

  qa('[data-sms-token]', composer).forEach(button => {
    button.addEventListener('click', () => {
      if (!message) return;
      const token = button.dataset.smsToken || '';
      const start = message.selectionStart ?? message.value.length;
      const end = message.selectionEnd ?? start;
      message.setRangeText(token, start, end, 'end');
      message.focus();
      message.dispatchEvent(new Event('input', {bubbles:true}));
    });
  });

  const refresh = q('[data-sms-refresh-balance]', composer);
  refresh?.addEventListener('click', async () => {
    const value = q('[data-sms-balance-value]', composer);
    refresh.disabled = true;
    if (value) value.textContent = 'Checking…';
    try {
      const response = await fetch(refresh.dataset.url, {
        headers: {'X-Requested-With': 'XMLHttpRequest'},
        credentials: 'same-origin',
      });
      const payload = await response.json();
      if (value) value.textContent = payload.display || 'Unavailable';
    } catch (_) {
      if (value) value.textContent = 'Unavailable';
    } finally {
      refresh.disabled = false;
    }
  });

  composer.addEventListener('submit', event => {
    const selected = checks.filter(input => input.checked && !input.disabled).length;
    const manual = q('textarea[name="manual_recipients"]', composer)?.value.trim() || '';
    const body = message?.value.trim() || '';
    let error = '';
    if (!selected && !manual) error = 'Select at least one customer/contact or enter a manual phone number.';
    else if (!body) error = 'Write the SMS message before sending.';

    if (error) {
      event.preventDefault();
      if (clientError) {
        clientError.hidden = false;
        clientError.textContent = error;
        clientError.scrollIntoView({behavior:'smooth', block:'center'});
      }
    } else if (clientError) {
      clientError.hidden = true;
      clientError.textContent = '';
    }
  });

  syncRows();
  syncSummary();
})();
