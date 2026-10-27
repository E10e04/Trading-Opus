// Minimal, safe preload: no Node APIs exposed. The app talks to the backend
// over HTTP/WebSocket exactly like the web build.
const { contextBridge } = require('electron');

contextBridge.exposeInMainWorld('tradingOpus', {
  platform: process.platform,
  desktop: true,
});
