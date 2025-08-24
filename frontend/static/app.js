async function fetchJSON(url) {
  const r = await fetch(url);
  return await r.json();
}
async function refresh() {
  const offers = await fetchJSON('/api/offers');
  document.getElementById('offers').innerText = JSON.stringify(offers,null,2);
  const picks = await fetchJSON('/api/picks');
  document.getElementById('picks').innerText = JSON.stringify(picks,null,2);
}
setInterval(refresh, 2000);
refresh();
