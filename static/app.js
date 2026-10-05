'use strict';
/*
 * NeighborTools browser script. One file renders every page:
 *   /                home and demo links
 *   /borrow?p=<id>   borrower view for a participant link (or p=guest)
 *   /owner?o=<id>    owner view
 *   /results         simulated research results and reset
 * All data lives on the server (SQLite). This script only fetches and displays it,
 * and re-fetches every few seconds so changes made in other browsers show up.
 */

const app = document.getElementById('app');
const params = new URLSearchParams(location.search);
const page = location.pathname.replace(/\/+$/, '') || '/';
const pid = params.get('p');
const oid = params.get('o');
const POLL_MS = 5000;

let config = null;          // owners, participants, research rules
let data = null;            // latest server data for this page
let lastJson = '';          // lets polling skip redraws when nothing changed
const ui = { open: null };  // the one inline form or confirmation that is open

/* ------------------------------------------------------------------ helpers */

async function api(url, method = 'GET', body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(json.error || `Server error (${res.status})`);
    err.details = json.errors || [];
    throw err;
  }
  return json;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const pad = n => String(n).padStart(2, '0');
const clock = (h, m) => `${h % 12 || 12}:${pad(m)} ${h < 12 ? 'AM' : 'PM'}`;
const dayName = d => `${DAYS[d.getDay()]}, ${MONTHS[d.getMonth()]} ${d.getDate()}`;

// 'YYYY-MM-DD' -> 'Sat, Oct 10'
function fmtDay(ymd) {
  const [y, m, d] = ymd.split('-').map(Number);
  return dayName(new Date(y, m - 1, d));
}
// Local 'YYYY-MM-DDTHH:MM' -> 'Sat, Oct 10, 9:00 AM'
function fmtLocal(value) {
  if (!value) return '—';
  const [ymd, hm] = value.split('T');
  const [h, m] = hm.split(':').map(Number);
  return `${fmtDay(ymd)}, ${clock(h, m)}`;
}
// UTC timestamp -> this browser's local time
function fmtStamp(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return `${dayName(d)}, ${clock(d.getHours(), d.getMinutes())}`;
}
const fmtRange = (start, end) => `${fmtLocal(start)} → ${fmtLocal(end)}`;
function fmtMinutes(total) {
  if (total < 60) return `${total} min`;
  const h = Math.floor(total / 60), m = total % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}
function localNow() {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const STATUS = { pending: 'Pending', accepted: 'Accepted', declined: 'Declined', picked_up: 'Picked up', returned: 'Returned' };
const badge = status => `<span class="badge s-${esc(status)}">${STATUS[status] || esc(status)}</span>`;
const isOpen = (kind, id) => ui.open !== null && ui.open.kind === kind && String(ui.open.id) === String(id);
const participantName = p => `${p.label} (${p.name})`;
const isParticipant = id => id === 'guest' || config.participants.some(p => p.id === id);
const isOwner = id => config.owners.some(o => o.id === id);

let toastTimer;
function toast(text, kind = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = text;
  el.className = `show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = ''; }, 5000);
}

function setFooter(text, isError = false) {
  const el = document.getElementById('footer');
  el.textContent = text;
  el.classList.toggle('error', isError);
}

/* --------------------------------------------------------- render + refresh */

function draw() {
  app.innerHTML = view.render();
}

async function refresh(force = false) {
  if (view.load) {
    try {
      const next = await view.load();
      setFooter(`Updated ${new Date().toLocaleTimeString()} · refreshes automatically every ${POLL_MS / 1000} seconds`);
      const json = JSON.stringify(next);
      if (!force && json === lastJson) return;  // redraw only on change, so clicks and text selection survive
      data = next;
      lastJson = json;
    } catch (err) {
      setFooter(`Can't reach the server (${err.message}). Is it still running?`, true);
      if (!force) return;
    }
  }
  draw();
}

