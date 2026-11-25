import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
process.argv[3]??=resolve(fileURLToPath(new URL('../../',import.meta.url)),'spatial_validation','external');process.argv.push('--observation-regression','--geometry-regression','--result-retention-regression','--spatial-regression');await import('./upload.browser.mjs');
