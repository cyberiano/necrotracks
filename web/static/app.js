'use strict';
// Necrotracks — web sin build step. Tres vistas por hash: #show, #setlists (+ #setlist/SLUG), #biblioteca.
// El estado en vivo llega por SSE (/api/events); si se corta, se muestra y el navegador reconecta solo.

const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const mmss = s => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const STATE_LABEL = {stopped: 'Parado', playing: 'Sonando', paused: 'En pausa', waiting: 'Esperando'};

let info = null;
const live = {engine: null, state: null, at: 0};
let current = null;  // vista activa: {dirty?(), onLive?(), onKey?(e)}

// ── API ──────────────────────────────────────────────────────────────────────

function errText(data, status) {
  const d = data && data.detail;
  if (Array.isArray(d)) return d.map(x => x.msg).join('; ');
  return d || `Error ${status}`;
}

async function api(method, url, body) {
  const opts = {method, headers: {}};
  if (body !== undefined) {
    opts.body = JSON.stringify(body);
    opts.headers['Content-Type'] = 'application/json';
  }
  let r;
  try { r = await fetch(url, opts); } catch { throw new Error('Sin conexión con Necrotracks'); }
  const data = (r.headers.get('content-type') || '').includes('json') ? await r.json() : null;
  if (!r.ok) throw new Error(errText(data, r.status));
  return data;
}

async function cmd(c, extra = {}) {
  try { await api('POST', '/api/cmd', {cmd: c, ...extra}); return true; } catch (e) { toast(e.message, 'bad'); return false; }
}

function upload(file, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', '/api/incoming/' + encodeURIComponent(file.name));
    x.upload.onprogress = e => onProgress(e.loaded / (e.total || file.size || 1));
    x.onload = () => {
      if (x.status < 300) return resolve();
      let data = null; try { data = JSON.parse(x.responseText); } catch {}
      reject(new Error(errText(data, x.status)));
    };
    x.onerror = () => reject(new Error('Se cortó la conexión mientras subía'));
    x.send(file);
  });
}

let toastTimer;
function toast(msg, kind = 'ok') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = kind;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, kind === 'bad' ? 6000 : 2500);
}

// ── Estado en vivo ───────────────────────────────────────────────────────────

function connect() {
  const es = new EventSource('/api/events');
  es.onmessage = e => {
    const m = JSON.parse(e.data);
    live.at = Date.now();
    live.engine = m.engine;
    live.state = m.state || null;
    renderConn();
    current?.onLive?.();
  };
  es.addEventListener('ping', () => { live.at = Date.now(); renderConn(); });
  es.onerror = () => renderConn();
}

const webOk = () => live.at > 0 && Date.now() - live.at < 25000;
const sounding = () => live.state && live.state.state !== 'stopped';

function renderConn() {
  const c = $('#conn'), b = $('#banner');
  let cls = 'ok', txt, banner = '';
  if (!webOk()) {
    cls = 'bad'; txt = 'Sin conexión';
    banner = 'Sin conexión con Necrotracks. Si estaba sonando, sigue sonando: lo que se cortó es la web.';
  } else if (!live.engine) {
    cls = 'bad'; txt = 'Engine caído';
    banner = 'El engine no responde. ¿El iRig está enchufado? Al reconectarlo vuelve solo, con la set list cargada.';
  } else {
    txt = live.state?.setlist ? STATE_LABEL[live.state.state] : 'Sin set list';
    if (live.state?.state === 'playing') cls = 'play';
  }
  c.className = 'conn ' + cls;
  c.textContent = txt;
  b.textContent = banner;
  b.hidden = !banner;
}
setInterval(renderConn, 2000);

// ── Ruteo ────────────────────────────────────────────────────────────────────

const views = {show: viewShow, setlists: viewSetlists, setlist: viewSetlist, biblioteca: viewLibrary, controles: viewControls};
let lastHash = location.hash, skipHash = false;