function openPanel(kind, id) {
  ui.open = { kind, id };
  draw();
  const form = app.querySelector('form');
  if (form) {
    form.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    form.querySelector('input, textarea')?.focus({ preventScroll: true });
  }
}

async function afterChange(message) {
  ui.open = null;
  toast(message);
  await refresh(true);
}

/** Run a form submit; on failure, show the server's validation messages inside the form. */
async function submitForm(form, work) {
  const button = form.querySelector('[type=submit]');
  button.disabled = true;
  try {
    await work(Object.fromEntries(new FormData(form)));
  } catch (err) {
    const box = form.querySelector('.form-errors');
    const list = (err.details || []).map(d => `<li>${esc(d)}</li>`).join('');
    box.innerHTML = `<strong>${esc(err.message)}</strong>${list ? `<ul>${list}</ul>` : ''}`;
    box.hidden = false;
    box.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    button.disabled = false;
  }
}

const actions = {};  // data-action="name" -> handler(dataset, element)
const forms = {};    // data-form="name"   -> handler(form)

app.addEventListener('click', event => {
  const el = event.target.closest('[data-action]');
  if (!el || el.disabled) return;
  event.preventDefault();
  actions[el.dataset.action](el.dataset, el);
});
app.addEventListener('submit', event => {
  event.preventDefault();
  forms[event.target.dataset.form](event.target);
});
actions.close = () => { ui.open = null; draw(); };

/* -------------------------------------------------------------------- nav */

function renderNav() {
  const link = (href, text, active, title) =>
    `<a href="${esc(href)}"${active ? ' aria-current="page"' : ''}${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</a>`;
  document.getElementById('nav').innerHTML = `
    <div class="nav-inner">
      <a class="brand" href="/">NeighborTools</a>
      <span class="nav-tag">Demo menu</span>
      <span class="nav-group"><span class="nav-label">Borrower:</span>
        ${config.participants.map((p, i) => link(`/borrow?p=${p.id}`, `P${i + 1}`,
          page === '/borrow' && pid === p.id, `${participantName(p)} · ID ${p.id}`)).join('')}
        ${link('/borrow?p=guest', 'Guest', page === '/borrow' && pid === 'guest', 'Not counted in results')}
      </span>
      <span class="nav-group"><span class="nav-label">Owner:</span>
        ${config.owners.map(o => link(`/owner?o=${o.id}`, o.firstName, page === '/owner' && oid === o.id)).join('')}
      </span>
      ${link('/results', 'Results', page === '/results')}
    </div>`;
}

function participantLinks() {
  return `<ul class="links">
    ${config.participants.map(p =>
      `<li><a href="/borrow?p=${esc(p.id)}">${esc(participantName(p))}</a> <code>${esc(p.id)}</code></li>`).join('')}
    <li><a href="/borrow?p=guest">Guest</a> <span class="muted">(not counted in results)</span></li>
  </ul>`;
}

function ownerLinks() {
  return `<ul class="links">${config.owners.map(o =>
    `<li><a href="/owner?o=${esc(o.id)}">${esc(o.firstName)}</a> <span class="muted">· ${esc(o.area)}</span></li>`).join('')}</ul>`;
}

/* ------------------------------------------------------------------- home */

function renderHome() {
  return `
    <section>
      <h1>NeighborTools</h1>
      <p class="lead">Borrow equipment from nearby neighbors without calling around: see what's available,
        send one request, and get a confirmed pickup time.</p>
    </section>
    <div class="columns">
      <section class="card">
        <h2>1. Borrower view</h2>
        <p>Each fictional participant has a unique link. Opening one records that participant's first
          visit and starts their 48-hour window.</p>
        ${participantLinks()}
      </section>
      <section class="card">
        <h2>2. Owner view</h2>
        <p>Accept or decline requests, confirm times, record pickup and return, and edit equipment.</p>
        ${ownerLinks()}
      </section>
      <section class="card">
        <h2>3. Results</h2>
        <p>Simulated progress toward the 3-of-5 threshold, a log of every request, and the reset button.</p>
        <ul class="links"><li><a href="/results">Open results</a></li></ul>
      </section>
    </div>
    <section class="card">
      <h2>Research question</h2>
      <blockquote>${esc(config.researchQuestion)}</blockquote>
      <p class="warn">This is a classroom demo with fictional equipment, owners, and participants.
        Clicking through it does <strong>not</strong> show that the research question has been validated.</p>
    </section>`;
}

