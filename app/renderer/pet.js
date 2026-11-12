'use strict';

const canvas = document.getElementById('sprite');
const ctx = canvas.getContext('2d');
const bubble = document.getElementById('bubble');
const ipc = window.rasgulla;

const FRAMES = 12;
const FRAME_MS = 120;

let setName = 'idle';
let wantedSet = 'idle';
let index = 0;
let images = [];
let raf = null;
let lastTime = 0;
let bubbleTimer = null;
let loading = false;

const AMBER = [245, 165, 36];
const PINK = [236, 72, 153];
const NEUTRAL = [244, 238, 245];

// The smoke test reads this to see what the overlay really did.
const petState = { framesLoaded: 0, frameErrors: 0, setName: 'idle' };
window.__rasgullaPet = petState;

function tintPixel(data, i) {
  const r = data[i];
  const g = data[i + 1];
  const b = data[i + 2];
  const a = data[i + 3];
  if (a < 128) return;
  const brightness = (r + g + b) / 3;
  const isAmber = brightness > 120 && r > g && g > b;
  const isPink = brightness > 100 && r > b && b > g;
  if (isAmber) {
    data[i] = AMBER[0];
    data[i + 1] = AMBER[1];
    data[i + 2] = AMBER[2];
  } else if (isPink) {
    data[i] = PINK[0];
    data[i + 1] = PINK[1];
    data[i + 2] = PINK[2];
  } else {
    data[i] = NEUTRAL[0];
    data[i + 1] = NEUTRAL[1];
    data[i + 2] = NEUTRAL[2];
  }
}

function tintImage(img) {
  // A frame that never loaded has no size, and getImageData refuses to read a
  // zero-sized region - that used to throw and kill the whole overlay.
  if (!img || !img.width || !img.height) return null;
  const off = document.createElement('canvas');
  off.width = img.width;
  off.height = img.height;
  const octx = off.getContext('2d');
  octx.drawImage(img, 0, 0);
  const data = octx.getImageData(0, 0, img.width, img.height);
  for (let i = 0; i < data.data.length; i += 4) {
    tintPixel(data.data, i);
  }
  octx.putImageData(data, 0, 0);
  return off;
}

function loadImage(url) {
  return new Promise((resolve) => {
    const img = new Image();
    // The handlers go on before the source, so a cached frame cannot fire its
    // event before anyone is listening.
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
}

async function loadSet(name) {
  loading = true;
  const next = [];
  let errors = 0;
  for (let i = 0; i < FRAMES; i++) {
    const file = `${name}_${String(i).padStart(2, '0')}.png`;
    const img = await loadImage(`orb-vendor://assets/pet-frames/${file}`);
    const tinted = tintImage(img);
    if (!tinted) {
      errors += 1;
      continue;
    }
    next.push(tinted);
  }
  loading = false;
  if (next.length) {
    images = next;
    setName = name;
    index = 0;
    petState.framesLoaded = images.length;
    petState.frameErrors = errors;
    petState.setName = name;
    draw();
  }
  if (wantedSet !== setName && wantedSet !== name) {
    // another set was asked for while this one was loading
    loadSet(wantedSet);
  }
}

function requestSet(name) {
  wantedSet = name;
  if (loading) return;
  if (name === setName && images.length) return;
  loadSet(name);
}

function draw() {
  if (!images.length) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const img = images[index % images.length];
  const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
  const w = img.width * scale;
  const h = img.height * scale;
  const x = (canvas.width - w) / 2;
  const y = (canvas.height - h) / 2;
  ctx.drawImage(img, x, y, w, h);
}

function tick(now) {
  if (!raf) return;
  if (!lastTime) lastTime = now;
  // The old code compared a clamped delta against a larger limit, so the
  // sprite never advanced a frame.
  if (now - lastTime >= FRAME_MS) {
    lastTime = now;
    if (images.length) {
      index = (index + 1) % images.length;
      draw();
    }
  }
  raf = requestAnimationFrame(tick);
}

function start() {
  cancelAnimationFrame(raf);
  lastTime = 0;
  raf = requestAnimationFrame(tick);
}

function stop() {
  cancelAnimationFrame(raf);
  raf = null;
}

function showBubble(text) {
  bubble.textContent = text;
  if (bubbleTimer) {
    clearTimeout(bubbleTimer);
    bubbleTimer = null;
  }
  bubbleTimer = setTimeout(() => {
    bubble.textContent = '';
    bubbleTimer = null;
  }, 3000);
}

function randomIdleLine() {
  const lines = [
    'Bolo kya karna hai.',
    'Ready hoon.',
    'Kaam batao.',
    'Soch rahi hoon...',
  ];
  return lines[Math.floor(Math.random() * lines.length)];
}

// -- canvas drag -------------------------------------------------------------

let dragging = false;
let dragOffset = { x: 0, y: 0 };

canvas.addEventListener('mousedown', (event) => {
  if (event.button === 0) {
    dragging = true;
    dragOffset = { x: event.clientX, y: event.clientY };
  }
});

window.addEventListener('mousemove', (event) => {
  if (!dragging) return;
  const dx = event.clientX - dragOffset.x;
  const dy = event.clientY - dragOffset.y;
  if (dx === 0 && dy === 0) return;
  // Send the step, not the distance from the start, or the window runs away.
  dragOffset = { x: event.clientX, y: event.clientY };
  ipc.movePet(dx, dy).catch(() => {});
});

window.addEventListener('mouseup', () => {
  dragging = false;
});

canvas.addEventListener('dblclick', () => {
  ipc.focusOrb().catch(() => {});
});

canvas.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  showBubble(randomIdleLine());
});

// -- follow the orb ----------------------------------------------------------

ipc.onPetState((payload) => {
  const name = payload && payload.state ? String(payload.state) : '';
  const busy = name === 'thinking' || name === 'searching' || name === 'speaking';
  requestSet(busy ? 'work' : 'idle');
});

requestSet('idle').then(start);
