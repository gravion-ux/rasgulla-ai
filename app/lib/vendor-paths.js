'use strict';

const path = require('path');

// Maps an orb-vendor:// request onto a file on disk.
//
// Two roots are reachable and nothing else:
//   orb-vendor://speaking-orb.js               -> app/vendor/speaking-orb.js
//   orb-vendor://assets/pet-frames/idle_00.png  -> <project>/assets/pet-frames/idle_00.png
//
// The scheme is deliberately not a "standard" scheme, so Chromium hands the
// request over as the exact string that was typed.  That keeps the mapping
// obvious and lets the path be checked here instead of being guessed from a
// normalised URL.

const SCHEME = 'orb-vendor:';

function resolveVendorPath(rawUrl, roots) {
  const appDir = roots && roots.appDir;
  const projectRoot = roots && roots.projectRoot;
  if (typeof rawUrl !== 'string' || !appDir || !projectRoot) {
    return { ok: false, reason: 'bad request' };
  }

  let rest = rawUrl.trim();
  const lower = rest.toLowerCase();
  if (lower.startsWith(SCHEME + '//')) {
    rest = rest.slice(SCHEME.length + 2);
  } else if (lower.startsWith(SCHEME)) {
    rest = rest.slice(SCHEME.length);
  } else {
    return { ok: false, reason: 'wrong scheme' };
  }

  rest = rest.split('#')[0].split('?')[0];
  if (!rest) {
    return { ok: false, reason: 'empty path' };
  }

  let decoded;
  try {
    decoded = decodeURIComponent(rest);
  } catch (_) {
    return { ok: false, reason: 'bad encoding' };
  }
  decoded = decoded.replace(/^[\\/]+/, '');
  if (!decoded || decoded.includes('\0')) {
    return { ok: false, reason: 'empty path' };
  }

  let base;
  let relative;
  if (decoded === 'assets' || decoded.startsWith('assets/') || decoded.startsWith('assets\\')) {
    base = path.join(projectRoot, 'assets');
    relative = decoded.slice('assets'.length).replace(/^[\\/]+/, '');
  } else {
    base = path.join(appDir, 'vendor');
    relative = decoded.startsWith('vendor/') || decoded.startsWith('vendor\\')
      ? decoded.slice('vendor'.length).replace(/^[\\/]+/, '')
      : decoded;
  }
  if (!relative) {
    return { ok: false, reason: 'empty path' };
  }

  const file = path.resolve(base, relative);
  const inside = path.relative(base, file);
  if (inside.startsWith('..') || path.isAbsolute(inside)) {
    return { ok: false, reason: 'outside the allowed root' };
  }
  return { ok: true, file };
}

module.exports = { resolveVendorPath };