/* --------------------------------------------------------------- borrower */

function loadBoard() {
  return isParticipant(pid) ? api(`/api/board?p=${encodeURIComponent(pid)}`) : null;
}

function renderBorrower() {
  if (!data) {
    return `
      <section>
        <h1>Choose a participant link</h1>
        ${pid ? `<p class="warn">There is no participant with ID <code>${esc(pid)}</code>.</p>` : ''}
        <p>Each fictional participant has their own link with a unique ID. Opening one records that
          participant's first visit.</p>
        ${participantLinks()}
      </section>`;
  }
  const me = data.participant;
  const who = me.isGuest
    ? 'You are browsing as a <strong>guest</strong>. Guest requests work normally but are not counted in the results, and all guests share this list.'
    : `Participant link: <strong>${esc(participantName(me))}</strong> · ID <code>${esc(me.id)}</code>.
       Visits and requests from this link are recorded automatically for the simulated results.`;
  return `
    <section>
      <h1>Equipment board</h1>
      <p class="lead">Equipment that nearby neighbors are willing to lend. Pick an item and your times;
        the owner accepts or declines right here, so there's no need to call around.</p>
      <p class="whoami">${who}</p>
    </section>
    ${data.requests.length ? `
      <section>
        <h2>${me.isGuest ? 'Guest requests' : 'Your requests'} <span class="count">${data.requests.length}</span></h2>
        <div class="stack">${data.requests.map(borrowerRequestCard).join('')}</div>
      </section>` : ''}
    <section>
      <h2>Available equipment <span class="tag">fictional sample data</span></h2>
      <div class="grid">${data.tools.map(borrowerToolCard).join('') || '<p class="empty">No equipment is listed right now.</p>'}</div>
    </section>`;
}

function reservationsFact(tool) {
  if (!tool.reservations.length) return '';
  return `<dt>Already reserved</dt><dd>${tool.reservations.map(r => esc(fmtRange(r.start, r.end))).join('<br>')}</dd>`;
}

function borrowerToolCard(t) {
  const open = isOpen('request', t.id);
  const ended = t.availableTo < data.today;
  return `
    <article class="card${open ? ' wide' : ''}">
      <div class="card-head"><h3>${esc(t.name)}</h3><span class="owner">Owner: ${esc(t.ownerName)}</span></div>
      <p>${esc(t.description)}</p>
      <dl class="facts">
        <dt>Pickup area</dt><dd>${esc(t.pickupArea)}</dd>
        <dt>Available</dt><dd>${fmtDay(t.availableFrom)} – ${fmtDay(t.availableTo)}</dd>
        ${reservationsFact(t)}
      </dl>
      ${open ? requestForm(t) : `<button class="primary" data-action="open-request" data-id="${esc(t.id)}"${ended ? ' disabled' : ''}>
        ${ended ? 'No longer available' : 'Request this item'}</button>`}
    </article>`;
}

