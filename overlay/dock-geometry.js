// One geometry contract for native pill windows, visible dock previews and release.
// All values are device-independent screen coordinates, including negative origins.
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.DockGeometry = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, () => {
  const POSITIONS = ['bottom-center', 'middle-left', 'middle-right'];
  const EDGE_GAP = 24, LONG = 420, CROSS = 160;
  const ACTIVE_LENGTH = 104, ACTIVE_THICKNESS = 34;
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const isSide = position => position !== 'bottom-center';

  // Clamp the complete native window, rather than just its visible capsule.
  function pillBounds(position, area) {
    const side = isSide(position);
    const width = Math.min(side ? CROSS : LONG, area.width);
    const height = Math.min(side ? LONG : CROSS, area.height);
    return {
      x: Math.round(position === 'middle-left' ? area.x : position === 'middle-right' ? area.x + area.width - width : area.x + (area.width - width) / 2),
      y: Math.round(side ? area.y + (area.height - height) / 2 : area.y + area.height - height),
      width, height,
    };
  }

  // These are the active capsule bounds, also used for the visible drop offer.
  function dockBounds(position, area) {
    const window = pillBounds(position, area), side = isSide(position);
    const length = Math.min(ACTIVE_LENGTH, side ? window.height : window.width);
    const thickness = Math.min(ACTIVE_THICKNESS, Math.max(1, (side ? window.width : window.height) - EDGE_GAP));
    const width = side ? thickness : length, height = side ? length : thickness;
    let x = position === 'middle-left' ? window.x + EDGE_GAP : position === 'middle-right' ? window.x + window.width - EDGE_GAP - width : window.x + (window.width - width) / 2;
    let y = side ? window.y + (window.height - height) / 2 : window.y + window.height - EDGE_GAP - height;
    return {x:Math.round(clamp(x, area.x, area.x + area.width - width)), y:Math.round(clamp(y, area.y, area.y + area.height - height)), width, height};
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
  return {POSITIONS, EDGE_GAP, ACTIVE_LENGTH, ACTIVE_THICKNESS, WINDOW_LENGTH:LONG, WINDOW_CROSS:CROSS, pillBounds, dockBounds, candidate, isSide};
});