function route() {
  current?.leave?.();
  const [name, arg] = location.hash.slice(1).split('/');
  const fn = views[name] || viewShow;
  document.body.classList.toggle('show-mode', fn === viewShow);
  const tab = name === 'setlist' ? 'setlists' : (views[name] ? name : 'show');
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + tab));
  current = fn(arg ? decodeURIComponent(arg) : null);
}

window.addEventListener('hashchange', () => {
  if (skipHash) { skipHash = false; return; }
  if (current?.dirty?.() && !confirm('Hay cambios sin guardar. ¿Salir igual?')) {
    skipHash = true; location.hash = lastHash; return;
  }
  lastHash = location.hash;
  route();
});
window.addEventListener('beforeunload', e => { if (current?.dirty?.()) e.preventDefault(); });
document.addEventListener('keydown', e => {
  if (e.target.closest('input, select, textarea') || e.metaKey || e.ctrlKey || e.altKey) return;
  current?.onKey?.(e);
});

// ── Show Mode ────────────────────────────────────────────────────────────────

function describe(item) {
  if (!item) return '';
  if (item.behavior === 'wait') return `Esperar ${item.wait} s y reproducir la siguiente`;
  return info.behaviors[item.behavior] || item.behavior;
}

function viewShow() {
  const v = $('#view');
  v.innerHTML = `
    <section class="show" id="sh" hidden>
      <div class="sh-top">
        <span id="sh-setlist" class="muted"></span>
        <button id="sh-change" class="link">Cambiar set list</button>
      </div>
      <div class="sh-head"><span id="sh-state" class="badge"></span><span id="sh-block" class="block"></span></div>
      <div class="sh-song"><span id="sh-num" class="num"></span><h1 id="sh-song"></h1></div>
      <div class="progress"><div id="sh-bar"></div></div>
      <div class="times"><span id="sh-pos"></span><span id="sh-rem"></span></div>
      <div id="sh-after" class="after"></div>
      <div id="sh-next" class="next"></div>
      <div class="transport">
        <button data-cmd="prev" class="tbtn"><b>⏮</b><small>Anterior</small></button>
        <button data-cmd="play_pause" id="sh-play" class="tbtn main"><b>▶</b><small>Play</small></button>
        <button data-cmd="stop" id="sh-stop" class="tbtn stop"><b>■</b><small>Stop</small></button>
        <button data-cmd="next" class="tbtn"><b>⏭</b><small>Siguiente</small></button>
      </div>
      <p class="muted small hint">Anterior, Siguiente y tocar una canción de la lista funcionan solo con la reproducción parada.
        Teclado: espacio = play/pausa · Esc = stop · ← → = anterior/siguiente.</p>
      <ol id="sh-list" class="sh-list"></ol>
    </section>
    <section id="sh-pick" hidden>
      <h2>Elegí la set list</h2>
      <div id="sh-pick-list" class="muted">Cargando…</div>
      <button id="sh-pick-cancel" class="link" hidden>Volver al show</button>
    </section>`;

  let detail = null, detailSlug = null, picking = false;

  async function loadDetail(slug) {
    detailSlug = slug;
    try { detail = await api('GET', '/api/setlists/' + encodeURIComponent(slug)); } catch { detail = null; }
    renderList();
    render();
  }

  function renderList() {
    const s = live.state, ol = $('#sh-list');
    if (!detail || !s) { ol.innerHTML = ''; return; }
    let block;
    ol.innerHTML = detail.items.map((it, i) => {
      const song = detail.songs[it.song];
      let head = '';
      if (it.block !== block) { block = it.block; head = `<li class="blk-head">${esc(block || 'Sin bloque')}</li>`; }
      return `${head}<li data-i="${i}" class="${i === s.index ? 'cur' : ''}">
        <span class="n">${i + 1}</span><span class="t">${esc(song ? song.name : it.song)}</span>
        <span class="d">${song ? mmss(song.duration) : ''}</span></li>`;
    }).join('');
  }

  async function showPicker(cancelable) {
    picking = true;
    $('#sh').hidden = true;
    $('#sh-pick').hidden = false;
    $('#sh-pick-cancel').hidden = !cancelable;
    let lists;
    try { lists = await api('GET', '/api/setlists'); } catch (e) { $('#sh-pick-list').textContent = e.message; return; }
    $('#sh-pick-list').innerHTML = lists.length ? `<ul class="cards">${lists.map(l => `
      <li><div><strong>${esc(l.name)}</strong><br><span class="muted small">${l.count} canciones · ${mmss(l.duration)}</span>
        ${l.problems.length ? `<br><span class="bad-text small">✗ ${esc(l.problems[0])}${l.problems.length > 1 ? ` (+${l.problems.length - 1})` : ''}</span>` : ''}</div>
        <button data-load="${esc(l.slug)}" class="primary" ${l.problems.length ? 'disabled' : ''}>Cargar</button></li>`).join('')}</ul>`
      : 'No hay set lists. Armá una en <a href="#setlists">Set lists</a>.';
  }

  function hidePicker() {
    picking = false;
    $('#sh-pick').hidden = true;
    render();
  }

  function render() {
    const s = live.state;
    if (!live.engine || !s) { $('#sh').hidden = true; $('#sh-pick').hidden = true; return; }
    if (!s.setlist) { if (!picking) showPicker(false); return; }
    if (picking) return;
    if (s.slug !== detailSlug) { loadDetail(s.slug); }
    $('#sh').hidden = false;
    const item = detail && detail.slug === s.slug ? detail.items[s.index] : null;
    const moving = s.state === 'playing' || s.state === 'paused';
    $('#sh-setlist').textContent = s.setlist;
    $('#sh-change').hidden = s.state !== 'stopped';
    const badge = $('#sh-state');
    badge.textContent = STATE_LABEL[s.state];
    badge.className = 'badge ' + s.state;
    $('#sh-block').textContent = s.block || '';
    $('#sh-num').textContent = `${s.index + 1}/${s.count}`;
    $('#sh-song').textContent = s.song || '—';
    const pos = moving ? s.position : 0;
    $('#sh-bar').style.width = s.duration ? `${Math.min(100, 100 * pos / s.duration)}%` : '0';
    $('#sh-pos').textContent = mmss(pos);
    $('#sh-rem').textContent = '−' + mmss(s.duration - pos);
    const after = $('#sh-after');
    if (s.state === 'waiting') {
      after.innerHTML = `<span class="countdown">Arranca en ${Math.ceil(s.wait_remaining)} s</span>
        <span class="muted small">Play = ya · Stop = cancelar la espera</span>`;
    } else {
      after.innerHTML = item ? `<span class="muted">Al terminar:</span> ${esc(describe(item))}` : '';
    }
    $('#sh-next').innerHTML = s.next ? `<span class="muted">Próxima:</span> ${esc(s.next)}` : '<span class="muted">Última de la set list</span>';
    const play = $('#sh-play');
    play.innerHTML = s.state === 'playing' ? '<b>⏸</b><small>Pausa</small>'
      : s.state === 'waiting' ? '<b>▶</b><small>Ya</small>' : '<b>▶</b><small>Play</small>';
    v.querySelector('[data-cmd=prev]').disabled = moving || s.index === 0;
    v.querySelector('[data-cmd=next]').disabled = moving || s.index + 1 >= s.count;
    $('#sh-stop').disabled = s.state === 'stopped';
    const ol = $('#sh-list');
    ol.classList.toggle('locked', moving);
    ol.querySelectorAll('li[data-i]').forEach(li => li.classList.toggle('cur', +li.dataset.i === s.index));
  }

  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (b?.dataset.cmd) return cmd(b.dataset.cmd);
    if (b?.dataset.load) {
      if (await cmd('load', {setlist: b.dataset.load})) { detailSlug = null; hidePicker(); }
      return;
    }
    if (b?.id === 'sh-change') return showPicker(true);
    if (b?.id === 'sh-pick-cancel') return hidePicker();
    const li = e.target.closest('li[data-i]');
    if (li) {
      if (sounding() && live.state.state !== 'waiting') return toast('Solo con la reproducción parada', 'bad');
      cmd('goto', {index: +li.dataset.i});
    }
  });

  render();
  return {
    onLive: render,
    onKey(e) {
      const keys = {' ': 'play_pause', Escape: 'stop', ArrowLeft: 'prev', ArrowRight: 'next'};
      if (keys[e.key] && live.state?.setlist) { e.preventDefault(); cmd(keys[e.key]); }
    },
  };
}

