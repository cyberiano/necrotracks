'use strict';
// Necrotracks — web sin build step. Vistas por hash: #show, #setlists (+ #setlist/SLUG), #biblioteca, #controles.
// El estado en vivo llega por SSE (/api/events); si se corta, se muestra y el navegador reconecta solo.

const $ = (sel, el = document) => el.querySelector(sel);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const mmss = s => { s = Math.max(0, Math.round(s || 0)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
const pad2 = n => String(n).padStart(2, '0');
const ic = (name, cls = '') => `<svg class="ic${cls ? ' ' + cls : ''}" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
const STATE_LABEL = {stopped: 'Parado', playing: 'Sonando', paused: 'En pausa', waiting: 'Esperando'};

let info = null;
const live = {engine: null, state: null, at: 0};
let current = null;  // vista activa: {dirty?(), onLive?(), onKey?(e), leave?()}

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
  let cls = 'ok', txt, banner = '', kind = '';
  if (!live.at) {
    cls = ''; txt = 'Conectando…';  // todavía no llegó el primer estado: no es un corte
  } else if (!webOk()) {
    cls = 'bad'; txt = 'Sin conexión';
    banner = 'Sin conexión con Necrotracks. Si estaba sonando, sigue sonando: lo que se cortó es la web.';
  } else if (!live.engine) {
    cls = 'bad'; txt = 'Engine caído'; kind = 'engine';
    banner = 'El engine no responde. ¿El iRig está enchufado? Al reconectarlo vuelve solo, con la set list cargada.';
  } else {
    txt = live.state?.setlist ? STATE_LABEL[live.state.state] : 'Sin set list';
    if (live.state?.state === 'playing') cls = 'play';
  }
  c.className = 'conn ' + cls;
  c.textContent = txt;
  b.textContent = banner;
  b.className = kind;
  b.hidden = !banner;
}
setInterval(renderConn, 2000);

// ── Ruteo ────────────────────────────────────────────────────────────────────

const views = {show: viewShow, setlists: viewSetlists, setlist: viewSetlist, biblioteca: viewLibrary,
  ajustes: viewSettings, controles: viewSettings};
const TAB_OF = {setlist: 'setlists', controles: 'ajustes'};
let lastHash = location.hash, skipHash = false;

function route() {
  current?.leave?.();
  const [name, arg] = location.hash.slice(1).split('/');
  const fn = views[name] || viewShow;
  const tab = TAB_OF[name] || (views[name] ? name : 'show');
  document.querySelectorAll('#tabs a').forEach(a => a.classList.toggle('on', a.getAttribute('href') === '#' + tab));
  window.scrollTo(0, 0);
  // Cada vista le pone sus escuchadores a #view: se reemplaza por uno limpio para que no se acumulen
  // (si no, después de ir y volver, un toque en Play mandaba play_pause dos veces: play y pausa).
  const old = $('#view'), fresh = old.cloneNode(false);
  old.replaceWith(fresh);
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

// ── Piezas comunes ───────────────────────────────────────────────────────────

function describe(item) {
  if (!item) return '';
  if (item.behavior === 'wait') return `Esperar ${item.wait} s y reproducir la siguiente`;
  return info.behaviors[item.behavior] || item.behavior;
}

function statusChip(problems) {
  return problems.length
    ? `<span class="chip bad">${ic('alert')}${plural(problems.length, 'problema', 'problemas')}</span>`
    : `<span class="chip ok">${ic('check')}Lista</span>`;
}

function setlistCard(l, i, {link = false, loaded = null, primary = false} = {}) {
  const name = link ? `<a class="name" href="#setlist/${encodeURIComponent(l.slug)}">${esc(l.name)}</a>`
    : `<span class="name">${esc(l.name)}</span>`;
  return `<li class="card">
    <span class="idx">${pad2(i + 1)}</span>
    <div class="body">${name}
      <div class="meta"><span>${plural(l.count, 'canción', 'canciones')}</span><span>${mmss(l.duration)}</span>
        ${statusChip(l.problems)}${l.slug === loaded ? '<span class="chip live">Cargada</span>' : ''}</div>
      ${!link && l.problems.length ? `<div class="warn">${ic('alert')}<span>${esc(l.problems[0])}</span></div>` : ''}
    </div>
    <button class="btn ${primary ? 'primary' : ''}" data-load="${esc(l.slug)}" ${l.problems.length ? 'disabled' : ''}>Cargar</button>
  </li>`;
}

// ── Show Mode ────────────────────────────────────────────────────────────────

function viewShow() {
  const v = $('#view');
  v.innerHTML = `
    <div class="wrap">
      <section class="show" id="sh" hidden>
        <div class="deck">
          <div id="sh-out" class="note warn" hidden></div>
          <div class="sh-top">
            <span id="sh-setlist" class="kicker"></span>
            <button id="sh-change" class="btn ghost sm">${ic('list')}Cambiar</button>
          </div>
          <div class="sh-status"><span id="sh-state" class="state"></span><span id="sh-block" class="block"></span><span id="sh-num" class="num"></span></div>
          <h1 id="sh-song" class="sh-title"></h1>
          <div class="meter"><div id="sh-bar" class="meter-fill"></div></div>
          <div class="times"><span id="sh-pos"></span><span id="sh-rem" class="rem"></span></div>
          <div class="info">
            <div class="info-row"><span id="sh-after-label" class="label">Al terminar</span><span id="sh-after"></span></div>
            <div class="info-row"><span class="label">Próxima</span><span id="sh-next"></span></div>
          </div>
          <div class="transport">
            <button data-cmd="prev" class="btn tbtn">${ic('prev')}<span>Anterior</span></button>
            <button data-cmd="play_pause" id="sh-play" class="btn tbtn main">${ic('play')}<span>Play</span></button>
            <button data-cmd="stop" id="sh-stop" class="btn tbtn stop">${ic('stop')}<span>Stop</span></button>
            <button data-cmd="next" class="btn tbtn">${ic('next')}<span>Siguiente</span></button>
          </div>
          <p class="hint">Anterior, Siguiente y elegir de la lista funcionan solo con la reproducción parada.
            <span class="keys"><kbd>Espacio</kbd> play/pausa · <kbd>Esc</kbd> stop · <kbd>←</kbd><kbd>→</kbd> anterior/siguiente</span></p>
        </div>
        <aside>
          <h2 class="title">Set list <span id="sh-count" class="count"></span></h2>
          <ol id="sh-list" class="sh-list"></ol>
        </aside>
      </section>
      <section id="sh-pick" hidden>
        <div class="empty">
          <svg class="emblem" aria-hidden="true"><use href="#emblem"/></svg>
          <h1 class="title center">Elegí la set list</h1>
        </div>
        <ul id="sh-pick-list" class="cards pick-list"><li class="muted center">Cargando…</li></ul>
        <p class="center"><button id="sh-pick-cancel" class="btn ghost sm" hidden>${ic('back')}Volver al show</button></p>
      </section>
      <section id="sh-off" class="empty" hidden>
        <svg class="emblem dim" aria-hidden="true"><use href="#emblem"/></svg>
        <p>Esperando al engine…</p>
      </section>
    </div>`;

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
    $('#sh-count').textContent = pad2(detail.items.length);
    let block;
    ol.innerHTML = detail.items.map((it, i) => {
      const song = detail.songs[it.song];
      let head = '';
      if (it.block !== block) { block = it.block; if (block) head = `<li class="blk-head">${esc(block)}</li>`; }
      return `${head}<li data-i="${i}">
        <span class="n">${pad2(i + 1)}</span><span class="t">${esc(song ? song.name : it.song)}</span>
        <span class="d">${song ? mmss(song.duration) : ''}</span></li>`;
    }).join('');
  }

  async function showPicker(cancelable) {
    picking = true;
    $('#sh').hidden = true;
    $('#sh-pick').hidden = false;
    $('#sh-pick-cancel').hidden = !cancelable;
    let lists;
    try { lists = await api('GET', '/api/setlists'); } catch (e) { $('#sh-pick-list').innerHTML = `<li class="bad-text center">${esc(e.message)}</li>`; return; }
    $('#sh-pick-list').innerHTML = lists.length
      ? lists.map((l, i) => setlistCard(l, i, {primary: true, loaded: live.state?.slug})).join('')
      : '<li class="muted center">No hay set lists. Armá una en <a href="#setlists">Set lists</a>.</li>';
  }

  function hidePicker() {
    picking = false;
    $('#sh-pick').hidden = true;
    render();
  }

  function render() {
    const s = live.state;
    if (!live.engine || !s) {
      $('#sh').hidden = true; $('#sh-pick').hidden = true; $('#sh-off').hidden = false;
      return;
    }
    $('#sh-off').hidden = true;
    if (!s.setlist) { if (!picking) showPicker(false); return; }
    if (picking) return;
    if (s.slug !== detailSlug) loadDetail(s.slug);
    $('#sh').hidden = false;
    const item = detail && detail.slug === s.slug ? detail.items[s.index] : null;
    const moving = s.state === 'playing' || s.state === 'paused';
    const out = $('#sh-out');
    out.hidden = !s.output?.fallback;
    if (s.output?.fallback) {
      out.innerHTML = `${ic('alert')}<span>Sale por el <strong>${esc(s.output.label)}</strong>: no está la interfaz.
        Al conectarla vuelve sola, con la reproducción parada.</span>`;
    }
    $('#sh-setlist').textContent = s.setlist;
    $('#sh-change').hidden = s.state !== 'stopped';
    const badge = $('#sh-state');
    badge.textContent = STATE_LABEL[s.state];
    badge.className = 'state ' + s.state;
    $('#sh-block').textContent = s.block || '';
    $('#sh-num').textContent = `${pad2(s.index + 1)} / ${pad2(s.count)}`;
    $('#sh-song').textContent = s.song || '—';
    const pos = moving ? s.position : 0;
    $('#sh-bar').style.width = s.duration ? `${Math.min(100, 100 * pos / s.duration)}%` : '0';
    $('#sh-pos').textContent = mmss(pos);
    $('#sh-rem').textContent = '−' + mmss(s.duration - pos);
    if (s.state === 'waiting') {
      $('#sh-after-label').textContent = 'Arranca en';
      $('#sh-after').innerHTML = `<span class="countdown">${Math.ceil(s.wait_remaining)} s</span>
        <span class="muted small">Play = ya · Stop = cancelar</span>`;
    } else {
      $('#sh-after-label').textContent = 'Al terminar';
      $('#sh-after').textContent = describe(item);
    }
    $('#sh-next').innerHTML = s.next ? esc(s.next) : '<span class="muted">Última de la set list</span>';
    $('#sh-play').innerHTML = s.state === 'playing' ? `${ic('pause')}<span>Pausa</span>`
      : `${ic('play')}<span>${s.state === 'waiting' ? 'Ya' : 'Play'}</span>`;
    v.querySelector('[data-cmd=prev]').disabled = moving || s.index === 0;
    v.querySelector('[data-cmd=next]').disabled = moving || s.index + 1 >= s.count;
    $('#sh-stop').disabled = s.state === 'stopped';
    const ol = $('#sh-list');
    ol.classList.toggle('locked', moving);
    ol.querySelectorAll('li[data-i]').forEach(li => li.classList.toggle('cur', +li.dataset.i === s.index));
  }

  let lastTap = {cmd: null, at: 0};
  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (b?.dataset.cmd) {
      // En el escenario un doble toque no puede convertirse en play + pausa
      const now = Date.now();
      if (lastTap.cmd === b.dataset.cmd && now - lastTap.at < 300) return;
      lastTap = {cmd: b.dataset.cmd, at: now};
      return cmd(b.dataset.cmd);
    }
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
    <div class="wrap"><section>
      <h1 class="title">Set lists</h1>
      <form id="sl-new" class="row">
        <input name="name" placeholder="Nombre de la set list nueva" required>
        <button class="btn primary">${ic('plus')}Crear</button>
      </form>
      <ul id="sl-list" class="cards"><li class="muted">Cargando…</li></ul>
    </section></div>`;

  async function load() {
    let lists;
    try { lists = await api('GET', '/api/setlists'); } catch (e) { $('#sl-list').innerHTML = `<li class="bad-text">${esc(e.message)}</li>`; return; }
    $('#sl-list').innerHTML = lists.length
      ? lists.map((l, i) => setlistCard(l, i, {link: true, loaded: live.state?.slug})).join('')
      : '<li class="muted">Todavía no hay set lists.</li>';
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
  v.innerHTML = '<div class="wrap"><section class="muted">Cargando…</section></div>';
  let sl = null, songs = [], saved = '';
  const model = () => JSON.stringify({name: sl.name, items: sl.items});
  const dirty = () => sl !== null && model() !== saved;
  const songName = s => (songs.find(x => x.slug === s) || {}).name || s;
  const songDur = s => (songs.find(x => x.slug === s) || {}).duration || 0;

  async function load() {
    try {
      [sl, songs] = await Promise.all([api('GET', '/api/setlists/' + encodeURIComponent(slug)), api('GET', '/api/songs')]);
    } catch (e) {
      v.innerHTML = `<div class="wrap"><section><a href="#setlists" class="back">${ic('back')}Set lists</a><p class="bad-text">${esc(e.message)}</p></section></div>`;
      return;
    }
    saved = model();
    render();
  }

  const behaviorOptions = sel => Object.entries(info.behaviors)
    .map(([k, label]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${esc(label)}</option>`).join('');

  function render() {
    const total = sl.items.reduce((a, it) => a + songDur(it.song), 0);
    v.innerHTML = `
      <div class="wrap"><section class="editor">
        <a href="#setlists" class="back">${ic('back')}Set lists</a>
        <input id="ed-name" class="title-input" value="${esc(sl.name)}" aria-label="Nombre de la set list">
        <div class="meta"><span>${plural(sl.items.length, 'canción', 'canciones')}</span><span>${mmss(total)}</span></div>
        <div id="ed-loaded"></div>
        <h2 class="title">Canciones</h2>
        <div class="table-wrap"><table class="items">
          <thead><tr><th>#</th><th>Canción</th><th>Bloque</th><th>Al terminar</th><th></th></tr></thead>
          <tbody>${sl.items.map((it, i) => `
            <tr data-i="${i}">
              <td class="n">${pad2(i + 1)}</td>
              <td>${esc(songName(it.song))} <span class="dur">${mmss(songDur(it.song))}</span></td>
              <td><input class="blk" value="${esc(it.block || '')}" placeholder="—" aria-label="Bloque"></td>
              <td><div class="beh-cell"><select class="beh" aria-label="Al terminar">${behaviorOptions(it.behavior)}</select>
                <span class="wait-box" ${it.behavior === 'wait' ? '' : 'hidden'}><input class="wait" type="number" min="1" step="1" value="${it.wait || 5}" aria-label="Segundos"> s</span></div></td>
              <td class="acts"><button class="btn icon sm" data-act="up" ${i === 0 ? 'disabled' : ''} title="Subir">${ic('up')}</button><button class="btn icon sm" data-act="down" ${i + 1 === sl.items.length ? 'disabled' : ''} title="Bajar">${ic('down')}</button><button class="btn icon sm danger" data-act="rm" title="Sacar de la set list">${ic('x')}</button></td>
            </tr>`).join('') || '<tr><td colspan="5" class="muted">Vacía: agregá canciones abajo.</td></tr>'}
          </tbody>
        </table></div>
        <div class="row">
          <select id="ed-add" aria-label="Canción para agregar">${songs.map(s => `<option value="${esc(s.slug)}">${esc(s.name)} (${mmss(s.duration)})</option>`).join('')}</select>
          <button id="ed-add-btn" class="btn" ${songs.length ? '' : 'disabled'}>${ic('plus')}Agregar</button>
        </div>
        <details class="tool">
          <summary>Asignar un bloque a varias canciones</summary>
          <div class="row">
            <input id="bt-name" placeholder="Nombre del bloque">
            <label>de <input id="bt-from" type="number" min="1" value="1" class="short"></label>
            <label>a <input id="bt-to" type="number" min="1" value="${Math.max(1, sl.items.length)}" class="short"></label>
            <select id="bt-beh"><option value="">No cambiar el comportamiento</option>${behaviorOptions('')}</select>
            <label id="bt-wait-box" hidden><input id="bt-wait" type="number" min="1" value="5" class="short"> s</label>
            <button id="bt-apply" class="btn sm">Aplicar</button>
          </div>
        </details>
        <div id="ed-problems">${problemsHtml(sl.problems)}</div>
        <div class="actions">
          <button id="ed-save" class="btn primary">Guardar</button>
          <span id="ed-dirty" class="muted small"></span>
          <span class="spacer"></span>
          <button id="ed-del" class="btn danger">${ic('trash')}Borrar set list</button>
        </div>
      </section></div>`;
    renderDirty();
    renderLoaded();
  }

  function problemsHtml(problems) {
    if (!problems) return '';
    return problems.length ? `<ul class="problems">${problems.map(p => `<li>${ic('alert')}<span>${esc(p)}</span></li>`).join('')}</ul>`
      : `<p><span class="chip ok">${ic('check')}Lista para tocar</span></p>`;
  }

  function renderDirty() {
    const d = $('#ed-dirty');
    if (d) d.textContent = dirty() ? 'Cambios sin guardar' : '';
  }

  function renderLoaded() {
    const box = $('#ed-loaded');
    if (!box) return;
    if (live.state?.slug !== slug) { box.innerHTML = ''; return; }
    box.innerHTML = `<div class="note">${ic('show')}<span>Es la set list cargada en el show. Los cambios guardados se aplican al recargarla.</span>
      ${live.state.state === 'stopped' ? `<button id="ed-reload" class="btn sm">${ic('reload')}Recargar en el show</button>`
        : '<span class="muted small">Se puede con la reproducción parada.</span>'}</div>`;
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
    <div class="wrap"><section>
      <h1 class="title">Importar canción</h1>
      <form id="imp" class="panel">
        <div class="pick">
          <label class="btn">${ic('upload')}Archivos<input type="file" id="imp-files" class="vh" multiple accept=".wav,.wave,.flac,.aif,.aiff,.mp3,.ogg,.zip,.mid,.midi,.mp4,.mov,.m4v"></label>
          <label class="btn ghost">${ic('folder')}Carpeta<input type="file" id="imp-dir" class="vh" webkitdirectory></label>
        </div>
        <div id="imp-picked" class="picked">WAV, FLAC, AIFF, MP3, MP4, ZIP o MIDI. Una canción por vez.</div>
        <div class="grid2">
          <label class="field"><span>Nombre de la canción</span><input id="imp-name" placeholder="El del archivo o la carpeta"></label>
          <label class="field"><span>Si es un único archivo estéreo</span>
            <select id="imp-layout">
              <option value="">Detectar por el nombre del archivo</option>
              <option value="click-pista">click-pista: L = click, R = pista (el de la banda)</option>
              <option value="foh">foh: pista estéreo, sin click</option>
            </select></label>
        </div>
        <div class="actions"><button class="btn primary" id="imp-go">${ic('upload')}Importar</button><span id="imp-status" class="status"></span></div>
        <p class="fine">${esc(info.conventions)}</p>
      </form>
      <h2 class="title">Biblioteca <span id="lib-count" class="count"></span></h2>
      <div id="lib" class="muted">Cargando…</div>
    </section></div>`;

  const status = (html, cls = '') => { const s = $('#imp-status'); s.innerHTML = html; s.className = 'status ' + cls; };
  const chosen = () => [...($('#imp-dir').files.length ? $('#imp-dir').files : $('#imp-files').files)].filter(f => !f.name.startsWith('.'));
  let busy = false;

  function syncBusy() {
    const go = $('#imp-go');
    if (!go) return;
    go.disabled = busy || sounding();
    if (!busy) status(sounding() ? `${ic('alert')}<span>Está sonando: el import se habilita con la reproducción parada.</span>` : '', sounding() ? 'bad' : '');
  }

  function showPicked() {
    const files = chosen();
    const mb = files.reduce((a, f) => a + f.size, 0) / 1048576;
    $('#imp-picked').innerHTML = files.length
      ? `<strong>${plural(files.length, 'archivo', 'archivos')}</strong> · ${mb.toFixed(1)} MB · ${esc(files.slice(0, 4).map(f => f.name).join(', '))}${files.length > 4 ? '…' : ''}`
      : 'WAV, FLAC, AIFF, MP3, MP4, ZIP o MIDI. Una canción por vez.';
  }

  async function load() {
    let songs;
    try { songs = await api('GET', '/api/songs'); } catch (e) { $('#lib').textContent = e.message; return; }
    $('#lib-count').textContent = pad2(songs.length);
    const yes = b => b ? `<span class="yes">${ic('check')}</span>` : '<span class="no">—</span>';
    $('#lib').innerHTML = songs.length ? `<div class="table-wrap"><table class="songs">
      <thead><tr><th>Canción</th><th>Duración</th><th>Pista</th><th>Click</th><th>Guía</th><th>MIDI</th><th>Video</th><th>En set lists</th><th></th></tr></thead>
      <tbody>${songs.map(s => `<tr>
        <td><strong>${esc(s.name)}</strong>${s.warnings.map(w => `<div class="warn">${ic('alert')}<span>${esc(w)}</span></div>`).join('')}</td>
        <td class="dur">${mmss(s.duration)}</td><td>${esc(s.foh || '—')}</td><td>${yes(s.click)}</td><td>${yes(s.guia)}</td><td>${yes(s.midi)}</td>
        <td>${s.video ? `<span class="yes">${s.video.height}p</span>` : '<span class="no">—</span>'}</td>
        <td class="small">${s.used_in.map(esc).join(', ') || '<span class="no">—</span>'}</td>
        <td class="acts"><button data-del="${esc(s.slug)}" data-name="${esc(s.name)}" class="btn icon sm danger" title="${s.used_in.length ? 'Está en una set list' : 'Borrar de la biblioteca'}" ${s.used_in.length ? 'disabled' : ''}>${ic('trash')}</button></td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="muted">La biblioteca está vacía.</p>';
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
    if (e.target.type === 'file') showPicked();
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
        await upload(f, p => status(`<span>Subiendo ${i + 1}/${files.length}: ${esc(f.name)} — ${Math.round(100 * p)}%</span>`));
      }
      status('<span>Importando… convierte y escribe despacio para no trabar al iRig: ~1 min por canción.</span>');
      const song = await api('POST', '/api/import', {name: name || null, layout: $('#imp-layout').value || null});
      status(`${ic('check')}<span>Importada: ${esc(song.name)} (${mmss(song.duration)})${song.warnings.map(w => `<br>${esc(w)}`).join('')}</span>`, 'ok');
      $('#imp').reset();
      showPicked();
      load();
    } catch (err) {
      status(`${ic('alert')}<span>${esc(err.message)}</span>`, 'bad');
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

// ── Ajustes: salida de audio y controles MIDI ────────────────────────────────

function viewSettings() {
  const v = $('#view');
  v.innerHTML = `
    <div class="wrap"><section>
      <h1 class="title">Salida de audio</h1>
      <div class="panel">
        <div id="out-now" class="port">Cargando…</div>
        <div class="grid2">
          <label class="field"><span>Perfil</span><select id="out-profile"></select></label>
          <label class="field"><span>Si no está la interfaz</span><select id="out-fallback"></select></label>
        </div>
        <div class="actions"><button id="out-save" class="btn primary">Guardar</button>
          <span class="muted small">Reinicia el engine: tarda unos segundos y solo se puede con la reproducción parada.</span></div>
      </div>
      <h2 class="title">Pantalla HDMI</h2>
      <div class="panel">
        <div id="hd-now" class="port">Cargando…</div>
        <div class="grid2">
          <label class="field"><span>Resolución</span><select id="hd-mode"></select></label>
        </div>
        <div class="actions"><button id="hd-save" class="btn primary">Aplicar</button>
          <span class="muted small">Reinicia solo el video, no el audio. Automática: la que pide la pantalla, salvo que sea 4:3 y haya una 16:9.</span></div>
        <div class="fit">
          <div class="field"><span>Encaje del video</span>
            <div id="fit-mode" class="seg"><button data-fit="fit">Ajustar</button><button data-fit="fill">Llenar</button><button data-fit="stretch">Estirar</button></div>
            <small id="fit-help" class="muted"></small></div>
          <label class="field"><span>Escala <b id="fit-scale-v"></b></span><input type="range" id="fit-scale" min="50" max="120" step="1"></label>
          <div class="grid2">
            <label class="field"><span>Horizontal <b id="fit-x-v"></b></span><input type="range" id="fit-x" min="-20" max="20" step="0.5"></label>
            <label class="field"><span>Vertical <b id="fit-y-v"></b></span><input type="range" id="fit-y" min="-20" max="20" step="0.5"></label>
          </div>
          <div class="actions"><button id="fit-pattern" class="btn"></button><button id="fit-reset" class="btn ghost">Restablecer</button></div>
          <p class="fine">Mostrá el patrón y ajustá hasta que el borde blanco se vea entero en los cuatro lados (la línea
            roja marca el 5 % que muchas pantallas recortan). Los cambios se ven al instante, quedan guardados y valen para
            todos los videos. Solo con la reproducción parada.</p>
        </div>
      </div>
      <h2 class="title">Controles MIDI</h2>
      <div id="ct-port" class="port">Cargando…</div>
      <div class="table-wrap"><table class="controls">
        <thead><tr><th>Acción</th><th>Controles asignados</th><th></th></tr></thead>
        <tbody id="ct-rows"></tbody>
      </table></div>
      <div id="ct-last" class="note last">${ic('controls')}<span>Pisá un footswitch para ver qué manda.</span></div>
      <p class="fine">Aprender: tocá el botón y pisá el footswitch dentro de los 15 s. Un footswitch dispara una sola
        acción; una acción puede tener varios (por ejemplo, el mismo footswitch en modo normal y en modo stomp).
        El pedal de expresión se ignora. Anterior y Siguiente funcionan solo con la reproducción parada.</p>
    </section></div>`;
  let data = null, learning = null, out = null, engineWas = live.engine;

  async function load() {
    try { data = await api('GET', '/api/controls'); } catch (e) { $('#ct-port').textContent = e.message; return; }
    render();
  }

  async function loadOutput() {
    try { out = await api('GET', '/api/output'); } catch (e) { $('#out-now').textContent = e.message; return; }
    const opts = sel => Object.entries(out.profiles).map(([k, label]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${esc(label)}</option>`).join('');
    $('#out-profile').innerHTML = opts(out.profile);
    $('#out-fallback').innerHTML = `<option value="" ${out.fallback ? '' : 'selected'}>No sonar: esperar a la interfaz</option>${opts(out.fallback)}`;
    renderOutput();
  }

  async function loadHdmi() {
    let h;
    try { h = await api('GET', '/api/hdmi'); } catch (e) { $('#hd-now').textContent = e.message; return; }
    if (!h.video) {
      $('#hd-now').innerHTML = `<span class="chip warn">${ic('alert')}Sin video</span><span>mpv no está instalado en la Pi.</span>`;
    } else if (!h.modes.length) {
      $('#hd-now').innerHTML = `<span class="chip bad">${ic('alert')}Sin pantalla</span><span>No hay nada conectado por HDMI.</span>`;
    } else {
      $('#hd-now').innerHTML = `<span class="chip ok">${ic('check')}Conectada</span><span>Sale en <strong>${esc(h.current || h.modes[0])}</strong>
        <span class="muted">· la pantalla pide ${esc(h.modes[0])}</span></span>`;
    }
    $('#hd-mode').innerHTML = `<option value="auto" ${h.mode === 'auto' ? 'selected' : ''}>Automática</option>` +
      h.modes.map(m => `<option value="${esc(m)}" ${m === h.mode ? 'selected' : ''}>${esc(m)}</option>`).join('');
    fit = h.fit;
    patternOn = h.pattern;
    renderFit();
  }

  // Encaje del video: cada cambio se manda en vivo (agrupado cada 150 ms) y se ve al instante en la pantalla.
  let fit = null, patternOn = false, fitTimer;
  const FIT_HELP = {fit: 'El video entero; si la pantalla no es 16:9, con franjas.',
    fill: 'Llena la pantalla y recorta lo que sobra.', stretch: 'Llena la pantalla deformando la imagen.'};
  const signed = n => `${n > 0 ? '+' : ''}${n} %`;

  function renderFit() {
    if (!fit) return;
    const locked = sounding();
    v.querySelectorAll('#fit-mode button').forEach(b => { b.classList.toggle('on', b.dataset.fit === fit.mode); b.disabled = locked; });
    $('#fit-help').textContent = FIT_HELP[fit.mode];
    for (const k of ['scale', 'x', 'y']) {
      const input = $('#fit-' + k);
      if (document.activeElement !== input) input.value = fit[k];
      input.disabled = locked;
    }
    $('#fit-scale-v').textContent = `${fit.scale} %`;
    $('#fit-x-v').textContent = signed(fit.x);
    $('#fit-y-v').textContent = signed(fit.y);
    const p = $('#fit-pattern');
    p.innerHTML = patternOn ? `${ic('x')}Ocultar patrón` : `${ic('show')}Mostrar patrón`;
    p.classList.toggle('primary', patternOn);
    p.disabled = $('#fit-reset').disabled = locked;
  }

  function sendFit(pattern) {
    clearTimeout(fitTimer);
    const body = pattern === undefined ? {...fit} : {...fit, pattern};
    fitTimer = setTimeout(async () => {
      try { await api('POST', '/api/fit', body); } catch (err) { toast(err.message, 'bad'); loadHdmi(); }
    }, pattern === undefined ? 150 : 0);
  }

  v.addEventListener('input', e => {
    const k = {'fit-scale': 'scale', 'fit-x': 'x', 'fit-y': 'y'}[e.target.id];
    if (!k || !fit) return;
    fit[k] = +e.target.value;
    renderFit();
    sendFit();
  });

  function renderOutput() {
    if (!out) return;
    const o = live.state?.output || out.output;
    $('#out-now').innerHTML = o.fallback
      ? `<span class="chip warn">${ic('alert')}Respaldo</span><span>Sale por el <strong>${esc(o.label)}</strong>: no está la interfaz.</span>`
      : `<span class="chip ok">${ic('check')}Activa</span><span>Sale por <strong>${esc(o.label)}</strong></span>`;
    $('#out-save').disabled = sounding();
  }

  function onLive() {
    render();
    renderOutput();
    renderFit();
    if (live.engine && !engineWas) loadOutput();  // volvió después de un reinicio
    engineWas = live.engine;
  }

  function render() {
    if (!data) return;
    const locked = learning || sounding();
    $('#ct-port').innerHTML = data.port
      ? `<span class="chip ok">${ic('check')}Conectado</span><span>Escuchando <strong>${esc(data.port)}</strong></span>`
      : `<span class="chip bad">${ic('alert')}Sin pedalera</span><span>No hay una pedalera MIDI conectada</span>`;
    $('#ct-rows').innerHTML = Object.entries(data.actions).map(([a, name]) => {
      const keys = Object.keys(data.map).filter(k => data.map[k] === a);
      return `<tr><td><strong>${esc(name)}</strong></td>
        <td>${keys.map(k => `<div>${esc(data.labels[k])}</div>`).join('') || '<span class="no">—</span>'}</td>
        <td class="acts"><button data-learn="${a}" class="btn sm ${learning === a ? 'primary' : ''}" ${locked ? 'disabled' : ''}>${learning === a ? 'Pisá el footswitch…' : 'Aprender'}</button><button data-forget="${a}" class="btn icon sm danger" title="Quitar los controles" ${keys.length && !locked ? '' : 'disabled'}>${ic('x')}</button></td></tr>`;
    }).join('');
    const l = data.last;
    $('#ct-last').innerHTML = l
      ? `${ic('controls')}<span>Última pisada: <strong>${esc(l.label)}</strong></span>${ic('arrow')}<span>${l.learned ? 'aprendida' : l.action ? esc(data.actions[l.action]) : '<span class="muted">sin acción</span>'}</span>
         <span class="muted small">hace ${l.ago < 60 ? Math.round(l.ago) + ' s' : mmss(l.ago)}</span>`
      : `${ic('controls')}<span>Pisá un footswitch para ver qué manda.</span>`;
  }

  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.fit && fit) { fit.mode = b.dataset.fit; renderFit(); return sendFit(); }
    if (b.id === 'fit-pattern' && fit) { patternOn = !patternOn; renderFit(); return sendFit(patternOn); }
    if (b.id === 'fit-reset' && fit) { fit = {mode: 'fit', scale: 100, x: 0, y: 0}; renderFit(); return sendFit(); }
    if (b.dataset.learn) {
      learning = b.dataset.learn; render();
      try {
        const r = await api('POST', '/api/learn', {action: learning});
        toast(`${data.actions[learning]}: ${r.label}`);
      } catch (err) { toast(err.message, 'bad'); }
      learning = null;
      load();
    } else if (b.dataset.forget) {
      try { await api('POST', '/api/unlearn', {action: b.dataset.forget}); } catch (err) { toast(err.message, 'bad'); }
      load();
    } else if (b.id === 'hd-save') {
      try {
        await api('POST', '/api/hdmi', {mode: $('#hd-mode').value});
        toast('Aplicado: reiniciando el video…');
        setTimeout(loadHdmi, 4000);
      } catch (err) { toast(err.message, 'bad'); }
    } else if (b.id === 'out-save') {
      try {
        const r = await api('POST', '/api/output', {profile: $('#out-profile').value, fallback: $('#out-fallback').value || null});
        toast(r.restart ? 'Guardado: reiniciando el engine…' : 'Guardado');
        if (!r.restart) loadOutput();
      } catch (err) { toast(err.message, 'bad'); }
    }
  });

  load();
  loadOutput();
  loadHdmi();
  const timer = setInterval(load, 1000);
  return {onLive, leave: () => clearInterval(timer)};
}

// ── Arranque ─────────────────────────────────────────────────────────────────

(async function start() {
  for (;;) {
    try { info = await api('GET', '/api/info'); break; }
    catch { $('#view').innerHTML = '<div class="wrap"><section class="empty"><p>Conectando con Necrotracks…</p></section></div>'; await new Promise(r => setTimeout(r, 2000)); }
  }
  connect();
  renderConn();
  route();
})();