function requestForm(t) {
  // Prefill name and contact from this participant's last request (not for the shared guest link).
  const last = data.participant.isGuest ? {} : (data.requests[0] || {});
  const start = t.availableFrom > data.today ? `${t.availableFrom}T00:00` : localNow();
  const end = `${t.availableTo}T23:59`;
  return `
    <form class="panel" data-form="request" data-tool="${esc(t.id)}">
      <h4>Request: ${esc(t.name)}</h4>
      <div class="form-errors" role="alert" hidden></div>
      <div class="fields">
        <label>Your name<input name="borrowerName" required maxlength="80" autocomplete="name" value="${esc(last.borrowerName)}"></label>
        <label>Phone or email<input name="contact" required maxlength="120" value="${esc(last.contact)}"></label>
        <label class="full">Maintenance job
          <textarea name="job" required maxlength="500" rows="2" placeholder="e.g. Pressure-wash the back deck before staining it"></textarea></label>
        <label>Pickup<input type="datetime-local" name="pickupAt" required min="${start}" max="${end}"></label>
        <label>Return<input type="datetime-local" name="returnAt" required min="${start}" max="${end}"></label>
      </div>
      <p class="hint">Times must fall between ${fmtDay(t.availableFrom)} and ${fmtDay(t.availableTo)}${
        t.reservations.length ? ' and must not overlap the reserved times above' : ''}. Return must be after pickup.</p>
      <div class="actions">
        <button class="primary" type="submit">Send request</button>
        <button type="button" data-action="close">Cancel</button>
      </div>
    </form>`;
}

function timeline(r) {
  const steps = [`Sent ${fmtStamp(r.createdAt)}`];
  if (r.decidedAt) steps.push(`${r.status === 'declined' ? 'Declined' : 'Accepted'} ${fmtStamp(r.decidedAt)}`);
  if (r.pickedUpAt) steps.push(`Picked up ${fmtStamp(r.pickedUpAt)}`);
  if (r.returnedAt) steps.push(`Returned ${fmtStamp(r.returnedAt)}`);
  return steps.join(' · ');
}

function borrowerRequestCard(r) {
  const owner = esc(r.ownerName);
  const message = {
    pending: `Waiting for ${owner} to accept or decline.`,
    accepted: `Accepted! Pick up at ${esc(r.pickupArea)} on <strong>${fmtLocal(r.confirmedPickup)}</strong>
               and return by <strong>${fmtLocal(r.confirmedReturn)}</strong>.`,
    declined: `${owner} declined this request.`,
    picked_up: `Picked up. Please return it by <strong>${fmtLocal(r.confirmedReturn)}</strong>.`,
    returned: 'Returned. Thanks for taking care of it!',
  }[r.status] || '';
  return `
    <article class="card">
      <div class="card-head"><h3>${esc(r.toolName)} <span class="muted">from ${owner}</span></h3>${badge(r.status)}</div>
      <p class="status-msg">${message}</p>
      <dl class="facts">
        <dt>Job</dt><dd>${esc(r.job)}</dd>
        <dt>Requested</dt><dd>${fmtRange(r.requestedPickup, r.requestedReturn)}</dd>
        ${r.confirmedPickup ? `<dt>Confirmed</dt><dd>${fmtRange(r.confirmedPickup, r.confirmedReturn)}</dd>` : ''}
        ${r.ownerNote ? `<dt>Note from ${owner}</dt><dd>${esc(r.ownerNote)}</dd>` : ''}
        <dt>Timeline</dt><dd>${timeline(r)}</dd>
      </dl>
    </article>`;
}

actions['open-request'] = d => openPanel('request', d.id);

forms.request = form => submitForm(form, async values => {
  await api('/api/requests', 'POST', { ...values, participantId: pid, toolId: form.dataset.tool });
  await afterChange('Request sent. The owner’s decision will appear under “Your requests”.');
  window.scrollTo({ top: 0, behavior: 'smooth' });
});

/* ------------------------------------------------------------------ owner */

function loadOwner() {
  return isOwner(oid) ? api(`/api/owner/${encodeURIComponent(oid)}`) : null;
}

