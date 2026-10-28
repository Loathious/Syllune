// Version comparisons must not offer older/unparseable releases as upgrades.
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {newer, releaseURL} = require('../release-updates');
test('release version ordering', () => {
  assert.equal(newer('v0.24.1', '0.24.0'), true);
  assert.equal(newer('v0.24.0', '0.24.0'), false);
  assert.equal(newer('v0.23.9', '0.24.0'), false);
  assert.equal(newer('invalid', '0.24.0'), false);
});
test('release page is the configured Syllune repository', () => {
  assert.equal(releaseURL(), 'https://github.com/Loathious/Syllune/releases');
});
