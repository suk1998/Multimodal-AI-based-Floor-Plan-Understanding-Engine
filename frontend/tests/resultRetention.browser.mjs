import {fileURLToPath} from 'node:url';import {resolve} from 'node:path';
process.argv[3]??=resolve(fileURLToPath(new URL('../../',import.meta.url)),'roi_result_retention_validation','external');process.argv.push('--observation-regression','--geometry-regression','--result-retention-regression');await import('./upload.browser.mjs');
