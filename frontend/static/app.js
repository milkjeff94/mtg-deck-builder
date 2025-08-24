async function fetchJSON(url) {
  const r = await fetch(url);
  return await r.json();
}

function renderTable(id, rows, cols) {
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
    for (const c of cols) {
      const td = document.createElement('td');
      td.textContent = row[c];
      tr.appendChild(td);
    }
    table.appendChild(tr);
  }
}

async function refresh() {
  const sugg = await fetchJSON('/api/suggestion');
  document.getElementById('suggestion').innerText = sugg.card || '(none)';

  const offers = await fetchJSON('/api/offers');
  renderTable('offers', offers.rows, ['card_name', 'offered_card_gihwr', 'is_picked']);

  const picks = await fetchJSON('/api/picks');
  renderTable('picks', picks.rows, ['picked_card', 'picked_card_gihwr', 'ts']);
}

setInterval(refresh, 2000);
refresh();
