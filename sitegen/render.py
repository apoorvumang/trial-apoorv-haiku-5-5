"""Deterministic rendering + quality checks + screenshot tiling for a generated site.

For each page: render N times (must be pixel-identical), run QC in the page, save the full screenshot,
viewport-sized tiles (what the agent will see) and a downscaled overview.

QC findings are structured ({check, where: main | chrome | page, el, text, y, ...}) so the repair step can feed them
back to the generator; `issues` is the one-line-per-check summary.
"""
import io
import json
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.async_api import async_playwright

FREEZE_CSS = """*, *::before, *::after {
  animation: none !important; transition: none !important; caret-color: transparent !important;
  scroll-behavior: auto !important; }"""

MAX_HEIGHT = 7 * 900  # px; height budget is 3-6 screens (7 for the one dense page)
MAX_FINDINGS = 12     # per check per page, to keep reports and repair prompts small

# Wait for fonts and every image (lazy ones are switched to eager, since a full-page shot must show them).
READY_JS = """async () => {
  await document.fonts.ready;
  const imgs = [...document.images];
  imgs.forEach(i => { i.loading = 'eager'; });
  await Promise.race([new Promise(r => setTimeout(r, 15000)),
    Promise.all(imgs.map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })))]);
}"""

# Runs in the page after load (first render only). Returns facts the QC rules need; all coordinates are document px.
QC_JS = """async () => {
  await (""" + READY_JS + """)();
  const imgs = [...document.images];
  const VW = innerWidth, VH = innerHeight;
  // elementFromPoint skips pointer-events: none, which decorations often set; it has no visual effect
  const pe = document.createElement('style');
  pe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
  document.head.appendChild(pe);
  scrollTo(0, 0);

  const bundled = new Set([...document.fonts].map(f => f.family.replace(/["']/g, '')));
  const generic = new Set(['serif','sans-serif','monospace','cursive','fantasy','system-ui','ui-sans-serif',
                           'ui-serif','ui-monospace','inherit','initial']);
  const unknownFonts = new Set();
  const rgba = s => { const m = s.match(/[\\d.]+/g) || []; return m.length >= 3 ? [+m[0], +m[1], +m[2], m[3] === undefined ? 1 : +m[3]] : null; };
  const over = (top, bot) => { const a = top[3]; return [0, 1, 2].map(i => top[i] * a + bot[i] * (1 - a)).concat([1]); };
  const lum = c => { const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const desc = el => el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + [...el.classList].slice(0, 3).map(c => '.' + c).join('');
  const where = el => el.closest('main') ? 'main' : 'chrome';
  const snip = s => s.replace(/\\s+/g, ' ').trim().slice(0, 60);
  const opMemo = new Map();
  const opacityOf = el => { if (!el) return 1; let o = opMemo.get(el);
    if (o === undefined) { o = +getComputedStyle(el).opacity * opacityOf(el.parentElement); opMemo.set(el, o); } return o; };
  // Background colour behind an element: composite ancestor backgrounds; null if an ancestor paints an image or
  // gradient (then the pixel check in Python decides).
  const bgOf = el => {
    const layers = [];
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.backgroundImage !== 'none') return null;
      const c = rgba(cs.backgroundColor);
      if (c && c[3] > 0) { layers.push(c); if (c[3] >= 1) break; }
    }
    let bg = [255, 255, 255, 1];
    for (const c of layers.reverse()) bg = over(c, bg);
    return bg;
  };
  // Visually-hidden text (sr-only patterns) is not on screen and is not checked.
  const srOnly = el => { for (let a = el; a && a !== document.body; a = a.parentElement) {
    const cs = getComputedStyle(a);
    if ((cs.clip && cs.clip !== 'auto') || /inset\\((50|100)%/.test(cs.clipPath) ||
        (cs.overflow !== 'visible' && (a.clientWidth <= 2 || a.clientHeight <= 2))) return true;
  } return false; };

  let textNodes = 0;
  const texts = [], stuck = [], all = [...document.querySelectorAll('body *')];
  // Sticky elements would follow the scrolling below and cover other text; pin them where a full-page shot shows them.
  const pinned = [];
  for (const el of all) {
    const pos = getComputedStyle(el).position;
    if (pos === 'sticky' || pos === 'fixed') stuck.push({ el: desc(el), position: pos, where: where(el) });
    if (pos === 'sticky') { pinned.push([el, el.style.getPropertyValue('position'), el.style.getPropertyPriority('position')]);
                            el.style.setProperty('position', 'static', 'important'); }
  }
  for (const el of all) {
    const cs = getComputedStyle(el);
    if (el.closest('select, option, textarea, template, noscript')) continue;
    const nodes = [...el.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim());
    if (!nodes.length) continue;
    textNodes++;
    const first = cs.fontFamily.split(',')[0].trim().replace(/["']/g, '');
    if (!bundled.has(first) && !generic.has(first)) unknownFonts.add(first);
    if (cs.visibility === 'hidden' || srOnly(el)) continue;
    const rects = [];
    for (const n of nodes) {
      const r = document.createRange(); r.selectNodeContents(n);
      for (const q of r.getClientRects())  // skip empty boxes and text parked off-canvas (skip links etc.)
        if (q.width >= 2 && q.height >= 2 && q.right + scrollX > 0 && q.bottom + scrollY > 0)
          rects.push([q.left + scrollX, q.top + scrollY, q.width, q.height]);
    }
    if (!rects.length) continue;
    const words = nodes.map(n => n.textContent).join(' ');
    const fg = rgba(cs.color) || [0, 0, 0, 1], op = opacityOf(el), alpha = fg[3] * op;
    const inSvg = !!el.closest('svg');
    // Outlined, shadowed, gradient-filled and SVG text: colour alone does not decide legibility. Pure punctuation
    // (separators, bullets) is decorative.
    const skipContrast = inSvg || !/[\\p{L}\\p{N}]/u.test(words) || parseFloat(cs.webkitTextStrokeWidth) > 0 || cs.textShadow !== 'none' ||
                         cs.backgroundClip === 'text' || cs.webkitBackgroundClip === 'text' || alpha < 0.05;
    let cssRatio = null;
    if (!skipContrast) {
      const bg = bgOf(el);
      if (bg) { const t = over([fg[0], fg[1], fg[2], alpha], bg), L1 = lum(t), L2 = lum(bg);
                cssRatio = +((Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05)).toFixed(2); }
    }
    texts.push({ el, inSvg, rects, nodes, words, text: snip(words), hyphens: cs.hyphens === 'auto',
                 fg: fg.slice(0, 3), alpha, skipContrast, cssRatio, faint: alpha < 0.5,
                 ellipsis: cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none') });
  }
  const info = t => ({ where: where(t.el), el: desc(t.el), text: t.text, y: Math.round(t.rects[0][1]) });

  // 1. Occlusion: sample points along each text line. The topmost element there must be the text itself (or an
  //    ancestor/descendant); another element that paints (image, svg, filled box) on top covers the text. Text drawn
  //    over a small out-of-flow painted box (badge, sticker, stamp) that is not its container collides with it.
  const paintMemo = new Map();
  const paints = t => { let p = paintMemo.get(t); if (p !== undefined) return p;
    const cs = getComputedStyle(t), c = rgba(cs.backgroundColor);
    p = !!t.closest('svg') || /^(IMG|VIDEO|CANVAS|PICTURE)$/.test(t.tagName) || cs.backgroundImage !== 'none' || (!!c && c[3] > 0.5);
    paintMemo.set(t, p); return p; };
  const badges = all.filter(e => { const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    return (cs.position === 'absolute' || cs.transform !== 'none') && r.width * r.height < 40000 && r.width > 0 && paints(e) && opacityOf(e) >= 0.5;
  }).map(e => { const r = e.getBoundingClientRect(); return [e, r.left + scrollX, r.top + scrollY, r.right + scrollX, r.bottom + scrollY]; });
  const pts = [];
  texts.forEach((t, i) => { if (!t.faint) for (const [x, y, w, h] of t.rects.slice(0, 8))
    for (const f of [0.1, 0.3, 0.5, 0.7, 0.9]) for (const g of [0.15, 0.35, 0.55, 0.8]) pts.push([i, x + w * f, y + h * g]); });
  pts.sort((a, b) => a[2] - b[2]);
  const covered = new Map(), under = new Map();
  for (const [i, x, y] of pts) {
    if (x < 0 || x >= VW || covered.has(i)) continue;
    if (y < scrollY + 100 || y > scrollY + VH - 100) scrollTo(0, Math.max(0, y - VH / 2));
    const top = document.elementFromPoint(x, y - scrollY), el = texts[i].el;
    if (!top) continue;
    if (top !== el && !el.contains(top) && !top.contains(el)) {
      if (paints(top) && opacityOf(top) >= 0.3) covered.set(i, top);
      continue;
    }
    if (under.has(i)) continue;
    const near = badges.filter(([b, l, t, r, btm]) => x >= l && x <= r && y >= t && y <= btm && !b.contains(el) && !el.contains(b));
    if (!near.length) continue;
    const stack = document.elementsFromPoint(x, y - scrollY);
    const b = near.find(([b]) => stack.includes(b));
    if (b) under.set(i, b[0]);
  }
  scrollTo(0, 0);
  const occluded = [...covered].map(([i, by]) => ({ ...info(texts[i]), by: desc(by) }));

  // 2. Text collisions: the middle half (roughly the glyph ink) of line boxes from different elements overlaps,
  //    or text runs over a badge-like box (from step 1).
  const boxes = [];
  texts.forEach((t, i) => { if (!t.faint) for (const [x, y, w, h] of t.rects) boxes.push([i, x, y + h * 0.25, w, h * 0.5]); });
  boxes.sort((a, b) => a[2] - b[2]);
  const collisions = [], pairs = new Set();
  for (let a = 0; a < boxes.length; a++) {
    const [i, x, y, w, h] = boxes[a];
    for (let b = a + 1; b < boxes.length && boxes[b][2] < y + h; b++) {
      const [j, x2, y2, w2, h2] = boxes[b];
      if (i === j) continue;
      const ix = Math.min(x + w, x2 + w2) - Math.max(x, x2), iy = Math.min(y + h, y2 + h2) - Math.max(y, y2);
      if (ix <= 0 || iy <= 0 || ix * iy < 0.15 * Math.min(w * h, w2 * h2)) continue;
      const key = Math.min(i, j) + ':' + Math.max(i, j);
      if (pairs.has(key)) continue;
      pairs.add(key);
      collisions.push({ ...info(texts[i]), other_el: desc(texts[j].el), other_text: texts[j].text, y: Math.round(Math.max(y, y2)) });
    }
  }
  for (const [i, b] of under) if (!covered.has(i)) collisions.push({ ...info(texts[i]), other_el: desc(b), other_text: snip(b.textContent) });

  // 3. Clipped text: a line sticks out of an ancestor that clips (overflow other than visible) or out of the viewport.
  //    Deliberate truncation (text-overflow: ellipsis, line-clamp) is fine.
  const clipped = [];
  for (const t of texts) {
    if (t.inSvg || t.ellipsis) continue;
    const clippers = [];  // [desc, clipsX, clipsY, left, top, width, height] of ancestors that clip
    for (let a = t.el; a && a !== document.documentElement; a = a.parentElement) {
      const cs = getComputedStyle(a);
      const cx = cs.overflowX !== 'visible', cy = cs.overflowY !== 'visible';
      if (!cx && !cy) continue;
      if (cs.textOverflow === 'ellipsis' || (cs.webkitLineClamp && cs.webkitLineClamp !== 'none')) break;
      const r = a.getBoundingClientRect();
      clippers.push([a, cx, cy, r.left + a.clientLeft + scrollX, r.top + a.clientTop + scrollY, a.clientWidth, a.clientHeight]);
    }
    let found = null;
    for (const [x, y, w, h] of t.rects) {
      if (x < -3 || x + w > VW + 3) { found = 'viewport edge'; break; }
      for (const [a, cx, cy, L, T, W, H] of clippers) {
        const outX = Math.max(0, L - x) + Math.max(0, x + w - (L + W)), outY = Math.max(0, T - y) + Math.max(0, y + h - (T + H));
        if ((cx && outX > 3) || (cy && outY > 0.4 * h)) { found = desc(a); break; }
      }
      if (found) break;
    }
    if (found) clipped.push({ ...info(t), by: found });
  }

  // 4. Text over a photo (an image or background-image painted under it that is not its own container): the CSS
  //    background says nothing about legibility there, so Python judges contrast from the pixels alone.
  const isImg = e => /^(IMG|PICTURE|VIDEO)$/.test(e.tagName) || getComputedStyle(e).backgroundImage !== 'none';
  for (const t of [...texts].filter(t => !t.skipContrast).sort((a, b) => a.rects[0][1] - b.rects[0][1])) {
    const [x, y, w, h] = t.rects[0], cx = x + w / 2, cy = y + h / 2;
    if (cx < 0 || cx >= VW) continue;
    if (cy < scrollY + 100 || cy > scrollY + VH - 100) scrollTo(0, Math.max(0, cy - VH / 2));
    const stack = document.elementsFromPoint(cx, cy - scrollY), at = stack.findIndex(e => e === t.el || t.el.contains(e));
    t.overImg = at >= 0 && stack.slice(at + 1).some(e => !e.contains(t.el) && isImg(e));
  }
  scrollTo(0, 0);

  // 5. Copy that talks about the website itself instead of speaking as the organisation.
  const META = /\\b(fictional|fictitious|invented)\\b[^.]{0,80}\\b(organi[sz]ations?|concept|personnel|people|names|locations|business(es)?|compan(y|ies)|website|site|newspaper|figures|resemblance|range|numbers)\\b|\\b(organi[sz]ation|business|company|website|site|newspaper|names|people|personnel)\\b[^.]{0,80}\\b(fictional|fictitious)\\b|\\bdemonstration (only|purposes|page|site|snapshot|copy|data|form)\\b|\\b(for|is a|as a) demonstration\\b|\\bdemo (site|page|only|data|purposes|snapshot)\\b|\\bpreview only\\b|\\bnot live\\b|\\bstatic (web ?)?(site|page|html|website)\\b|\\bmust state\\b|\\bacceptance criteri|\\blorem ipsum\\b|\\bnothing (is|will be|gets) (submitted|saved|sent|stored|carried)\\b|\\bfor illustration\\b|\\b(this|the) (website|site|page) is (a )?(static|demo|prototype|mock)/i;
  const metaCopy = texts.filter(t => META.test(t.words)).map(t => {
    const m = t.words.match(META), ctx = t.words.slice(Math.max(0, m.index - 50), m.index + 90);
    return { ...info(t), text: ctx.replace(/\\s+/g, ' ').trim() };
  });

  // 6. Words, numbers, emails and code tokens split across lines (hyphenated breaks and code blocks excluded), and
  //    lone punctuation pushed onto its own line away from the text before it.
  const rng = document.createRange();
  const visRects = () => [...rng.getClientRects()].filter(q => q.width > 0.5 && q.height > 0.5);
  const spansLines = rs => rs.length > 1 && Math.max(...rs.map(q => q.top)) - Math.min(...rs.map(q => q.top)) > 0.5 * Math.min(...rs.map(q => q.height));
  const broken = [], orphans = [];
  for (const t of texts) {
    if (t.inSvg || t.hyphens || t.el.closest('pre')) continue;
    tokens: for (const n of t.nodes) for (const m of n.textContent.matchAll(/[^\\s\\-‐-—\\/­]{2,}/g)) {
      rng.setStart(n, m.index); rng.setEnd(n, m.index + m[0].length);
      const rs = visRects();
      if (spansLines(rs)) { broken.push({ ...info(t), text: m[0].slice(0, 60), y: Math.round(rs[0].top + scrollY) }); break tokens; }
    }
  }
  const isFrag = s => /^[^\\p{L}\\p{N}\\s“”"'‘’«»→←↑↓↗↘{}\\[\\]]{1,2}$/u.test(s.trim());  // quotes, arrows, braces: by design
  const prevContent = n => { let r = n.previousSibling; while (r && !r.textContent.trim()) r = r.previousSibling; return r; };
  for (const t of texts) {
    if (t.inSvg || t.el.closest('pre, code')) continue;
    for (const n of t.nodes) {
      if (!isFrag(n.textContent)) continue;
      let ref = prevContent(n);
      if (!ref && t.el.textContent.trim() === n.textContent.trim()) ref = prevContent(t.el);
      if (!ref) continue;
      rng.selectNodeContents(ref); const r1 = visRects().pop();
      rng.selectNodeContents(n); const r2 = visRects()[0];
      if (!r1 || !r2) continue;
      const refEl = ref.nodeType === 1 ? ref : ref.parentElement;
      if (parseFloat(getComputedStyle(t.el).fontSize) > 1.4 * parseFloat(getComputedStyle(refEl).fontSize)) continue;  // big decorative glyph
      if (r2.top >= r1.bottom - 2) orphans.push({ ...info(t), text: n.textContent.trim(), other_text: snip(ref.textContent), y: Math.round(r2.top + scrollY) });
    }
  }

  // 7. Code blocks that soft-wrap: more rendered lines than source lines.
  const wrappedCode = [];
  for (const pre of document.querySelectorAll('pre')) {
    if (!pre.getClientRects().length || srOnly(pre)) continue;
    const tops = [], tw = document.createTreeWalker(pre, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) { rng.selectNodeContents(n); for (const q of visRects()) tops.push(q.top); }
    tops.sort((a, b) => a - b);
    let lines = 0, last = -1e9;
    for (const y of tops) if (y - last > 3) { lines++; last = y; }
    const src = pre.textContent.replace(/\\s+$/, '').split('\\n').length;
    if (lines > src) wrappedCode.push({ where: where(pre), el: desc(pre), text: snip(pre.textContent),
      y: Math.round(pre.getBoundingClientRect().top + scrollY), detail: `${src} source lines render as ${lines} lines` });
  }

  // 8. Browser defaults leaking through: dd's 40px indent, a visible skip link.
  const ua = [], uaSeen = new Set();
  for (const dd of document.querySelectorAll('dd')) {
    if (parseFloat(getComputedStyle(dd).marginInlineStart) !== 40 || !dd.getClientRects().length || uaSeen.has(desc(dd))) continue;
    uaSeen.add(desc(dd));
    ua.push({ where: where(dd), el: desc(dd), text: snip(dd.textContent), y: Math.round(dd.getBoundingClientRect().top + scrollY),
              detail: 'dd keeps the browser default 40px left indent' });
  }
  for (const t of texts) if (!t.faint && /^\\s*skip (to|navigation)/i.test(t.words)) ua.push({ ...info(t), detail: 'skip link visible on screen' });

  // 9. Alignment: a flex row of buttons/items packed to the left inside a centred block; cards in one row vertically
  //    centred against each other so their tops do not line up.
  const misaligned = [], uneven = [];
  for (const f of all) {
    const cs = getComputedStyle(f), flexRow = /flex/.test(cs.display) && /^row/.test(cs.flexDirection);
    if (flexRow && cs.textAlign === 'center' && ['normal', 'flex-start', 'start', 'left'].includes(cs.justifyContent)) {
      const kids = [...f.children].filter(k => k.getClientRects().length && getComputedStyle(k).position !== 'absolute');
      if (kids.length && kids.some(k => /^(A|BUTTON)$/.test(k.tagName) || paints(k))) {
        const r = f.getBoundingClientRect(), L = r.left + f.clientLeft + parseFloat(cs.paddingLeft);
        const W = f.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
        const kl = Math.min(...kids.map(k => k.getBoundingClientRect().left)), kr = Math.max(...kids.map(k => k.getBoundingClientRect().right));
        if (W - (kr - kl) > 60 && Math.abs(kl - L) < 4)
          misaligned.push({ where: where(f), el: desc(f), text: snip(f.textContent), y: Math.round(r.top + scrollY),
                            detail: `items packed at the left of a centred ${Math.round(W)}px block` });
      }
    }
    if ((flexRow || /grid/.test(cs.display)) && cs.alignItems === 'center') {
      const cards = [...f.children].map(k => [k, k.getBoundingClientRect()]).filter(([k, r]) => r.width >= 160 && r.height >= 120 &&
        (paints(k) || parseFloat(getComputedStyle(k).borderTopWidth) > 0)).sort((a, b) => a[1].top - b[1].top);
      for (let a = 0; a < cards.length; a++) {
        const row = cards.filter(([, r]) => r.top < cards[a][1].bottom && r.bottom > cards[a][1].top);
        const tops = row.map(([, r]) => r.top);
        if (row.length > 1 && Math.max(...tops) - Math.min(...tops) > 6) {
          uneven.push({ where: where(f), el: desc(f), text: snip(f.textContent), y: Math.round(Math.min(...tops) + scrollY),
                        detail: `card tops differ by ${Math.round(Math.max(...tops) - Math.min(...tops))}px (align-items: center)` });
          break;
        }
      }
    }
  }

  // 10. Body text and code metrics inside <main>, for the cross-page type-drift check in Python.
  const typeStats = sel => [...document.querySelectorAll(sel)].filter(e => e.closest('main') && e.getClientRects().length &&
    e.textContent.trim().length >= 40).map(e => { const cs = getComputedStyle(e), fs = parseFloat(cs.fontSize);
      return [fs, cs.lineHeight === 'normal' ? 1.2 : parseFloat(cs.lineHeight) / fs, e.textContent.trim().length]; });
  const imgList = imgs.filter(i => i.getClientRects().length).map(i => ({ where: where(i), el: desc(i), src: i.getAttribute('src'),
    y: Math.round(i.getBoundingClientRect().top + scrollY) })).concat(all.filter(e => /images\\//.test(getComputedStyle(e).backgroundImage))
    .map(e => ({ where: where(e), el: desc(e), src: 'images/' + getComputedStyle(e).backgroundImage.split('images/')[1].split(/["')]/)[0],
                 y: Math.round(e.getBoundingClientRect().top + scrollY) })));

  // 11. Elements that stick out past the right edge (only reported when the page overflows).
  const wide = document.documentElement.scrollWidth > VW ? all.filter(e => {
    const r = e.getBoundingClientRect(), p = e.parentElement && e.parentElement.getBoundingClientRect();
    return r.right > VW + 1 && !(p && p.right > VW + 1);
  }).slice(0, 8).map(e => ({ where: where(e), el: desc(e), right: Math.round(e.getBoundingClientRect().right) })) : [];

  const bgUrls = all.map(e => getComputedStyle(e).backgroundImage).join(' ').match(/images\\/[^"')]+/g) || [];
  const srcs = imgs.map(i => i.getAttribute('src')).concat(bgUrls.map(u => u.split('/').slice(-2).join('/')));
  const controls = [...document.querySelectorAll('input[type=range], details:not([open])')]
    .map(e => ({ where: where(e), el: desc(e), text: snip(e.textContent) }));

  pe.remove();
  for (const [el, v, prio] of pinned) v ? el.style.setProperty('position', v, prio) : el.style.removeProperty('position');
  scrollTo(0, 0);
  return {
    scrollWidth: document.documentElement.scrollWidth,
    height: document.documentElement.scrollHeight,
    brokenImages: imgs.filter(i => !i.naturalWidth).map(i => i.getAttribute('src')),
    fontErrors: [...document.fonts].filter(f => f.status === 'error').map(f => f.family),
    unknownFonts: [...unknownFonts],
    links: [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href')),
    texts: texts.filter(t => !t.skipContrast).map(t => ({ ...info(t), rects: t.rects.slice(0, 3), fg: t.fg,
                                                          alpha: t.alpha, cssRatio: t.cssRatio, overImg: !!t.overImg })),
    textNodes, occluded, collisions, clipped, stuck, wide, controls, srcs, metaCopy, broken, orphans, wrappedCode, ua,
    misaligned, uneven, imgList, type: { p: typeStats('main p, main li'), pre: typeStats('main pre') },
    nodes: all.length,
    scripts: document.scripts.length,
  };
}"""


