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
// Instalada en el inicio del iPhone: no hay barra del navegador, así que una navegación que no vuelve
// (por ejemplo abrir un PDF) deja la app trabada y hay que cerrarla.
const standalone = () => window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;

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

function upload(file, onProgress, base = '/api/incoming/') {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('PUT', base + encodeURIComponent(file.name));
    x.upload.onprogress = e => onProgress(e.loaded / (e.total || file.size || 1));
    x.onload = () => {
      let data = null; try { data = JSON.parse(x.responseText); } catch {}
      if (x.status < 300) return resolve(data);
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
  ajustes: viewSettings, controles: viewSettings, imprimir: viewPrint};
const TAB_OF = {setlist: 'setlists', controles: 'ajustes', imprimir: 'setlists'};
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
// Instalada en el inicio del iPhone no hay barra ni tirar hacia abajo: sin esto no hay forma de recargar.
$('#reload').addEventListener('click', () => location.reload());
// Al volver a la app (por ejemplo, después de subir una canción desde la Mac) se refresca lo que se ve.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !current?.dirty?.()) route();
});
// Sin zoom: en el escenario un pellizco sin querer deja la pantalla corrida. El meta viewport alcanza
// con la app instalada; en Safari hace falta cortar los gestos a mano.
for (const ev of ['gesturestart', 'gesturechange', 'gestureend']) document.addEventListener(ev, e => e.preventDefault());

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
          <a class="btn" href="#imprimir/${encodeURIComponent(slug)}">${ic('print')}Imprimir</a>
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
        // El engine trabaja con su propia copia: si es la que está cargada y está parado, se recarga sola.
        // Si no, el show sigue con la lista vieja (canciones que faltan, Siguiente apagado) sin que se note.
        if (live.state?.slug !== slug || live.state.state !== 'stopped') toast('Guardada');
        else if (await cmd('load', {setlist: slug})) toast('Guardada y recargada en el show');
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

// ── Set list imprimible ──────────────────────────────────────────────────────
// Dos hojas: "piso" (número y nombre lo más grande que entre, para leerla parado) y "técnica"
// (duración, bloque y qué hace al terminar). El PDF lo hace el navegador: Imprimir → Guardar como PDF.
// Y abajo, la lista en texto para mandar por WhatsApp (seleccionable: la web va por HTTP y el
// "Compartir" del iPhone y el portapapeles moderno piden HTTPS).

