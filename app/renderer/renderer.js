'use strict';

// The whole face of Rasgulla: the name, three phases and the orb.
//
// She sleeps with the microphone open.  While she sleeps the orb only shows
// the sound of the room, because she is listening for her name or for two
// claps.  Click the orb to wake her yourself; the orb reacts to the level,
// the phase follows the work, and the spoken line is captioned on the orb.

const rasgulla = window.rasgulla;
const orb = document.getElementById('orb');
const audio = document.getElementById('voice');
const btnClose = document.getElementById('btnClose');
const btnPin = document.getElementById('btnPin');
const replyBox = document.getElementById('reply');

let voiceUrl = null;
// 'wake' while she sleeps, 'capture' while she listens to a command,
// 'idle' when the microphone is closed.
let micMode = 'idle';
let phase = 'listening';
// Set when the brain goes away, so the microphone is opened again when it
// comes back.
let brainWasDown = false;

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
  micMode = on ? 'capture' : 'wake';
  try {
    await rasgulla.sendToBrain({ command: 'mic', on: Boolean(on) });
  } catch (error) {
    console.error('[mic]', error.message);
    micMode = 'idle';
    setPhase('listening');
  }
}

async function onOrbClick() {
  if (phase === 'thinking' || phase === 'speaking') return;
  if (micMode === 'capture') {
    // Put her back to sleep.
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

// The word timings that came with the line are used when they are there.  When
// they are not, the caption is still shown: the words light up on an estimate
// instead of the reply staying invisible.
function estimateWords(spoken) {
  const perSecond = 2.6;
  return spoken.map((word, index) => ({
    w: word,
    start: index / perSecond,
    end: (index + 1) / perSecond,
  }));
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
  const spoken = clean.split(/\s+/).filter(Boolean);
  let lineWords = (words || []).map((word, index) => ({
    w: word.w || spoken[index] || '',
    start: Number(word.start) || 0,
    end: Number(word.end) || 0,
  }));
  if (!lineWords.length) {
    lineWords = estimateWords(spoken);
  }
  if (audioBase64) {
    setPhase('speaking');
    // The audio element is handed over, so the orb follows the real voice.
    orb.say(clean, { words: lineWords, audio });
    return;
  }
  setPhase('speaking');
  // No audio, but the timings are known: the caption still runs on the real
  // clock.  The browser speech voice is never used.
  orb.say(clean, { words: lineWords });
}

// The live caption under the face is transient: it follows the words and is
// gone a moment later.  A probe that looks at it once can easily miss it, so
// every caption the orb shows is remembered here.
const captionLog = [];
window.__rasgullaCaptionLog = captionLog;

function watchCaption() {
  const cap = orb.shadowRoot ? orb.shadowRoot.querySelector('.cap') : null;
  if (!cap) return;
  const observer = new MutationObserver(() => {
    const text = cap.textContent.trim();
    if (!text || captionLog[captionLog.length - 1] === text) return;
    captionLog.push(text);
    if (captionLog.length > 200) captionLog.shift();
  });
  observer.observe(cap, { childList: true, characterData: true, subtree: true });
}

watchCaption();

// The caption under the face follows the words as she talks and is gone a
// moment later.  The reply itself stays in the pane under the orb, in full,
// so it can be read, copied and scrolled through.
function showReply(text) {
  const clean = String(text || '').trim();
  if (!clean) return;
  replyBox.textContent = clean;
  replyBox.scrollTop = 0;
}

function handleMessage(message) {
  switch (message.type) {
    case 'ready':
      setPhase('listening');
      setLevel(0);
      if (brainWasDown) {
        // The brain came back after a crash, so the microphone is opened
        // again and the wake word works once more.
        brainWasDown = false;
        rasgulla.sendToBrain({ command: 'listen', on: true }).catch(() => {});
      }
      break;
    case 'level':
      setLevel(message.value);
      break;
    case 'mic_state':
      if (message.state === 'capture') micMode = 'capture';
      else if (message.state === 'wake') micMode = 'wake';
      else micMode = 'idle';
      break;
    case 'wake':
      // She heard her name, or two claps.
      micMode = 'capture';
      setPhase('listening');
      break;
    case 'greeting':
      showReply(message.speech);
      speak(message.speech, message.audio, message.words, message.mime);
      break;
    case 'transcript':
      if (message.final) {
        ask(message.text);
      }
      break;
    case 'plan':
      // The reply stays on the screen in full, whatever its length.  The
      // caption carries the answer and keeps its formatting; the speech is
      // the fallback for a reply that came without one.
      showReply(message.reply || message.caption || message.speech);
      speak(message.caption || message.speech || message.reply, message.audio, message.words, message.mime);
      break;
    case 'error':
      console.error('[brain]', message.message);
      setPhase('listening');
      break;
    case 'brain_down':
      console.error('[brain] the brain stopped');
      micMode = 'idle';
      brainWasDown = true;
      setPhase('listening');
      break;
  }
}

// A click inside the caption belongs to the caption: it is there for reading
// and scrolling, not for waking her up.
function onOrbClickWithCaption(event) {
  const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
  const inCaption = path.some((node) => node && node.classList && node.classList.contains('cap'));
  if (inCaption) return;
  onOrbClick();
}

orb.addEventListener('click', onOrbClickWithCaption);
orb.addEventListener('end', () => setPhase('listening'));
audio.addEventListener('ended', () => setPhase('listening'));

btnClose.addEventListener('click', () => {
  // She does not die: the window goes into the tray and she keeps listening
  // for her name.  The tray icon brings her back.
  rasgulla.hideApp().catch((error) => {
    console.error('[window]', error.message);
  });
});

btnPin.addEventListener('click', async () => {
  try {
    const pinned = await rasgulla.togglePin();
    btnPin.setAttribute('aria-pressed', pinned ? 'true' : 'false');
  } catch (error) {
    console.error('[pin]', error.message);
  }
});

rasgulla.onBrainEvent(handleMessage);
rasgulla.onBrainLog((log) => {
  console.log('[brain]', log.message);
});

// Test hooks: the acceptance test drives the app through these.
window.__rasgullaAsk = (text) => ask(text);
window.__rasgullaGetPhase = () => phase;
window.__rasgullaGetMicMode = () => micMode;
// The caption probe hands a reply to the renderer this way, so it measures the
// real path instead of a hand-made DOM.
window.__rasgullaMessage = (message) => handleMessage(message);

rasgulla.sendToBrain({ command: 'hello' }).catch(() => {});
// She sleeps with the microphone open, so the wake word is always heard.
rasgulla.sendToBrain({ command: 'listen', on: true }).catch(() => {});

window.__rasgullaRendererReady = true;