def _lum(rgb) -> np.ndarray:
    c = np.asarray(rgb, float) / 255
    c = np.where(c <= 0.03928, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    return c @ np.array([0.2126, 0.7152, 0.0722])


def _ratio(a: float, b: float) -> float:
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def low_contrast(texts: list[dict], arr: np.ndarray) -> list[dict]:
    """Text vs the colour actually behind it in the screenshot (median pixel of its line boxes; glyphs are a minority).

    With a known CSS background both measures must agree (< 2:1). Over a photo or gradient the CSS background is
    meaningless and the pixel measure alone decides (< 1.8:1; the median hides bright patches, so stay strict)."""
    out = []
    for t in texts:
        px = [arr[max(0, round(y)):round(y + h), max(0, round(x)):round(x + w)].reshape(-1, 3) for x, y, w, h in t["rects"]]
        px = np.concatenate(px) if px else np.empty((0, 3))
        if len(px) < 16:
            continue
        bg = np.median(px, axis=0)
        fg = np.array(t["fg"], float) * t["alpha"] + bg * (1 - t["alpha"])
        pixel = _ratio(_lum(fg), _lum(bg))
        css = None if t["overImg"] else t["cssRatio"]
        if (css is not None and css < 2.0 and pixel < 2.0) or (css is None and pixel < 1.8):
            out.append({k: t[k] for k in ("where", "el", "text", "y")} | {"ratio": round(min(pixel, css or pixel), 2)})
    return out


def _tiles(png: bytes, width: int, vh: int, out: Path, stem: str) -> int:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for top in range(0, img.height, vh):
        img.crop((0, top, width, min(top + vh, img.height))).save(out / f"{stem}_{n:02d}.png")
        n += 1
    scale = 480 / width
    img.resize((480, max(1, round(img.height * scale))), Image.LANCZOS).save(out / f"{stem}_overview.png")
    return n


def _findings(facts: dict, arr: np.ndarray, site_dir: Path, blocked: list[str], deterministic: bool,
              viewport, placeholders: set[str]) -> list[dict]:
    f = []
    page = lambda check, detail: f.append({"check": check, "where": "page", "detail": detail})  # noqa: E731
    if not deterministic:
        page("nondeterministic_render", "three renders differ")
    if facts["scrollWidth"] > viewport[0]:
        page("horizontal_overflow", f"page is {facts['scrollWidth']}px wide; elements past the edge: {facts['wide']}")
    if facts["height"] > MAX_HEIGHT:
        f.append({"check": "over_height_budget", "where": "page", "height": facts["height"],
                  "detail": f"page is {facts['height']}px tall; the budget is {MAX_HEIGHT}px"})
    if blocked:
        page("external_requests", f"requests outside the site: {sorted(set(blocked))[:10]}")
    if facts["brokenImages"]:
        page("broken_images", f"images that failed to load: {facts['brokenImages']}")
    if facts["fontErrors"] or facts["unknownFonts"]:
        page("fonts", f"fonts not in fonts/fonts.css: {sorted(set(facts['fontErrors'] + facts['unknownFonts']))}")
    if facts["scripts"]:
        page("has_script", "the page contains <script>")
    gray = arr.mean(axis=2)
    if gray.std() < 8 or facts["textNodes"] < 15:
        page("near_blank", "the page is nearly empty")
    dead = sorted({h for h in facts["links"] if h.endswith(".html") and not (site_dir / h.split("#")[0]).exists()})
    if dead:
        page("dead_links", f"links to pages that do not exist: {dead}")
    dups = sorted(s for s, n in _count(facts["srcs"]).items() if n > 1)
    if dups:
        page("duplicate_image", f"image files used more than once on this page: {dups}")
    shown = [{k: i[k] for k in ("where", "el", "y")} | {"text": i["src"]} for i in facts["imgList"] if i["src"] in placeholders]
    for check, items in (("low_contrast", low_contrast(facts["texts"], arr)), ("occluded_text", facts["occluded"]),
                         ("text_collision", facts["collisions"]), ("clipped_text", facts["clipped"]),
                         ("broken_token", facts["broken"]), ("orphan_fragment", facts["orphans"]),
                         ("wrapped_code", facts["wrappedCode"]), ("ua_default", facts["ua"]),
                         ("misaligned_row", facts["misaligned"]), ("uneven_row", facts["uneven"]),
                         ("placeholder_image", shown), ("meta_copy", facts["metaCopy"]),
                         ("sticky_or_fixed", facts["stuck"]), ("interactive_control", facts["controls"])):
        f += [{"check": check, **i} for i in items[:MAX_FINDINGS]]
        if len(items) > MAX_FINDINGS:
            f.append({"check": check, "where": "page", "detail": f"{len(items) - MAX_FINDINGS} more not listed",
                      "more": len(items) - MAX_FINDINGS})
    return f


def _wmedian(rows: list, col: int) -> float:
    """Median of rows[i][col] weighted by rows[i][-1] (characters of text)."""
    rows = sorted(rows, key=lambda r: r[col])
    half, acc = sum(r[-1] for r in rows) / 2, 0
    for r in rows:
        acc += r[-1]
        if acc >= half:
            return r[col]
    return rows[-1][col]


def type_drift(stats: dict[str, dict]) -> dict[str, list[dict]]:
    """Pages whose body text (or code) is >15% smaller or tighter than the median of the other pages.

    Only shrinking is flagged: a repair squeezing a page into the height budget is the failure this catches, while a
    deliberately larger article size is fine."""
    out = defaultdict(list)
    for kind, min_chars in (("p", 300), ("pre", 200)):
        med = {n: (_wmedian(st[kind], 0), _wmedian(st[kind], 1)) for n, st in stats.items()
               if sum(r[-1] for r in st[kind]) >= min_chars}
        for name, (fs, lh) in med.items():
            others = [v for n, v in med.items() if n != name]
            if len(others) < 2:
                continue
            ofs, olh = float(np.median([o[0] for o in others])), float(np.median([o[1] for o in others]))
            # tighter leading on much larger text (a lede, a pull quote) is normal typography, not drift
            if fs < 0.85 * ofs or (kind == "p" and lh < 0.85 * olh and fs <= 1.1 * ofs):
                what = "body text" if kind == "p" else "code"
                out[name].append({"check": "type_drift", "where": "main", "detail":
                                  f"{what} here is {fs:.0f}px / line-height {lh:.2f}; the rest of the site uses "
                                  f"{ofs:.0f}px / {olh:.2f}"})
    return out


def _count(xs) -> dict:
    d = defaultdict(int)
    for x in xs:
        d[x] += 1
    return d


def summarize(findings: list[dict]) -> list[str]:
    """One line per check: count and the first example."""
    by = defaultdict(list)
    for f in findings:
        by[f["check"]].append(f)
    out = []
    for check, fs in by.items():
        e = fs[0]
        ex = e.get("detail") or f"{e.get('where')} {e.get('el')} y={e.get('y')} '{e.get('text', '')}'" + (
            f" under {e['by']}" if e.get("by") else "")
        n = sum(f.get("more", 1) for f in fs)
        out.append(f"{check}:{n} (e.g. {ex})" if n > 1 or "detail" not in e else f"{check}: {ex}")
    return out


async def render_site(site_dir: Path, shots_dir: Path, viewport=(1440, 900), repeats: int = 3) -> dict:
    site_dir, shots_dir = site_dir.resolve(), shots_dir.resolve()
    pages = sorted(p.name for p in site_dir.glob("*.html"))
    report = {"pages": {}, "issues": []}
    images_json = site_dir.parent / "meta" / "images.json"
    placeholders = {i["file"] for i in json.loads(images_json.read_text()) if i.get("source") != "model"} \
        if images_json.exists() else set()
    stats = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--font-render-hinting=none", "--disable-lcd-text",
                                                 "--force-color-profile=srgb", "--hide-scrollbars"])
        for name in pages:
            blocked: list[str] = []
            shots: list[bytes] = []
            facts = {}
            for _ in range(repeats):
                ctx = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]},
                                                device_scale_factor=1, reduced_motion="reduce",
                                                java_script_enabled=True)

                async def route(r):
                    url = r.request.url
                    if url.startswith(f"file://{site_dir}/"):
                        await r.continue_()
                    else:
                        blocked.append(url)
                        await r.abort()

                await ctx.route("**/*", route)
                page = await ctx.new_page()
                await page.goto(f"file://{site_dir}/{name}", wait_until="load")
                await page.add_style_tag(content=FREEZE_CSS)
                if not facts:
                    facts = await page.evaluate(QC_JS)
                else:
                    await page.evaluate(READY_JS)
                shots.append(await page.screenshot(full_page=True, animations="disabled", caret="hide"))
                await ctx.close()

            arrs = [np.asarray(Image.open(io.BytesIO(s)).convert("RGB")) for s in shots]
            deterministic = all(a.shape == arrs[0].shape and np.array_equal(a, arrs[0]) for a in arrs[1:])
            stem = name.removesuffix(".html")
            (shots_dir / "full").mkdir(parents=True, exist_ok=True)
            (shots_dir / "full" / f"{stem}.png").write_bytes(shots[-1])
            n_tiles = _tiles(shots[-1], viewport[0], viewport[1], shots_dir / "tiles", stem)

            stats[name] = facts["type"]
            report["pages"][name] = {"height": facts["height"], "tiles": n_tiles, "nodes": facts["nodes"],
                                     "text_nodes": facts["textNodes"], "deterministic": deterministic,
                                     "findings": _findings(facts, arrs[0], site_dir, blocked, deterministic, viewport,
                                                           placeholders)}
        await browser.close()
    for name, fs in type_drift(stats).items():
        report["pages"][name]["findings"] += fs
    for name, p in report["pages"].items():
        p["issues"] = summarize(p["findings"])
        report["issues"] += [f"{name}: {i}" for i in p["issues"]]
    if len(pages) < 5:
        report["issues"].append(f"site: only {len(pages)} pages")
    used = " ".join(p.read_text() for p in [*site_dir.glob("*.html"), *site_dir.glob("*.css")])
    unused = sorted(p.name for p in (site_dir / "images").glob("*") if p.name not in used) if (site_dir / "images").exists() else []
    if unused:
        report["issues"].append(f"site: unused_images:{unused}")
    report["unused_images"] = unused
    # remove tiles left over from a taller earlier render of the same page
    for t in (shots_dir / "tiles").glob("*_[0-9][0-9].png"):
        m = re.fullmatch(r"(.+)_(\d\d)\.png", t.name)
        if m and m.group(1) + ".html" in report["pages"] and int(m.group(2)) >= report["pages"][m.group(1) + ".html"]["tiles"]:
            t.unlink()
    (shots_dir / "render_report.json").write_text(json.dumps(report, indent=2))
    return report
