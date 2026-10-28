-- Varsler fra Kronesporet: abonnenter, hva de følger, og varslene som er sendt (Cloudflare D1 / SQLite).
-- Ingen e-postadresser ligger i det offentlige repoet; alt her ligger i D1-databasen.
-- Opprett:  npx wrangler d1 execute kronesporet-varsler --remote --file=schema.sql

CREATE TABLE IF NOT EXISTS abonnenter (
    email              TEXT PRIMARY KEY,               -- små bokstaver
    token              TEXT NOT NULL UNIQUE,           -- i lenkene for å bekrefte og avslutte, og client_reference_id i Stripe
    -- Betaling: 'ny' (ikke betalt ennå), 'active', 'past_due', 'canceled' (fra Stripe), 'gratis' (settes for hånd
    -- for redaksjoner, forskere og offentlige virksomheter, se gratis_grunn).
    status             TEXT NOT NULL DEFAULT 'ny'
                       CHECK (status IN ('ny', 'active', 'past_due', 'canceled', 'gratis')),
    plan               TEXT NOT NULL DEFAULT 'standard',  -- 'standard' (månedlig) | 'aar'
    gratis_grunn       TEXT,
    stripe_customer_id TEXT,
    horisonter         TEXT NOT NULL DEFAULT '3,6,12', -- måneder før en avtale løper ut
    csv                INTEGER NOT NULL DEFAULT 1,     -- legg ved CSV-uttrekk med historikk
    ventende           TEXT,                           -- JSON med nytt utvalg som venter på bekreftelse
    bekreft_sendt      TEXT,                           -- siste bekreftelses-e-post (sperre mot misbruk)
    bekreftet          TEXT,                           -- når e-posten ble bekreftet (dobbel opt-in); NULL = ikke bekreftet
    avmeldt            TEXT,                           -- når abonnenten avsluttet varslene; NULL = aktiv
    opprettet          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    oppdatert          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_abonnenter_stripe ON abonnenter (stripe_customer_id);

-- Hva abonnenten følger. En kontrakt treffer når den treffer en kjøper eller leverandør (eller ingen er
-- valgt) og en CPV-kode eller et søkeord (eller ingen er valgt). Se src/alerts/engine.py.
CREATE TABLE IF NOT EXISTS utvalg (
    email  TEXT NOT NULL REFERENCES abonnenter (email) ON DELETE CASCADE,
    type   TEXT NOT NULL CHECK (type IN ('kjoper', 'leverandor', 'cpv', 'sokeord')),
    verdi  TEXT NOT NULL,      -- orgnr, orgnr eller navn, CPV-kode eller -prefiks, søkeord
    PRIMARY KEY (email, type, verdi)
);

-- Ett varsel per rad, meldt inn av scripts/send_alerts.py. Neste varsel regner ut hva som er nytt fra
-- siste rad: tildelinger lagt inn etter `sendt`, og avtaler som har krysset en horisont etter `kjoring`.
CREATE TABLE IF NOT EXISTS sendte_varsler (
    id           INTEGER PRIMARY KEY,
    email        TEXT NOT NULL,
    kjoring      TEXT NOT NULL,     -- referansedatoen for dataene (siste publiseringsdato i Doffin)
    tildelinger  INTEGER NOT NULL DEFAULT 0,
    utlop        INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'sendt',
    sendt        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sendte_email ON sendte_varsler (email, sendt);

-- Stripe-hendelser som er behandlet, så samme hendelse ikke behandles to ganger.
CREATE TABLE IF NOT EXISTS stripe_hendelser (
    id        TEXT PRIMARY KEY,
    type      TEXT,
    mottatt   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
