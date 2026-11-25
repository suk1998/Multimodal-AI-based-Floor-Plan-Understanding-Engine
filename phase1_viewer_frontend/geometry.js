/* CSS viewport pixels <-> original extracted-image pixels. No PDF or DOM state. */
(function (root) {
  "use strict";
  function dimensions(...values) {
    if (values.some(v => !Number.isFinite(v) || v <= 0)) throw new RangeError("Invalid dimensions");
  }
  function fit(width, height, viewportWidth, viewportHeight, padding = 16) {
    dimensions(width, height, viewportWidth, viewportHeight);
    const scale = Math.min(Math.max(1, viewportWidth - 2 * padding) / width,
                           Math.max(1, viewportHeight - 2 * padding) / height);
    return {scale, offsetX: (viewportWidth - width * scale) / 2,
                   offsetY: (viewportHeight - height * scale) / 2};
  }
  function toNative(view, x, y) {
    return {x: (x - view.offsetX) / view.scale, y: (y - view.offsetY) / view.scale};
  }
  function toScreen(view, x, y) {
    return {x: x * view.scale + view.offsetX, y: y * view.scale + view.offsetY};
  }
  function zoomAt(view, factor, x, y, minScale, maxScale) {
    const point = toNative(view, x, y);
    const scale = Math.max(minScale, Math.min(maxScale, view.scale * factor));
    return {scale, offsetX: x - point.x * scale, offsetY: y - point.y * scale};
  }
  function panBy(view, dx, dy) {
    return {...view, offsetX: view.offsetX + dx, offsetY: view.offsetY + dy};
  }
  function resize(view, oldWidth, oldHeight, newWidth, newHeight) {
    const center = toNative(view, oldWidth / 2, oldHeight / 2);
    return {...view, offsetX: newWidth / 2 - center.x * view.scale,
                     offsetY: newHeight / 2 - center.y * view.scale};
  }
  const api = Object.freeze({fit, toNative, toScreen, zoomAt, panBy, resize});
  root.FloorplanViewport = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(globalThis);
