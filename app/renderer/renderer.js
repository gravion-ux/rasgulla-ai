'use strict';

const rasgulla = window.rasgulla;
const orb = document.getElementById('orb');
const audio = document.getElementById('voice');
const entry = document.getElementById('entry');
const btnSend = document.getElementById('btnSend');
const btnPin = document.getElementById('btnPin');
const btnCollapse = document.getElementById('btnCollapse');
const btnClose = document.getElementById('btnClose');
const transcript = document.getElementById('transcript');
const planCard = document.getElementById('plan-card');
const planList = document.getElementById('plan-list');
const btnRunPlan = document.getElementById('btnRunPlan');
const btnClearPlan = document.getElementById('btnClearPlan');
const confirmCard = document.getElementById('confirm-card');
const confirmText = document.getElementById('confirm-text');
const btnConfirmYes = document.getElementById('btnConfirmYes');
const btnConfirmNo = document.getElementById('btnConfirmNo');
const routeCaption = document.getElementById('route-caption');

let pendingConfirm = null;
let voiceUrl = null;

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[c]));
}

function appendTranscript(speaker, message, tag = 'speaker') {
  const div = document.createElement('div');
  div.className = tag;
  div.innerHTML = `<span class="speaker">${escapeHtml(speaker)}:</span> ${escapeHtml(message)}`;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

function pill(state) {
  for (const button of document.querySelectorAll('.pill button')) {
    button.setAttribute('aria-pressed', button.dataset.state === state ? 'true' : 'false');
  }
  // The pet overlay follows the orb: idle frames while resting, work frames
  // while a request is in flight.
  if (typeof rasgulla.setOrbState === 'function') {
    rasgulla.setOrbState(state);
  }
}

function setLevel(value) {
  orb.setAttribute('level', Math.min(1, Math.max(0, Number(value) || 0)).toFixed(3));
}

// Lets the shell drive the orb state, so the pet overlay can follow it.
window.__rasgullaSetOrbState = (state) => pill(state);

function saySpoken(text, audioBase64, words, mime) {
  const clean = String(text || '').trim();
  if (!clean) return;
  orb.setAttribute('state', 'speaking');
  pill('speaking');
  if (voiceUrl) {
    URL.revokeObjectURL(voiceUrl);
    voiceUrl = null;
  }
  if (audioBase64) {
    const binary = atob(audioBase64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const blob = new Blob([bytes], { type: mime || 'audio/mpeg' });
    voiceUrl = URL.createObjectURL(blob);
    audio.src = voiceUrl;
  }
  const lineWords = (words || []).map((w, i) => ({
    w: w.w || (clean.split(/\s+/)[i] || ''),
    start: Number(w.start) || 0,
    end: Number(w.end) || 0,
  }));
  orb.say(clean, { words: lineWords, audio });
  if (!audioBase64) {
    // No real audio: the orb falls back to its estimated envelope.
    const rest = orb.getAttribute('rest') || 'listening';
    orb.setAttribute('state', rest);
    pill(rest);
  } else {
    audio.addEventListener('ended', () => {
      const rest = orb.getAttribute('rest') || 'listening';
      orb.setAttribute('state', rest);
      pill(rest);
    }, { once: true });
  }
}

async function handleMessage(message) {
  const route = message.route || {};
  const descriptions = Array.isArray(message.descriptions) ? message.descriptions : [];
  const rejections = Array.isArray(message.rejections) ? message.rejections : [];
  switch (message.type) {
    case 'ready':
      pill('listening');
      setLevel(0);
      appendTranscript(
        'System',
        `Orb ready. ${message.key_found ? 'Assistant key found.' : 'No assistant key found. Set RASGULLA_API_KEY and restart.'}`,
        'note'
      );
      break;
    case 'state':
      pill(message.state);
      break;
    case 'level':
      setLevel(message.value);
      break;
    case 'transcript':
      appendTranscript('You', message.text, 'me');
      if (message.final) {
        pill('thinking');
        rasgulla.sendToBrain({ command: 'route', text: message.text, mode: 'auto', speak: true });
      }
      break;
    case 'plan':
      appendTranscript('Rasgulla', message.reply);
      routeCaption.textContent = `Route: ${route.tier_label || route.tier || '-'} / ${route.label || '-'} - ${route.seconds || 0}s - ${route.tokens || 0} tokens`;
      if (message.warning) appendTranscript('Rasgulla', message.warning, 'note');
      if (message.clarify) appendTranscript('Rasgulla', message.clarify, 'note');
      if (message.speech) {
        saySpoken(message.speech, message.audio, message.words, message.mime);
      }
      if (message.actions && message.actions.length) {
        planCard.classList.remove('hidden');
        planList.innerHTML = '';
        for (const [index, action] of message.actions.entries()) {
          const row = document.createElement('div');
          row.className = 'plan-row';
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.value = index;
          checkbox.addEventListener('change', () => {
            const approved = Array.from(planList.querySelectorAll('input:checked')).map((el) => Number(el.value));
            btnRunPlan.disabled = approved.length === 0;
          });
          const desc = document.createElement('span');
          desc.className = 'desc';
          desc.textContent = `${index + 1}. ${descriptions[index] || ''}`;
          row.appendChild(checkbox);
          row.appendChild(desc);
          if (rejections.includes(descriptions[index] || '')) {
            const badge = document.createElement('span');
            badge.className = 'rejected';
            badge.textContent = 'rejected';
            row.appendChild(badge);
          }
          planList.appendChild(row);
        }
      } else {
        planCard.classList.add('hidden');
      }
      break;
    case 'confirm':
      pendingConfirm = { index: message.index };
      confirmText.textContent = `Run: ${message.description}?`;
      confirmCard.classList.remove('hidden');
      break;
    case 'action_result':
      appendTranscript('Rasgulla', message.text, 'note');
      confirmCard.classList.add('hidden');
      pendingConfirm = null;
      break;
    case 'plan_done':
      confirmCard.classList.add('hidden');
      pendingConfirm = null;
      pill('listening');
      break;
    case 'error':
      appendTranscript('Rasgulla', message.message, 'note');
      break;
    case 'brain_down':
      appendTranscript('System', 'Brain process stopped.', 'note');
      break;
  }
}

rasgulla.onBrainEvent(handleMessage);
rasgulla.onBrainLog((log) => {
  console.log('[brain]', log.message);
});

btnSend.addEventListener('click', async () => {
  const text = entry.value.trim();
  if (!text) return;
  entry.value = '';
  appendTranscript('You', text, 'me');
  pill('thinking');
  try {
    await rasgulla.sendToBrain({ command: 'route', text, mode: 'auto', speak: true });
  } catch (error) {
    appendTranscript('System', `The brain did not answer: ${error.message}`, 'note');
    pill('listening');
  }
});

btnPin.addEventListener('click', async () => {
  const result = await rasgulla.togglePin();
  btnPin.textContent = result.pinned ? 'unpin' : 'pin';
});

btnClose.addEventListener('click', () => {
  rasgulla.sendToBrain({ command: 'shutdown' }).catch(() => {});
  window.close();
});

btnRunPlan.addEventListener('click', async () => {
  const approved = Array.from(planList.querySelectorAll('input:checked')).map((el) => Number(el.value));
  if (approved.length === 0) return;
  planCard.classList.add('hidden');
  await rasgulla.sendToBrain({ command: 'run_plan', approved });
});

btnClearPlan.addEventListener('click', () => {
  planCard.classList.add('hidden');
  planList.innerHTML = '';
  btnRunPlan.disabled = true;
});

btnConfirmYes.addEventListener('click', async () => {
  if (pendingConfirm == null) return;
  confirmCard.classList.add('hidden');
  await rasgulla.sendToBrain({ command: 'act', index: pendingConfirm.index, ok: true });
  pendingConfirm = null;
});

btnConfirmNo.addEventListener('click', () => {
  if (pendingConfirm == null) return;
  confirmCard.classList.add('hidden');
  rasgulla.sendToBrain({ command: 'act', index: pendingConfirm.index, ok: false }).catch(() => {});
  pendingConfirm = null;
});

entry.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') btnSend.click();
});

// -- layout -------------------------------------------------------------------

function applyLayout(isExpanded) {
  const expanded = Boolean(isExpanded);
  document.body.classList.toggle('expanded', expanded);
  panel.classList.toggle('hidden', !expanded);
  btnCollapse.textContent = expanded ? 'collapse' : 'expand';
  btnCollapse.title = expanded ? 'Collapse the chat panel' : 'Expand the chat panel';
}

btnCollapse.addEventListener('click', async () => {
  try {
    const result = await rasgulla.toggleExpand();
    applyLayout(result && result.expanded);
  } catch (error) {
    appendTranscript('System', `The window could not be resized: ${error.message}`, 'note');
  }
});

rasgulla.onLayout((state) => {
  applyLayout(state && state.expanded);
});

applyLayout(false);

rasgulla.sendToBrain({ command: 'hello' }).catch(() => {});

window.__rasgullaRendererReady = true;
