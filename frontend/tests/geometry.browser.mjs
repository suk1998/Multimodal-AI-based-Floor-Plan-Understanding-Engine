import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
process.argv[3]??=resolve(fileURLToPath(new URL('../../',import.meta.url)),'geometry_validation','external');process.argv.push('--observation-regression','--geometry-regression');await import('./upload.browser.mjs');
