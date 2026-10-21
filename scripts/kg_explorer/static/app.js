const $ = (id) => document.getElementById(id);
const nf = (n) => Number(n).toLocaleString("ko-KR");
const esc = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const COLORS = [
  "#258a72",
  "#6c9b86",
  "#6693b4",
  "#af956a",
  "#60817d",
  "#9a89b4",
  "#73a3a6",
  "#89965c",
  "#527e75",
  "#8d9db8",
  "#acaa7b",
  "#b3a470",
  "#be8b87",
];
const RGB = COLORS.map((c) =>
  [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16) / 255),
);
const LABELS = [
  "소재",
  "투입물 표현",
  "공정명 표현",
  "산출물 표현",
  "변환 관계 주장",
  "자료 출처",
  "근거 기록",
  "범위가 있는 주장",
  "공정 후보",
  "공정 단계",
  "연결 구간",
  "적용 조건",
  "리스크·확인 과제",
];
const schemaPositions = [
  [-960, -360],
  [-600, -360],
  [-240, -360],
  [120, -360],
  [-240, 0],
  [840, 0],
  [480, 0],
  [-600, 0],
  [-600, 360],
  [-240, 360],
  [120, 360],
  [-960, 360],
  [480, 360],
];
let G,
  positions,
  adjacency,
  degrees,
  groups,
  idIndex,
  activeKinds,
  activeRelations;
let mode = "schema",
  selected = null,
  selectedKind = null,
  selectedEdge = null;
let visible = [],
  visibleEdges = [],
  visibleSet = new Set(),
  localDepth = 2,
  localLimited = false,
  detailSequence = 0;
let width = 1,
  height = 1,
  dpr = 1,
  cam = { x: 0, y: 0, scale: 1 },
  fitScale = 1,
  dirty = true,
  hover = null,
  pointer = null;
const canvas = $("graph"),
  overlay = $("labels"),
  stage = $("graph-stage"),
  ctx = overlay.getContext("2d");
let gl = canvas.getContext("webgl", {
  alpha: true,
  antialias: true,
  preserveDrawingBuffer: true,
});
let fallback = null,
  pointProgram,
  lineProgram,
  pointBuffer,
  lineBuffer,
  pointCount = 0,
  lineCount = 0,
  pointsData,
  linesData;
const overviewHTML = $("inspector-content").innerHTML;

