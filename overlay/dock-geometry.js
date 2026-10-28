// dock-geometry.js
// One geometry contract for the pill window, the dock previews and release,
// shared by main.js and pill.html. The recording capsule lies along its edge
// (upright on the side docks) and the window keeps one size, so changing
// docks only ever moves it.
// All values are device-independent screen coordinates, including negative origins.
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DockGeometry = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const POSITIONS = ['bottom-center', 'middle-left', 'middle-right'];
  // WINDOW_* is the transparent window; ACTIVE_* is the recording capsule.
  const EDGE_GAP = 24, WINDOW_WIDTH = 420, WINDOW_HEIGHT = 160;
  const ACTIVE_LENGTH = 88, ACTIVE_THICKNESS = 32;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const isSide = position => position !== 'bottom-center';

  // The window hugs its edge: bottom-center sits on the bottom edge, the side
  // docks sit against the left or right edge, vertically centred.
  function pillBounds(position, area) {
    const width = Math.min(WINDOW_WIDTH, area.width);
    const height = Math.min(WINDOW_HEIGHT, area.height);
    const x = position === 'middle-left' ? area.x
      : position === 'middle-right' ? area.x + area.width - width
      : area.x + (area.width - width) / 2;
    const y = isSide(position) ? area.y + (area.height - height) / 2 : area.y + area.height - height;
    return {x: Math.round(x), y: Math.round(y), width, height};
  }

  // Where the recording capsule appears inside that window; also the drop
  // target the dock preview shows while dragging.
  function dockBounds(position, area) {
    const window = pillBounds(position, area);
    const width = Math.min(isSide(position) ? ACTIVE_THICKNESS : ACTIVE_LENGTH, window.width);
    const height = Math.min(isSide(position) ? ACTIVE_LENGTH : ACTIVE_THICKNESS, window.height);
    const gapX = Math.min(EDGE_GAP, window.width - width), gapY = Math.min(EDGE_GAP, window.height - height);
    const x = position === 'middle-left' ? window.x + gapX
      : position === 'middle-right' ? window.x + window.width - gapX - width
      : window.x + (window.width - width) / 2;
    const y = isSide(position) ? window.y + (window.height - height) / 2 : window.y + window.height - gapY - height;
    return {
      x: Math.round(clamp(x, area.x, area.x + area.width - width)),
      y: Math.round(clamp(y, area.y, area.y + area.height - height)),
      width, height,
    };
  }

  // Preview highlighting and release both call this exact nearest-dock rule.
  function candidate(point, area) {
    let nearest = POSITIONS[0], distance = Infinity;
    for (const position of POSITIONS) {
      const dock = dockBounds(position, area);
      const squared = (point.x - dock.x - dock.width / 2) ** 2 + (point.y - dock.y - dock.height / 2) ** 2;
      if (squared < distance) { nearest = position; distance = squared; }
    }
    return nearest;
  }

  // cubic-bezier timing function, solved the way CSS does it.
  function cubicBezier(x1, y1, x2, y2) {
    const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
    const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
    const curveX = t => ((ax * t + bx) * t + cx) * t;
    const curveY = t => ((ay * t + by) * t + cy) * t;
    const slopeX = t => (3 * ax * t + 2 * bx) * t + cx;
    return (x) => {
      if (x <= 0) return 0;
      if (x >= 1) return 1;
      let t = x;
      for (let i = 0; i < 8; i++) {
        const error = curveX(t) - x;
        if (Math.abs(error) < 1e-5) return curveY(t);
        const slope = slopeX(t);
        if (Math.abs(slope) < 1e-6) break;
        t -= error / slope;
      }
      let lo = 0, hi = 1;
      t = x;
      while (hi - lo > 1e-5) {
        if (curveX(t) < x) lo = t; else hi = t;
        t = (lo + hi) / 2;
      }
      return curveY(t);
    };
  }
  // Strong ease-out: the glide leaves at the speed of the hand that let go.
  const easeOut = cubicBezier(0.23, 1, 0.32, 1);

  // Short hops stay quick; a glide across the screen still lands under 300 ms.
  const glideMs = distance => Math.round(clamp(170 + distance * 0.06, 180, 280));

  return {POSITIONS, EDGE_GAP, ACTIVE_LENGTH, ACTIVE_THICKNESS, WINDOW_WIDTH, WINDOW_HEIGHT,
    pillBounds, dockBounds, candidate, isSide, cubicBezier, easeOut, glideMs};
});