function renderOwner() {
  if (!data) {
    return `
      <section>
        <h1>Choose an owner link</h1>
        ${oid ? `<p class="warn">There is no owner with ID <code>${esc(oid)}</code>.</p>` : ''}
        ${ownerLinks()}
      </section>`;
  }
  const { owner, requests, tools } = data;
  const withStatus = (...statuses) => requests.filter(r => statuses.includes(r.status));
  const list = (items, emptyText) => items.map(ownerRequestCard).join('') || `<p class="empty">${emptyText}</p>`;
  const pending = withStatus('pending');
  const active = withStatus('accepted', 'picked_up');
  const past = withStatus('declined', 'returned');
  return `
    <section>
      <h1>${esc(owner.firstName)}'s equipment</h1>
      <p class="lead">Owner link for a fictional neighbor. Accept or decline requests, confirm times, and
        record pickups and returns. Borrowers see each update on their board.</p>
    </section>
    <section>
      <h2>Needs a decision <span class="count">${pending.length}</span></h2>
      <div class="stack">${list(pending, 'No pending requests.')}</div>
    </section>
    <section>
      <h2>Reserved and on loan <span class="count">${active.length}</span></h2>
      <div class="stack">${list(active, 'Nothing is reserved right now.')}</div>
    </section>
    <section>
      <h2>Your equipment <span class="tag">fictional sample data</span></h2>
      ${isOpen('tool', 'new') ? `<div class="card">${toolForm(null)}</div>`
        : '<p><button data-action="new-tool">+ Add equipment</button></p>'}
      <div class="stack">${tools.map(ownerToolCard).join('')}</div>
    </section>
    <section>
      <h2>History <span class="count">${past.length}</span></h2>
      <div class="stack">${list(past, 'No declined or returned requests yet.')}</div>
    </section>`;
}

function ownerRequestCard(r) {
  let controls = '';
  if (isOpen('accept', r.id)) {
    controls = acceptForm(r);
  } else if (isOpen('decline', r.id)) {
    controls = declineForm(r);
  } else if (r.status === 'pending') {
    controls = `<div class="actions">
      <button class="primary" data-action="accept" data-id="${r.id}">Accept…</button>
      <button data-action="decline" data-id="${r.id}">Decline…</button></div>`;
  } else if (r.status === 'accepted') {
    controls = `<div class="actions">
      <button class="primary" data-action="picked-up" data-id="${r.id}">Mark picked up</button>
      <button data-action="decline" data-id="${r.id}">Cancel reservation…</button></div>`;
  } else if (r.status === 'picked_up') {
    controls = `<div class="actions"><button class="primary" data-action="returned" data-id="${r.id}">Mark returned</button></div>`;
  }
  return `
    <article class="card">
      <div class="card-head"><h3>${esc(r.toolName)} <span class="muted">· request #${r.id}</span></h3>${badge(r.status)}</div>
      <dl class="facts">
        <dt>Borrower</dt><dd>${esc(r.borrowerName)} · ${esc(r.contact)} <span class="muted">(${esc(r.participantLabel)})</span></dd>
        <dt>Job</dt><dd>${esc(r.job)}</dd>
        <dt>Requested</dt><dd>${fmtRange(r.requestedPickup, r.requestedReturn)}</dd>
        ${r.confirmedPickup ? `<dt>Confirmed</dt><dd><strong>${fmtRange(r.confirmedPickup, r.confirmedReturn)}</strong></dd>` : ''}
        ${r.ownerNote ? `<dt>Your note</dt><dd>${esc(r.ownerNote)}</dd>` : ''}
        <dt>Timeline</dt><dd>${timeline(r)}</dd>
      </dl>
      ${r.conflict ? `<p class="warn">These times overlap an accepted reservation
        (${fmtRange(r.conflict.start, r.conflict.end)}). Change the times when accepting, or decline.</p>` : ''}
      ${controls}
    </article>`;
}

function acceptForm(r) {
  return `
    <form class="panel" data-form="accept" data-id="${r.id}">
      <h4>Confirm pickup and return times</h4>
      <div class="form-errors" role="alert" hidden></div>
      <div class="fields">
        <label>Pickup<input type="datetime-local" name="pickupAt" required value="${esc(r.requestedPickup)}"></label>
        <label>Return<input type="datetime-local" name="returnAt" required value="${esc(r.requestedReturn)}"></label>
        <label class="full">Note to borrower (optional)
          <input name="note" maxlength="300" placeholder="e.g. It's in the shed by the driveway."></label>
      </div>
      <p class="hint">Accepting reserves the item for these times. Times that overlap another accepted reservation are blocked.</p>
      <div class="actions">
        <button class="primary" type="submit">Accept and reserve</button>
        <button type="button" data-action="close">Cancel</button>
      </div>
    </form>`;
}