// ── Set lists ────────────────────────────────────────────────────────────────

function viewSetlists() {
  const v = $('#view');
  v.innerHTML = `
    <section>
      <h2>Set lists</h2>
      <form id="sl-new" class="row">
        <input name="name" placeholder="Nombre de la set list nueva" required>
        <button class="primary">Crear</button>
      </form>
      <div id="sl-list" class="muted">Cargando…</div>
    </section>`;

  async function load() {
    let lists;
    try { lists = await api('GET', '/api/setlists'); } catch (e) { $('#sl-list').textContent = e.message; return; }
    const loaded = live.state?.slug;
    $('#sl-list').innerHTML = lists.length ? `<ul class="cards">${lists.map(l => `
      <li><a href="#setlist/${encodeURIComponent(l.slug)}"><strong>${esc(l.name)}</strong>${l.slug === loaded ? ' <span class="tag">cargada</span>' : ''}<br>
        <span class="muted small">${l.count} canciones · ${mmss(l.duration)}</span>
        ${l.problems.length ? `<br><span class="bad-text small">✗ ${l.problems.length} problema(s)</span>` : '<br><span class="ok-text small">✓ Lista para tocar</span>'}</a>
        <button data-load="${esc(l.slug)}" ${l.problems.length ? 'disabled' : ''}>Cargar</button></li>`).join('')}</ul>`
      : '<p>Todavía no hay set lists.</p>';
  }

  v.addEventListener('submit', async e => {
    e.preventDefault();
    const name = e.target.name.value.trim();
    try {
      const sl = await api('POST', '/api/setlists', {name});
      location.hash = '#setlist/' + encodeURIComponent(sl.slug);
    } catch (err) { toast(err.message, 'bad'); }
  });
  v.addEventListener('click', async e => {
    const b = e.target.closest('button[data-load]');
    if (b && await cmd('load', {setlist: b.dataset.load})) location.hash = '#show';
  });
  load();
  return {};
}

