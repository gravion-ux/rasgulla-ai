'use strict';

// Guard for URLs that the shell is about to hand to the operating system.
//
// This mirrors core_app.is_link_local_url, which already refuses cloud metadata
// endpoints and link-local addresses for the actions the model proposes.  The
// shell applies the same rule to anything the renderer asks it to open, and
// adds the scheme and credential rules a desktop opener needs.

const BLOCKED_HOSTS = new Set(['metadata.google.internal', 'metadata.goog']);
const ALLOWED_PROTOCOLS = new Set(['http:', 'https:']);

function hostnameOf(parsed) {
  return (parsed.hostname || '').trim().toLowerCase();
}

function isLinkLocalAddress(host) {
  let name = String(host || '').trim().toLowerCase();
  if (name.startsWith('[') && name.endsWith(']')) {
    name = name.slice(1, -1);
  }
  const mapped = name.startsWith('::ffff:') ? name.slice(7) : name;
  const ipv4 = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(mapped);
  if (ipv4) {
    const parts = ipv4.slice(1).map(Number);
    if (parts.some((part) => part > 255)) {
      return false;
    }
    return parts[0] === 169 && parts[1] === 254;
  }
  // IPv6 link-local fe80::/10 - the first hextet runs from fe80 to febf.
  return /^fe[89ab][0-9a-f]:/.test(name);
}

function isAllowedExternalUrl(raw) {
  if (typeof raw !== 'string') {
    return false;
  }
  const text = raw.trim();
  if (!text) {
    return false;
  }
  let parsed;
  try {
    parsed = new URL(text);
  } catch (_) {
    return false;
  }
  if (!ALLOWED_PROTOCOLS.has(parsed.protocol)) {
    return false;
  }
  if (parsed.username || parsed.password) {
    return false;
  }
  const host = hostnameOf(parsed);
  if (!host || BLOCKED_HOSTS.has(host)) {
    return false;
  }
  return !isLinkLocalAddress(host);
}

module.exports = { isAllowedExternalUrl, isLinkLocalAddress };