function hash(s) {
  let x = 2166136261;
  for (let i = 0; i < s.length; i++) {
    x ^= s.charCodeAt(i);
    x = Math.imul(x, 16777619);
  }
  return x >>> 0;
}
function screen(p) {
  return [
    (p[0] - cam.x) * cam.scale + width / 2,
    (p[1] - cam.y) * cam.scale + height / 2,
  ];
}
function world(x, y) {
  return [
    (x - width / 2) / cam.scale + cam.x,
    (y - height / 2) / cam.scale + cam.y,
  ];
}
function nodePoint(i) {
  return [positions[i * 2], positions[i * 2 + 1]];
}
function shader(type, source) {
  const s = gl.createShader(type);
  gl.shaderSource(s, source);
  gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
    throw Error(gl.getShaderInfoLog(s));
  return s;
}
function program(points) {
  const p = gl.createProgram();
  gl.attachShader(
    p,
    shader(
      gl.VERTEX_SHADER,
      `attribute vec2 aPos;attribute vec4 aColor;attribute float aSize;uniform vec2 uCam,uView;uniform float uScale,uDpr;varying vec4 vColor;void main(){vec2 q=(aPos-uCam)*uScale+uView/2.0;gl_Position=vec4(q.x/uView.x*2.0-1.0,1.0-q.y/uView.y*2.0,0,1);gl_PointSize=aSize*uDpr;vColor=aColor;}`,
    ),
  );
  gl.attachShader(
    p,
    shader(
      gl.FRAGMENT_SHADER,
      `precision mediump float;varying vec4 vColor;void main(){${points ? "vec2 d=gl_PointCoord-vec2(0.5);float r=length(d);if(r>0.5)discard;float alpha=vColor.a*(1.0-smoothstep(0.35,0.5,r));gl_FragColor=vec4(vColor.rgb*alpha,alpha);" : "gl_FragColor=vec4(vColor.rgb*vColor.a,vColor.a);"}}`,
    ),
  );
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS))
    throw Error(gl.getProgramInfoLog(p));
  return p;
}
function initRenderer() {
  if (gl) {
    pointProgram = program(true);
    lineProgram = program(false);
    pointBuffer = gl.createBuffer();
    lineBuffer = gl.createBuffer();
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    $("renderer-badge").textContent = "WEBGL · GPU CANVAS";
  } else {
    fallback = canvas.getContext("2d");
    $("renderer-badge").textContent = "CANVAS 2D · FALLBACK";
  }
}
function resize() {
  width = stage.clientWidth;
  height = stage.clientHeight;
  dpr = Math.min(devicePixelRatio || 1, 2);
  for (const c of [canvas, overlay]) {
    c.width = Math.round(width * dpr);
    c.height = Math.round(height * dpr);
  }
  if (gl) gl.viewport(0, 0, canvas.width, canvas.height);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  dirty = true;
}
function bind(p, buffer, data, points) {
  gl.useProgram(p);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  const stride = 28;
  for (const [name, n, offset] of [
    ["aPos", 2, 0],
    ["aColor", 4, 8],
    ["aSize", 1, 24],
  ]) {
    const loc = gl.getAttribLocation(p, name);
    if (loc >= 0) {
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, n, gl.FLOAT, false, stride, offset);
    }
  }
  gl.uniform2f(gl.getUniformLocation(p, "uCam"), cam.x, cam.y);
  gl.uniform2f(gl.getUniformLocation(p, "uView"), width, height);
  gl.uniform1f(gl.getUniformLocation(p, "uScale"), cam.scale);
  gl.uniform1f(gl.getUniformLocation(p, "uDpr"), dpr);
  gl.drawArrays(
    points ? gl.POINTS : gl.LINES,
    0,
    points ? pointCount : lineCount,
  );
}
function rebuildBuffers() {
  if (!G) return;
  pointsData = new Float32Array(visible.length * 7);
  let j = 0;
  for (const i of visible) {
    const n = G.nodes[i],
      c = RGB[n[2]],
      size =
        mode === "local"
          ? Math.min(14, 5 + Math.log2(n[3] + 1) * 0.7)
          : Math.min(10, 2.1 + Math.log2(n[3] + 1) * 0.45);
    pointsData.set(
      [
        positions[i * 2],
        positions[i * 2 + 1],
        ...c,
        mode === "local" ? 0.95 : 0.76,
        size,
      ],
      j,
    );
    j += 7;
  }
  pointCount = visible.length;
  linesData = new Float32Array(visibleEdges.length * 14);
  j = 0;
  for (const ei of visibleEdges) {
    const e = G.edges[ei],
      c = RGB[G.nodes[e[0]][2]],
      alpha = mode === "local" ? 0.24 : 0.004;
    linesData.set(
      [
        positions[e[0] * 2],
        positions[e[0] * 2 + 1],
        ...c,
        alpha,
        1,
        positions[e[1] * 2],
        positions[e[1] * 2 + 1],
        ...c,
        alpha,
        1,
      ],
      j,
    );
    j += 14;
  }
  lineCount = visibleEdges.length * 2;
  if (gl) {
    gl.bindBuffer(gl.ARRAY_BUFFER, pointBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, pointsData, gl.STATIC_DRAW);
    gl.bindBuffer(gl.ARRAY_BUFFER, lineBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, linesData, gl.STATIC_DRAW);
  }
  dirty = true;
}
function buildPositions() {
  positions = new Float32Array(G.nodes.length * 2);
  groups = G.kinds.map(() => []);
  G.nodes.forEach((n, i) => groups[n[2]].push(i));
  for (let k = 0; k < groups.length; k++) {
    const center = schemaPositions[k],
      ids = groups[k],
      radius = Math.max(30, Math.min(155, Math.sqrt(ids.length) * 1.05));
    for (const i of ids) {
      const seed = hash(G.nodes[i][0]),
        t = ((seed % 100000) / 100000) * Math.PI * 2,
        r = Math.sqrt(((seed >>> 9) % 10000) / 10000) * radius;
      positions[i * 2] = center[0] + Math.cos(t) * r;
      positions[i * 2 + 1] = center[1] + Math.sin(t) * r;
    }
  }
}
function selectNeighborhood() {
  const start = selected ?? idIndex.get("material:pet");
  localLimited = false;
  if (start === undefined) return new Set();
  const found = new Set([start]),
    queue = [[start, 0]],
    limit = 6000;
  for (let q = 0; q < queue.length; q++) {
    const [i, depth] = queue[q];
    if (depth >= localDepth) continue;
    for (const ei of adjacency[i]) {
      const [a, b, r] = G.edges[ei];
      if (!activeRelations.has(r)) continue;
      const other = a === i ? b : a;
      if (!activeKinds.has(G.nodes[other][2])) continue;
      if (!found.has(other)) {
        if (found.size >= limit) {
          localLimited = true;
          continue;
        }
        found.add(other);
        queue.push([other, depth + 1]);
      }
    }
  }
  return found;
}
function localLayout() {
  if (!visible.length) return;
  const ids = visible.filter((i) => i !== selected),
    center = selected;
  let maxRing = 1;
  const distances = new Map([[center, 0]]),
    queue = [center];
  for (let q = 0; q < queue.length; q++) {
    const n = queue[q];
    for (const ei of adjacency[n]) {
      const [a, b, r] = G.edges[ei],
        other = a === n ? b : a;
      if (
        visibleSet.has(other) &&
        activeRelations.has(r) &&
        !distances.has(other)
      ) {
        distances.set(other, distances.get(n) + 1);
        queue.push(other);
        maxRing = Math.max(maxRing, distances.get(other));
      }
    }
  }
  positions[center * 2] = 0;
  positions[center * 2 + 1] = 0;
  const rings = Array.from({ length: maxRing + 1 }, () => []);
  for (const i of ids) rings[distances.get(i) || 1].push(i);
  for (let ring = 1; ring < rings.length; ring++) {
    rings[ring].sort((a, b) => G.nodes[a][2] - G.nodes[b][2] || a - b);
    const n = rings[ring].length,
      r = 110 + ring * 110 + Math.sqrt(n) * 5;
    rings[ring].forEach((i, j) => {
      const t = (j / Math.max(n, 1)) * Math.PI * 2 - Math.PI / 2;
      positions[i * 2] = Math.cos(t) * r;
      positions[i * 2 + 1] = Math.sin(t) * r;
    });
  }
}
function refreshView(fit = true) {
  if (!G) return;
  if (mode === "schema") {
    visible = [];
    visibleEdges = [];
  } else {
    const allowed = mode === "local" ? selectNeighborhood() : null;
    visible = [];
    for (let i = 0; i < G.nodes.length; i++)
      if (activeKinds.has(G.nodes[i][2]) && (!allowed || allowed.has(i)))
        visible.push(i);
    visibleSet = new Set(visible);
    visibleEdges = [];
    G.edges.forEach((e, i) => {
      if (
        activeRelations.has(e[2]) &&
        visibleSet.has(e[0]) &&
        visibleSet.has(e[1])
      )
        visibleEdges.push(i);
    });
    if (mode === "local" && selected !== null) localLayout();
    else buildPositions();
    rebuildBuffers();
  }
  $("empty-state").hidden = mode === "schema" || visible.length > 0;
  updateCounts();
  if (fit) fitView();
  dirty = true;
}
function updateCounts() {
  if (mode === "schema") {
    $("visible-count").textContent =
      `${activeKinds.size}개 유형 · ${G.aggregates.filter(([a, b, r]) => activeKinds.has(a) && activeKinds.has(b) && activeRelations.has(r)).length}개 연결`;
    $("visible-scope").textContent = "전체 데이터 집계";
  } else {
    $("visible-count").textContent =
      `${nf(visible.length)} 노드 · ${nf(visibleEdges.length)} 관계`;
    $("visible-scope").textContent =
      mode === "local"
        ? localLimited
          ? "주변 탐색 · 6,000노드 상한 적용"
          : `선택 노드 ${localDepth}단계 이내`
        : visible.length === G.nodes.length &&
            visibleEdges.length === G.edges.length
          ? "전체 데이터"
          : "필터 적용";
  }
  $("local-count").textContent = mode === "local" ? nf(visible.length) : "선택";
  $("total-count").textContent =
    `전체 ${nf(G.nodes.length)} 노드 / ${nf(G.edges.length)} 관계`;
  $("graph-notice").textContent =
    mode === "schema"
      ? "노드 유형을 클릭하면 실제 노드로 들어갑니다. 엣지는 실제 관계 수를 집계합니다."
      : mode === "all"
        ? "실제 전체 노드를 유형별로 배치했습니다. 위치는 공정 순서를 뜻하지 않습니다."
        : `선택 노드에서 ${localDepth}단계 이내 연결 · 노드를 더블클릭하면 중심을 옮깁니다.${localLimited ? " 탐색 상한 6,000개 적용." : ""}`;
}
function fitView() {
  let pts;
  if (mode === "schema")
    pts = schemaPositions.filter((_, i) => activeKinds.has(i));
  else pts = visible.map(nodePoint);
  if (!pts.length) return;
  let minX = Infinity,
    maxX = -Infinity,
    minY = Infinity,
    maxY = -Infinity;
  for (const [x, y] of pts) {
    minX = Math.min(minX, x);
    maxX = Math.max(maxX, x);
    minY = Math.min(minY, y);
    maxY = Math.max(maxY, y);
  }
  const pad = mode === "schema" ? 110 : 95;
  cam.x = (minX + maxX) / 2;
  cam.y = (minY + maxY) / 2;
  cam.scale = Math.min(
    (width - 55) / (maxX - minX + pad * 2),
    (height - 65) / (maxY - minY + pad * 2),
  );
  cam.scale = Math.max(0.03, cam.scale);
  fitScale = cam.scale;
  updateZoom();
  dirty = true;
}
function updateZoom() {
  $("zoom-value").textContent =
    `${Math.round((cam.scale / Math.max(fitScale, 0.001)) * 100)}%`;
}
function setMode(next) {
  if (!G) return;
  if (next === "local" && selected === null)
    selected = idIndex.get("material:pet") ?? 0;
  mode = next;
  selectedEdge = null;
  for (const el of document.querySelectorAll("[data-mode]"))
    el.classList.toggle("active", el.dataset.mode === mode);
  $("breadcrumb-mode").textContent = {
    schema: "구조 개요",
    all: "전체 노드",
    local: "주변 연결",
  }[mode];
  refreshView();
  if (mode === "local") inspectNode(selected);
}
function drawArrow(x1, y1, x2, y2, color, size = 4) {
  const a = Math.atan2(y2 - y1, x2 - x1);
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(x2 - Math.cos(a - 0.45) * size, y2 - Math.sin(a - 0.45) * size);
  ctx.lineTo(x2 - Math.cos(a + 0.45) * size, y2 - Math.sin(a + 0.45) * size);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
}
function schemaRadius(k) {
  return 35 + Math.min(13, Math.log10(G.kinds[k].count + 1) * 3);
}
function schemaEdgeGeometry(a, b) {
  const p = screen(schemaPositions[a]),
    q = screen(schemaPositions[b]),
    dx = q[0] - p[0],
    dy = q[1] - p[1],
    d = Math.hypot(dx, dy) || 1,
    ra = Math.max(18, schemaRadius(a) * cam.scale),
    rb = Math.max(18, schemaRadius(b) * cam.scale);
  return {
    x1: p[0] + (dx / d) * ra,
    y1: p[1] + (dy / d) * ra,
    x2: q[0] - (dx / d) * rb,
    y2: q[1] - (dy / d) * rb,
    dx,
    dy,
    d,
  };
}
function drawSchema() {
  const relevant =
    selectedKind ?? (hover?.kind === "schema" ? hover.index : null);
  for (let e = 0; e < G.aggregates.length; e++) {
    const [a, b, r, count] = G.aggregates[e];
    if (!activeKinds.has(a) || !activeKinds.has(b) || !activeRelations.has(r))
      continue;
    const { x1, y1, x2, y2, dx, dy, d } = schemaEdgeGeometry(a, b),
      active =
        (relevant !== null && (a === relevant || b === relevant)) ||
        selectedEdge === e ||
        (hover?.kind === "edge" && hover.index === e),
      alpha = relevant !== null && !active ? 0.1 : active ? 0.66 : 0.34;
    ctx.strokeStyle = `rgba(71,111,91,${alpha})`;
    ctx.lineWidth = active
      ? 1.9
      : Math.min(2.3, 0.5 + Math.log10(count + 1) * 0.38);
    const bend = 10 + (r % 3) * 9,
      cx = (x1 + x2) / 2 - (dy / d) * bend,
      cy = (y1 + y2) / 2 + (dx / d) * bend;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.quadraticCurveTo(cx, cy, x2, y2);
    ctx.stroke();
    drawArrow(cx, cy, x2, y2, `rgba(71,111,91,${alpha + 0.1})`, 4);
    if (active) {
      const tx = 0.25 * x1 + 0.5 * cx + 0.25 * x2,
        ty = 0.25 * y1 + 0.5 * cy + 0.25 * y2;
      ctx.font = `${active ? 10 : 8}px 'Segoe UI',sans-serif`;
      const text = active ? `${G.relations[r].id} · ${nf(count)}` : nf(count),
        w = ctx.measureText(text).width;
      ctx.fillStyle = "#f9fbfaee";
      ctx.fillRect(tx - w / 2 - 4, ty - 6, w + 8, 13);
      ctx.fillStyle = active ? "#4d7e63" : "#91a497";
      ctx.textAlign = "center";
      ctx.fillText(text, tx, ty + 3);
    }
  }
  for (let k = 0; k < G.kinds.length; k++) {
    if (!activeKinds.has(k)) continue;
    const [x, y] = screen(schemaPositions[k]),
      radius = Math.max(18, schemaRadius(k) * cam.scale),
      active =
        selectedKind === k || (hover?.kind === "schema" && hover.index === k);
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fillStyle = COLORS[k] + "13";
    ctx.fill();
    ctx.strokeStyle = COLORS[k] + (active ? "dd" : "65");
    ctx.lineWidth = active ? 2 : 1;
    ctx.stroke();
    ctx.textAlign = "center";
    ctx.fillStyle = COLORS[k];
    ctx.font = `500 ${Math.max(12, Math.min(22, radius * 0.53))}px 'Segoe UI',sans-serif`;
    ctx.fillText(nf(G.kinds[k].count), x, y + 4);
    ctx.fillStyle = "#496758";
    ctx.font = "500 11px 'Segoe UI','Malgun Gothic',sans-serif";
    ctx.fillText(LABELS[k], x, y + radius + 17);
    ctx.fillStyle = "#94a59a";
    ctx.font = "9px 'Segoe UI',sans-serif";
    ctx.fillText(G.kinds[k].id, x, y + radius + 31);
  }
}
function drawFallback() {
  fallback.setTransform(dpr, 0, 0, dpr, 0, 0);
  fallback.clearRect(0, 0, width, height);
  fallback.strokeStyle = mode === "all" ? "#618b6a0a" : "#618b6a30";
  fallback.lineWidth = 0.6;
  fallback.beginPath();
  for (const i of visibleEdges) {
    const e = G.edges[i],
      a = screen(nodePoint(e[0])),
      b = screen(nodePoint(e[1]));
    fallback.moveTo(...a);
    fallback.lineTo(...b);
  }
  fallback.stroke();
  for (const i of visible) {
    const n = G.nodes[i],
      p = screen(nodePoint(i));
    if (p[0] < -10 || p[0] > width + 10 || p[1] < -10 || p[1] > height + 10)
      continue;
    fallback.fillStyle = COLORS[n[2]];
    fallback.beginPath();
    fallback.arc(...p, mode === "all" ? 1.4 : 3, 0, Math.PI * 2);
    fallback.fill();
  }
}
function shortened(s, n = 35) {
  return s.length > n ? s.slice(0, n) + "…" : s;
}
function drawSelection() {
  if (selected === null || !visibleSet.has(selected)) return;
  const p = screen(nodePoint(selected));
  const neighbors = new Set();
  ctx.lineWidth = 1.2;
  for (const ei of adjacency[selected]) {
    const e = G.edges[ei];
    if (
      !visibleSet.has(e[0]) ||
      !visibleSet.has(e[1]) ||
      !activeRelations.has(e[2])
    )
      continue;
    const other = e[0] === selected ? e[1] : e[0],
      q = screen(nodePoint(other));
    neighbors.add(other);
    ctx.strokeStyle = "#3b816077";
    ctx.beginPath();
    ctx.moveTo(...p);
    ctx.lineTo(...q);
    ctx.stroke();
    if (mode === "local" && neighbors.size < 70) {
      const a = e[0] === selected ? p : q,
        b = e[0] === selected ? q : p;
      drawArrow(...a, ...b, "#3b8160aa", 4);
    }
  }
  ctx.beginPath();
  ctx.arc(...p, 9, 0, Math.PI * 2);
  ctx.strokeStyle = "#248662";
  ctx.lineWidth = 2;
  ctx.stroke();
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  ctx.arc(...p, 4, 0, Math.PI * 2);
  ctx.fill();
  const labels = [
    selected,
    ...(mode === "local" && visible.length < 120 ? [...neighbors] : []),
  ];
  ctx.font = "10px 'Segoe UI','Malgun Gothic',sans-serif";
  ctx.textAlign = "left";
  let used = [];
  for (const i of labels) {
    const q = screen(nodePoint(i));
    if (q[0] < 0 || q[0] > width || q[1] < 0 || q[1] > height) continue;
    if (
      i !== selected &&
      used.some(
        (t) => Math.abs(t[0] - q[0]) < 120 && Math.abs(t[1] - q[1]) < 14,
      )
    )
      continue;
    used.push(q);
    const t = shortened(G.nodes[i][1], i === selected ? 55 : 28),
      tw = ctx.measureText(t).width;
    ctx.fillStyle = "#fbfdfbf0";
    ctx.fillRect(q[0] + 10, q[1] - 8, tw + 6, 16);
    ctx.fillStyle = i === selected ? "#245c3c" : "#66806b";
    ctx.fillText(t, q[0] + 13, q[1] + 4);
  }
}
function drawLabels() {
  if (mode !== "all") return;
  ctx.textAlign = "center";
  for (let k = 0; k < groups.length; k++) {
    if (!activeKinds.has(k)) continue;
    const [x, y] = screen(schemaPositions[k]);
    ctx.font = "500 10px 'Segoe UI','Malgun Gothic',sans-serif";
    const label = `${LABELS[k]} · ${nf(groups[k].length)}`,
      tw = ctx.measureText(label).width;
    const radius =
      Math.max(30, Math.min(155, Math.sqrt(groups[k].length) * 1.05)) *
      cam.scale;
    ctx.fillStyle = "#fbfdfbed";
    ctx.fillRect(x - tw / 2 - 6, y - radius - 28, tw + 12, 19);
    ctx.fillStyle = "#4e725b";
    ctx.fillText(label, x, y - radius - 15);
  }
}
function render() {
  if (dirty && G) {
    const t = performance.now();
    dirty = false;
    ctx.clearRect(0, 0, width, height);
    if (gl) {
      gl.clearColor(0, 0, 0, 0);
      gl.clear(gl.COLOR_BUFFER_BIT);
    }
    if (mode === "schema") {
      if (fallback) fallback.clearRect(0, 0, canvas.width, canvas.height);
      drawSchema();
    } else {
      if (gl) {
        bind(lineProgram, lineBuffer, linesData, false);
        bind(pointProgram, pointBuffer, pointsData, true);
      } else drawFallback();
      drawLabels();
      drawSelection();
    }
    $("render-time").textContent =
      `${(performance.now() - t).toFixed(0)} ms · ${gl ? "WebGL" : "Canvas 2D"}`;
  }
  requestAnimationFrame(render);
}
function hitTest(x, y) {
  if (!G) return null;
  if (mode === "schema") {
    for (let k = 0; k < G.kinds.length; k++) {
      if (!activeKinds.has(k)) continue;
      const p = screen(schemaPositions[k]);
      if (
        Math.hypot(p[0] - x, p[1] - y) <
        Math.max(18, schemaRadius(k) * cam.scale) + 6
      )
        return { kind: "schema", index: k };
    }
    for (let i = 0; i < G.aggregates.length; i++) {
      const [a, b, r] = G.aggregates[i];
      if (!activeKinds.has(a) || !activeKinds.has(b) || !activeRelations.has(r))
        continue;
      const p = schemaEdgeGeometry(a, b),
        bend = 10 + (r % 3) * 9,
        cx = (p.x1 + p.x2) / 2 - (p.dy / p.d) * bend,
        cy = (p.y1 + p.y2) / 2 + (p.dx / p.d) * bend;
      for (let j = 2; j < 19; j++) {
        const t = j / 20,
          px = (1 - t) ** 2 * p.x1 + 2 * (1 - t) * t * cx + t * t * p.x2,
          py = (1 - t) ** 2 * p.y1 + 2 * (1 - t) * t * cy + t * t * p.y2;
        if (Math.hypot(px - x, py - y) < 6) return { kind: "edge", index: i };
      }
    }
    return null;
  }
  let best = null,
    bestD = mode === "local" ? 100 : 55;
  for (const i of visible) {
    const px = (positions[i * 2] - cam.x) * cam.scale + width / 2,
      py = (positions[i * 2 + 1] - cam.y) * cam.scale + height / 2;
    if (Math.abs(px - x) > 12 || Math.abs(py - y) > 12) continue;
    const d = (px - x) ** 2 + (py - y) ** 2;
    if (d < bestD) {
      bestD = d;
      best = i;
    }
  }
  return best === null ? null : { kind: "node", index: best };
}
function updateTooltip(hit, x, y) {
  const tip = $("tooltip");
  if (!hit) {
    tip.hidden = true;
    return;
  }
  if (hit.kind === "schema") {
    const k = G.kinds[hit.index];
    tip.innerHTML = `${esc(k.label)}<small>${esc(k.id)} · 실제 ${nf(k.count)}개 노드</small>`;
  } else if (hit.kind === "edge") {
    const [a, b, r, n] = G.aggregates[hit.index];
    tip.innerHTML = `${esc(G.relations[r].id)}<small>${esc(LABELS[a])} → ${esc(LABELS[b])} · ${nf(n)}개</small>`;
  } else {
    const n = G.nodes[hit.index];
    tip.innerHTML = `${esc(shortened(n[1], 95))}<small>${esc(G.kinds[n[2]].id)} · 연결 ${nf(n[3])}개</small>`;
  }
  tip.hidden = false;
  tip.style.left = `${Math.max(4, Math.min(x + 15, width - 290))}px`;
  tip.style.top = `${Math.min(y + 15, height - 80)}px`;
}
function inspectKind(k) {
  selectedKind = k;
  selectedEdge = null;
  dirty = true;
  const kind = G.kinds[k],
    links = G.aggregates.filter(([a, b]) => a === k || b === k);
  $("inspector-content").innerHTML =
    `<div class="node-kind" style="color:${COLORS[k]}">${esc(kind.id)}</div><h2 class="node-title">${esc(kind.label)}</h2><p class="intro-copy" style="display:block">이 유형의 실제 노드를 모두 펼쳐 보거나 관계별 연결 수를 확인하세요.</p><div class="details-grid"><div><span>노드</span><strong>${nf(kind.count)}</strong></div><div><span>관계 연결</span><strong>${nf(links.reduce((s, x) => s + x[3], 0))}</strong></div></div><button id="open-kind" class="primary-button">${nf(kind.count)}개 노드 펼쳐 보기 ↗</button><div class="detail-label">실제 관계 집계</div>${links.map(([a, b, r, count]) => `<div class="schema-relation-row">${esc(LABELS[a])} → ${esc(LABELS[b])}<br><strong>${esc(G.relations[r].id)}</strong> · ${nf(count)}</div>`).join("")}<div class="detail-label">노드 예시</div>${groups[
      k
    ]
      .slice(0, 15)
      .map(
        (i) =>
          `<button class="neighbor" data-node="${i}">${esc(shortened(G.nodes[i][1], 65))}<small>연결 ${nf(G.nodes[i][3])}개</small></button>`,
      )
      .join("")}`;
  $("open-kind").onclick = () => {
    activeKinds = new Set([k]);
    syncFilters();
    setMode("all");
  };
  bindNodeButtons();
}
function inspectEdge(i) {
  selectedEdge = i;
  selectedKind = null;
  dirty = true;
  const [a, b, r, n] = G.aggregates[i],
    rel = G.relations[r];
  $("inspector-content").innerHTML =
    `<div class="node-kind">RELATIONSHIP</div><h2 class="node-title">${esc(rel.id)}</h2><div class="details-grid"><div><span>실제 관계 수</span><strong>${nf(n)}</strong></div></div><div class="detail-label">방향</div><p class="detail-line">${esc(G.kinds[a].label)}<br>↓ ${esc(rel.id)}<br>${esc(G.kinds[b].label)}</p><div class="detail-label">온톨로지 정의</div><p class="detail-line">시작 유형: ${esc(rel.domain.join(", "))}<br>도착 유형: ${esc(rel.range.join(", "))}</p><button id="open-relation" class="primary-button">이 관계의 실제 연결 보기 ↗</button>`;
  $("open-relation").onclick = () => {
    activeKinds = new Set([a, b]);
    activeRelations = new Set([r]);
    syncFilters();
    setMode("all");
  };
}
function bindNodeButtons() {
  for (const el of document.querySelectorAll("[data-node]"))
    el.onclick = () => {
      selected = Number(el.dataset.node);
      if (mode === "schema") {
        activeKinds = new Set(G.kinds.map((_, i) => i));
        activeRelations = new Set(G.relations.map((_, i) => i));
        syncFilters();
        setMode("local");
      } else {
        inspectNode(selected);
        dirty = true;
      }
    };
}
function usefulProps(props) {
  const preferred = [
    "statement",
    "text",
    "validation",
    "input_form",
    "material",
    "scope",
    "locator",
    "record_key",
    "origin",
    "source_key",
    "sequence_verified",
    "product_verified",
    "dispatch_approved",
    "model_transfer_allowed",
    "raw_record",
    "payload",
    "metadata",
  ];
  const keys = [
    ...preferred.filter((k) => k in props),
    ...Object.keys(props).filter((k) => !preferred.includes(k)),
  ];
  return keys
    .map((k) => {
      const v = props[k];
      return `<div class="property"><dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v, null, 2) : String(v))}</dd></div>`;
    })
    .join("");
}
async function inspectNode(i) {
  selected = i;
  selectedKind = null;
  selectedEdge = null;
  dirty = true;
  const seq = ++detailSequence,
    n = G.nodes[i],
    connections = adjacency[i],
    outgoing = connections.filter((ei) => G.edges[ei][0] === i).length;
  const neighbors = connections.slice(0, 60);
  $("inspector-content").innerHTML =
    `<div class="node-kind" style="color:${COLORS[n[2]]}">${esc(G.kinds[n[2]].id)}</div><h2 class="node-title">${esc(n[1])}</h2><div class="node-id">${esc(n[0])}</div><div class="details-grid"><div><span>나가는 관계</span><strong>${nf(outgoing)}</strong></div><div><span>들어오는 관계</span><strong>${nf(connections.length - outgoing)}</strong></div></div><button id="explore-node" class="primary-button">이 노드의 주변 연결 탐색 ◎</button><div class="neighborhood-controls"><label for="depth-select">탐색 깊이</label><select id="depth-select"><option value="1">1단계 · 직접 연결</option><option value="2">2단계 · 주변 확장</option><option value="3">3단계 · 근거까지</option></select></div><div class="risk-notice">관계의 존재는 실제 처리 가능성이나 인수 승인을 의미하지 않습니다.</div><details open><summary class="detail-label">연결된 노드 · ${nf(connections.length)}</summary>${neighbors
      .map((ei) => {
        const e = G.edges[ei],
          other = e[0] === i ? e[1] : e[0];
        return `<button class="neighbor" data-node="${other}"><small>${e[0] === i ? "→" : "←"} ${esc(G.relations[e[2]].id)}</small>${esc(shortened(G.nodes[other][1], 70))}</button>`;
      })
      .join(
        "",
      )}${connections.length > 60 ? `<p class="subset-badge">목록에는 처음 60개만 표시합니다. 전체 관계는 캔버스 데이터에 포함되어 있습니다.</p>` : ""}</details><details><summary class="detail-label">원본 속성 · NEO4J</summary><div id="node-properties">원문 속성을 읽는 중…</div></details>`;
  $("depth-select").value = String(localDepth);
  $("depth-select").onchange = (e) => {
    localDepth = Number(e.target.value);
    if (mode === "local") refreshView();
  };
  $("explore-node").onclick = () => {
    activeKinds = new Set(G.kinds.map((_, j) => j));
    activeRelations = new Set(G.relations.map((_, j) => j));
    syncFilters();
    setMode("local");
  };
  bindNodeButtons();
  try {
    const res = await fetch(
      "/api/node?id=" +
        encodeURIComponent(n[0]) +
        "&generation=" +
        encodeURIComponent(G.meta.generation),
    );
    const result = await res.json();
    if (seq !== detailSequence) return;
    if (!res.ok) throw Error(result.detail || "원문 조회 실패");
    $("node-properties").innerHTML =
      `<dl>${usefulProps(result.properties)}</dl>`;
    const urls = findUrls(result.properties);
    if (urls.length)
      $("node-properties").insertAdjacentHTML(
        "afterbegin",
        `<div class="detail-label">출처 링크</div>${urls
          .slice(0, 12)
          .map(
            (url) =>
              `<p><a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(shortened(url, 110))} ↗</a></p>`,
          )
          .join("")}`,
      );
  } catch (err) {
    if (seq === detailSequence && $("node-properties"))
      $("node-properties").innerHTML =
        `<p class="detail-error">${esc(err.message)}</p>`;
  }
}
function findUrls(v, urls = new Set()) {
  if (typeof v === "string" && /^https?:\/\//.test(v)) urls.add(v);
  else if (v && typeof v === "object")
    for (const x of Object.values(v)) findUrls(x, urls);
  return [...urls];
}
function restoreOverview() {
  selected = null;
  selectedKind = null;
  selectedEdge = null;
  detailSequence++;
  $("inspector-content").innerHTML = overviewHTML;
  $("stat-nodes").textContent = nf(G.nodes.length);
  $("stat-edges").textContent = nf(G.edges.length);
  $("stat-kinds").textContent = G.kinds.length;
  $("stat-relations").textContent = G.relations.length;
  dirty = true;
}
function syncFilters() {
  for (const el of document.querySelectorAll("[data-kind]"))
    el.checked = activeKinds.has(Number(el.dataset.kind));
  for (const el of document.querySelectorAll("[data-relation]"))
    el.checked = activeRelations.has(Number(el.dataset.relation));
}
function initControls() {
  $("kind-filters").innerHTML = G.kinds
    .map(
      (k, i) =>
        `<label class="filter-row" title="${esc(k.label)}"><input type="checkbox" data-kind="${i}" checked><span class="filter-color" style="background:${COLORS[i]}"></span><span class="filter-name">${esc(k.id)}</span><span class="filter-count">${nf(k.count)}</span></label>`,
    )
    .join("");
  $("relation-filters").innerHTML = G.relations
    .map(
      (r, i) =>
        `<label class="filter-row"><input type="checkbox" data-relation="${i}" checked><span class="filter-name">${esc(r.id)}</span><span class="filter-count">${nf(r.count)}</span></label>`,
    )
    .join("");
  for (const el of document.querySelectorAll("[data-kind]"))
    el.onchange = () => {
      const k = Number(el.dataset.kind);
      el.checked ? activeKinds.add(k) : activeKinds.delete(k);
      refreshView();
    };
  for (const el of document.querySelectorAll("[data-relation]"))
    el.onchange = () => {
      const r = Number(el.dataset.relation);
      el.checked ? activeRelations.add(r) : activeRelations.delete(r);
      refreshView();
    };
  $("types-reset").onclick = () => {
    activeKinds = new Set(G.kinds.map((_, i) => i));
    activeRelations = new Set(G.relations.map((_, i) => i));
    syncFilters();
    refreshView();
  };
  for (const el of document.querySelectorAll("[data-mode]"))
    el.onclick = () => setMode(el.dataset.mode);
  $("materials").innerHTML = ["pp", "pet", "pe", "aluminium", "paper"]
    .map(
      (key) =>
        `<button class="material-button" data-material="${key}">${{ pp: "PP", pet: "PET", pe: "PE", aluminium: "알루미늄", paper: "종이" }[key]}</button>`,
    )
    .join("");
  for (const el of document.querySelectorAll("[data-material]"))
    el.onclick = () => {
      selected = idIndex.get("material:" + el.dataset.material);
      activeKinds = new Set(G.kinds.map((_, i) => i));
      activeRelations = new Set(G.relations.map((_, i) => i));
      localDepth = 3;
      syncFilters();
      document
        .querySelectorAll("[data-material]")
        .forEach((b) => b.classList.toggle("active", b === el));
      setMode("local");
    };
  $("clear-selection").onclick = restoreOverview;
  $("fit-button").onclick = fitView;
  const zoom = (f) => {
    cam.scale = Math.max(0.02, Math.min(20, cam.scale * f));
    updateZoom();
    dirty = true;
  };
  $("zoom-in").onclick = () => zoom(1.4);
  $("zoom-out").onclick = () => zoom(1 / 1.4);
  $("fullscreen-button").onclick = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.querySelector(".canvas-area").requestFullscreen();
    } catch {
      document.querySelector(".canvas-area").classList.toggle("expanded");
    }
    resize();
    fitView();
  };
  $("info-button").onclick = () => {
    $("snapshot-info").textContent = JSON.stringify(G.meta, null, 2);
    $("info-dialog").showModal();
  };
  $("close-info").onclick = () => $("info-dialog").close();
  $("close-image").onclick = () => $("image-dialog").close();
  $("download-button").onclick = () => {
    const a = document.createElement("a");
    a.href = "/graph.json";
    a.download = "resource-knowledge-graph.json";
    a.click();
  };
  $("export-button").onclick = () => {
    dirty = true;
    const capture = document.createElement("canvas");
    capture.width = canvas.width;
    capture.height = canvas.height;
    const c = capture.getContext("2d");
    c.fillStyle = "#f9fbfa";
    c.fillRect(0, 0, capture.width, capture.height);
    c.drawImage(canvas, 0, 0);
    c.drawImage(overlay, 0, 0);
    const url = capture.toDataURL("image/png");
    $("export-preview").src = url;
    $("export-image-link").href = url;
    $("export-image-link").download = "resource-graph-" + mode + ".png";
    $("image-dialog").showModal();
  };
  $("search").addEventListener("input", (e) => searchNodes(e.target.value));
  document.addEventListener("keydown", (e) => {
    if (
      e.key === "/" &&
      !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)
    ) {
      e.preventDefault();
      $("search").focus();
    }
    if (e.key === "Escape" && document.querySelector(".canvas-area.expanded")) {
      document.querySelector(".canvas-area").classList.remove("expanded");
      resize();
      fitView();
    }
    if (e.key === "Escape") {
      $("search-results").hidden = true;
      $("search").blur();
    }
  });
}
function searchNodes(query) {
  const container = $("search-results"),
    q = query.trim().toLowerCase();
  if (!q) {
    container.hidden = true;
    return;
  }
  const exact = [],
    others = [];
  let total = 0;
  for (let i = 0; i < G.nodes.length; i++) {
    const n = G.nodes[i],
      label = n[1].toLowerCase(),
      id = n[0].toLowerCase();
    if (label.includes(q) || id.includes(q)) {
      total++;
      if (label === q || id === "material:" + q) exact.push(i);
      else if (others.length < 30) others.push(i);
    }
  }
  const found = [...exact, ...others].slice(0, 25);
  container.hidden = false;
  container.innerHTML =
    `<div class="search-message">${nf(total)}개 일치${total > 25 ? " · 25개 표시" : ""}</div>` +
    found
      .map(
        (i) =>
          `<button class="search-result" data-search-node="${i}">${esc(shortened(G.nodes[i][1], 70))}<small>${esc(G.kinds[G.nodes[i][2]].id)} · 연결 ${nf(G.nodes[i][3])}개</small></button>`,
      )
      .join("");
  for (const el of container.querySelectorAll("[data-search-node]"))
    el.onclick = () => {
      selected = Number(el.dataset.searchNode);
      container.hidden = true;
      activeKinds = new Set(G.kinds.map((_, i) => i));
      activeRelations = new Set(G.relations.map((_, i) => i));
      localDepth = 2;
      syncFilters();
      setMode("local");
    };
}
canvas.addEventListener("pointerdown", (e) => {
  canvas.setPointerCapture(e.pointerId);
  pointer = {
    x: e.offsetX,
    y: e.offsetY,
    startX: e.offsetX,
    startY: e.offsetY,
    camX: cam.x,
    camY: cam.y,
    moved: false,
  };
  $("tooltip").hidden = true;
});
canvas.addEventListener("pointermove", (e) => {
  if (pointer) {
    const dx = e.offsetX - pointer.startX,
      dy = e.offsetY - pointer.startY;
    if (Math.hypot(dx, dy) > 4) pointer.moved = true;
    cam.x = pointer.camX - dx / cam.scale;
    cam.y = pointer.camY - dy / cam.scale;
    dirty = true;
  } else {
    hover = hitTest(e.offsetX, e.offsetY);
    canvas.style.cursor = hover ? "pointer" : "grab";
    updateTooltip(hover, e.offsetX, e.offsetY);
    if (mode === "schema") dirty = true;
  }
});
canvas.addEventListener("pointerup", (e) => {
  const p = pointer;
  pointer = null;
  if (!p || p.moved) return;
  const hit = hitTest(e.offsetX, e.offsetY);
  if (!hit) return;
  if (hit.kind === "schema") inspectKind(hit.index);
  else if (hit.kind === "edge") inspectEdge(hit.index);
  else inspectNode(hit.index);
});
canvas.addEventListener("pointercancel", () => (pointer = null));
canvas.addEventListener("pointerleave", () => {
  $("tooltip").hidden = true;
  hover = null;
  dirty = true;
});
canvas.addEventListener("dblclick", (e) => {
  const hit = hitTest(e.offsetX, e.offsetY);
  if (hit?.kind === "schema") {
    activeKinds = new Set([hit.index]);
    syncFilters();
    setMode("all");
  } else if (hit?.kind === "node") {
    selected = hit.index;
    setMode("local");
  }
});
canvas.addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    const before = world(e.offsetX, e.offsetY);
    cam.scale = Math.max(
      0.02,
      Math.min(20, cam.scale * Math.exp(-e.deltaY * 0.0013)),
    );
    const after = world(e.offsetX, e.offsetY);
    cam.x += before[0] - after[0];
    cam.y += before[1] - after[1];
    updateZoom();
    dirty = true;
  },
  { passive: false },
);
new ResizeObserver(() => {
  resize();
  if (G) fitView();
}).observe(stage);
async function init() {
  try {
    initRenderer();
    const response = await fetch("/data/graph");
    if (!response.ok) throw Error("전체 그래프를 불러오지 못했습니다.");
    G = await response.json();
    if (!G.nodes.length || !G.edges.length)
      throw Error("내보낸 그래프가 비어 있습니다.");
    idIndex = new Map(G.nodes.map((n, i) => [n[0], i]));
    adjacency = Array.from({ length: G.nodes.length }, () => []);
    G.edges.forEach(([a, b], i) => {
      adjacency[a].push(i);
      adjacency[b].push(i);
    });
    activeKinds = new Set(G.kinds.map((_, i) => i));
    activeRelations = new Set(G.relations.map((_, i) => i));
    buildPositions();
    initControls();
    restoreOverview();
    $("all-count").textContent = nf(G.nodes.length);
    $("relation-count").textContent = G.relations.length;
    $("snapshot-id").textContent = G.meta.generation;
    $("snapshot-date").firstChild.textContent =
      new Date(G.meta.exported_at).toLocaleDateString("ko-KR") + " 스냅샷";
    $("generation").textContent = G.meta.namespace;
    $("loading").hidden = true;
    resize();
    refreshView();
    document.body.dataset.ready = "true";
    requestAnimationFrame(render);
  } catch (error) {
    $("loading").innerHTML =
      `<strong>그래프를 열 수 없습니다</strong><span>${esc(error.message)}</span><span>로컬 서버와 그래프 내보내기 상태를 확인해 주세요.</span>`;
    document.body.dataset.ready = "error";
    console.error(error);
  }
}
init();
