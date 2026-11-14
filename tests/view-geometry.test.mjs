import assert from 'node:assert/strict';
import { fitRect, limitView, zoomView, outlineSegments } from '../frontend/view-geometry.mjs';

const perimeter = boxes => outlineSegments(boxes).reduce((n, [x1, y1, x2, y2]) => n + Math.abs(x2 - x1) + Math.abs(y2 - y1), 0);
assert.equal(perimeter([[0, 0, 10, 10]]), 40);
assert.equal(perimeter([[0, 0, 10, 10], [2, 2, 8, 8]]), 40, 'Nested defect does not add another outline');
assert.equal(perimeter([[0, 0, 10, 10], [5, 0, 15, 10]]), 50, 'Overlap has no internal edges');
assert.equal(perimeter([[0, 0, 10, 10], [10, 0, 20, 10]]), 60, 'Touching boxes share one outside contour');
assert.equal(perimeter([[0, 0, 10, 10], [20, 0, 30, 10]]), 80);
assert.deepEqual(outlineSegments([]), []);

const size = { w: 1200, h: 800 }, viewport = { w: 390, h: 844 };
const initial = { scale: 1, x: 0, y: 0 };
const before = fitRect(size, viewport, initial);
const point = { x: 245, y: 422 };
const zoomed = zoomView(initial, 2, point, size, viewport);
const after = fitRect(size, viewport, zoomed);
assert.ok(Math.abs((point.x - before.x) / before.w - (point.x - after.x) / after.w) < 1e-9, 'Zoom keeps the point under the cursor');
assert.equal(zoomed.scale, 2);
assert.deepEqual(zoomView(zoomed, 1, point, size, viewport), initial);
assert.deepEqual(limitView({ scale: 1, x: 9999, y: -9999 }, size, viewport), initial);
assert.equal(limitView({ scale: 999, x: 0, y: 0 }, size, viewport).scale, 4);
console.log('View geometry: union contours, zoom anchor, reset and pan limits passed.');