function declineForm(r) {
  const cancelling = r.status === 'accepted';
  return `
    <form class="panel" data-form="decline" data-id="${r.id}">
      <h4>${cancelling ? 'Cancel this reservation?' : 'Decline this request?'}</h4>
      <div class="form-errors" role="alert" hidden></div>
      <div class="fields">
        <label class="full">Note to borrower (optional)
          <input name="note" maxlength="300" placeholder="e.g. Sorry, I need it that weekend."></label>
      </div>
      <div class="actions">
        <button class="danger" type="submit">${cancelling ? 'Cancel reservation' : 'Decline request'}</button>
        <button type="button" data-action="close">Keep it</button>
      </div>
    </form>`;
}

function ownerToolCard(t) {
  if (isOpen('tool', t.id)) return `<div class="card">${toolForm(t)}</div>`;
  return `
    <article class="card">
      <div class="card-head">
        <h3>${esc(t.name)} ${t.listed ? '' : '<span class="badge s-none">Hidden from board</span>'}</h3>
        <button data-action="edit-tool" data-id="${esc(t.id)}">Edit</button>
      </div>
      <p>${esc(t.description)}</p>
      <dl class="facts">
        <dt>Pickup area</dt><dd>${esc(t.pickupArea)}</dd>
        <dt>Available</dt><dd>${fmtDay(t.availableFrom)} – ${fmtDay(t.availableTo)}${
          t.relativeDates ? ' <span class="muted">(sample dates move with today’s date)</span>' : ''}</dd>
        ${reservationsFact(t)}
      </dl>
    </article>`;
}

function toolForm(t) {
  const v = t || { name: '', description: '', pickupArea: data.owner.area, availableFrom: data.today, availableTo: '', listed: true };
  return `
    <form class="panel" data-form="tool" data-id="${t ? esc(t.id) : ''}">
      <h4>${t ? `Edit ${esc(t.name)}` : 'Add equipment'}</h4>
      <div class="form-errors" role="alert" hidden></div>
      <div class="fields">
        <label>Name<input name="name" required maxlength="80" value="${esc(v.name)}"></label>
        <label>Pickup area<input name="pickupArea" required maxlength="120" value="${esc(v.pickupArea)}"></label>
        <label class="full">Description<textarea name="description" required maxlength="500" rows="2">${esc(v.description)}</textarea></label>
        <label>Available from<input type="date" name="availableFrom" required value="${esc(v.availableFrom)}"></label>
        <label>Available until<input type="date" name="availableTo" required value="${esc(v.availableTo)}"></label>
        <label class="check full"><input type="checkbox" name="listed"${v.listed ? ' checked' : ''}> Show on the borrower board</label>
      </div>
      <div class="actions">
        <button class="primary" type="submit">${t ? 'Save changes' : 'Add equipment'}</button>
        <button type="button" data-action="close">Cancel</button>
      </div>
    </form>`;
}

async function ownerAction(id, action, message, button) {
  button.disabled = true;
  try {
    await api(`/api/requests/${id}/${action}`, 'POST', { ownerId: oid });
    await afterChange(message);
  } catch (err) {
    toast(err.message, 'error');
    button.disabled = false;
  }
}

actions.accept = d => openPanel('accept', d.id);
actions.decline = d => openPanel('decline', d.id);
actions['new-tool'] = () => openPanel('tool', 'new');
actions['edit-tool'] = d => openPanel('tool', d.id);
actions['picked-up'] = (d, el) => ownerAction(d.id, 'picked-up', 'Marked as picked up.', el);
actions.returned = (d, el) => ownerAction(d.id, 'returned', 'Marked as returned. The item is free again.', el);

