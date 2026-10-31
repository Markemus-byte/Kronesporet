// Kronesporet varsler: påmelding, bekreftelse og avmelding, abonnentlisten for utsendingen og Stripe-webhooken.
//
// Ruter:
//   POST /pamelding        skjemaet på kronesporet.no/varsler.html (vanlig HTML-skjema, ingen JavaScript nødvendig)
//   GET|POST /bekreft?t=   dobbel opt-in: siden har en knapp, så lenkesjekkere i e-postprogrammer ikke bekrefter
//   GET|POST /avmeld?t=    avslutt varslene (POST virker også som ettklikk-avmelding, RFC 8058)
//   POST /slett?t=         slett abonnenten helt
//   GET  /api/abonnenter   for scripts/send_alerts.py (Bearer API_TOKEN)
//   POST /api/sendt        scripts/send_alerts.py melder inn sendte varsler (Bearer API_TOKEN)
//   POST /stripe/webhook   betalingsstatus fra Stripe (STRIPE_WEBHOOK_SECRET)
//
// Datamodellen er i schema.sql. Hemmeligheter (wrangler secret put): API_TOKEN, BREVO_API_KEY, STRIPE_WEBHOOK_SECRET.

const TYPES = ["kjoper", "leverandor", "cpv", "sokeord"];
const HORIZONS = ["3", "6", "12"];
const MAX_PER_TYPE = 50;
const RESEND_MINUTES = 10; // minst så lenge mellom to bekreftelses-e-poster til samme adresse
const SENDING = "bekreftet IS NOT NULL AND avmeldt IS NULL AND status IN ('active', 'gratis')";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    const post = request.method === "POST";
    try {
      if (path === "/pamelding" && post) return await signup(request, env);
      if (path === "/bekreft") return post ? await confirm(url, env) : await confirmPage(url, env);
      if (path === "/avmeld") return post ? await unsubscribe(url, env) : await unsubscribePage(url, env);
      if (path === "/slett" && post) return await remove(url, env);
      if (path === "/api/abonnenter" && request.method === "GET") return await listSubscribers(request, env);
      if (path === "/api/sendt" && post) return await logSent(request, env);
      if (path === "/stripe/webhook" && post) return await stripeWebhook(request, env);
      if (path === "/") return Response.redirect(`${env.SITE_URL}/varsler.html`, 302);
      return new Response("Ikke funnet", { status: 404 });
    } catch (e) {
      console.error(e);
      return new Response("Noe gikk galt. Prøv igjen senere.", { status: 500 });
    }
  },
};

// --- hjelpere -------------------------------------------------------------------

const now = () => new Date().toISOString().slice(0, 19).replace("T", " "); // som CURRENT_TIMESTAMP i SQLite

function back(env, status) {
  return Response.redirect(`${env.SITE_URL}/varsler.html?status=${status}#meld-interesse`, 303);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function htmlPage(env, title, body, status = 200) {
  const html = `<!doctype html><html lang="nb"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>${escapeHtml(title)} – Kronesporet</title>
<style>body{font-family:system-ui,Arial,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1rem;color:#1c1c1c;
background:#f7f5f0;line-height:1.5}button{font:inherit;padding:.6rem 1.1rem;border:0;border-radius:6px;background:#1d4ed8;
color:#fff;cursor:pointer}button.sekundar{background:#6b6b6b}a{color:#1d4ed8}</style></head>
<body><p><a href="${env.SITE_URL}/">Kronesporet</a></p><h1>${escapeHtml(title)}</h1>${body}</body></html>`;
  return new Response(html, { status, headers: { "content-type": "text/html; charset=utf-8" } });
}

function randomToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(24));
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join(""); // bare [0-9a-f], gyldig client_reference_id
}

function splitValues(raw) {
  return [...new Set(String(raw || "").split(/[\n\r,;]+/).map((v) => v.trim()).filter(Boolean))];
}

// Rydd og avgrens det som er skrevet i skjemaet. Ugyldige verdier hoppes over.
export function parseSelection(form) {
  const out = [];
  const add = (type, values) => values.slice(0, MAX_PER_TYPE).forEach((verdi) => out.push({ type, verdi }));
  add("kjoper", splitValues(form.get("kjoper")).map((v) => v.replace(/\s/g, "")).filter((v) => /^\d{9}$/.test(v)));
  add("leverandor", splitValues(form.get("leverandor"))
    .map((v) => (/^[\d\s]{9,11}$/.test(v) ? v.replace(/\s/g, "") : v)).filter((v) => v.length >= 3 && v.length <= 100));
  add("cpv", splitValues(form.get("cpv")).map((v) => v.replace(/[^\d-]/g, "")).filter((v) => /^\d{2,8}(-\d)?$/.test(v)));
  add("sokeord", splitValues(form.get("sokeord")).filter((v) => v.length >= 3 && v.length <= 60));
  const horizons = form.getAll("horisont").filter((h) => HORIZONS.includes(h));
  return { utvalg: out, horisonter: (horizons.length ? horizons : HORIZONS).join(","), csv: form.get("csv") ? 1 : 0 };
}