function viewSetlist(slug) {
  const v = $('#view');
  v.innerHTML = '<section class="muted">Cargando…</section>';
  let sl = null, songs = [], saved = '';
  const model = () => JSON.stringify({name: sl.name, items: sl.items});
  const dirty = () => sl !== null && model() !== saved;
  const songName = s => (songs.find(x => x.slug === s) || {}).name || s;
  const songDur = s => (songs.find(x => x.slug === s) || {}).duration || 0;

  async function load() {
    try {
      [sl, songs] = await Promise.all([api('GET', '/api/setlists/' + encodeURIComponent(slug)), api('GET', '/api/songs')]);
    } catch (e) { v.innerHTML = `<section><p class="bad-text">${esc(e.message)}</p><a href="#setlists">Volver</a></section>`; return; }
    saved = model();
    render();
  }

  const behaviorOptions = sel => Object.entries(info.behaviors)
    .map(([k, label]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${esc(label)}</option>`).join('');

  function render() {
    const total = sl.items.reduce((a, it) => a + songDur(it.song), 0);
    v.innerHTML = `
      <section class="editor">
        <a href="#setlists" class="link">← Set lists</a>
        <input id="ed-name" class="title-input" value="${esc(sl.name)}" aria-label="Nombre de la set list">
        <p class="muted small">${sl.items.length} canciones · ${mmss(total)}</p>
        <div id="ed-loaded"></div>
        <div class="table-wrap"><table class="items">
          <thead><tr><th>#</th><th>Canción</th><th>Bloque</th><th>Al terminar</th><th></th></tr></thead>
          <tbody>${sl.items.map((it, i) => `
            <tr data-i="${i}">
              <td class="n">${i + 1}</td>
              <td>${esc(songName(it.song))} <span class="muted small">${mmss(songDur(it.song))}</span></td>
              <td><input class="blk" value="${esc(it.block || '')}" placeholder="—" aria-label="Bloque"></td>
              <td class="beh-cell"><select class="beh" aria-label="Al terminar">${behaviorOptions(it.behavior)}</select>
                <span class="wait-box" ${it.behavior === 'wait' ? '' : 'hidden'}><input class="wait" type="number" min="1" step="1" value="${it.wait || 5}" aria-label="Segundos"> s</span></td>
              <td class="acts"><button data-act="up" ${i === 0 ? 'disabled' : ''} title="Subir">↑</button><button data-act="down" ${i + 1 === sl.items.length ? 'disabled' : ''} title="Bajar">↓</button><button data-act="rm" title="Sacar">✕</button></td>
            </tr>`).join('') || '<tr><td colspan="5" class="muted">Vacía: agregá canciones abajo.</td></tr>'}
          </tbody>
        </table></div>
        <div class="row">
          <select id="ed-add">${songs.map(s => `<option value="${esc(s.slug)}">${esc(s.name)} (${mmss(s.duration)})</option>`).join('')}</select>
          <button id="ed-add-btn" ${songs.length ? '' : 'disabled'}>Agregar</button>
        </div>
        <details class="block-tool">
          <summary>Asignar un bloque a varias canciones</summary>
          <div class="row wrap">
            <input id="bt-name" placeholder="Nombre del bloque">
            <label>de <input id="bt-from" type="number" min="1" value="1" class="short"></label>
            <label>a <input id="bt-to" type="number" min="1" value="${Math.max(1, sl.items.length)}" class="short"></label>
            <select id="bt-beh"><option value="">(no cambiar el comportamiento)</option>${behaviorOptions('')}</select>
            <label id="bt-wait-box" hidden><input id="bt-wait" type="number" min="1" value="5" class="short"> s</label>
            <button id="bt-apply">Aplicar</button>
          </div>
        </details>
        <div id="ed-problems">${problemsHtml(sl.problems)}</div>
        <div class="row actions">
          <button id="ed-save" class="primary">Guardar</button>
          <span id="ed-dirty" class="muted small"></span>
          <span class="spacer"></span>
          <button id="ed-del" class="danger">Borrar set list</button>
        </div>
      </section>`;
    renderDirty();
    renderLoaded();
  }

  function problemsHtml(problems) {
    if (!problems) return '';
    return problems.length ? `<ul class="problems">${problems.map(p => `<li>✗ ${esc(p)}</li>`).join('')}</ul>`
      : '<p class="ok-text small">✓ Lista para tocar</p>';
  }

  function renderDirty() {
    const d = $('#ed-dirty');
    if (d) d.textContent = dirty() ? 'Cambios sin guardar' : '';
  }

  function renderLoaded() {
    const box = $('#ed-loaded');
    if (!box || live.state?.slug !== slug) { if (box) box.innerHTML = ''; return; }
    box.innerHTML = `<p class="note">Esta es la set list cargada en el show. Los cambios guardados se aplican al recargarla.
      ${live.state.state === 'stopped' ? '<button id="ed-reload">Recargar en el show</button>' : '(se puede con la reproducción parada)'}</p>`;
  }

  v.addEventListener('input', e => {
    if (!sl) return;
    const tr = e.target.closest('tr[data-i]');
    if (e.target.id === 'ed-name') sl.name = e.target.value;
    else if (tr && e.target.classList.contains('blk')) sl.items[+tr.dataset.i].block = e.target.value || null;
    else if (tr && e.target.classList.contains('wait')) sl.items[+tr.dataset.i].wait = +e.target.value;
    else if (e.target.id === 'bt-beh') $('#bt-wait-box').hidden = e.target.value !== 'wait';
    renderDirty();
  });
  v.addEventListener('change', e => {
    const tr = e.target.closest('tr[data-i]');
    if (tr && e.target.classList.contains('beh')) {
      const it = sl.items[+tr.dataset.i];
      it.behavior = e.target.value;
      if (it.behavior === 'wait' && !(it.wait > 0)) it.wait = 5;
      tr.querySelector('.wait-box').hidden = it.behavior !== 'wait';
      tr.querySelector('.wait').value = it.wait;
      renderDirty();
    }
  });
  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b || !sl) return;
    const tr = b.closest('tr[data-i]');
    const items = sl.items;
    if (tr) {
      const i = +tr.dataset.i;
      if (b.dataset.act === 'up') items.splice(i - 1, 0, items.splice(i, 1)[0]);
      if (b.dataset.act === 'down') items.splice(i + 1, 0, items.splice(i, 1)[0]);
      if (b.dataset.act === 'rm') items.splice(i, 1);
      return render();
    }
    if (b.id === 'ed-add-btn') {
      const last = items[items.length - 1];
      items.push({song: $('#ed-add').value, behavior: info.default_behavior, wait: 0, block: last ? last.block : null});
      return render();
    }
    if (b.id === 'bt-apply') {
      const from = +$('#bt-from').value - 1, to = +$('#bt-to').value - 1;
      if (!(from >= 0 && from <= to && to < items.length)) return toast(`Rango inválido (hay ${items.length} canciones)`, 'bad');
      const name = $('#bt-name').value.trim() || null, beh = $('#bt-beh').value, wait = +$('#bt-wait').value;
      if (beh === 'wait' && !(wait > 0)) return toast('La espera tiene que ser mayor que 0', 'bad');
      for (const it of items.slice(from, to + 1)) {
        it.block = name;
        if (beh) { it.behavior = beh; it.wait = beh === 'wait' ? wait : 0; }
      }
      return render();
    }
    if (b.id === 'ed-save') {
      try {
        sl = await api('PUT', '/api/setlists/' + encodeURIComponent(slug), {name: sl.name, items: sl.items});
        saved = model();
        render();
        toast('Guardada');
      } catch (err) { toast(err.message, 'bad'); }
      return;
    }
    if (b.id === 'ed-reload') {
      if (dirty()) return toast('Guardá los cambios primero', 'bad');
      if (await cmd('load', {setlist: slug})) toast('Recargada en el show');
      return;
    }
    if (b.id === 'ed-del') {
      if (!confirm(`¿Borrar la set list "${sl.name}"? Las canciones quedan en la biblioteca.`)) return;
      try {
        await api('DELETE', '/api/setlists/' + encodeURIComponent(slug));
        sl = null;
        location.hash = '#setlists';
      } catch (err) { toast(err.message, 'bad'); }
    }
  });

  load();
  return {dirty, onLive: renderLoaded};
}

// ── Biblioteca ───────────────────────────────────────────────────────────────

function viewLibrary() {
  const v = $('#view');
  v.innerHTML = `
    <section>
      <h2>Importar una canción</h2>
      <form id="imp" class="stack">
        <label>Archivos (WAV, FLAC, AIFF, MP3, ZIP, MIDI)
          <input type="file" id="imp-files" multiple accept=".wav,.wave,.flac,.aif,.aiff,.mp3,.ogg,.zip,.mid,.midi"></label>
        <label>o una carpeta <input type="file" id="imp-dir" webkitdirectory></label>
        <label>Nombre de la canción <input id="imp-name" placeholder="(el del archivo o la carpeta)"></label>
        <label>Si es un único archivo estéreo, qué es
          <select id="imp-layout">
            <option value="">Detectar por el nombre del archivo</option>
            <option value="click-pista">click-pista: L = click, R = pista (el formato de la banda)</option>
            <option value="foh">foh: pista estéreo, sin click</option>
          </select></label>
        <div class="row"><button class="primary" id="imp-go">Importar</button><span id="imp-status" class="small"></span></div>
        <p class="muted small">${esc(info.conventions)}</p>
      </form>
      <h2>Biblioteca</h2>
      <div id="lib" class="muted">Cargando…</div>
    </section>`;

  const status = (html, cls = '') => { const s = $('#imp-status'); s.innerHTML = html; s.className = 'small ' + cls; };
  const chosen = () => [...($('#imp-dir').files.length ? $('#imp-dir').files : $('#imp-files').files)].filter(f => !f.name.startsWith('.'));
  let busy = false;

  function syncBusy() {
    const go = $('#imp-go');
    if (!go) return;
    go.disabled = busy || sounding();
    if (!busy) status(sounding() ? 'Está sonando: el import se habilita con la reproducción parada.' : '', sounding() ? 'bad-text' : '');
  }

  async function load() {
    let songs;
    try { songs = await api('GET', '/api/songs'); } catch (e) { $('#lib').textContent = e.message; return; }
    const yes = b => b ? 'sí' : '—';
    $('#lib').innerHTML = songs.length ? `<div class="table-wrap"><table class="songs">
      <thead><tr><th>Canción</th><th>Duración</th><th>Pista</th><th>Click</th><th>Guía</th><th>MIDI</th><th>En set lists</th><th></th></tr></thead>
      <tbody>${songs.map(s => `<tr>
        <td><strong>${esc(s.name)}</strong>${s.warnings.map(w => `<div class="warn small">⚠ ${esc(w)}</div>`).join('')}</td>
        <td>${mmss(s.duration)}</td><td>${esc(s.foh || '—')}</td><td>${yes(s.click)}</td><td>${yes(s.guia)}</td><td>${yes(s.midi)}</td>
        <td class="small">${s.used_in.map(esc).join(', ') || '<span class="muted">—</span>'}</td>
        <td><button data-del="${esc(s.slug)}" data-name="${esc(s.name)}" class="danger small-btn" ${s.used_in.length ? 'disabled title="Está en una set list"' : ''}>Borrar</button></td>
      </tr>`).join('')}</tbody></table></div>` : '<p>La biblioteca está vacía.</p>';
  }

  v.addEventListener('change', e => {
    if (e.target.id === 'imp-dir' && e.target.files.length) {
      $('#imp-files').value = '';
      const rel = e.target.files[0].webkitRelativePath || '';
      if (rel.includes('/')) $('#imp-name').value = rel.split('/')[0];
    } else if (e.target.id === 'imp-files' && e.target.files.length) {
      $('#imp-dir').value = '';
      if (e.target.files.length === 1) $('#imp-name').value = e.target.files[0].name.replace(/\.[^.]+$/, '');
    }
  });

  v.addEventListener('submit', async e => {
    e.preventDefault();
    const files = chosen();
    if (!files.length) return toast('Elegí archivos o una carpeta', 'bad');
    const name = $('#imp-name').value.trim();
    if (files.length > 1 && !name) return toast('Son varios archivos: poné el nombre de la canción', 'bad');
    busy = true; syncBusy();
    try {
      await api('DELETE', '/api/incoming');
      for (const [i, f] of files.entries()) {
        await upload(f, p => status(`Subiendo ${i + 1}/${files.length}: ${esc(f.name)} — ${Math.round(100 * p)}%`));
      }
      status('Importando… convierte y escribe despacio para no trabar al iRig: ~1 min por canción.');
      const song = await api('POST', '/api/import', {name: name || null, layout: $('#imp-layout').value || null});
      status(`✓ Importada: ${esc(song.name)} (${mmss(song.duration)})` + song.warnings.map(w => `<br>⚠ ${esc(w)}`).join(''), 'ok-text');
      $('#imp').reset();
      load();
    } catch (err) {
      status('✗ ' + esc(err.message), 'bad-text');
    } finally {
      busy = false;
      $('#imp-go').disabled = sounding();
    }
  });

  v.addEventListener('click', async e => {
    const b = e.target.closest('button[data-del]');
    if (!b || !confirm(`¿Borrar "${b.dataset.name}" de la biblioteca? Se borran también sus archivos.`)) return;
    try { await api('DELETE', '/api/songs/' + encodeURIComponent(b.dataset.del)); toast('Borrada'); load(); }
    catch (err) { toast(err.message, 'bad'); }
  });

  load();
  syncBusy();
  return {onLive: syncBusy};
}

// ── Controles MIDI ───────────────────────────────────────────────────────────

function viewControls() {
  const v = $('#view');
  v.innerHTML = `
    <section>
      <h2>Controles MIDI</h2>
      <p id="ct-port" class="muted">Cargando…</p>
      <div class="table-wrap"><table class="controls">
        <thead><tr><th>Acción</th><th>Controles asignados</th><th></th></tr></thead>
        <tbody id="ct-rows"></tbody>
      </table></div>
      <p id="ct-last" class="note">Pisá un footswitch para ver qué manda.</p>
      <p class="muted small">Aprender: tocá el botón y pisá el footswitch dentro de los 15 s. Un footswitch dispara una sola
        acción; una acción puede tener varios (por ejemplo, el mismo footswitch en modo normal y en modo stomp).
        El pedal de expresión se ignora. Anterior y Siguiente funcionan solo con la reproducción parada.</p>
    </section>`;
  let data = null, learning = null;

  async function load() {
    try { data = await api('GET', '/api/controls'); } catch (e) { $('#ct-port').textContent = e.message; return; }
    render();
  }

  function render() {
    if (!data) return;
    const locked = learning || sounding();
    $('#ct-port').innerHTML = data.port ? `Escuchando <strong>${esc(data.port)}</strong>`
      : '<span class="bad-text">No hay pedalera MIDI conectada</span>';
    $('#ct-rows').innerHTML = Object.entries(data.actions).map(([a, name]) => {
      const keys = Object.keys(data.map).filter(k => data.map[k] === a);
      return `<tr><td><strong>${esc(name)}</strong></td>
        <td>${keys.map(k => `<div>${esc(data.labels[k])}</div>`).join('') || '<span class="muted">—</span>'}</td>
        <td class="acts"><button data-learn="${a}" class="${learning === a ? 'primary' : ''}" ${locked ? 'disabled' : ''}>${learning === a ? 'Pisá el footswitch…' : 'Aprender'}</button>
          <button data-forget="${a}" ${keys.length && !locked ? '' : 'disabled'}>Quitar</button></td></tr>`;
    }).join('');
    const l = data.last;
    $('#ct-last').innerHTML = l
      ? `Última pisada: <strong>${esc(l.label)}</strong> → ${l.learned ? 'aprendida' : l.action ? esc(data.actions[l.action]) : '<span class="muted">sin acción</span>'}
         <span class="muted small">(hace ${l.ago < 60 ? Math.round(l.ago) + ' s' : mmss(l.ago)})</span>`
      : 'Pisá un footswitch para ver qué manda.';
  }

  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.learn) {
      learning = b.dataset.learn; render();
      try {
        const r = await api('POST', '/api/learn', {action: learning});
        toast(`${data.actions[learning]} ← ${r.label}`);
      } catch (err) { toast(err.message, 'bad'); }
      learning = null;
      load();
    } else if (b.dataset.forget) {
      try { await api('POST', '/api/unlearn', {action: b.dataset.forget}); } catch (err) { toast(err.message, 'bad'); }
      load();
    }
  });

  load();
  const timer = setInterval(load, 1000);
  return {onLive: render, leave: () => clearInterval(timer)};
}

// ── Arranque ─────────────────────────────────────────────────────────────────

(async function start() {
  for (;;) {
    try { info = await api('GET', '/api/info'); break; }
    catch { $('#view').innerHTML = '<section class="muted">Conectando con Necrotracks…</section>'; await new Promise(r => setTimeout(r, 2000)); }
  }
  connect();
  renderConn();
  route();
})();
