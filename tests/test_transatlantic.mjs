import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

// Test HTML files include Transatlantic navigation
const indexHtml = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');
const etfsHtml = fs.readFileSync(path.join(root, 'static', 'etfs.html'), 'utf8');
const transatlanticHtml = fs.readFileSync(path.join(root, 'static', 'transatlantic.html'), 'utf8');

assert(indexHtml.includes('<a href="/transatlantic.html">Transatlantic 50</a>'), 'index.html missing Transatlantic link');
assert(etfsHtml.includes('<a href="/transatlantic.html">Transatlantic 50</a>'), 'etfs.html missing Transatlantic link');
assert(transatlanticHtml.includes('<a href="/transatlantic.html" aria-current="page">Transatlantic 50</a>'), 'transatlantic.html missing active link');
assert(transatlanticHtml.includes('content="transatlantic"'), 'transatlantic.html missing universe meta');
assert(transatlanticHtml.includes('id="quadrant-section" class="active"'), 'transatlantic.html should default to quadrant section active');

// Test universe module with transatlantic document meta
globalThis.document = { querySelector: selector => selector.includes('atlas-universe') ? { content: 'transatlantic' } : null };
const { universeMode, isTransatlantic, transatlanticQuadrantDefaults, defaultSort, storageKey } = await import('../static/universe.js');
assert.equal(universeMode, 'transatlantic');
assert.equal(isTransatlantic, true);
assert.equal(defaultSort, 'rank');
assert.equal(storageKey('atlas.last.v1'), 'atlas.transatlantic.last.v1');
assert.equal(transatlanticQuadrantDefaults.x, 'net_income', 'Transatlantic X axis should default to net_income');
assert.equal(transatlanticQuadrantDefaults.y, 'williams_r', 'Transatlantic Y axis should default to williams_r');
assert.equal(transatlanticQuadrantDefaults.xPrefer, 'higher', 'Transatlantic X axis should prefer higher net income');
assert.equal(transatlanticQuadrantDefaults.yPrefer, 'lower', 'Transatlantic Y axis should prefer lower Williams %R');
assert.equal(transatlanticQuadrantDefaults.xScale, 'symlog', 'Transatlantic X scale should be symlog');
assert.equal(transatlanticQuadrantDefaults.yScale, 'linear', 'Transatlantic Y scale should be linear');

// Test quadrant quick preset buttons in quadrant.js
const quadrantJs = fs.readFileSync(path.join(root, 'static', 'quadrant.js'), 'utf8');
assert(quadrantJs.includes('quad-preset-weekly'), 'quadrant.js missing quad-preset-weekly');
assert(quadrantJs.includes('quad-preset-monthly'), 'quadrant.js missing quad-preset-monthly');

console.log('Transatlantic 50 navigation, quadrant defaults and preset controls passed');
