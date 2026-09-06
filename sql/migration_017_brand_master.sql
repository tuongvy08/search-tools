-- Migration 017: Brand Master, Brand Aliases, products.source_brand, and Brand Data Migration
-- Phase 6B2B1-C: Canonical Brand Master, Import Safety, and Data Standardization.
--
-- Safety and Scope:
-- - Additive and idempotent. Safe to run repeatedly.
-- - Creates canonical table `brand_master` (35 canonical brands from approved workbook Sheet1!A3:C38).
-- - Creates alias dictionary table `brand_aliases` (121 audited old brands + self-aliases).
-- - Adds `products.source_brand` and backfills 100% of retained products.
-- - Preflight check: fail closed if any unknown brand exists in products.
-- - Single-pass update for products via hash join with staging mapping table.
-- - Deletes 192,233 products belonging to 21 Delete Set brands.
-- - Deletes test product id=1344915 if precondition matches (brand='Phụ lục I', name='TEST XÓA', code/cas empty).
-- - Canonicalizes team_brands with ON CONFLICT DO NOTHING, cleans legacy pseudo-brands and Delete Set.
-- - Upserts exactly 35 canonical brand rates to exchange_rates (no TRUNCATE).
-- - Cleans obsolete test brands from brand_compliance_settings.
-- - Leaves regulatory_rules and import_jobs completely untouched.
--
-- Atomicity (Phase 6B2B2-Fix1): this entire file is wrapped in one explicit
-- BEGIN/COMMIT transaction. Without it, a plain `psql -v ON_ERROR_STOP=1 -f
-- <this file>` run -- psql's default execution mode, and exactly how this
-- migration is invoked in the deploy runbook -- autocommits EVERY top-level
-- statement individually, including each `CREATE TEMP TABLE ... ON COMMIT
-- DROP` in Section 4: the temp table's own implicit per-statement commit
-- drops it again immediately, before the very next `INSERT INTO
-- staging_brand_mapping ...` statement can run, aborting the whole script
-- with "relation ... does not exist" -- confirmed on staging 2026-09-06.
-- Explicit BEGIN/COMMIT fixes this two ways at once: (a) the temp tables
-- now live for the whole transaction, only dropping at the final COMMIT,
-- exactly as their "ON COMMIT DROP" clause and this file's own comments
-- always intended; and (b) the Section 4 fail-closed preflight
-- `RAISE EXCEPTION` (and any other SQL error, via ON_ERROR_STOP=1 exiting
-- psql and closing the connection) now rolls back everything in this file
-- -- brand_master/brand_aliases/products.source_brand included -- instead
-- of leaving a partially-committed schema behind. No CREATE INDEX
-- CONCURRENTLY, VACUUM, or other transaction-incompatible statement exists
-- anywhere in this file (audited 2026-09-06), so wrapping the whole thing
-- is safe.

BEGIN;

-- ============================================================================
-- 1. DDL: Brand Master & Brand Aliases & products.source_brand
-- ============================================================================

