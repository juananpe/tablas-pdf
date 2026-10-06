const $ = id => document.getElementById(id);
let doc = null, selected = new Set(), result = null, mode = 'records', busy = false, noticeTimer;
function notify(message, error = false) {
  clearTimeout(noticeTimer); $('notice').textContent = message;
  $('notice').classList.toggle('error', error); $('notice').hidden = false;
  noticeTimer = setTimeout(() => $('notice').hidden = true, error ? 9000 : 3000);
}
async function request(url, options) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la operación.');
  return data;
}
function controls() {
  $('upload').disabled = busy; $('change').disabled = busy;
  $('select-all').disabled = busy; $('clear').disabled = busy;
  $('extract').disabled = busy || !selected.size;
  $('copy').disabled = busy || !result; $('download').disabled = busy || !result;
  $('selected-count').textContent = selected.size ? `${selected.size} página${selected.size === 1 ? '' : 's'} seleccionada${selected.size === 1 ? '' : 's'}` : 'Ninguna página seleccionada';
  document.body.classList.toggle('busy', busy);
  document.querySelectorAll('.page-card input').forEach(input => input.disabled = busy);
}
function resetResult() {
  result = null; $('json').hidden = true; $('warnings').hidden = true;
  $('result-meta').hidden = true; $('result-placeholder').hidden = false;
  $('output-status').textContent = 'Listo para extraer'; controls();
}
function selectionChanged() {
  document.querySelectorAll('.page-card').forEach(card => {
    const checked = selected.has(Number(card.dataset.page));
    card.classList.toggle('selected', checked); card.querySelector('input').checked = checked;
  });
  resetResult();
}
async function upload(file) {
  if (!file || busy) return;
  if (file.size > 50 * 1024 * 1024) return notify('El límite es de 50 MB por PDF.', true);
  busy = true; controls(); $('output-status').textContent = 'Cargando PDF…';
  const form = new FormData(); form.append('file', file);
  try {
    const newDoc = await request('/api/documents', {method:'POST', body:form});
    doc = newDoc; selected.clear(); resetResult(); $('pages').replaceChildren(); $('pages').hidden = false;
    $('filename').textContent = doc.nombre;
    $('filemeta').textContent = `${doc.paginas} páginas · ${(doc.bytes / 1024 / 1024).toFixed(1)} MB`;
    $('upload').hidden = true; $('source-placeholder').hidden = true;
    $('document-info').hidden = false; $('selection-toolbar').hidden = false; $('change').hidden = false;
    for (let page = 1; page <= doc.paginas; page++) {
      const card = document.createElement('label'); card.className = 'page-card'; card.dataset.page = page;
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.setAttribute('aria-label', `Página ${page}, contiene tablas`);
      const thumb = document.createElement('div'); thumb.className = 'thumb';
      const img = document.createElement('img'); img.alt = `Vista de la página ${page}`; img.loading = 'lazy';
      img.src = `/api/documents/${doc.id}/pages/${page}/thumbnail`;
      img.onerror = () => {img.alt = `No se pudo mostrar la página ${page}`;}; thumb.append(img);
      const caption = document.createElement('div'); caption.className = 'page-caption';
      const text = document.createElement('span'); text.textContent = `Página ${page}`;
      const badge = document.createElement('small'); badge.textContent = '✓ Tabla'; caption.append(text, badge);
      checkbox.addEventListener('change', () => {
        if (busy) {checkbox.checked = selected.has(page); return;}
        checkbox.checked ? selected.add(page) : selected.delete(page); selectionChanged();
      });
      card.append(checkbox, thumb, caption); $('pages').append(card);
    }
    $('output-status').textContent = 'Marca las páginas que contienen tablas';
    notify('PDF cargado. Selecciona las páginas con tablas.');
  } catch (error) {notify(error.message, true); $('output-status').textContent = result ? 'Resultado anterior conservado' : 'Error al cargar el PDF';}
  finally {busy = false; controls(); $('file').value = '';}
}
function viewData() {
  if (!result) return {};
  const {tablas, ...data} = result;
  if (mode === 'tables') return {documento:result.documento, paginas_seleccionadas:result.paginas_seleccionadas, tablas, avisos:result.avisos};
  return data;
}
function renderResult() {
  const serialized = JSON.stringify(viewData(), null, 2);
  // Escape PDF-derived text before adding syntax highlighting.
  const escaped = serialized.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  $('json').innerHTML = escaped.replace(/("(?:\\.|[^"\\])*"\s*:?)|\b(-?\d+(?:\.\d+)?)\b|\b(null|true|false)\b/g, (match, string, number) => {
    const cls = string ? (string.endsWith(':') ? 'json-key' : 'json-string') : number ? 'json-number' : 'json-null';
    return `<span class="${cls}">${match}</span>`;
  });
  $('json').hidden = false; $('result-placeholder').hidden = true;
  $('result-meta').textContent = `${result.registros.length} registros · ${result.tablas.length} tablas detectadas · Páginas ${result.paginas_seleccionadas.join(', ')}`;
  $('result-meta').hidden = false;
  $('warnings').replaceChildren(); $('warnings').hidden = !result.avisos.length;
  if (result.avisos.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = `${result.avisos.length} aviso${result.avisos.length === 1 ? '' : 's'} para revisar`;
    details.append(summary);
    for (const warning of result.avisos) {const p = document.createElement('p'); p.textContent = warning; details.append(p);}
    $('warnings').append(details);
  }
  $('output-status').textContent = result.avisos.length ? 'Extracción terminada · revisa los avisos' : 'Extracción terminada';
  controls();
}
function setMode(next) {
  mode = next;
  for (const [id, value] of [['records-tab','records'],['tables-tab','tables']]) {
    $(id).classList.toggle('active', mode === value); $(id).setAttribute('aria-selected', String(mode === value));
  }
  if (result) renderResult();
}
$('upload').onclick = $('change').onclick = () => $('file').click();
$('file').onchange = () => upload($('file').files[0]);
for (const name of ['dragenter','dragover']) $('upload').addEventListener(name, event => {event.preventDefault(); if (!busy) $('upload').classList.add('dragover');});
$('upload').addEventListener('dragleave', () => $('upload').classList.remove('dragover'));
$('upload').addEventListener('drop', event => {event.preventDefault(); $('upload').classList.remove('dragover'); upload(event.dataTransfer.files[0]);});
// Stop the browser from navigating to a dropped PDF outside the upload area.
window.addEventListener('dragover', event => event.preventDefault());
window.addEventListener('drop', event => event.preventDefault());
$('select-all').onclick = () => {if (!busy && doc) {selected = new Set(Array.from({length:doc.paginas},(_,i)=>i+1)); selectionChanged();}};
$('clear').onclick = () => {if (!busy) {selected.clear(); selectionChanged();}};
$('extract').onclick = async () => {
  if (!doc || !selected.size || busy) return;
  busy = true; controls(); $('result-placeholder').hidden = true; $('json').hidden = true;
  $('processing').hidden = false; $('warnings').hidden = true; $('result-meta').hidden = true;
  $('extract').querySelector('span').textContent = 'Extrayendo…';
  try {
    result = await request(`/api/documents/${doc.id}/extract`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paginas:[...selected].sort((a,b)=>a-b)})});
    if (!result.registros.length) setMode('tables'); else setMode('records');
    renderResult();
  } catch (error) {notify(error.message, true); if (result) renderResult(); else {$('result-placeholder').hidden = false; $('output-status').textContent = 'Error al extraer. Puedes volver a intentarlo.';}}
  finally {busy = false; $('processing').hidden = true; $('extract').querySelector('span').textContent = 'Extraer tablas'; controls();}
};
$('records-tab').onclick = () => setMode('records'); $('tables-tab').onclick = () => setMode('tables');
$('copy').onclick = async () => {
  try {await navigator.clipboard.writeText(JSON.stringify(viewData(), null, 2)); notify('JSON de esta vista copiado.');}
  catch {notify('No se pudo copiar. Puedes descargar el JSON completo.', true);}
};
$('download').onclick = () => {
  const anchor = document.createElement('a');
  anchor.href = `/api/documents/${doc.id}/result.json`;
  anchor.download = doc.nombre.replace(/\.pdf$/i,'') + '-tablas.json';
  document.body.append(anchor); anchor.click(); anchor.remove();
};
