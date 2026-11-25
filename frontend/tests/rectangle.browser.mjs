// Extends the real uploaded-PDF browser suite with rectangle-specific pointer scenarios.
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';
process.argv[3]??=resolve(fileURLToPath(new URL('../../',import.meta.url)),'rectangle_roi_validation',process.argv[2]?.startsWith('https:')?'external':'local');
process.argv.push('--rectangle-regression');
await import('./upload.browser.mjs');
