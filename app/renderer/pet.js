'use strict';

const canvas = document.getElementById('sprite');
const ctx = canvas.getContext('2d');
const bubble = document.getElementById('bubble');

const FRAMES = 12;
let setName = 'idle';
let index = 0;
let images = [];
let raf = null;
let lastTime = 0;
let bubbleTimer = null;

const AMBER = [245, 165, 36];
const PINK = [236, 72, 153];
const NEUTRAL = [244, 238, 245];

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

async function loadSet(name) {
  setName = name;
  images = [];
  for (let i = 0; i < FRAMES; i++) {
    const img = new Image();
    img.src = `orb-vendor://assets/pet-frames/${name}_${String(i).padStart(2, '0')}.png`;
    await new Promise((resolve) => {
      img.onload = resolve;
      img.onerror = resolve;
    });
    images.push(tintImage(img));
  }
  index = 0;
  draw();
}

function draw() {
  if (!images[index]) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const img = images[index];
  const scale = Math.min(canvas.width / img.width, canvas.height / img.height);
  const w = img.width * scale;
  const h = img.height * scale;
  const x = (canvas.width - w) / 2;
  const y = (canvas.height - h) / 2;
  ctx.drawImage(img, x, y, w, h);
}

function tick(now) {
  if (!raf) return;
  const dt = lastTime ? Math.min((now - lastTime) / 1000, 0.05) : 0;
  lastTime = now;
  if (dt > 0.08) {
    index = (index + 1) % images.length;
    draw();
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

// Canvas drag
let dragging = false;
let dragOffset = { x: 0, y: 0 };
const ipc = window.rasgulla;

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
  ipc.sendToBrain({ command: 'pet_move', dx, dy }).catch(() => {});
});

window.addEventListener('mouseup', () => {
  dragging = false;
});

canvas.addEventListener('dblclick', () => {
  ipc.sendToBrain({ command: 'focus_orb' }).catch(() => {});
});

canvas.addEventListener('contextmenu', (event) => {
  event.preventDefault();
  showBubble(randomIdleLine());
});

loadSet('idle').then(start);
