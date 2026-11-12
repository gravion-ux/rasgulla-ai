'use strict';

// Unit tests for the pure helpers in app/lib.  Run with:
//   node --test app/test_node_units.js

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const { isAllowedExternalUrl } = require('./lib/url-guard');
const { resolveVendorPath } = require('./lib/vendor-paths');
const { WINDOW_SIZES } = require('./lib/layout');

const APP_DIR = path.resolve(__dirname);
const PROJECT_ROOT = path.resolve(__dirname, '..');
const ROOTS = { appDir: APP_DIR, projectRoot: PROJECT_ROOT };

test('url guard allows ordinary http and https links', () => {
  assert.equal(isAllowedExternalUrl('https://example.com'), true);
  assert.equal(isAllowedExternalUrl('http://example.com/path?q=1'), true);
});

test('url guard allows loopback, like the brain guard does', () => {
  assert.equal(isAllowedExternalUrl('http://127.0.0.1:8000/'), true);
  assert.equal(isAllowedExternalUrl('http://localhost:3000/'), true);
});

test('url guard refuses non http schemes', () => {
  assert.equal(isAllowedExternalUrl('file:///C:/Windows/System32'), false);
  assert.equal(isAllowedExternalUrl('javascript:alert(1)'), false);
  assert.equal(isAllowedExternalUrl('data:text/html,<b>x'), false);
  assert.equal(isAllowedExternalUrl('ms-msdt:/id'), false);
  assert.equal(isAllowedExternalUrl('mailto:someone@example.com'), false);
});

test('url guard refuses cloud metadata endpoints', () => {
  assert.equal(isAllowedExternalUrl('https://metadata.google.internal/computeMetadata/v1/'), false);
  assert.equal(isAllowedExternalUrl('https://metadata.goog/'), false);
});

test('url guard refuses link-local addresses', () => {
  assert.equal(isAllowedExternalUrl('https://169.254.169.254/latest/meta-data/'), false);
  assert.equal(isAllowedExternalUrl('http://[fe80::1]/'), false);
});

test('url guard refuses embedded credentials', () => {
  assert.equal(isAllowedExternalUrl('https://user:pass@example.com/'), false);
});

test('url guard refuses junk', () => {
  assert.equal(isAllowedExternalUrl(''), false);
  assert.equal(isAllowedExternalUrl('   '), false);
  assert.equal(isAllowedExternalUrl(null), false);
  assert.equal(isAllowedExternalUrl(undefined), false);
  assert.equal(isAllowedExternalUrl(42), false);
  assert.equal(isAllowedExternalUrl('not a url'), false);
});

test('vendor paths map the component file into app/vendor', () => {
  const result = resolveVendorPath('orb-vendor://speaking-orb.js', ROOTS);
  assert.equal(result.ok, true);
  assert.equal(result.file, path.join(APP_DIR, 'vendor', 'speaking-orb.js'));
});

test('vendor paths map pet frames into the project assets folder', () => {
  const result = resolveVendorPath('orb-vendor://assets/pet-frames/idle_00.png', ROOTS);
  assert.equal(result.ok, true);
  assert.equal(result.file, path.join(PROJECT_ROOT, 'assets', 'pet-frames', 'idle_00.png'));
});

test('vendor paths accept an explicit vendor prefix', () => {
  const result = resolveVendorPath('orb-vendor://vendor/speaking-orb.js', ROOTS);
  assert.equal(result.ok, true);
  assert.equal(result.file, path.join(APP_DIR, 'vendor', 'speaking-orb.js'));
});

test('vendor paths refuse to escape their root', () => {
  for (const bad of [
    'orb-vendor://../core_app.py',
    'orb-vendor://assets/../../core_app.py',
    'orb-vendor://assets/%2e%2e/%2e%2e/core_app.py',
    'orb-vendor://vendor/../../core_app.py',
  ]) {
    const result = resolveVendorPath(bad, ROOTS);
    assert.equal(result.ok, false, `expected refusal for ${bad}`);
  }
});

test('vendor paths refuse an empty or scheme-only request', () => {
  assert.equal(resolveVendorPath('orb-vendor://', ROOTS).ok, false);
  assert.equal(resolveVendorPath('orb-vendor://?x=1', ROOTS).ok, false);
});

test('vendor paths ignore a query string', () => {
  const result = resolveVendorPath('orb-vendor://speaking-orb.js?v=2', ROOTS);
  assert.equal(result.ok, true);
  assert.equal(result.file, path.join(APP_DIR, 'vendor', 'speaking-orb.js'));
});

test('window sizes match the design spec', () => {
  assert.deepEqual(WINDOW_SIZES.compact, { width: 440, height: 660 });
  assert.deepEqual(WINDOW_SIZES.expanded, { width: 980, height: 700 });
});
