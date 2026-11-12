'use strict';

// The whole face of Rasgulla: the name, three phases and the orb.
// Click the orb to open the microphone; the orb reacts to the level, the
// phase follows the work, and the spoken line is captioned on the orb.

const rasgulla = window.rasgulla;
const orb = document.getElementById('orb');
const audio = document.getElementById('voice');
const btnClose = document.getElementById('btnClose');

let voiceUrl = null;
let micOpen = false;
let phase = 'listening';

function setPhase(next) {
  phase = next;
  for (const button of document.querySelectorAll('.pill button')) {
    button.setAttribute('aria-pressed', button.dataset.state === next ? 'true' : 'false');
  }
  if (next !== 'speaking') {
    // The orb drives itself while it speaks, so only the resting states are
    // written here.
    orb.setAttribute('state', next);
  }
}

function setLevel(value) {
  const level = Math.min(1, Math.max(0, Number(value) || 0));
  orb.setAttribute('level', level.toFixed(3));
}

async function setMic(on) {
  try {
    await rasgulla.sendToBrain({ command: 'mic', on: Boolean(on) });
    micOpen = Boolean(on);
  } catch (error) {
    console.error('[mic]', error.message);
    micOpen = false;
    setPhase('listening');
  }
}

async function onOrbClick() {
  if (phase === 'thinking' || phase === 'speaking') return;
  if (micOpen) {
    await setMic(false);
    setPhase('listening');
    return;
  }
  setPhase('listening');
  await setMic(true);
}

function ask(text) {
  const request = String(text || '').trim();
  if (!request) return;
  setPhase('thinking');
  rasgulla.sendToBrain({ command: 'route', text: request, mode: 'auto', speak: true })
    .catch((error) => {
      console.error('[brain]', error.message);
      setPhase('listening');
    });
}

function speak(text, audioBase64, words, mime) {
  const clean = String(text || '').trim();
  if (!clean) return;
  if (voiceUrl) {
    URL.revokeObjectURL(voiceUrl);
    voiceUrl = null;
  }
  if (audioBase64) {
    const binary = atob(audioBase64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    voiceUrl = URL.createObjectURL(new Blob([bytes], { type: mime || 'audio/mpeg' }));
    audio.src = voiceUrl;
  }
  const spoken = clean.split(/\s+/);
  const lineWords = (words || []).map((word, index) => ({
    w: word.w || spoken[index] || '',
    start: Number(word.start) || 0,
    end: Number(word.end) || 0,
  }));
  if (audioBase64) {
    setPhase('speaking');
    // The audio element is handed over, so the orb follows the real voice.
    orb.say(clean, { words: lineWords, audio });
    return;
  }
  if (!lineWords.length) return;
  setPhase('speaking');
  // No audio, but the timings are known: the caption still runs on the real
  // clock.  The browser speech voice is never used.
  orb.say(clean, { words: lineWords });
}

function handleMessage(message) {
  switch (message.type) {
    case 'ready':
      setPhase('listening');
      setLevel(0);
      break;
    case 'level':
      setLevel(message.value);
      break;
    case 'mic_state':
      micOpen = message.state === 'live';
      break;
    case 'transcript':
      if (message.final) {
        micOpen = false;
        ask(message.text);
      }
      break;
    case 'plan':
      speak(message.speech, message.audio, message.words, message.mime);
      break;
    case 'error':
      console.error('[brain]', message.message);
      setPhase('listening');
      break;
    case 'brain_down':
      console.error('[brain] the brain stopped');
      micOpen = false;
      setPhase('listening');
      break;
  }
}

orb.addEventListener('click', onOrbClick);
orb.addEventListener('end', () => setPhase('listening'));
audio.addEventListener('ended', () => setPhase('listening'));

btnClose.addEventListener('click', () => {
  rasgulla.sendToBrain({ command: 'shutdown' }).catch(() => {});
  window.close();
});

rasgulla.onBrainEvent(handleMessage);
rasgulla.onBrainLog((log) => {
  console.log('[brain]', log.message);
});

// Test hooks: the acceptance test drives the app through these.
window.__rasgullaAsk = (text) => ask(text);
window.__rasgullaGetPhase = () => phase;

rasgulla.sendToBrain({ command: 'hello' }).catch(() => {});

window.__rasgullaRendererReady = true;
