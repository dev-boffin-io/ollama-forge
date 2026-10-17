// lib/assets.js
// Access for the bundled front-end assets.
//
// When running from source, assets are read from ./public on disk by server.js.
// When compiled into a Single Executable Application, the build step replaces
// __EMBEDDED_ASSETS__ with an object mapping file name -> file contents, so the
// UI travels inside the binary.

const embedded =
  typeof __EMBEDDED_ASSETS__ !== "undefined" ? __EMBEDDED_ASSETS__ : null;

export function isEmbedded() {
  return embedded !== null;
}

export function getEmbeddedAsset(name) {
  if (!embedded) return null;
  return Object.prototype.hasOwnProperty.call(embedded, name) ? embedded[name] : null;
}
