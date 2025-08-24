async function fetchJSON(url) {
  const r = await fetch(url);
  return await r.json();
}

function renderTable(id, rows, cols, rowClassFn) {
  const table = document.getElementById(id);
  table.innerHTML = '';
  if (!rows || rows.length === 0) {
    table.innerText = '(none)';
    return;
  }
  const header = document.createElement('tr');
  for (const c of cols) {
    const th = document.createElement('th');
    th.textContent = c;
    header.appendChild(th);
  }
  table.appendChild(header);
  for (const row of rows) {
    const tr = document.createElement('tr');
    if (typeof rowClassFn === 'function') {
      const cls = rowClassFn(row);
      if (cls) tr.className = cls;
    }
    for (const c of cols) {
      const td = document.createElement('td');
      td.textContent = row[c];
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
}

async function refresh() {
  const params = new URLSearchParams(window.location.search);
  const dirParam = params.get('dir');
  const q = dirParam ? `?dir=${encodeURIComponent(dirParam)}` : '';
  // Update status badge from consistency API
  const statusEl = document.getElementById('status');
  if (statusEl) {
    statusEl.textContent = 'checking…';
    statusEl.className = 'status-badge unknown';
    try {
      const cons = await fetchJSON(`/api/consistency${q}`);
      if (cons && typeof cons.ok === 'boolean') {
        if (cons.ok) {
          statusEl.textContent = 'OK';
          statusEl.className = 'status-badge ok';
        } else {
          statusEl.textContent = 'Mismatch';
          statusEl.className = 'status-badge mismatch';
        }
      } else {
        statusEl.textContent = 'unknown';
        statusEl.className = 'status-badge unknown';
      }
    } catch (e) {
      statusEl.textContent = 'unknown';
      statusEl.className = 'status-badge unknown';
    }
  }
  let suggText = '(none)';
  try {
    const sugg = await fetchJSON(`/api/suggestion${q}`);
    if (sugg && (sugg.suggested_card || sugg.card)) {
      const name = sugg.suggested_card || sugg.card;
      const wr = (sugg.gihwr !== undefined && sugg.gihwr !== null) ? `GIHWR=${sugg.gihwr}` : '';
      const sc = (sugg.score !== undefined && sugg.score !== null) ? `score=${sugg.score}` : '';
      suggText = [name, [wr, sc].filter(Boolean).join(', ')].filter(Boolean).join('  ');
      const el = document.getElementById('suggestion');
      el.innerText = suggText;
      if (sugg.explain) {
        el.title = `base=${sugg.explain.base_gihwr}, open=${sugg.explain.openness_bonus}, curve=${sugg.explain.curve_bonus}, creature=${sugg.explain.creature_balance_bonus}`;
      }
    } else {
      document.getElementById('suggestion').innerText = '(none)';
    }
  } catch (e) {
    document.getElementById('suggestion').innerText = '(none)';
  }

  const offers = await fetchJSON(`/api/offers${q}`);
  let offerRows = offers.rows || [];
  // Sort by score if present, else by GIHWR
  offerRows = offerRows.slice().sort((a,b) => {
    const as = (a.score !== undefined && a.score !== null) ? a.score : (a.offered_card_gihwr ?? -1);
    const bs = (b.score !== undefined && b.score !== null) ? b.score : (b.offered_card_gihwr ?? -1);
    return bs - as;
  });
  const offerCols = ['card_name', ...(offerRows.some(r => 'score' in r) ? ['score'] : []), 'offered_card_gihwr', 'is_picked'];
  renderTable('offers', offerRows, offerCols, (r) => r.is_picked ? 'picked' : '');

  const picks = await fetchJSON(`/api/picks${q}`);
  renderTable('picks', picks.rows || [], ['picked_card', 'picked_card_gihwr', 'ts']);
}

setInterval(refresh, 2000);
refresh();
