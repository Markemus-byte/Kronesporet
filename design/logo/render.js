// Tegner en HTML-fil til PNG med Chromium (brukes av lag_logo.py): node render.js inn.html ut.png bredde høyde [skala]
const { chromium } = require('playwright');
(async () => {
  const [,, src, dst, w, h, scale] = process.argv;
  const b = await chromium.launch();
  const pg = await b.newPage({ viewport: { width: +w, height: +h }, deviceScaleFactor: +(scale || 1) });
  await pg.goto('file://' + require('path').resolve(src));
  await pg.screenshot({ path: dst, omitBackground: true, clip: { x: 0, y: 0, width: +w, height: +h } });
  await b.close();
})();
