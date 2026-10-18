/* Shared pixel-art engine for the WiFind pages (swamp game, Rescue Watch).

   One 20-colour palette, a hand-made 5x7 bitmap font, sprites as text grids,
   whole-pixel drawing helpers and an SNES-style window. Pages draw on one small
   canvas that is scaled up by a whole number (see Pixel.fit), so every pixel on
   screen, text included, is the same size. Loaded as a plain script; everything
   is on window.Pixel. */
(() => {
  "use strict";
  // ------------------------------------------------------------- palette (20 colours)
  const P = {
    K: "#1a1c2c", W: "#f4f4f4", L: "#94b0c2", G: "#566c86", D: "#333c57", N: "#29366f", B: "#3b5dc9",
    S: "#41a6f6", C: "#73eff7", T: "#257179", g: "#38b764", l: "#a7f070", f: "#1e4d2b", m: "#6a8f3f",
    Y: "#ffcd75", O: "#ef7d57", R: "#b13e53", P: "#5d275d", w: "#8b5a3c", d: "#5c3a28",
  };


  // ------------------------------------------------------------- 5x7 bitmap font
  const GLYPHS = {
    A: "01110 10001 10001 11111 10001 10001 10001", B: "11110 10001 10001 11110 10001 10001 11110",
    C: "01110 10001 10000 10000 10000 10001 01110", D: "11100 10010 10001 10001 10001 10010 11100",
    E: "11111 10000 10000 11110 10000 10000 11111", F: "11111 10000 10000 11110 10000 10000 10000",
    G: "01110 10001 10000 10111 10001 10001 01111", H: "10001 10001 10001 11111 10001 10001 10001",
    I: "111 010 010 010 010 010 111", J: "00111 00010 00010 00010 00010 10010 01100",
    K: "10001 10010 10100 11000 10100 10010 10001", L: "10000 10000 10000 10000 10000 10000 11111",
    M: "10001 11011 10101 10101 10001 10001 10001", N: "10001 10001 11001 10101 10011 10001 10001",
    O: "01110 10001 10001 10001 10001 10001 01110", P: "11110 10001 10001 11110 10000 10000 10000",
    Q: "01110 10001 10001 10001 10101 10010 01101", R: "11110 10001 10001 11110 10100 10010 10001",
    S: "01111 10000 10000 01110 00001 00001 11110", T: "11111 00100 00100 00100 00100 00100 00100",
    U: "10001 10001 10001 10001 10001 10001 01110", V: "10001 10001 10001 10001 10001 01010 00100",
    W: "10001 10001 10001 10101 10101 10101 01010", X: "10001 10001 01010 00100 01010 10001 10001",
    Y: "10001 10001 10001 01010 00100 00100 00100", Z: "11111 00001 00010 00100 01000 10000 11111",
    0: "01110 10001 10011 10101 11001 10001 01110", 1: "010 110 010 010 010 010 111",
    2: "01110 10001 00001 00010 00100 01000 11111", 3: "11111 00010 00100 00010 00001 10001 01110",
    4: "00010 00110 01010 10010 11111 00010 00010", 5: "11111 10000 11110 00001 00001 10001 01110",
    6: "00110 01000 10000 11110 10001 10001 01110", 7: "11111 00001 00010 00100 01000 01000 01000",
    8: "01110 10001 10001 01110 10001 10001 01110", 9: "01110 10001 10001 01111 00001 00010 01100",
    "!": "1 1 1 1 1 0 1", "?": "01110 10001 00001 00010 00100 00000 00100", ".": "0 0 0 0 0 0 1",
    ",": "00 00 00 00 00 01 10", "'": "1 1 0 0 0 0 0", '"': "101 101 000 000 000 000 000",
    ":": "0 0 1 0 0 1 0", "-": "000 000 000 111 000 000 000", "/": "00001 00010 00010 00100 01000 01000 10000",
    "(": "01 10 10 10 10 10 01", ")": "10 01 01 01 01 01 10", "+": "000 000 010 111 010 000 000",
    "=": "000 000 111 000 111 000 000", "%": "11001 11010 00010 00100 01000 01011 10011", " ": "00 00 00 00 00 00 00",
  };
  const FONT = {};
  for (const [ch, rows] of Object.entries(GLYPHS)) FONT[ch] = rows.split(" ").map((r) => [...r].map(Number));

  // text arrives from the laptop/AI in any case and with any punctuation
  const tidy = (s) => String(s).toUpperCase().replace(/[“”]/g, '"').replace(/[‘’]/g, "'").replace(/…/g, "...")
    .replace(/[—–·]/g, "-").replace(/[^A-Z0-9!?.,'":\-/()+=% ]/g, "");

  function textWidth(s, bold) {
    let w = 0;
    for (const ch of tidy(s)) w += (FONT[ch] || FONT["?"])[0].length + 1 + (bold ? 1 : 0);
    return Math.max(0, w - 1);
  }
  // one line of text; opts: bold, outline (colour key), shadow (colour key)
  function text(ctx, s, x, y, col, opts = {}) {
    s = tidy(s); x = Math.floor(x); y = Math.floor(y);
    const plot = (ox, oy, c) => {
      ctx.fillStyle = P[c];
      let cx = x + ox;
      for (const ch of s) {
        const g = FONT[ch] || FONT["?"];
        for (let r = 0; r < 7; r++) for (let k = 0; k < g[r].length; k++) {
          if (g[r][k]) ctx.fillRect(cx + k, y + oy + r, opts.bold ? 2 : 1, 1);
        }
        cx += g[0].length + 1 + (opts.bold ? 1 : 0);
      }
    };
    if (opts.outline) for (const [ox, oy] of [[-1, 0], [1, 0], [0, -1], [0, 1], [-1, -1], [1, -1], [-1, 1], [1, 1]]) plot(ox, oy, opts.outline);
    if (opts.shadow) plot(0, 1, opts.shadow);
    plot(0, 0, col);
  }
  function wrap(s, maxW) {
    const lines = [];
    let line = "";
    for (const word of tidy(s).split(" ")) {
      const t = line ? line + " " + word : word;
      if (textWidth(t) > maxW && line) { lines.push(line); line = word; } else line = t;
    }
    if (line) lines.push(line);
    return lines;
  }

  // ------------------------------------------------------------- sprites (text grids, "." = see-through)
  function sprite(ctx, rows, x, y, map = {}) {
    x = Math.floor(x); y = Math.floor(y);
    for (let r = 0; r < rows.length; r++) for (let c = 0; c < rows[r].length; c++) {
      const k = rows[r][c];
      if (k === ".") continue;
      ctx.fillStyle = P[map[k] || k];
      ctx.fillRect(x + c, y + r, 1, 1);
    }
  }

  const PORTRAIT = [ // the gator's face for the dialog box
    "...KKKK....KKKK...",
    "..KggggK..KggggK..",
    "..KgWWKK..KgWWKK..",
    "..KgWKKK..KgWKKK..",
    ".KggggggKKggggggK.",
    "KgggllllggllllgggK",
    "KggggggggggggggggK",
    "KgggggggggggggKgKK",
    "KggggggggggggggggK",
    "KWKWKWKWKWKWKWKWKK",
    "KRRRRRRRRRRRRRRRRK",
    "KWKWKWKWKWKWKWKWKK",
    "KggggggggggggggggK",
    "KfffffffffffffffK.",
    ".KKKKKKKKKKKKKKKK.",
  ];

  // ------------------------------------------------------------- drawing helpers (always whole pixels)
  const rect = (ctx, c, x, y, w, h) => { ctx.fillStyle = P[c]; ctx.fillRect(Math.floor(x), Math.floor(y), Math.floor(w), Math.floor(h)); };
  function ellipse(ctx, c, cx, cy, rx, ry) {       // filled, scanline by scanline
    cx = Math.floor(cx); cy = Math.floor(cy); rx = Math.floor(rx); ry = Math.floor(ry);
    ctx.fillStyle = P[c];
    for (let y = -ry; y <= ry; y++) {
      const half = Math.round(rx * Math.sqrt(Math.max(0, 1 - (y * y) / (ry * ry + 0.0001))));
      ctx.fillRect(cx - half, cy + y, half * 2 + 1, 1);
    }
  }
  function ring(ctx, c, cx, cy, r, every = 1) {     // midpoint circle; every=2+ -> dotted (fading)
    ctx.fillStyle = P[c];
    let x = r, y = 0, err = 1 - r, n = 0;
    while (x >= y) {
      if (n++ % every === 0) for (const [a, b] of [[x, y], [y, x], [-y, x], [-x, y], [-x, -y], [-y, -x], [y, -x], [x, -y]]) ctx.fillRect(cx + a, cy + b, 1, 1);
      y++;
      if (err < 0) err += 2 * y + 1; else { x--; err += 2 * (y - x) + 1; }
    }
  }
  // ordered dither between two colours (4x4 Bayer): an even pixel pattern, never a smooth blend
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];
  const ditherPick = (x, y, t) => t * 16 > BAYER[(y & 3) * 4 + (x & 3)];
  function windowBox(ctx, x, y, w, h, fill = "N") { // SNES-style window: dark rim, white + grey border
    rect(ctx, "K", x + 1, y, w - 2, h); rect(ctx, "K", x, y + 1, w, h - 2);
    rect(ctx, "W", x + 1, y + 1, w - 2, h - 2);
    rect(ctx, "L", x + 2, y + 2, w - 4, h - 4);
    rect(ctx, fill, x + 3, y + 3, w - 6, h - 6);
  }

  async function get(path) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    try {
      const r = await fetch(path, { cache: "no-store", signal: ctl.signal });
      return r.ok ? await r.json() : null;
    } catch { return null; } finally { clearTimeout(t); }
  }

  // Fit a canvas to the window: the largest whole-number scale that keeps at
  // least minW x minH game pixels, and as many game pixels as fill the screen.
  function fit(canvas, minW = 300, minH = 170) {
    const scale = Math.max(2, Math.floor(Math.min(innerWidth / minW, innerHeight / minH)));
    const w = Math.floor(innerWidth / scale), h = Math.floor(innerHeight / scale);
    canvas.width = w; canvas.height = h;
    canvas.style.width = w * scale + "px"; canvas.style.height = h * scale + "px";
    canvas.getContext("2d").imageSmoothingEnabled = false;
    return { W: w, H: h, SCALE: scale };
  }

  window.Pixel = { P, FONT, tidy, textWidth, text, wrap, sprite, PORTRAIT, rect, ellipse, ring, BAYER, ditherPick,
                   windowBox, get, fit };
})();
