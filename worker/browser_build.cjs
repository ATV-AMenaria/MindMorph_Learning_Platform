// Trusted compiler; submitted source is parsed/bundled but never executed in Node.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const entry = path.resolve('/work', process.argv[2]);
const loader = '/work/.mm-loader.tsx';
fs.writeFileSync(loader, `import React from 'react'; import {createRoot} from 'react-dom/client'; import App from ${JSON.stringify(entry)}; createRoot(document.getElementById('root')!).render(<App/>);`);
const result = spawnSync('tsc', ['--noEmit', '--strict', '--jsx', 'react-jsx', '--target', 'ES2022',
  '--moduleResolution', 'node', '--module', 'commonjs', '--esModuleInterop',
  '--typeRoots', '/opt/mm/node_modules/@types', '--baseUrl', '/opt/mm/node_modules', '--allowImportingTsExtensions', entry, loader],
  { encoding: 'utf8', timeout: 25000, maxBuffer: 65536 });
if (result.status !== 0) { process.stderr.write((result.stdout || result.stderr || 'Type checking failed').slice(0, 60000)); process.exit(1); }
try {
  require('esbuild').buildSync({ entryPoints: [loader], outfile: '/work/.mm-bundle.js', bundle: true,
    platform: 'browser', format: 'iife', jsx: 'automatic', nodePaths: ['/opt/mm/node_modules'],
    define: { 'process.env.NODE_ENV': '"production"' }, logLevel: 'silent' });
} catch (error) { process.stderr.write(String(error.message).slice(0, 60000)); process.exit(1); }

