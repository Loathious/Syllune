// Geometry contracts keep the offered dock and the released placement identical.
// Fixtures include negative-origin and unusually small work areas.
const test = require('node:test');
const assert = require('node:assert/strict');
const geometry = require('../dock-geometry');
const area = {x: -1920, y: -100, width: 1920, height: 1040};

test('every dock lies along its edge in one window size, inside a negative-origin display', () => {
  for (const position of geometry.POSITIONS) {
    const dock = geometry.dockBounds(position, area);
    const window = geometry.pillBounds(position, area);
    assert.ok(dock.x >= area.x && dock.y >= area.y);
    assert.ok(dock.x + dock.width <= area.x + area.width);
    assert.ok(dock.y + dock.height <= area.y + area.height);
    assert.ok(window.x >= area.x && window.y >= area.y);
    assert.ok(window.x + window.width <= area.x + area.width);
    assert.ok(window.y + window.height <= area.y + area.height);
    assert.ok(geometry.isSide(position) ? dock.height > dock.width : dock.width > dock.height);
    assert.deepEqual([window.width, window.height], [geometry.WINDOW_WIDTH, geometry.WINDOW_HEIGHT]);
  }
});

test('a release over each visible preview selects that preview, including negative coordinates', () => {
  const fixtures = [
    [{x:-960,y:899}, 'bottom-center'],
    [{x:-1879,y:420}, 'middle-left'],
    [{x:-41,y:420}, 'middle-right'],
  ];
  for (const [point, expected] of fixtures) assert.equal(geometry.candidate(point, area), expected);
});

test('side window and bottom window align their visual capsule with the preview', () => {
  assert.deepEqual(geometry.dockBounds('bottom-center', area), {x:-988,y:890,width:56,height:26});
  assert.deepEqual(geometry.dockBounds('middle-left', area), {x:-1896,y:392,width:26,height:56});
  assert.deepEqual(geometry.dockBounds('middle-right', area), {x:-50,y:392,width:26,height:56});
  assert.deepEqual(geometry.pillBounds('middle-right', area), {x:-420,y:340,width:420,height:160});
});

test('small work areas clamp all native windows and preview geometry', () => {
  const small = {x:230, y:-600, width:110, height:180};
  for (const position of geometry.POSITIONS) {
    for (const rect of [geometry.pillBounds(position, small), geometry.dockBounds(position, small)]) {
      assert.ok(rect.width > 0 && rect.height > 0);
      assert.ok(rect.x >= 230 && rect.y >= -600);
      assert.ok(rect.x + rect.width <= 340 && rect.y + rect.height <= -420);
    }
    const dock = geometry.dockBounds(position, small);
    assert.equal(geometry.candidate({x:dock.x + dock.width/2,y:dock.y+dock.height/2}, small), position);
  }
});

test('the glide eases out and stays under 300 ms however far it goes', () => {
  assert.equal(geometry.easeOut(0), 0);
  assert.equal(geometry.easeOut(1), 1);
  assert.ok(geometry.easeOut(0.25) > 0.6);
  assert.equal(geometry.glideMs(0), 180);
  assert.equal(geometry.glideMs(5000), 280);
});
