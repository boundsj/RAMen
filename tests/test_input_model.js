const assert = require('node:assert/strict');
const { load } = require('./load_js');
const I = load('InputModel.js');
assert.equal(I.valid('雪 🍜', 4), true);
assert.equal(I.valid('雪 🍜', 3), false, 'limits match UTF-16 cursor offsets');
for (const text of ['two\nlines', 'tab\t', 'nul\0', '\u2028', '\u2029', '\x7f'])
  assert.equal(I.valid(text, 2048), false, 'single-line plain text only');
assert.equal(I.valid('<b>plain</b>', 128), true);
assert.equal(I.replace('abc', 1, 2, '雪', 3).text, 'a雪c');
assert.equal(I.replace('abc', 1, 2, '🍜', 4).cursor, 3);
assert.equal(I.replace('abc', 1, 2, 'too long', 4), null, 'whole paste refused, never truncated');
for (const [a,b] of [[-1,0],[0,4],[2,1],[1.5,2]])
  assert.equal(I.replace('abc', a, b, 'x', 10), null);
console.log('Input model tests passed');
