import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.join(__dirname, '..');

const indexHtml = fs.readFileSync(path.join(root, 'static', 'index.html'), 'utf8');
const etfsHtml = fs.readFileSync(path.join(root, 'static', 'etfs.html'), 'utf8');
const transatlanticHtml = fs.readFileSync(path.join(root, 'static', 'transatlantic.html'), 'utf8');
const winnerHtml = fs.readFileSync(path.join(root, 'static', 'winner.html'), 'utf8');

// Assert navigation links across pages
assert(indexHtml.includes('<a href="/winner.html">Last Year\'s Winner</a>'), 'index.html missing Winner link');
assert(etfsHtml.includes('<a href="/winner.html">Last Year\'s Winner</a>'), 'etfs.html missing Winner link');
assert(transatlanticHtml.includes('<a href="/winner.html">Last Year\'s Winner</a>'), 'transatlantic.html missing Winner link');
assert(winnerHtml.includes('<a href="/winner.html" class="px-3 py-1.5 rounded-md bg-[#e8f0fe] text-[#1a73e8]" aria-current="page">Last Year\'s Winner</a>'), 'winner.html missing active link');

// Assert key strategy contents in winner.html
assert(winnerHtml.includes('Hold Last Year\'s Winner'), 'winner.html missing headline');
assert(winnerHtml.includes('SNDK'), 'winner.html missing active SNDK holding');
assert(winnerHtml.includes('$1,012.51'), 'winner.html missing stop loss threshold');
assert(winnerHtml.includes('MRNA'), 'winner.html missing 2027 leader Moderna');
assert(winnerHtml.includes('+716.3% YTD'), 'winner.html missing Moderna return');
assert(winnerHtml.includes('+477.4% YTD'), 'winner.html missing SanDisk return');
assert(winnerHtml.includes('Google Sans'), 'winner.html missing Google Sans font styling');

console.log('Hold Last Year\'s Winner navigation, Google CSS styling and strategy content passed');
