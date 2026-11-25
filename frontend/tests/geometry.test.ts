import assert from "node:assert/strict";
import {geometry} from "../src/geometry.ts";
import {createRequire} from "node:module";

// Run the existing eight cases against the exact implementation imported by React.
const require = createRequire(import.meta.url);
const old = require("../../phase1_viewer_frontend/geometry.js");
assert.equal(geometry, old);
require("../../tests/phase1_viewport_test.cjs");
console.log("PASS TypeScript facade reuses the original JavaScript function object.");