async function sendMail(env, to, subject, text, html) {
  if (!env.BREVO_API_KEY) throw new Error("BREVO_API_KEY mangler");
  const r = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: { "api-key": env.BREVO_API_KEY, "content-type": "application/json", accept: "application/json" },
    body: JSON.stringify({ sender: { name: "Kronesporet", email: env.AVSENDER }, to: [{ email: to }],
                           subject, textContent: text, htmlContent: html }),
  });
  if (!r.ok) throw new Error(`Brevo svarte ${r.status}: ${(await r.text()).slice(0, 300)}`);
}

function describe(sel) {
  const names = { kjoper: "Kjøpere (orgnr)", leverandor: "Leverandører", cpv: "CPV-koder", sokeord: "Søkeord" };
  return TYPES.map((t) => [names[t], sel.utvalg.filter((u) => u.type === t).map((u) => u.verdi)])
    .filter(([, v]) => v.length).map(([n, v]) => `${n}: ${v.join(", ")}`);
}

async function byToken(env, url) {
  const t = url.searchParams.get("t") || "";
  if (!/^[0-9a-f]{48}$/.test(t)) return null;
  return env.DB.prepare("SELECT * FROM abonnenter WHERE token = ?").bind(t).first();
}

function authorized(request, env) {
  const got = request.headers.get("authorization") || "";
  const want = `Bearer ${env.API_TOKEN || ""}`;
  if (!env.API_TOKEN || got.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < want.length; i++) diff |= got.charCodeAt(i) ^ want.charCodeAt(i);
  return diff === 0;
}

// --- påmelding og bekreftelse -----------------------------------------------------

async function signup(request, env) {
  const form = await request.formData();
  if (form.get("nettside")) return back(env, "sjekk-epost"); // honningkrukke: feltet er skjult for mennesker
  const email = String(form.get("epost") || "").trim().toLowerCase();
  if (!/^[^\s@]{1,64}@[^\s@]{1,190}\.[a-z]{2,}$/.test(email)) return back(env, "ugyldig-epost");
  const sel = parseSelection(form);
  if (!sel.utvalg.length) return back(env, "mangler-utvalg");

  const row = await env.DB.prepare("SELECT token, bekreft_sendt FROM abonnenter WHERE email = ?").bind(email).first();
  if (row?.bekreft_sendt && Date.now() - Date.parse(row.bekreft_sendt.replace(" ", "T") + "Z") < RESEND_MINUTES * 60e3) {
    return back(env, "vent");
  }
  const token = row?.token || randomToken();
  await env.DB.prepare(
    `INSERT INTO abonnenter (email, token, ventende, bekreft_sendt) VALUES (?1, ?2, ?3, ?4)
     ON CONFLICT (email) DO UPDATE SET ventende = ?3, bekreft_sendt = ?4, oppdatert = ?4`,
  ).bind(email, token, JSON.stringify(sel), now()).run();

  const link = `${new URL(request.url).origin}/bekreft?t=${token}`;
  const lines = describe(sel);
  await sendMail(env, email, "Bekreft varsler fra Kronesporet",
    `Hei!\n\nNoen (forhåpentligvis du) har meldt denne adressen på varsler fra Kronesporet.no:\n\n${lines.join("\n")}\n` +
    `Horisont for avtaler som løper ut: ${sel.horisonter.replaceAll(",", ", ")} måneder.\n\n` +
    `Bekreft her: ${link}\n\nHar du ikke bedt om dette, kan du se bort fra e-posten. Da skjer ingenting.\n\nKronesporet.no\n`,
    `<p>Hei!</p><p>Noen (forhåpentligvis du) har meldt denne adressen på varsler fra Kronesporet.no:</p><ul>` +
    lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("") +
    `<li>Horisont for avtaler som løper ut: ${sel.horisonter.replaceAll(",", ", ")} måneder</li></ul>` +
    `<p><a href="${link}">Bekreft påmeldingen</a></p>` +
    `<p>Har du ikke bedt om dette, kan du se bort fra e-posten. Da skjer ingenting.</p><p>Kronesporet.no</p>`);
  return back(env, "sjekk-epost");
}

