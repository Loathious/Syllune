// Geometry contracts keep the offered dock and the released placement identical.
// Fixtures include negative-origin and unusually small work areas.
const test = require('node:test');
const assert = require('node:assert/strict');
const geometry = require('../dock-geometry');
const area = {x: -1920, y: -100, width: 1920, height: 1040};

test('all docks use the correct orientation and stay inside a negative-origin display', () => {
  for (const position of geometry.POSITIONS) {
    const dock = geometry.dockBounds(position, area);
    const window = geometry.pillBounds(position, area);
    assert.ok(dock.x >= area.x && dock.y >= area.y);
    assert.ok(dock.x + dock.width <= area.x + area.width);
    assert.ok(dock.y + dock.height <= area.y + area.height);
    assert.ok(window.x >= area.x && window.y >= area.y);
    assert.ok(window.x + window.width <= area.x + area.width);
    assert.ok(window.y + window.height <= area.y + area.height);
    assert.equal(dock.width > dock.height, position === 'bottom-center');
    assert.equal(window.width > window.height, position === 'bottom-center');
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
  assert.deepEqual(geometry.dockBounds('bottom-center', area), {x:-1012,y:882,width:104,height:34});
  assert.deepEqual(geometry.dockBounds('middle-left', area), {x:-1896,y:368,width:34,height:104});
  assert.deepEqual(geometry.pillBounds('middle-right', area), {x:-160,y:210,width:160,height:420});
});

test('small work areas clamp all native windows and preview geometry without losing orientation', () => {
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