function viewPrint(slug) {
  const v = $('#view');
  v.innerHTML = '<div class="wrap"><section class="muted">Cargando…</section></div>';
  let sl = null, mode = 'piso';

  const song = it => sl.songs[it.song] || {};
  const songName = it => song(it).name || it.song;
  const songDur = it => song(it).duration || 0;

  // Las canciones con su bloque como encabezado, en el orden de la set list
  function lines() {
    const out = [];
    let block;
    sl.items.forEach((it, i) => {
      if (it.block !== block) { block = it.block; if (block) out.push({block}); }
      out.push({it, i});
    });
    return out;
  }

  function head() {
    const total = sl.items.reduce((a, it) => a + songDur(it), 0);
    return `<header class="sheet-head">
      <svg class="emblem" aria-hidden="true"><use href="#emblem"/></svg>
      <div><h1>${esc(sl.name)}</h1>
        <p class="meta">${plural(sl.items.length, 'canción', 'canciones')} · ${mmss(total)}</p></div>
    </header>`;
  }

  function floorSheet() {
    // Que entre en una hoja: con muchas canciones, más chico
    const size = sl.items.length > 16 ? 24 : sl.items.length > 12 ? 30 : sl.items.length > 8 ? 34 : 40;
    return `<div class="sheet floor">${head()}
      <ol class="songs" style="font-size:${size}px">${lines().map(l => l.block !== undefined
        ? `<li class="blk">${esc(l.block)}</li>`
        : `<li><span class="n">${pad2(l.i + 1)}</span><span class="t">${esc(songName(l.it))}</span></li>`).join('')}</ol>
    </div>`;
  }

  function techSheet() {
    const total = sl.items.reduce((a, it) => a + songDur(it), 0);
    return `<div class="sheet">${head()}
      <table><thead><tr><th>#</th><th>Canción</th><th>Bloque</th><th>Dura</th><th>Al terminar</th></tr></thead>
        <tbody>${sl.items.map((it, i) => `<tr>
          <td class="n">${pad2(i + 1)}</td>
          <td><strong>${esc(songName(it))}</strong></td>
          <td>${esc(it.block || '—')}</td>
          <td class="dur">${mmss(songDur(it))}</td>
          <td class="beh">${esc(describe(it))}</td></tr>`).join('')}</tbody></table>
      <div class="sheet-foot"><span>${plural(sl.items.length, 'canción', 'canciones')}</span>
        <span>Duración total ${mmss(total)}</span><span>Necrotracks</span></div>
    </div>`;
  }

  function asText() {
    const total = sl.items.reduce((a, it) => a + songDur(it), 0);
    const rows = lines().map(l => l.block !== undefined
      ? `\n— ${l.block} —`
      : `${pad2(l.i + 1)}. ${songName(l.it)} (${mmss(songDur(l.it))})`);
    return `${sl.name.toUpperCase()}\n${rows.join('\n')}\n\n${sl.items.length} canciones · ${mmss(total)}`.trim();
  }

  function render() {
    v.innerHTML = `
      <div class="wrap"><section>
        <a href="#setlist/${encodeURIComponent(slug)}" class="back">${ic('back')}Volver a la set list</a>
        <div class="sheet-actions">
          <div class="seg">
            <button data-mode="piso" class="${mode === 'piso' ? 'on' : ''}">Para el piso</button>
            <button data-mode="tecnica" class="${mode === 'tecnica' ? 'on' : ''}">Técnica</button>
          </div>
          <a class="btn primary" id="pr-pdf" download="${esc(slug)}-${mode === 'piso' ? 'piso' : 'tecnica'}.pdf"
            href="/api/setlists/${encodeURIComponent(slug)}/pdf?mode=${mode === 'piso' ? 'piso' : 'tecnica'}">${ic('download')}Descargar PDF</a>
          <button id="pr-print" class="btn">${ic('print')}Imprimir</button>
        </div>
        ${mode === 'piso' ? floorSheet() : techSheet()}
        <div class="textbox">
          <h2 class="title">Para mandar por mensaje</h2>
          <textarea id="pr-text" readonly>${esc(asText())}</textarea>
          <div class="actions"><button id="pr-copy" class="btn">${ic('copy')}Copiar</button>
            <span class="muted small">Si no copia, tocá el texto, seleccioná todo y copiá a mano.</span></div>
        </div>
        <p class="fine">Descargar PDF lo arma la Pi. Imprimir usa el navegador y sale solo la hoja, sin los
          botones.${standalone() ? ' Estás en la app instalada en el inicio: si el PDF no se guarda, abrí' +
          ' necrotracks.local en Safari y bajalo de ahí, o usá Imprimir (el diálogo aparece recién después de' +
          ' cerrar y volver a abrir la app).' : ''}</p>
      </section></div>`;
  }

  v.addEventListener('click', async e => {
    const link = e.target.closest('#pr-pdf');
    if (link && standalone()) {
      // En la app instalada, seguir el enlace abre el PDF adentro y no hay forma de volver: se baja por código.
      e.preventDefault();
      try {
        const r = await fetch(link.href);
        if (!r.ok) throw new Error('No se pudo armar el PDF');
        const url = URL.createObjectURL(await r.blob());
        const a = document.createElement('a');
        a.href = url;
        a.download = link.getAttribute('download');
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 10000);
      } catch (err) { toast(err.message, 'bad'); }
      return;
    }
    const b = e.target.closest('button');
    if (!b || !sl) return;
    if (b.dataset.mode) { mode = b.dataset.mode; return render(); }
    if (b.id === 'pr-print') return window.print();
    if (b.id === 'pr-copy') {
      const text = asText();
      let ok = false;
      try {
        if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; }
      } catch { ok = false; }
      if (!ok) {  // por HTTP no hay portapapeles moderno: a la vieja usanza
        const ta = $('#pr-text');
        ta.focus();
        ta.setSelectionRange(0, ta.value.length);
        try { ok = document.execCommand('copy'); } catch { ok = false; }
      }
      toast(ok ? 'Copiada' : 'No se pudo copiar: seleccioná el texto y copialo a mano', ok ? 'ok' : 'bad');
    }
  });

  (async function load() {
    try { sl = await api('GET', '/api/setlists/' + encodeURIComponent(slug)); }
    catch (e) {
      v.innerHTML = `<div class="wrap"><section><a href="#setlists" class="back">${ic('back')}Set lists</a>
        <p class="bad-text">${esc(e.message)}</p></section></div>`;
      return;
    }
    render();
  })();
  return {};
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

  let songs = [], renaming = null;  // renaming: slug de la canción que se está renombrando

  // En el celular las columnas no entran: los canales van resumidos en una línea (antes se escondían)
  const detalle = s => [s.foh ? `Pista ${s.foh}` : null, s.click ? 'Click' : null, s.guia ? 'Guía' : null,
    s.midi ? 'MIDI' : null, s.video ? `Video ${s.video.height}p` : null].filter(Boolean).join(' · ') || 'Sin canales';

  async function load() {
    try { songs = await api('GET', '/api/songs'); } catch (e) { $('#lib').textContent = e.message; return; }
    renderLib();
  }

  function renderLib() {
    $('#lib-count').textContent = pad2(songs.length);
    const yes = b => b ? `<span class="yes">${ic('check')}</span>` : '<span class="no">—</span>';
    const nameCell = s => renaming === s.slug
      ? `<div class="rn-cell"><input class="rn" value="${esc(s.name)}" aria-label="Nombre de la canción">
          <button data-save-rn="${esc(s.slug)}" class="btn icon sm primary" title="Guardar">${ic('check')}</button>
          <button data-cancel-rn="1" class="btn icon sm" title="Cancelar">${ic('x')}</button></div>`
      : `<strong>${esc(s.name)}</strong>${s.warnings.map(w => `<div class="warn">${ic('alert')}<span>${esc(w)}</span></div>`).join('')}`;
    $('#lib').innerHTML = songs.length ? `<div class="table-wrap"><table class="songs">
      <thead><tr><th>Canción</th><th>Duración</th><th>Pista</th><th>Click</th><th>Guía</th><th>MIDI</th><th>Video</th><th>En set lists</th><th></th></tr></thead>
      <tbody>${songs.map(s => `<tr>
        <td>${nameCell(s)}</td>
        <td class="dur">${mmss(s.duration)}</td><td>${esc(s.foh || '—')}</td><td>${yes(s.click)}</td><td>${yes(s.guia)}</td><td>${yes(s.midi)}</td>
        <td>${s.video ? `<span class="yes">${s.video.height}p</span>` : '<span class="no">—</span>'}</td>
        <td class="small">${s.used_in.map(esc).join(', ') || '<span class="no">—</span>'}</td>
        <td class="acts"><button data-rn="${esc(s.slug)}" class="btn icon sm" title="Cambiar el nombre">${ic('edit')}</button><button data-del="${esc(s.slug)}" data-name="${esc(s.name)}" class="btn icon sm danger" title="${s.used_in.length ? 'Está en una set list' : 'Borrar de la biblioteca'}" ${s.used_in.length ? 'disabled' : ''}>${ic('trash')}</button></td>
        <td class="det">${esc(detalle(s))}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p class="muted">La biblioteca está vacía.</p>';
    $('.rn')?.focus();
  }

  async function saveName(slug) {
    const input = $('.rn');
    const name = (input ? input.value : '').trim();
    if (!name) return toast('La canción necesita un nombre', 'bad');
    try {
      await api('PATCH', '/api/songs/' + encodeURIComponent(slug), {name});
      renaming = null;
      toast('Renombrada');
      load();
    } catch (err) { toast(err.message, 'bad'); }
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

  v.addEventListener('keydown', e => {  // Enter guarda el nombre, Escape cancela
    if (!e.target.classList.contains('rn')) return;
    if (e.key === 'Enter') { e.preventDefault(); saveName(renaming); }
    if (e.key === 'Escape') { renaming = null; renderLib(); }
  });

  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.dataset.rn) { renaming = b.dataset.rn; return renderLib(); }
    if (b.dataset.cancelRn) { renaming = null; return renderLib(); }
    if (b.dataset.saveRn) return saveName(b.dataset.saveRn);
    if (!b.dataset.del || !confirm(`¿Borrar "${b.dataset.name}" de la biblioteca? Se borran también sus archivos.`)) return;
    try { await api('DELETE', '/api/songs/' + encodeURIComponent(b.dataset.del)); toast('Borrada'); load(); }
    catch (err) { toast(err.message, 'bad'); }
  });

  load();
  syncBusy();
  return {onLive: syncBusy};
}