forms.accept = form => submitForm(form, async values => {
  await api(`/api/requests/${form.dataset.id}/accept`, 'POST', { ...values, ownerId: oid });
  await afterChange('Accepted and reserved. The borrower now sees the confirmed times.');
});
forms.decline = form => submitForm(form, async values => {
  await api(`/api/requests/${form.dataset.id}/decline`, 'POST', { ...values, ownerId: oid });
  await afterChange('Done. The borrower now sees that it was declined.');
});
forms.tool = form => submitForm(form, async values => {
  const id = form.dataset.id;
  const body = { ...values, ownerId: oid, listed: form.elements.listed.checked };
  await api(id ? `/api/tools/${encodeURIComponent(id)}` : '/api/tools', id ? 'PUT' : 'POST', body);
  await afterChange(id ? 'Equipment updated.' : 'Equipment added.');
});

/* ---------------------------------------------------------------- results */

function shareBase() {
  const onThisComputer = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
  return onThisComputer && config.lanUrls.length ? config.lanUrls[0] : location.origin;
}

function participantRow(p) {
  const status = {
    not_visited: '<span class="badge s-none">Not opened yet</span>',
    window_open: `<span class="badge s-pending">Window open</span><br><span class="muted">until ${fmtStamp(p.windowEndsAt)}</span>`,
    counted: '<span class="badge s-accepted">Counted</span>',
    not_counted: `<span class="badge s-declined">Not counted</span><br><span class="muted">no request within ${data.windowHours} h</span>`,
  }[p.state];
  return `
    <tr>
      <td><strong>${esc(p.label)}</strong><br><span class="muted">${esc(p.name)} (fictional)</span></td>
      <td><a href="${esc(p.link)}">${esc(shareBase() + p.link)}</a></td>
      <td>${fmtStamp(p.firstVisitAt)}</td>
      <td>${fmtStamp(p.firstRequestAt)}${p.requestCount > 1 ? `<br><span class="muted">${p.requestCount} requests in total</span>` : ''}</td>
      <td>${p.minutesToFirstRequest == null ? '—' : fmtMinutes(p.minutesToFirstRequest)}</td>
      <td>${status}</td>
    </tr>`;
}

function requestRow(r) {
  const confirmed = Boolean(r.confirmedPickup);
  return `
    <tr>
      <td>#${r.id}</td>
      <td>${fmtStamp(r.createdAt)}</td>
      <td>${esc(r.participantLabel)}</td>
      <td>${esc(r.toolName)} <span class="muted">(${esc(r.ownerName)})</span></td>
      <td>${fmtRange(confirmed ? r.confirmedPickup : r.requestedPickup, confirmed ? r.confirmedReturn : r.requestedReturn)}
        <br><span class="muted">${confirmed ? 'confirmed by owner' : 'requested'}</span></td>
      <td>${badge(r.status)}</td>
    </tr>`;
}