CREATE TABLE IF NOT EXISTS brand_master (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    normalized_name TEXT NOT NULL UNIQUE,
    currency_code   TEXT NOT NULL CONSTRAINT chk_brand_currency CHECK (currency_code IN ('AUD', 'USD', 'EUR', 'GBP', 'VND')),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_master_normalized_name
    ON brand_master (normalized_name);

CREATE TABLE IF NOT EXISTS brand_aliases (
    id               SERIAL PRIMARY KEY,
    alias            TEXT NOT NULL,
    normalized_alias TEXT NOT NULL UNIQUE,
    brand_id         INTEGER NOT NULL REFERENCES brand_master(id) ON DELETE CASCADE,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brand_aliases_brand_id
    ON brand_aliases (brand_id);

CREATE INDEX IF NOT EXISTS idx_brand_aliases_normalized_alias
    ON brand_aliases (normalized_alias);

-- Defensive: guarantee `updated_at` exists even if `brand_aliases` was
-- somehow pre-created with an older/partial shape (mirrors the same
-- defensive pattern used below for `exchange_rates.updated_at`). The
-- ON CONFLICT DO UPDATE clauses in Section 3 write to this column, so its
-- absence would abort the whole migration transaction.
ALTER TABLE brand_aliases ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS source_brand TEXT;

-- ============================================================================
-- 2. Seed Brand Master (35 canonical brands)
-- ============================================================================

INSERT INTO brand_master (name, normalized_name, currency_code)
VALUES
    ('A2S', 'A2S', 'EUR'),
    ('AccuStandard', 'ACCUSTANDARD', 'USD'),
    ('Analytica Chemie', 'ANALYTICA CHEMIE', 'USD'),
    ('Anax', 'ANAX', 'USD'),
    ('Angene', 'ANGENE', 'USD'),
    ('BOC', 'BOC', 'USD'),
    ('BP', 'BP', 'GBP'),
    ('Biopurify', 'BIOPURIFY', 'USD'),
    ('Biosynth', 'BIOSYNTH', 'USD'),
    ('CATO', 'CATO', 'USD'),
    ('CIL', 'CIL', 'USD'),
    ('CPAchem', 'CPACHEM', 'EUR'),
    ('Cayman Chemical', 'CAYMAN CHEMICAL', 'USD'),
    ('ChemFaces', 'CHEMFACES', 'USD'),
    ('Chiron', 'CHIRON', 'USD'),
    ('Chromadex', 'CHROMADEX', 'USD'),
    ('EDQM', 'EDQM', 'EUR'),
    ('Extrasynthese', 'EXTRASYNTHESE', 'EUR'),
    ('HPC', 'HPC', 'EUR'),
    ('IsoSciences', 'ISOSCIENCES', 'USD'),
    ('LGC', 'LGC', 'EUR'),
    ('Larodan', 'LARODAN', 'EUR'),
    ('NIST', 'NIST', 'USD'),
    ('NMI', 'NMI', 'AUD'),
    ('NSI', 'NSI', 'USD'),
    ('Nacalai', 'NACALAI', 'USD'),
    ('PhytoLab', 'PHYTOLAB', 'EUR'),
    ('SPEX', 'SPEX', 'USD'),
    ('Sigma', 'SIGMA', 'USD'),
    ('TCS Biosciences', 'TCS BIOSCIENCES', 'EUR'),
    ('TLC Pharmaceutical', 'TLC PHARMACEUTICAL', 'USD'),
    ('TRC', 'TRC', 'USD'),
    ('True Indicating', 'TRUE INDICATING', 'USD'),
    ('USP', 'USP', 'USD'),
    ('Witega', 'WITEGA', 'EUR')
ON CONFLICT (name) DO UPDATE
SET normalized_name = EXCLUDED.normalized_name,
    currency_code   = EXCLUDED.currency_code,
    updated_at      = NOW();

-- ============================================================================
-- 3. Seed Brand Aliases (121 audited mappings + 35 self-aliases)
-- ============================================================================

INSERT INTO brand_aliases (alias, normalized_alias, brand_id)
SELECT DISTINCT ON (UPPER(TRIM(v.alias)))
    v.alias,
    UPPER(TRIM(v.alias)),
    bm.id
FROM (
    VALUES
    ('A2S', 'A2S'),
    ('A2S (Pháp)', 'A2S'),
    ('AccuStandard', 'AccuStandard'),
    ('AccuStandard (Mỹ)', 'AccuStandard'),
    ('Analytica Chemie', 'Analytica Chemie'),
    ('Analytica Chemie (Ấn Độ)', 'Analytica Chemie'),
    ('Anax', 'Anax'),
    ('Anax (Ấn Độ)', 'Anax'),
    ('Angene', 'Angene'),
    ('Angene (TQ)', 'Angene'),
    ('BOC', 'BOC'),
    ('BOC (Mỹ)', 'BOC'),
    ('BP', 'BP'),
    ('BP (Anh)', 'BP'),
    ('Biopurify', 'Biopurify'),
    ('Biopurify (Trung Quốc)', 'Biopurify'),
    ('Biosynth', 'Biosynth'),
    ('Biosynth (Anh)', 'Biosynth'),
    ('CATO', 'CATO'),
    ('CATO (TQ)', 'CATO'),
    ('CATO (Trung Quốc)', 'CATO'),
    ('CIL', 'CIL'),
    ('CIL (Mỹ)', 'CIL'),
    ('CPAchem', 'CPAchem'),
    ('CPAchem (Bulgaria)', 'CPAchem'),
    ('Cato (Trung Quốc)', 'CATO'),
    ('Cayman Chemical', 'Cayman Chemical'),
    ('Cayman Chemical (Mỹ)', 'Cayman Chemical'),
    ('ChemFaces', 'ChemFaces'),
    ('ChemFaces (Trung Quốc)', 'ChemFaces'),
    ('Chiron', 'Chiron'),
    ('Chiron (Na Uy)', 'Chiron'),
    ('Chromadex', 'Chromadex'),
    ('Chromadex (Mỹ)', 'Chromadex'),
    ('EDQM', 'EDQM'),
    ('EDQM (Pháp)', 'EDQM'),
    ('Extrasynthese', 'Extrasynthese'),
    ('Extrasynthese (Pháp)', 'Extrasynthese'),
    ('HPC', 'HPC'),
    ('HPC (Đức)', 'HPC'),
    ('IsoSciences', 'IsoSciences'),
    ('IsoSciences (Entegris)', 'IsoSciences'),
    ('LGC', 'LGC'),
    ('LGC (ACQ Science)', 'LGC'),
    ('LGC (ARMI/MBH)', 'LGC'),
    ('LGC (Alcan)', 'LGC'),
    ('LGC (Alpha Resources)', 'LGC'),
    ('LGC (Angstrom)', 'LGC'),
    ('LGC (Arconic)', 'LGC'),
    ('LGC (BAM)', 'LGC'),
    ('LGC (BAS)', 'LGC'),
    ('LGC (BCR)', 'LGC'),
    ('LGC (BDS)', 'LGC'),
    ('LGC (BP)', 'LGC'),
    ('LGC (Brammer)', 'LGC'),
    ('LGC (Breitländer)', 'LGC'),
    ('LGC (CCRL)', 'LGC'),
    ('LGC (CDN)', 'LGC'),
    ('LGC (CIL)', 'LGC'),
    ('LGC (CPA)', 'LGC'),
    ('LGC (CTIF)', 'LGC'),
    ('LGC (Cayman Chemical)', 'LGC'),
    ('LGC (Cerilliant)', 'LGC'),
    ('LGC (Certan)', 'LGC'),
    ('LGC (ChromaDex)', 'LGC'),
    ('LGC (Cifga)', 'LGC'),
    ('LGC (Dillinger Hütte)', 'LGC'),
    ('LGC (ECRM by BAS)', 'LGC'),
    ('LGC (ECRM by Irsid)', 'LGC'),
    ('LGC (EDQM)', 'LGC'),
    ('LGC (EMS)', 'LGC'),
    ('LGC (ENVCanada)', 'LGC'),
    ('LGC (ERM)', 'LGC'),
    ('LGC (FIP)', 'LGC'),
    ('LGC (FP)', 'LGC'),
    ('LGC (Fluxana)', 'LGC'),
    ('LGC (Geological Survey of Japan)', 'LGC'),
    ('LGC (HK)', 'LGC'),
    ('LGC (Hajek & Koucky)', 'LGC'),
    ('LGC (ICRM)', 'LGC'),
    ('LGC (ICTJ)', 'LGC'),
    ('LGC (IMN)', 'LGC'),
    ('LGC (IMZ)', 'LGC'),
    ('LGC (IP)', 'LGC'),
    ('LGC (IRMM)', 'LGC'),
    ('LGC (InBio)', 'LGC'),
    ('LGC (JP)', 'LGC'),
    ('LGC (JRC)', 'LGC'),
    ('LGC (JSAC)', 'LGC'),
    ('LGC (JSM)', 'LGC'),
    ('LGC (LTM)', 'LGC'),
    ('LGC (Larodan)', 'LGC'),
    ('LGC (Leco)', 'LGC'),
    ('LGC (Lipomed)', 'LGC'),
    ('LGC (LoGiCal)', 'LGC'),
    ('LGC (Maaßen)', 'LGC'),
    ('LGC (Medichem)', 'LGC'),
    ('LGC (Mikromol)', 'LGC'),
    ('LGC (Muva)', 'LGC'),
    ('LGC (NCS)', 'LGC'),
    ('LGC (NIST)', 'LGC'),
    ('LGC (NMIA)', 'LGC'),
    ('LGC (NMIJ)', 'LGC'),
    ('LGC (NRC)', 'LGC'),
    ('LGC (Nippon Light Metal Co)', 'LGC'),
    ('LGC (Paragon Scientific)', 'LGC'),
    ('LGC (RTC)', 'LGC'),
    ('LGC (ReCCS)', 'LGC'),
    ('LGC (Reagecon)', 'LGC'),
    ('LGC (Recipe)', 'LGC'),
    ('LGC (Romer)', 'LGC'),
    ('LGC (SGUDS)', 'LGC'),
    ('LGC (SPL)', 'LGC'),
    ('LGC (SUS Nell)', 'LGC'),
    ('LGC (Seishin)', 'LGC'),
    ('LGC (Sero)', 'LGC'),
    ('LGC (Sigma-Aldrich)', 'LGC'),
    ('LGC (TLC)', 'LGC'),
    ('LGC (VHG)', 'LGC'),
    ('LGC (Vaskut)', 'LGC'),
    ('LGC (WEPAL)', 'LGC'),
    ('LGC (Whitehouse Scientific)', 'LGC'),
    ('LGC (XRF)', 'LGC'),
    ('LGC (XRS)', 'LGC'),
    ('LGC (autofluxer)', 'LGC'),
    ('LGC (chemplex)', 'LGC'),
    ('LGC (Đức)', 'LGC'),
    ('Larodan', 'Larodan'),
    ('Larodan (Thụy Điển)', 'Larodan'),
    ('NIST', 'NIST'),
    ('NIST (Mỹ)', 'NIST'),
    ('NMI', 'NMI'),
    ('NMI (Úc)', 'NMI'),
    ('NSI', 'NSI'),
    ('NSI (Mỹ)', 'NSI'),
    ('Nacalai', 'Nacalai'),
    ('Nacalai (Nhật)', 'Nacalai'),
    ('PhytoLab', 'PhytoLab'),
    ('SPEX', 'SPEX'),
    ('SPEX (Mỹ)', 'SPEX'),
    ('Sigma', 'Sigma'),
    ('Sigma (Mỹ)', 'Sigma'),
    ('TCS Biosciences', 'TCS Biosciences'),
    ('TCS Biosciences (Anh)', 'TCS Biosciences'),
    ('TLC (Mỹ)', 'TLC Pharmaceutical'),
    ('TLC Pharmaceutical', 'TLC Pharmaceutical'),
    ('TRC', 'TRC'),
    ('TRC (Canada)', 'TRC'),
    ('True Indicating', 'True Indicating'),
    ('True Indicating (Mỹ)', 'True Indicating'),
    ('USP', 'USP'),
    ('USP (Mỹ)', 'USP'),
    ('Witega', 'Witega'),
    ('Witega (Đức)', 'Witega')
) AS v(alias, canonical_name)
JOIN brand_master bm ON bm.name = v.canonical_name
ORDER BY UPPER(TRIM(v.alias)), v.alias
ON CONFLICT (normalized_alias) DO UPDATE
SET brand_id   = EXCLUDED.brand_id,
    updated_at = NOW();

-- Explicitly ensure self-aliases for all 35 canonical brands in brand_master
INSERT INTO brand_aliases (alias, normalized_alias, brand_id)
SELECT bm.name, bm.normalized_name, bm.id
FROM brand_master bm
ON CONFLICT (normalized_alias) DO UPDATE
SET brand_id   = EXCLUDED.brand_id,
    updated_at = NOW();

-- ============================================================================
-- 4. Staging Tables & Fail-Closed Preflight Validation
-- ============================================================================

CREATE TEMP TABLE IF NOT EXISTS staging_brand_mapping (
    old_brand       TEXT PRIMARY KEY,
    canonical_brand TEXT NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE IF NOT EXISTS approved_delete_manifest (
    brand          TEXT PRIMARY KEY,
    expected_count INT NOT NULL
) ON COMMIT DROP;

CREATE TEMP TABLE IF NOT EXISTS delete_set_brands (
    brand           TEXT PRIMARY KEY
) ON COMMIT DROP;

CREATE TEMP TABLE IF NOT EXISTS staging_canonical_rates (
    brand           TEXT PRIMARY KEY,
    rate            NUMERIC NOT NULL
) ON COMMIT DROP;

-- Populate staging tables
INSERT INTO staging_brand_mapping (old_brand, canonical_brand)
VALUES
    ('A2S (Pháp)', 'A2S'),
    ('AccuStandard (Mỹ)', 'AccuStandard'),
    ('Analytica Chemie (Ấn Độ)', 'Analytica Chemie'),
    ('Anax (Ấn Độ)', 'Anax'),
    ('Angene (TQ)', 'Angene'),
    ('BOC (Mỹ)', 'BOC'),
    ('BP (Anh)', 'BP'),
    ('Biopurify (Trung Quốc)', 'Biopurify'),
    ('Biosynth (Anh)', 'Biosynth'),
    ('CATO (TQ)', 'CATO'),
    ('CATO (Trung Quốc)', 'CATO'),
    ('CIL (Mỹ)', 'CIL'),
    ('CPAchem (Bulgaria)', 'CPAchem'),
    ('Cato (Trung Quốc)', 'CATO'),
    ('Cayman Chemical (Mỹ)', 'Cayman Chemical'),
    ('ChemFaces (Trung Quốc)', 'ChemFaces'),
    ('Chiron (Na Uy)', 'Chiron'),
    ('Chromadex (Mỹ)', 'Chromadex'),
    ('EDQM (Pháp)', 'EDQM'),
    ('Extrasynthese (Pháp)', 'Extrasynthese'),
    ('HPC (Đức)', 'HPC'),
    ('IsoSciences (Entegris)', 'IsoSciences'),
    ('LGC (ACQ Science)', 'LGC'),
    ('LGC (ARMI/MBH)', 'LGC'),
    ('LGC (Alcan)', 'LGC'),
    ('LGC (Alpha Resources)', 'LGC'),
    ('LGC (Angstrom)', 'LGC'),
    ('LGC (Arconic)', 'LGC'),
    ('LGC (BAM)', 'LGC'),
    ('LGC (BAS)', 'LGC'),
    ('LGC (BCR)', 'LGC'),
    ('LGC (BDS)', 'LGC'),
    ('LGC (BP)', 'LGC'),
    ('LGC (Brammer)', 'LGC'),
    ('LGC (Breitländer)', 'LGC'),
    ('LGC (CCRL)', 'LGC'),
    ('LGC (CDN)', 'LGC'),
    ('LGC (CIL)', 'LGC'),
    ('LGC (CPA)', 'LGC'),
    ('LGC (CTIF)', 'LGC'),
    ('LGC (Cayman Chemical)', 'LGC'),
    ('LGC (Cerilliant)', 'LGC'),
    ('LGC (Certan)', 'LGC'),
    ('LGC (ChromaDex)', 'LGC'),
    ('LGC (Cifga)', 'LGC'),
    ('LGC (Dillinger Hütte)', 'LGC'),
    ('LGC (ECRM by BAS)', 'LGC'),
    ('LGC (ECRM by Irsid)', 'LGC'),
    ('LGC (EDQM)', 'LGC'),
    ('LGC (EMS)', 'LGC'),
    ('LGC (ENVCanada)', 'LGC'),
    ('LGC (ERM)', 'LGC'),
    ('LGC (FIP)', 'LGC'),
    ('LGC (FP)', 'LGC'),
    ('LGC (Fluxana)', 'LGC'),
    ('LGC (Geological Survey of Japan)', 'LGC'),
    ('LGC (HK)', 'LGC'),
    ('LGC (Hajek & Koucky)', 'LGC'),
    ('LGC (ICRM)', 'LGC'),
    ('LGC (ICTJ)', 'LGC'),
    ('LGC (IMN)', 'LGC'),
    ('LGC (IMZ)', 'LGC'),
    ('LGC (IP)', 'LGC'),
    ('LGC (IRMM)', 'LGC'),
    ('LGC (InBio)', 'LGC'),
    ('LGC (JP)', 'LGC'),
    ('LGC (JRC)', 'LGC'),
    ('LGC (JSAC)', 'LGC'),
    ('LGC (JSM)', 'LGC'),
    ('LGC (LTM)', 'LGC'),
    ('LGC (Larodan)', 'LGC'),
    ('LGC (Leco)', 'LGC'),
    ('LGC (Lipomed)', 'LGC'),
    ('LGC (LoGiCal)', 'LGC'),
    ('LGC (Maaßen)', 'LGC'),
    ('LGC (Medichem)', 'LGC'),
    ('LGC (Mikromol)', 'LGC'),
    ('LGC (Muva)', 'LGC'),
    ('LGC (NCS)', 'LGC'),
    ('LGC (NIST)', 'LGC'),
    ('LGC (NMIA)', 'LGC'),
    ('LGC (NMIJ)', 'LGC'),
    ('LGC (NRC)', 'LGC'),
    ('LGC (Nippon Light Metal Co)', 'LGC'),
    ('LGC (Paragon Scientific)', 'LGC'),
    ('LGC (RTC)', 'LGC'),
    ('LGC (ReCCS)', 'LGC'),
    ('LGC (Reagecon)', 'LGC'),
    ('LGC (Recipe)', 'LGC'),
    ('LGC (Romer)', 'LGC'),
    ('LGC (SGUDS)', 'LGC'),
    ('LGC (SPL)', 'LGC'),
    ('LGC (SUS Nell)', 'LGC'),
    ('LGC (Seishin)', 'LGC'),
    ('LGC (Sero)', 'LGC'),
    ('LGC (Sigma-Aldrich)', 'LGC'),
    ('LGC (TLC)', 'LGC'),
    ('LGC (VHG)', 'LGC'),
    ('LGC (Vaskut)', 'LGC'),
    ('LGC (WEPAL)', 'LGC'),
    ('LGC (Whitehouse Scientific)', 'LGC'),
    ('LGC (XRF)', 'LGC'),
    ('LGC (XRS)', 'LGC'),
    ('LGC (autofluxer)', 'LGC'),
    ('LGC (chemplex)', 'LGC'),
    ('LGC (Đức)', 'LGC'),
    ('Larodan (Thụy Điển)', 'Larodan'),
    ('NIST (Mỹ)', 'NIST'),
    ('NMI (Úc)', 'NMI'),
    ('NSI (Mỹ)', 'NSI'),
    ('Nacalai (Nhật)', 'Nacalai'),
    ('PhytoLab', 'PhytoLab'),
    ('SPEX (Mỹ)', 'SPEX'),
    ('Sigma (Mỹ)', 'Sigma'),
    ('TCS Biosciences (Anh)', 'TCS Biosciences'),
    ('TLC (Mỹ)', 'TLC Pharmaceutical'),
    ('TLC Pharmaceutical', 'TLC Pharmaceutical'),
    ('TRC (Canada)', 'TRC'),
    ('True Indicating (Mỹ)', 'True Indicating'),
    ('USP (Mỹ)', 'USP'),
    ('Witega (Đức)', 'Witega')
ON CONFLICT (old_brand) DO UPDATE
SET canonical_brand = EXCLUDED.canonical_brand;

INSERT INTO approved_delete_manifest (brand, expected_count)
VALUES
    ('ACROS', 29744),
    ('Aozeal(Mỹ)', 5214),
    ('Aquigen', 4649),
    ('Axios Research', 7173),
    ('BIOREAGENTS', 705),
    ('Bertin Technologies (not active - use vendor # 5869)', 127),
    ('Biosense Laboratories AS', 85),
    ('Chemservice (Mỹ)', 3108),
    ('Clearsynth', 74058),
    ('Columbia Bioscience, Inc.', 254),
    ('Eurofins Calixar', 24),
    ('FISHER CHEMICAL', 4070),
    ('MAYBRIDGE', 6741),
    ('Merck', 1331),
    ('NIFC (Việt Nam)', 103),
    ('Oxford - Ấn Độ', 463),
    ('Phụ lục I', 1),
    ('TCI', 54340),
    ('TEST1', 7),
    ('TEST2', 7),
    ('THERMO SCIENTIFIC', 29)
ON CONFLICT (brand) DO UPDATE
SET expected_count = EXCLUDED.expected_count;

INSERT INTO delete_set_brands (brand)
SELECT brand FROM approved_delete_manifest
ON CONFLICT (brand) DO NOTHING;

INSERT INTO staging_canonical_rates (brand, rate)
VALUES
    ('A2S', 31500.0),
    ('AccuStandard', 26500.0),
    ('Analytica Chemie', 26500.0),
    ('Anax', 26500.0),
    ('Angene', 26500.0),
    ('BOC', 26500.0),
    ('BP', 35500.0),
    ('Biopurify', 26500.0),
    ('Biosynth', 26500.0),
    ('CATO', 26500.0),
    ('CIL', 26500.0),
    ('CPAchem', 31500.0),
    ('Cayman Chemical', 26500.0),
    ('ChemFaces', 26500.0),
    ('Chiron', 26500.0),
    ('Chromadex', 26500.0),
    ('EDQM', 31500.0),
    ('Extrasynthese', 31500.0),
    ('HPC', 31500.0),
    ('IsoSciences', 26500.0),
    ('LGC', 31500.0),
    ('Larodan', 31500.0),
    ('NIST', 26500.0),
    ('NMI', 17200.0),
    ('NSI', 26500.0),
    ('Nacalai', 26500.0),
    ('PhytoLab', 31500.0),
    ('SPEX', 26500.0),
    ('Sigma', 26500.0),
    ('TCS Biosciences', 31500.0),
    ('TLC Pharmaceutical', 26500.0),
    ('TRC', 26500.0),
    ('True Indicating', 26500.0),
    ('USP', 26500.0),
    ('Witega', 31500.0)
ON CONFLICT (brand) DO UPDATE
SET rate = EXCLUDED.rate;

-- Preflight verification block (Fail-Closed against target drift)
DO $$
DECLARE
    v_canonical_count         INT;
    v_dup_norm_count          INT;
    v_unmapped_count          INT;
    v_unmapped_sample         TEXT;
    v_delete_set_total_actual INT;
    r                         RECORD;
BEGIN
    -- 1. Verify exactly 35 canonical brands in brand_master
    SELECT COUNT(*) INTO v_canonical_count FROM brand_master WHERE is_active = TRUE;
    IF v_canonical_count <> 35 THEN
        RAISE EXCEPTION 'Preflight check failed: expected 35 active canonical brands, got %', v_canonical_count;
    END IF;

    -- 2. Verify no duplicate normalized keys in brand_master
    SELECT COUNT(*) INTO v_dup_norm_count
    FROM (
        SELECT normalized_name FROM brand_master GROUP BY normalized_name HAVING COUNT(*) > 1
    ) dup;
    IF v_dup_norm_count > 0 THEN
        RAISE EXCEPTION 'Preflight check failed: duplicate normalized_name in brand_master';
    END IF;

    -- 3. Verify all existing non-empty brands in products are accounted for
    SELECT COUNT(DISTINCT p.brand) INTO v_unmapped_count
    FROM products p
    WHERE p.brand IS NOT NULL
      AND TRIM(p.brand) <> ''
      AND p.brand NOT IN (SELECT old_brand FROM staging_brand_mapping)
      AND p.brand NOT IN (SELECT brand FROM delete_set_brands)
      AND p.brand NOT IN (SELECT name FROM brand_master);

    IF v_unmapped_count > 0 THEN
        SELECT string_agg(quote_literal(b), ', ') INTO v_unmapped_sample
        FROM (
            SELECT DISTINCT p.brand AS b
            FROM products p
            WHERE p.brand IS NOT NULL
              AND TRIM(p.brand) <> ''
              AND p.brand NOT IN (SELECT old_brand FROM staging_brand_mapping)
              AND p.brand NOT IN (SELECT brand FROM delete_set_brands)
              AND p.brand NOT IN (SELECT name FROM brand_master)
            LIMIT 5
        ) s;
        RAISE EXCEPTION 'Preflight check failed: % unmapped brand(s) found in products. Sample: %', v_unmapped_count, v_unmapped_sample;
    END IF;

    -- 4. Verify test row 1344915 preconditions: fail closed if present with mismatching attributes
    IF EXISTS (SELECT 1 FROM products WHERE id = 1344915) THEN
        IF NOT EXISTS (
            SELECT 1 FROM products
            WHERE id = 1344915
              AND brand = 'Phụ lục I'
              AND name = 'TEST XÓA'
              AND (code IS NULL OR TRIM(code) = '')
              AND (cas IS NULL OR TRIM(cas) = '')
        ) THEN
            RAISE EXCEPTION 'Preflight check failed: row id=1344915 exists but preconditions do not match (expected brand="Phụ lục I", name="TEST XÓA", empty code/cas)';
        END IF;
    END IF;

    -- 5. Target drift check: verify delete set counts on target match approved manifest
    SELECT COUNT(*) INTO v_delete_set_total_actual
    FROM products p
    JOIN approved_delete_manifest m ON p.brand = m.brand;

    -- If target database contains unmigrated delete set products, all counts must match manifest exactly
    IF v_delete_set_total_actual > 0 THEN
        IF v_delete_set_total_actual <> 192233 THEN
            RAISE EXCEPTION 'Target drift detected: total delete set products count is %, expected 192,233. Halting migration before hard-delete.',
                v_delete_set_total_actual;
        END IF;

        FOR r IN
            SELECT m.brand, m.expected_count, COALESCE(p.actual_count, 0) AS actual_count
            FROM approved_delete_manifest m
            LEFT JOIN (
                SELECT brand, COUNT(*) AS actual_count
                FROM products
                GROUP BY brand
            ) p ON p.brand = m.brand
            WHERE COALESCE(p.actual_count, 0) <> m.expected_count
        LOOP
            RAISE EXCEPTION 'Target drift detected for delete set brand "%": expected % products, found %. Halting migration before hard-delete.',
                r.brand, r.expected_count, r.actual_count;
        END LOOP;
    END IF;
END $$;

-- ============================================================================
-- 5. Data Migration: Products (Single-Pass Update & Controlled Delete)
-- ============================================================================

-- Delete test row 1344915 if precondition matches
DELETE FROM products
WHERE id = 1344915
  AND brand = 'Phụ lục I'
  AND name = 'TEST XÓA'
  AND (code IS NULL OR TRIM(code) = '')
  AND (cas IS NULL OR TRIM(cas) = '');

-- Delete products in Delete Set
DELETE FROM products
WHERE brand IN (SELECT brand FROM delete_set_brands);

-- Single-pass UPDATE for retained products
UPDATE products p
SET source_brand = COALESCE(p.source_brand, p.brand),
    brand        = m.canonical_brand
FROM staging_brand_mapping m
WHERE p.brand = m.old_brand
  AND (p.brand <> m.canonical_brand OR p.source_brand IS NULL);

-- ============================================================================
-- 6. Data Migration: team_brands (Canonicalize & Clean)
-- ============================================================================

-- Insert canonical brands for teams having old aliases
INSERT INTO team_brands (team_id, brand)
SELECT DISTINCT tb.team_id, m.canonical_brand
FROM team_brands tb
JOIN staging_brand_mapping m ON tb.brand = m.old_brand
ON CONFLICT (team_id, brand) DO NOTHING;

-- Delete old non-canonical aliases from team_brands
DELETE FROM team_brands tb
USING staging_brand_mapping m
WHERE tb.brand = m.old_brand
  AND tb.brand NOT IN (SELECT name FROM brand_master);

-- Clean legacy pseudo-brands and Delete Set from team_brands
DELETE FROM team_brands
WHERE brand IN ('CẤM NHẬP', 'Phụ lục I', 'Phụ lục II', 'Phụ lục III');

DELETE FROM team_brands
WHERE brand IN (SELECT brand FROM delete_set_brands);

-- ============================================================================
-- 7. Data Migration: brand_compliance_settings & exchange_rates
-- ============================================================================

-- Clean obsolete test brands from brand_compliance_settings
DELETE FROM brand_compliance_settings
WHERE brand_norm IN ('TEST1', 'TEST2')
   OR brand_norm IN (SELECT UPPER(TRIM(brand)) FROM delete_set_brands);

-- Upsert 35 canonical brand rates to exchange_rates (no TRUNCATE)
ALTER TABLE exchange_rates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

INSERT INTO exchange_rates (brand, rate, updated_at)
SELECT brand, rate, NOW()
FROM staging_canonical_rates
ON CONFLICT (brand) DO UPDATE
SET rate       = EXCLUDED.rate,
    updated_at = NOW();

-- Delete obsolete legacy keys from exchange_rates
DELETE FROM exchange_rates
WHERE brand NOT IN (SELECT name FROM brand_master);

-- ============================================================================
-- 8. DB-Level Schema Integrity (Lock-Safe: NOT VALID -> VALIDATE CONSTRAINT)
-- ============================================================================
-- Rationale: application-level validation (Brand Gateway) alone is NOT a
-- sufficient guarantee against bad data -- a bypassed write path (a raw
-- script, a future code path that forgets to call the gateway, a manual
-- psql session) could still insert products.brand or team_brands.brand
-- values outside brand_master, or a NULL products.source_brand. These
-- constraints make that structurally impossible at the database level,
-- regardless of which code path performs the write.
--
-- Each ADD CONSTRAINT is split into two steps to stay lock-safe on
-- PostgreSQL 12+ (including 16): the NOT VALID form takes only a brief
-- ACCESS EXCLUSIVE lock to add the catalog entry (no table scan, near
-- instant), and immediately starts enforcing the rule for all NEW
-- writes. VALIDATE CONSTRAINT then scans existing rows under a much
-- weaker SHARE UPDATE EXCLUSIVE lock (blocks other DDL, but NOT
-- concurrent SELECT/INSERT/UPDATE/DELETE), so the app stays responsive
-- during that scan. Both steps are guarded to stay idempotent: NOT VALID
-- is only added once (pg_constraint existence check, since PostgreSQL
-- has no native "ADD CONSTRAINT IF NOT EXISTS"), and VALIDATE CONSTRAINT
-- on an already-validated constraint is a fast no-op.

-- Supports the new FK lookups below and general brand-filtered queries
-- (importer, preflight, search). Not previously needed because nothing
-- joined against products.brand at scale; the new FK constraint does.
CREATE INDEX IF NOT EXISTS idx_products_brand ON products (brand);

-- 8a. products.source_brand must never be NULL for any retained/new row.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_products_source_brand_not_null'
    ) THEN
        ALTER TABLE products
            ADD CONSTRAINT chk_products_source_brand_not_null
            CHECK (source_brand IS NOT NULL) NOT VALID;
    END IF;
END $$;

ALTER TABLE products VALIDATE CONSTRAINT chk_products_source_brand_not_null;

-- On PostgreSQL 12+, SET NOT NULL can skip its own full-table verification
-- scan when a validated CHECK constraint already proves the same guarantee,
-- making this a fast metadata-only change (no additional lock escalation).
ALTER TABLE products ALTER COLUMN source_brand SET NOT NULL;

-- 8b. products.brand must always reference an active-or-inactive canonical
-- brand_master row (RESTRICT/NO ACTION by default: cannot delete a
-- brand_master row that products still reference). Deliberately NOT applied
-- to products.source_brand, which is historical provenance/alias text (may
-- legitimately hold retired alias strings like 'LGC (Mikromol)') and must
-- stay free-form.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_products_brand_master'
    ) THEN
        ALTER TABLE products
            ADD CONSTRAINT fk_products_brand_master
            FOREIGN KEY (brand) REFERENCES brand_master(name) NOT VALID;
    END IF;
END $$;

ALTER TABLE products VALIDATE CONSTRAINT fk_products_brand_master;

-- 8c. team_brands.brand must always reference a canonical brand_master row,
-- so a team can never be granted visibility into a non-canonical/legacy brand.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_team_brands_brand_master'
    ) THEN
        ALTER TABLE team_brands
            ADD CONSTRAINT fk_team_brands_brand_master
            FOREIGN KEY (brand) REFERENCES brand_master(name) NOT VALID;
    END IF;
END $$;

ALTER TABLE team_brands VALIDATE CONSTRAINT fk_team_brands_brand_master;

COMMIT;