// ── Ajustes: salida de audio, pantalla HDMI, pantalla en reposo y controles MIDI ──

const FIT_HELP = {fit: 'Entero; si la pantalla no es 16:9, con franjas.',
  fill: 'Llena la pantalla y recorta lo que sobra.',
  stretch: 'Llena la pantalla deformando la imagen. Ancho, alto y posición no se aplican.'};
const signedPct = n => `${n > 0 ? '+' : ''}${n} %`;
const FIT_KEYS = ['scale_x', 'scale_y', 'x', 'y'];

// Controles de encaje (modo, ancho, alto, posición). Se usan para los videos y para la pantalla de reposo.
// Ancho y alto van por separado: hay pantallas que deforman y hay que compensarlas en el escenario.
function fitHtml(p, label, {pattern = false} = {}) {
  const modes = {fit: 'Ajustar', fill: 'Llenar', stretch: 'Estirar'};
  return `<div class="fit">
    <div class="field"><span>${esc(label)}</span>
      <div class="seg">${Object.entries(modes).map(([k, l]) => `<button data-fit-group="${p}" data-fit="${k}">${l}</button>`).join('')}</div>
      <small id="${p}-help" class="muted"></small></div>
    <div class="grid2">
      <label class="field"><span>Ancho <b id="${p}-scale_x-v"></b></span>
        <input type="range" id="${p}-scale_x" data-fit-group="${p}" data-k="scale_x" min="50" max="120" step="1"></label>
      <label class="field"><span>Alto <b id="${p}-scale_y-v"></b></span>
        <input type="range" id="${p}-scale_y" data-fit-group="${p}" data-k="scale_y" min="50" max="120" step="1"></label>
    </div>
    <label class="check"><input type="checkbox" id="${p}-link" data-fit-group="${p}"><span>Mover ancho y alto juntos</span></label>
    <div class="grid2">
      <label class="field"><span>Horizontal <b id="${p}-x-v"></b></span>
        <input type="range" id="${p}-x" data-fit-group="${p}" data-k="x" min="-50" max="50" step="0.5"></label>
      <label class="field"><span>Vertical <b id="${p}-y-v"></b></span>
        <input type="range" id="${p}-y" data-fit-group="${p}" data-k="y" min="-50" max="50" step="0.5"></label>
    </div>
    <div class="actions">${pattern ? `<button id="${p}-pattern" class="btn"></button>` : ''}
      <button id="${p}-reset" class="btn ghost">Restablecer</button></div>
  </div>`;
}