function renderResults() {
  const r = data;
  const segments = [];
  for (let i = 0; i < r.total; i++) {
    segments.push(`<span class="seg${i < r.counted ? ' on' : ''}"></span>`);
    if (i === r.threshold - 1) segments.push('<span class="goal" title="Threshold"></span>');
  }
  const needed = r.threshold - r.counted;
  let verdict;
  if (r.thresholdMet) {
    verdict = `Threshold reached in this simulated run (${r.counted} of ${r.total}).`;
  } else if (r.maxPossible < r.threshold) {
    verdict = `Threshold can no longer be reached in this run: at most ${r.maxPossible} of ${r.total} can count.`;
  } else {
    verdict = `${needed} more participant${needed === 1 ? '' : 's'} needed. Up to ${r.maxPossible} of ${r.total} can still count.`;
  }
  const lanNote = config.lanUrls.length
    ? `Links use this computer's network address so they also work on other devices on the same network.`
    : `These links work on this computer. To open them on a second device, restart the server with <code>python app.py --lan</code>.`;
  return `
    <section class="sim-banner" role="note">
      <strong>Simulated demo activity.</strong> These numbers come from fictional participants and classroom
      test clicks. They are <strong>not</strong> evidence that the research question has been validated.
    </section>
    <section>
      <h1>Results</h1>
      <blockquote>${esc(config.researchQuestion)}</blockquote>
    </section>
    <section class="card progress">
      <p class="big"><strong>${r.counted} of ${r.total}</strong> participants counted
        <span class="muted">· threshold: ${r.threshold} of ${r.total}</span></p>
      <div class="meter" role="img" aria-label="${r.counted} of ${r.total} counted; threshold is ${r.threshold}">${segments.join('')}</div>
      <p>${verdict}</p>
    </section>
    <section>
      <h2>Participant links</h2>
      <p class="hint">${lanNote} Opening a link records that participant's first visit and starts their ${r.windowHours}-hour window.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Participant</th><th>Link</th><th>First visit</th><th>First request</th><th>Visit → request</th><th>Status</th></tr></thead>
        <tbody>${r.participants.map(participantRow).join('')}</tbody>
      </table></div>
      <p class="hint">Counting rule: a participant counts once if they submit a complete request (all fields
        valid) within ${r.windowHours} hours of their first visit, whatever the owner later decides. The app
        cannot judge whether a request is <em>genuine</em>; that is the researcher's call.
        Guest requests (${r.guestRequests}) are never counted.</p>
    </section>
    <section>
      <h2>All requests <span class="count">${r.requests.length}</span></h2>
      ${r.requests.length ? `<div class="table-wrap"><table>
        <thead><tr><th>#</th><th>Sent</th><th>From</th><th>Item (owner)</th><th>Times</th><th>Status</th></tr></thead>
        <tbody>${r.requests.map(requestRow).join('')}</tbody>
      </table></div>` : '<p class="empty">No requests yet.</p>'}
    </section>
    <section class="card danger-zone">
      <h2>Reset demo</h2>
      <p>Clears all requests, visits, and tracking, and restores the original sample equipment and
        availability. Participant and owner links stay the same.</p>
      ${isOpen('reset', 'demo') ? `
        <p><strong>Are you sure?</strong> This can't be undone.</p>
        <div class="actions">
          <button class="danger" data-action="reset-confirm">Yes, reset the demo</button>
          <button data-action="close">Cancel</button>
        </div>` : '<button class="danger" data-action="reset">Reset demo…</button>'}
    </section>`;
}

actions.reset = () => openPanel('reset', 'demo');
actions['reset-confirm'] = async (d, el) => {
  el.disabled = true;
  try {
    await api('/api/reset', 'POST', {});
    await afterChange('Demo reset. Requests and tracking cleared; sample equipment restored.');
  } catch (err) {
    toast(err.message, 'error');
    el.disabled = false;
  }
};

/* ------------------------------------------------------------------ start */

const views = {
  '/': { title: 'Home', render: renderHome },
  '/borrow': { title: 'Equipment board', load: loadBoard, render: renderBorrower },
  '/owner': { title: 'Owner', load: loadOwner, render: renderOwner },
  '/results': { title: 'Results', load: () => api('/api/results'), render: renderResults },
};
const view = views[page] || views['/'];

async function start() {
  try {
    config = await api('/api/config');
  } catch (err) {
    app.innerHTML = `<p class="warn">Could not load the app (${esc(err.message)}). Is the server running?</p>`;
    return;
  }
  document.title = `${view.title} · NeighborTools (classroom demo)`;
  renderNav();
  if (page === '/borrow' && pid && pid !== 'guest' && isParticipant(pid)) {
    // Record the visit. The server keeps only the first one as the start of the 48-hour window.
    await api('/api/visits', 'POST', { participantId: pid }).catch(() => {});
  }
  await refresh(true);
  if (view.load) {
    const poll = () => { if (!document.hidden && !app.querySelector('form')) refresh(); };
    setInterval(poll, POLL_MS);
    document.addEventListener('visibilitychange', poll);
  }
}

start();
