export function fitRect(size, viewport, view = { scale: 1, x: 0, y: 0 }) {
  const ratio = Math.min(viewport.w / size.w, viewport.h / size.h) * view.scale;
  const w = size.w * ratio, h = size.h * ratio;
  return { x: (viewport.w - w) / 2 + view.x, y: (viewport.h - h) / 2 + view.y, w, h };
}

export function limitView(view, size, viewport) {
  const scale = Math.max(1, Math.min(4, view.scale));
  const r = fitRect(size, viewport, { scale, x: 0, y: 0 });
  const dx = Math.max(0, (r.w - viewport.w) / 2), dy = Math.max(0, (r.h - viewport.h) / 2);
  return { scale, x: Math.max(-dx, Math.min(dx, view.x)) || 0, y: Math.max(-dy, Math.min(dy, view.y)) || 0 };
}

export function zoomView(view, scale, point, size, viewport) {
  scale = Math.max(1, Math.min(4, scale));
  const k = scale / view.scale;
  return limitView({ scale,
    x: point.x - viewport.w / 2 - (point.x - viewport.w / 2 - view.x) * k,
    y: point.y - viewport.h / 2 - (point.y - viewport.h / 2 - view.y) * k }, size, viewport);
}

// Only exposed edges of the rectangle union; shared and internal edges disappear.
export function outlineSegments(boxes) {
  if (!boxes.length) return [];
  const xs = [...new Set(boxes.flatMap(b => [b[0], b[2]]))].sort((a, b) => a - b);
  const ys = [...new Set(boxes.flatMap(b => [b[1], b[3]]))].sort((a, b) => a - b);
  const filled = xs.slice(1).map((x, i) => ys.slice(1).map((y, j) =>
    boxes.some(b => (xs[i] + x) / 2 > b[0] && (xs[i] + x) / 2 < b[2]
      && (ys[j] + y) / 2 > b[1] && (ys[j] + y) / 2 < b[3])));
  const edges = [];
  for (let i = 0; i < xs.length - 1; i++) {
    for (let j = 0; j < ys.length - 1; j++) {
      if (!filled[i][j]) continue;
      if (!filled[i - 1]?.[j]) edges.push([xs[i], ys[j], xs[i], ys[j + 1]]);
      if (!filled[i + 1]?.[j]) edges.push([xs[i + 1], ys[j], xs[i + 1], ys[j + 1]]);
      if (!filled[i][j - 1]) edges.push([xs[i], ys[j], xs[i + 1], ys[j]]);
      if (!filled[i][j + 1]) edges.push([xs[i], ys[j + 1], xs[i + 1], ys[j + 1]]);
    }
  }
  return edges;
}

// Callouts live outside all visible object regions, not just outside other labels.
export function layoutLabels(labels, bounds) {
  if (![bounds.left, bounds.right, bounds.top, bounds.bottom].every(Number.isFinite)
      || bounds.right <= bounds.left || bounds.bottom <= bounds.top) return [];
  labels = labels.filter(l => Number.isFinite(l.w) && l.w > 0
    && l.box?.length === 4 && l.box.every(Number.isFinite) && l.box[2] > l.box[0] && l.box[3] > l.box[1]);
  const placed = [], result = [];
  const overlaps = (a, b, gap = 6) => a.x < b.x+b.w+gap && a.x+a.w+gap > b.x && a.y < b.y+b.h+gap && a.y+a.h+gap > b.y;
  const objects = labels.map(l => ({ x: l.box[0], y: l.box[1], w: l.box[2]-l.box[0], h: l.box[3]-l.box[1] }));
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const ordered = [...labels].sort((a,b) => (a.box[0]+a.box[2])-(b.box[0]+b.box[2]) || a.number-b.number);
  for (const label of ordered) {
    let found;
    for (const mode of ['name', 'number', 'edge']) {
      const numbered = mode !== 'name', w = numbered ? 36 : label.w, h = 40;
      if (w > bounds.right-bounds.left || h > bounds.bottom-bounds.top) continue;
      const xs = [bounds.left, bounds.right-w, (label.box[0]+label.box[2]-w)/2];
      const ys = [bounds.top, bounds.bottom-h];
      for (const r of [...objects, ...placed]) {
        xs.push(r.x-w-10, r.x+r.w+10, r.x, r.x+r.w-w);
        ys.push(r.y-h-10, r.y+r.h+10);
      }
      for (let i=0; i<80; i++) { const x=bounds.left+i*(w+8); if(x+w>bounds.right) break; xs.push(x); }
      for (let i=0; i<80; i++) { const y=bounds.top+i*(h+8); if(y+h>bounds.bottom) break; ys.push(y); }
      const candidates = [];
      for (const x of new Set(xs.map(x => clamp(x,bounds.left,bounds.right-w)))) {
        for (const y of new Set(ys.map(y => clamp(y,bounds.top,bounds.bottom-h)))) {
          const r = {x,y,w,h,numbered};
          if (placed.some(p => overlaps(r,p))) continue;
          if (mode !== 'edge' && objects.some(p => overlaps(r,p,10))) continue;
          const point = [clamp(x+w/2,label.box[0],label.box[2]),clamp(y+h/2,label.box[1],label.box[3])];
          const distance = Math.hypot(x+w/2-point[0], y+h/2-point[1]);
          candidates.push({...r, score: distance});
        }
      }
      candidates.sort((a,b) => a.score-b.score || a.y-b.y || a.x-b.x);
      if (candidates.length) { found=candidates[0]; break; }
    }
    if (found) { placed.push(found); result.push({...label,...found}); }
  }
  return result;
}

export function calloutLeader(label) {
  const [x1,y1,x2,y2] = label.box, cx=label.x+label.w/2, cy=label.y+label.h/2;
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  let ex=clamp(cx,x1,x2), ey=clamp(cy,y1,y2);
  if (cx>=x1 && cx<=x2 && cy>=y1 && cy<=y2) {
    const edges=[[x1,cy],[x2,cy],[cx,y1],[cx,y2]].sort((a,b)=>Math.hypot(a[0]-cx,a[1]-cy)-Math.hypot(b[0]-cx,b[1]-cy));
    [ex,ey]=edges[0];
  }
  const dx=ex-cx,dy=ey-cy;
  const t=1/Math.max(Math.abs(dx)/(label.w/2),Math.abs(dy)/(label.h/2),1);
  return [cx+dx*t,cy+dy*t,ex,ey];
}