async function confirmPage(url, env) {
  const row = await byToken(env, url);
  if (!row) return htmlPage(env, "Lenken virker ikke", "<p>Lenken er ugyldig eller utløpt. Meld deg på igjen.</p>", 404);
  const t = escapeHtml(row.token);
  return htmlPage(env, "Bekreft varsler", `<p>Trykk på knappen for å bekrefte varsler til ${escapeHtml(row.email)}.</p>
<form method="post" action="/bekreft?t=${t}"><button type="submit">Bekreft</button></form>`);
}

async function confirm(url, env) {
  const row = await byToken(env, url);
  if (!row) return back(env, "feil");
  const stamp = now();
  const stmts = [];
  if (row.ventende) {
    const sel = JSON.parse(row.ventende);
    stmts.push(env.DB.prepare("DELETE FROM utvalg WHERE email = ?").bind(row.email));
    for (const u of sel.utvalg) {
      stmts.push(env.DB.prepare("INSERT OR IGNORE INTO utvalg (email, type, verdi) VALUES (?, ?, ?)")
        .bind(row.email, u.type, u.verdi));
    }
    stmts.push(env.DB.prepare("UPDATE abonnenter SET horisonter = ?, csv = ? WHERE email = ?")
      .bind(sel.horisonter, sel.csv, row.email));
  }
  stmts.push(env.DB.prepare(
    "UPDATE abonnenter SET bekreftet = COALESCE(bekreftet, ?1), avmeldt = NULL, ventende = NULL, oppdatert = ?1 WHERE email = ?2",
  ).bind(stamp, row.email));
  await env.DB.batch(stmts);

  if (["active", "gratis"].includes(row.status)) return back(env, "bekreftet");
  if (env.STRIPE_PAYMENT_LINK) {
    const pay = new URL(env.STRIPE_PAYMENT_LINK);
    pay.searchParams.set("prefilled_email", row.email);
    pay.searchParams.set("client_reference_id", row.token);
    return Response.redirect(pay.toString(), 303);
  }
  return back(env, "bekreftet-betaling");
}

// --- avmelding og sletting --------------------------------------------------------

function portalLine(env) {
  return env.STRIPE_PORTAL_URL
    ? `<p>Betaler du for varslene, sier du opp betalingen i <a href="${escapeHtml(env.STRIPE_PORTAL_URL)}">kundeportalen</a>.</p>`
    : "";
}

async function unsubscribePage(url, env) {
  const row = await byToken(env, url);
  if (!row) return htmlPage(env, "Lenken virker ikke", "<p>Lenken er ugyldig. Skriv til oss hvis du vil avslutte.</p>", 404);
  const t = escapeHtml(row.token);
  return htmlPage(env, "Avslutt varsler", `<p>Vil du avslutte varslene til ${escapeHtml(row.email)}?</p>
<form method="post" action="/avmeld?t=${t}"><button type="submit">Avslutt varslene</button></form>
<p>Eller slett adressen og utvalget ditt helt:</p>
<form method="post" action="/slett?t=${t}"><button class="sekundar" type="submit">Slett meg</button></form>${portalLine(env)}`);
}

async function unsubscribe(url, env) {
  const row = await byToken(env, url);
  if (!row) return htmlPage(env, "Lenken virker ikke", "<p>Lenken er ugyldig.</p>", 404);
  await env.DB.prepare("UPDATE abonnenter SET avmeldt = ?1, oppdatert = ?1 WHERE email = ?2").bind(now(), row.email).run();
  return htmlPage(env, "Varslene er avsluttet", `<p>Du får ikke flere varsler fra Kronesporet. Du kan melde deg på igjen
når du vil på <a href="${env.SITE_URL}/varsler.html">varsler.html</a>.</p>${portalLine(env)}`);
}

async function remove(url, env) {
  const row = await byToken(env, url);
  if (!row) return htmlPage(env, "Lenken virker ikke", "<p>Lenken er ugyldig.</p>", 404);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM utvalg WHERE email = ?").bind(row.email),
    env.DB.prepare("DELETE FROM sendte_varsler WHERE email = ?").bind(row.email),
    env.DB.prepare("DELETE FROM abonnenter WHERE email = ?").bind(row.email),
  ]);
  return htmlPage(env, "Adressen er slettet", `<p>Adressen, utvalget og loggen over sendte varsler er slettet.
Kvitteringer for betaling oppbevares av Stripe etter bokføringsloven.</p>${portalLine(env)}`);
}

// --- API for utsendingen ----------------------------------------------------------

