// UI self-check, run INSIDE the page (Playwright evaluate) at 1440 and 390 wide, on REAL data.
// Exists because money tests cannot see a picture: chart labels overprinted for months while every
// test passed, and sample-data screenshots had too few points to show it. Returns JSON; empty arrays = pass.
//   overlaps   pairs of visible <text> in one SVG whose boxes intersect
//   outside    <text> that leaves its own SVG
//   tiny       visible text rendered under 11px
//   pageWide   the page scrolls sideways (documentElement wider than the viewport)
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && +cs.opacity > 0; };
  const overlaps = [], outside = [], tiny = [];
  document.querySelectorAll('svg').forEach((svg, si) => {
    if (!vis(svg)) return;
    const sr = svg.getBoundingClientRect();
    const T = [...svg.querySelectorAll('text')].filter(vis).map(t => ({ t, r: t.getBoundingClientRect() }));
    T.forEach(({ t, r }) => {
      if (r.left < sr.left - 1 || r.right > sr.right + 1 || r.top < sr.top - 1 || r.bottom > sr.bottom + 1)
        outside.push(`svg#${si}: "${t.textContent.trim()}"`);
    });
    for (let i = 0; i < T.length; i++) for (let j = i + 1; j < T.length; j++) {
      const a = T[i].r, b = T[j].r;
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left), h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 1 && h > 1) overlaps.push(`svg#${si}: "${T[i].t.textContent.trim()}" x "${T[j].t.textContent.trim()}"`);
    }
  });
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const seen = new Set();
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const el = n.parentElement; if (!el || seen.has(el) || !n.textContent.trim() || !vis(el)) continue;
    seen.add(el);
    const fs = parseFloat(getComputedStyle(el).fontSize);
    if (fs < 11) tiny.push(`${el.tagName.toLowerCase()}.${(el.getAttribute('class') || '').split(' ')[0]} ${fs}px "${n.textContent.trim().slice(0, 24)}"`);
  }
  const pageWide = document.documentElement.scrollWidth > innerWidth + 1 ? document.documentElement.scrollWidth : 0;
  return { width: innerWidth, overlaps: overlaps.length, outside: outside.length, tiny: tiny.length, pageWide,
           sample: { overlaps: overlaps.slice(0, 4), outside: outside.slice(0, 3), tiny: [...new Set(tiny)].slice(0, 6) } };
}