// Cada cambio se manda en vivo (agrupado cada 150 ms) y se ve al instante en la pantalla.
function fitControl(v, p, url, reset, {pattern = false} = {}) {
  const st = {fit: null, patternOn: false, timer: null, link: true};
  function render() {
    if (!st.fit) return;
    const locked = sounding();
    v.querySelectorAll(`button[data-fit-group="${p}"]`).forEach(b => {
      b.classList.toggle('on', b.dataset.fit === st.fit.mode);
      b.disabled = locked;
    });
    $(`#${p}-help`).textContent = FIT_HELP[st.fit.mode];
    for (const k of FIT_KEYS) {
      const input = $(`#${p}-${k}`);
      if (document.activeElement !== input) input.value = st.fit[k];
      input.disabled = locked;
    }
    const link = $(`#${p}-link`);
    link.checked = st.link;
    link.disabled = locked;
    $(`#${p}-scale_x-v`).textContent = `${st.fit.scale_x} %`;
    $(`#${p}-scale_y-v`).textContent = `${st.fit.scale_y} %`;
    $(`#${p}-x-v`).textContent = signedPct(st.fit.x);
    $(`#${p}-y-v`).textContent = signedPct(st.fit.y);
    $(`#${p}-reset`).disabled = locked;
    if (pattern) {
      const b = $(`#${p}-pattern`);
      b.innerHTML = st.patternOn ? `${ic('x')}Ocultar patrón` : `${ic('show')}Mostrar patrón`;
      b.classList.toggle('primary', st.patternOn);
      b.disabled = locked;
    }
  }
  function send(withPattern) {
    clearTimeout(st.timer);
    const body = withPattern === undefined ? {...st.fit} : {...st.fit, pattern: withPattern};
    st.timer = setTimeout(async () => {
      try { await api('POST', url, body); } catch (err) { toast(err.message, 'bad'); }
    }, withPattern === undefined ? 150 : 0);
  }
  v.addEventListener('input', e => {
    if (e.target.dataset.fitGroup !== p || !st.fit) return;
    if (e.target.id === `${p}-link`) { st.link = e.target.checked; return; }
    const k = e.target.dataset.k;
    st.fit[k] = +e.target.value;
    if (st.link && (k === 'scale_x' || k === 'scale_y')) st.fit.scale_x = st.fit.scale_y = +e.target.value;
    render();
    send();
  });
  v.addEventListener('click', e => {
    const b = e.target.closest('button');
    if (!b || !st.fit) return;
    if (b.dataset.fitGroup === p && b.dataset.fit) { st.fit.mode = b.dataset.fit; render(); send(); }
    else if (b.id === `${p}-reset`) { st.fit = {...reset}; render(); send(); }
    else if (pattern && b.id === `${p}-pattern`) { st.patternOn = !st.patternOn; render(); send(st.patternOn); }
  });
  return {
    set(fit, patternOn = false) {
      st.fit = {...fit};
      st.patternOn = patternOn;
      st.link = fit.scale_x === fit.scale_y;  // si vienen distintos, ya están separados a propósito
      render();
    },
    render,
  };
}

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
        ${fitHtml('fit', 'Encaje de los videos', {pattern: true})}
        <p class="fine">Mostrá el patrón y ajustá hasta que el borde blanco se vea entero en los cuatro lados (la línea
          roja marca el 5 % que muchas pantallas recortan). Los cambios se ven al instante, quedan guardados y valen para
          todos los videos. Solo con la reproducción parada.</p>
      </div>

      <h2 class="title">Pantalla en reposo</h2>
      <div class="panel">
        <div id="idle-now" class="port">Cargando…</div>
        <p class="muted small">Lo que se ve cuando no suena un video (parado, o una canción sin video): una imagen, o un
          video que se repite sin audio.</p>
        <div class="actions">
          <label class="btn">${ic('upload')}Subir imagen o video<input type="file" id="idle-file" class="vh" accept=".png,.jpg,.jpeg,.webp,.mp4,.mov,.m4v"></label>
          <button id="idle-logo" class="btn ghost">Volver al logo</button>
          <span id="idle-status" class="status"></span>
        </div>
        ${fitHtml('idle', 'Encaje')}
        <p class="fine">Imagen: PNG, JPG o WebP. Video: MP4 H.264 hasta 1080p a 30 fps, como los de las canciones.
          Los cambios se ven al instante en la pantalla. Solo con la reproducción parada.</p>
      </div>

      <h2 class="title">Red WiFi</h2>
      <div class="panel">
        <div id="wifi-now" class="port">Cargando…</div>
        <div class="grid2">
          <label class="field"><span>Red</span>
            <input id="wifi-ssid" placeholder="Nombre de la red" autocapitalize="off" autocorrect="off" spellcheck="false"></label>
          <label class="field"><span>Contraseña</span>
            <input id="wifi-pass" type="password" placeholder="Vacío si es abierta" autocapitalize="off" autocorrect="off" spellcheck="false"></label>
        </div>
        <div id="wifi-nets" class="nets"></div>
        <div class="actions">
          <button id="wifi-connect" class="btn primary">Conectar</button>
          <button id="wifi-scan" class="btn ghost sm">${ic('reload')}Buscar redes</button>
          <span id="wifi-status" class="status"></span>
        </div>
        <div id="wifi-saved"></div>
        <p class="fine">Al conectarse a otra red, la Pi se va de la actual: esta página se corta y hay que buscarla
          en la red nueva (necrotracks.local). Si la contraseña está mal y queda sin red, al reiniciarla vuelve el
          hotspot "Necrotracks". Solo con la reproducción parada.</p>
      </div>

      <h2 class="title">Sistema</h2>
      <div class="panel">
        <div id="sys-now" class="port">Cargando…</div>
        <div class="actions">
          <button id="sys-reboot" class="btn">${ic('reload')}Reiniciar</button>
          <button id="sys-off" class="btn danger">${ic('power')}Apagar</button>
          <span class="muted small">Apagá siempre desde acá: cortar la corriente de golpe puede arruinar la tarjeta SD.</span>
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
  const videoFit = fitControl(v, 'fit', '/api/fit', {mode: 'fit', scale_x: 100, scale_y: 100, x: 0, y: 0}, {pattern: true});
  const idleFit = fitControl(v, 'idle', '/api/idle-fit', {mode: 'fit', scale_x: 54, scale_y: 54, x: 0, y: 0});

  // Controles MIDI
  async function load() {
    try { data = await api('GET', '/api/controls'); } catch (e) { $('#ct-port').textContent = e.message; return; }
    render();
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

  // Salida de audio
  async function loadOutput() {
    try { out = await api('GET', '/api/output'); } catch (e) { $('#out-now').textContent = e.message; return; }
    const opts = sel => Object.entries(out.profiles).map(([k, label]) => `<option value="${k}" ${k === sel ? 'selected' : ''}>${esc(label)}</option>`).join('');
    $('#out-profile').innerHTML = opts(out.profile);
    $('#out-fallback').innerHTML = `<option value="" ${out.fallback ? '' : 'selected'}>No sonar: esperar a la interfaz</option>${opts(out.fallback)}`;
    renderOutput();
  }

  function renderOutput() {
    if (!out) return;
    const o = live.state?.output || out.output;
    $('#out-now').innerHTML = o.fallback
      ? `<span class="chip warn">${ic('alert')}Respaldo</span><span>Sale por el <strong>${esc(o.label)}</strong>: no está la interfaz.</span>`
      : `<span class="chip ok">${ic('check')}Activa</span><span>Sale por <strong>${esc(o.label)}</strong></span>`;
    $('#out-save').disabled = sounding();
  }

  // Pantalla HDMI y pantalla en reposo
  async function loadHdmi() {
    let h;
    try { h = await api('GET', '/api/hdmi'); } catch (e) {
      $('#hd-now').textContent = $('#idle-now').textContent = e.message;
      return;
    }
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
    videoFit.set(h.fit, h.pattern);
    const idle = h.idle;
    const what = !idle.file ? null : !idle.custom ? 'Logo' : idle.kind === 'video' ? 'Video' : 'Imagen';
    $('#idle-now').innerHTML = !idle.file
      ? `<span class="chip warn">${ic('alert')}Nada</span><span>Pantalla en negro</span>`
      : `<span class="chip ok">${ic('check')}${what}</span><span>${idle.custom ? `<strong>${esc(idle.file)}</strong>` : 'Logo de Necrópolis (el de siempre)'}</span>`;
    $('#idle-logo').disabled = !idle.custom;
    idleFit.set(idle.fit);
  }

  // Red WiFi
  async function loadWifi(rescan = false) {
    let w;
    const scan = $('#wifi-scan');
    // El escaneo forzado tarda unos segundos: se avisa y no se puede pedir dos veces
    if (rescan && scan) { scan.disabled = true; $('#wifi-nets').innerHTML = '<span class="muted small">Buscando redes…</span>'; }
    try { w = await api('GET', '/api/wifi' + (rescan ? '?rescan=1' : '')); }
    catch (e) { if ($('#wifi-now')) $('#wifi-now').textContent = e.message; return; }
    finally { if (scan) scan.disabled = false; }
    if (!$('#wifi-now')) return;
    if (!w.available) {
      $('#wifi-now').innerHTML = `<span class="chip warn">${ic('alert')}Sin WiFi</span><span>No hay una placa WiFi manejada por NetworkManager.</span>`;
      return;
    }
    $('#wifi-now').innerHTML = w.ssid
      ? `<span class="chip ${w.hotspot ? 'live' : 'ok'}">${ic('check')}${w.hotspot ? 'Hotspot' : 'Conectada'}</span>
         <span>${esc(w.ssid)} <span class="muted">· ${esc(w.ip || 'sin IP')}</span></span>`
      : `<span class="chip bad">${ic('alert')}Sin red</span><span>La Pi no está conectada a ninguna WiFi.</span>`;
    $('#wifi-nets').innerHTML = w.networks.map(n =>
      `<button class="btn sm ${n.in_use ? 'primary' : 'ghost'}" data-ssid="${esc(n.ssid)}">${esc(n.ssid)}
        <span class="sig">${n.signal}%</span></button>`).join('') || '<span class="muted small">No se vio ninguna red.</span>';
    const others = w.saved.filter(s => s.name !== 'necrotracks-hotspot');
    $('#wifi-saved').innerHTML = others.length
      ? `<div class="saved"><span class="label">Guardadas</span>${others.map(s =>
        `<span class="chip">${esc(s.name)}${s.active ? ' · en uso' : ''}
          <button class="btn icon sm danger" data-forget-wifi="${esc(s.name)}" title="Olvidar">${ic('x')}</button></span>`).join('')}</div>`
      : '';
    if (rescan) toast('Redes actualizadas');
  }

  // Sistema: temperatura, lugar libre y apagado
  async function loadSystem() {
    let s;
    try { s = await api('GET', '/api/system'); } catch { return; }
    const box = $('#sys-now');
    if (!box) return;
    const hot = s.temp !== null && s.temp >= 70;  // la Pi empieza a recortar a los 80
    const temp = s.temp === null ? 'Sin sensor' : `${s.temp} °C`;
    const parts = [`${(s.free / 1073741824).toFixed(1)} GB libres`];
    if (s.uptime !== null) parts.push(`prendida hace ${s.uptime < 3600 ? Math.round(s.uptime / 60) + ' min' : Math.floor(s.uptime / 3600) + ' h ' + Math.round(s.uptime % 3600 / 60) + ' min'}`);
    box.innerHTML = `<span class="chip ${hot ? 'warn' : 'ok'}">${ic(hot ? 'alert' : 'check')}${esc(temp)}</span>
      <span>${esc(parts.join(' · '))}</span>
      ${s.throttled ? `<div class="warn">${ic('alert')}<span>La Pi recortó por falta de tensión o por calor. Mirá la fuente y la ventilación.</span></div>` : ''}`;
    renderPower();
  }

  function renderPower() {
    const off = $('#sys-off'), reboot = $('#sys-reboot');
    if (off) off.disabled = reboot.disabled = sounding();
  }

  function onLive() {
    render();
    renderPower();
    renderOutput();
    videoFit.render();
    idleFit.render();
    if (live.engine && !engineWas) { loadOutput(); loadHdmi(); }  // volvió después de un reinicio
    engineWas = live.engine;
  }

  v.addEventListener('change', async e => {
    if (e.target.id !== 'idle-file' || !e.target.files.length) return;
    const f = e.target.files[0];
    e.target.value = '';
    const status = (html, cls = '') => { const s = $('#idle-status'); s.innerHTML = html; s.className = 'status ' + cls; };
    if (sounding()) return status(`${ic('alert')}<span>Está sonando: se cambia con la reproducción parada.</span>`, 'bad');
    try {
      const r = await upload(f, p => status(`<span>Subiendo ${esc(f.name)} — ${Math.round(100 * p)}%</span>`), '/api/idle/');
      status(`${ic('check')}<span>Listo: ${esc(r.file)}${(r.warnings || []).map(w => `<br>${esc(w)}`).join('')}</span>`, 'ok');
      loadHdmi();
    } catch (err) { status(`${ic('alert')}<span>${esc(err.message)}</span>`, 'bad'); }
  });

  v.addEventListener('click', async e => {
    const b = e.target.closest('button');
    if (!b) return;
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
    } else if (b.id === 'idle-logo') {
      try { await api('DELETE', '/api/idle'); toast('Volvió el logo'); loadHdmi(); } catch (err) { toast(err.message, 'bad'); }
    } else if (b.dataset.ssid) {
      $('#wifi-ssid').value = b.dataset.ssid;
      $('#wifi-pass').focus();
    } else if (b.id === 'wifi-scan') {
      loadWifi(true);
    } else if (b.dataset.forgetWifi) {
      if (!confirm(`¿Olvidar la red "${b.dataset.forgetWifi}"?`)) return;
      try { await api('DELETE', '/api/wifi/' + encodeURIComponent(b.dataset.forgetWifi)); loadWifi(); }
      catch (err) { toast(err.message, 'bad'); }
    } else if (b.id === 'wifi-connect') {
      const ssid = $('#wifi-ssid').value.trim();
      if (!ssid) return toast('Poné el nombre de la red', 'bad');
      if (!confirm(`¿Conectar la Pi a "${ssid}"? Se va de la red actual y esta página se va a cortar.`)) return;
      const status = (html, cls = '') => { const s = $('#wifi-status'); if (s) { s.innerHTML = html; s.className = 'status ' + cls; } };
      status('<span>Conectando… si la Pi cambia de red, esta página se corta.</span>');
      try {
        await api('POST', '/api/wifi', {ssid, password: $('#wifi-pass').value || null});
        $('#wifi-pass').value = '';
        status(`${ic('check')}<span>Conectada a ${esc(ssid)}.</span>`, 'ok');
        loadWifi();
      } catch (err) {
        status(`${ic('alert')}<span>${esc(err.message)}</span>`, 'bad');
      }
    } else if (b.id === 'sys-off' || b.id === 'sys-reboot') {
      const off = b.id === 'sys-off';
      if (!confirm(off ? '¿Apagar la Pi? Esperá a que se apaguen las luces antes de desenchufar.'
        : '¿Reiniciar la Pi? Tarda menos de un minuto en volver.')) return;
      try {
        await api('POST', '/api/power', {action: off ? 'off' : 'reboot'});
        toast(off ? 'Apagando…' : 'Reiniciando…');
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
  loadWifi();
  loadSystem();
  const timer = setInterval(load, 1000);
  const sysTimer = setInterval(loadSystem, 5000);
  return {onLive, leave: () => { clearInterval(timer); clearInterval(sysTimer); }};
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
