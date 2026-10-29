const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/js/article.js'), 'utf8');
function run(render) {
  const sections = ['overview', 'method', 'experiments', 'conclusion'].map(id => ({id}));
  const document = {
    readyState: 'complete',
    querySelector: selector => selector === '.article-content' ? sections[0] : null,
    querySelectorAll: selector => selector === '.article-content' ? sections : [],
    getElementById: () => null,
    addEventListener: () => {},
  };
  vm.runInNewContext(source, {document, window: {renderMathInElement: render}});
}
test('renders math in every research section', () => {
  const rendered = [];
  run(section => rendered.push(section.id));
  assert.deepEqual(rendered, ['overview', 'method', 'experiments', 'conclusion']);
});
test('an invalid formula in one section does not block later sections', () => {
  const rendered = [];
  run(section => {
    if (section.id === 'method') throw new Error('Invalid formula');
    rendered.push(section.id);
  });
  assert.deepEqual(rendered, ['overview', 'experiments', 'conclusion']);
});
test('missing math library leaves raw content usable', () => {
  assert.doesNotThrow(() => run(undefined));
});