async function listSubscribers(request, env) {
  if (!authorized(request, env)) return new Response("Ikke tilgang", { status: 401 });
  const { results: subs } = await env.DB.prepare(
    `SELECT a.email, a.token, a.status, a.plan, a.horisonter, a.csv,
            (SELECT kjoring FROM sendte_varsler s WHERE s.email = a.email ORDER BY sendt DESC, id DESC LIMIT 1) AS sist_sendt,
            (SELECT sendt FROM sendte_varsler s WHERE s.email = a.email ORDER BY sendt DESC, id DESC LIMIT 1) AS sist_sendt_tid
     FROM abonnenter a WHERE ${SENDING}`,
  ).all();
  const { results: sel } = await env.DB.prepare(
    `SELECT u.email, u.type, u.verdi FROM utvalg u JOIN abonnenter a ON a.email = u.email WHERE ${SENDING}`,
  ).all();
  const by = new Map(subs.map((s) => [s.email, { ...s, utvalg: [] }]));
  for (const u of sel) by.get(u.email)?.utvalg.push({ type: u.type, verdi: u.verdi });
  return Response.json([...by.values()]);
}

async function logSent(request, env) {
  if (!authorized(request, env)) return new Response("Ikke tilgang", { status: 401 });
  const rows = await request.json();
  if (!Array.isArray(rows) || rows.length > 1000) return new Response("Ugyldig", { status: 400 });
  await env.DB.batch(rows.map((r) => env.DB.prepare(
    "INSERT INTO sendte_varsler (email, kjoring, tildelinger, utlop, status) VALUES (?, ?, ?, ?, ?)",
  ).bind(String(r.email).toLowerCase(), String(r.kjoring), r.tildelinger | 0, r.utlop | 0, String(r.status || "sendt"))));
  return Response.json({ ok: rows.length });
}

// --- Stripe -----------------------------------------------------------------------

async function hmacHex(secret, payload) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payload));
  return [...new Uint8Array(sig)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function verifyStripe(body, header, secret, toleranceSeconds = 300, nowSeconds = Date.now() / 1000) {
  const pairs = String(header || "").split(",").map((p) => p.trim().split("="));
  const t = pairs.find(([k]) => k === "t")?.[1];
  const sigs = pairs.filter(([k]) => k === "v1").map(([, v]) => v);
  if (!secret || !t || !sigs.length || Math.abs(nowSeconds - Number(t)) > toleranceSeconds) return false;
  const want = await hmacHex(secret, `${t}.${body}`);
  return sigs.some((s) => s.length === want.length && [...s].every((c, i) => c === want[i]));
}

const STRIPE_STATUS = { active: "active", trialing: "active", past_due: "past_due", unpaid: "past_due",
                        canceled: "canceled", incomplete_expired: "canceled" };

async function stripeWebhook(request, env) {
  const body = await request.text();
  if (!(await verifyStripe(body, request.headers.get("stripe-signature"), env.STRIPE_WEBHOOK_SECRET))) {
    return new Response("Ugyldig signatur", { status: 400 });
  }
  const event = JSON.parse(body);
  const fresh = await env.DB.prepare("INSERT OR IGNORE INTO stripe_hendelser (id, type) VALUES (?, ?)")
    .bind(event.id, event.type).run();
  if (!fresh.meta.changes) return Response.json({ ok: true, duplikat: true });

  const obj = event.data.object;
  const stamp = now();
  // Gratis-abonnenter er satt for hånd og overstyres ikke av Stripe.
  const byCustomer = (status, extra = "", binds = []) => env.DB.prepare(
    `UPDATE abonnenter SET status = ?1, oppdatert = ?2${extra} WHERE stripe_customer_id = ?3 AND status != 'gratis'`,
  ).bind(status, stamp, obj.customer, ...binds).run();

  switch (event.type) {
    case "checkout.session.completed": {
      const token = /^[0-9a-f]{48}$/.test(obj.client_reference_id || "") ? obj.client_reference_id : null;
      const email = (obj.customer_details?.email || obj.customer_email || "").toLowerCase();
      await env.DB.prepare(
        `UPDATE abonnenter SET status = 'active', stripe_customer_id = ?1, oppdatert = ?2
         WHERE (token = ?3 OR (?3 IS NULL AND email = ?4)) AND status != 'gratis'`,
      ).bind(obj.customer, stamp, token, email).run();
      break;
    }
    case "customer.subscription.created":
    case "customer.subscription.updated": {
      const status = STRIPE_STATUS[obj.status];
      const interval = obj.items?.data?.[0]?.price?.recurring?.interval;
      if (status) await byCustomer(status, ", plan = ?4", [interval === "year" ? "aar" : "standard"]);
      break;
    }
    case "customer.subscription.deleted":
      await byCustomer("canceled");
      break;
    case "invoice.payment_failed":
      await byCustomer("past_due");
      break;
  }
  return Response.json({ ok: true });
}
