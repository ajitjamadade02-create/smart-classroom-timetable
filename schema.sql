-- =========================================================================
-- schema.sql — Smart Classroom & Timetable Scheduler (PostgreSQL)
-- Loaded automatically by app.py on startup (CREATE TABLE IF NOT EXISTS),
-- so this file and app.py always stay in sync: every column here is read
-- or written somewhere in app.py, and nothing in app.py references a
-- column that isn't defined here.
-- =========================================================================

CREATE TABLE IF NOT EXISTS admin (
    id            SERIAL PRIMARY KEY,
    login_id      TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT now()
);

-- One row per admin: the institute-wide clock (changeable start/end/period length)
CREATE TABLE IF NOT EXISTS institute (
    id             SERIAL PRIMARY KEY,
    admin_id       INTEGER UNIQUE REFERENCES admin(id) ON DELETE CASCADE,
    name           TEXT DEFAULT 'My Institute',
    day_start      TEXT DEFAULT '09:00',   -- 'HH:MM' 24h
    day_end        TEXT DEFAULT '17:00',
    period_minutes INTEGER DEFAULT 55,
    working_days   TEXT[] DEFAULT ARRAY['Mon','Tue','Wed','Thu','Fri'],
    holidays       TEXT[] DEFAULT ARRAY[]::TEXT[]
);

-- Adjustable short/lunch breaks, any number of them
CREATE TABLE IF NOT EXISTS break_slot (
    id            SERIAL PRIMARY KEY,
    institute_id  INTEGER REFERENCES institute(id) ON DELETE CASCADE,
    label         TEXT DEFAULT 'Break',
    kind          TEXT DEFAULT 'short',   -- short | lunch
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL
);

-- FY/SY/TY/LY x odd/even -> roman semester number
CREATE TABLE IF NOT EXISTS academic_year (
    id        SERIAL PRIMARY KEY,
    admin_id  INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    label     TEXT NOT NULL,     -- FY / SY / TY / LY
    parity    TEXT NOT NULL,     -- odd | even
    sem_no    TEXT NOT NULL      -- I..VIII
);

CREATE TABLE IF NOT EXISTS division (
    id            SERIAL PRIMARY KEY,
    year_id       INTEGER REFERENCES academic_year(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,     -- "SY A"
    class_teacher TEXT,
    strength      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS batch (
    id            SERIAL PRIMARY KEY,
    division_id   INTEGER REFERENCES division(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,     -- "B1"
    num_students  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS faculty (
    id                  SERIAL PRIMARY KEY,
    admin_id            INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,
    qualifications      TEXT[] DEFAULT ARRAY[]::TEXT[],  -- subject codes they can teach
    max_periods_per_day INTEGER DEFAULT 6
);

CREATE TABLE IF NOT EXISTS classroom (
    id        SERIAL PRIMARY KEY,
    admin_id  INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    room_no   TEXT NOT NULL,
    room_type TEXT DEFAULT 'classroom',  -- classroom | lab
    capacity  INTEGER DEFAULT 60
);

CREATE TABLE IF NOT EXISTS subject (
    id                     SERIAL PRIMARY KEY,
    year_id                INTEGER REFERENCES academic_year(id) ON DELETE CASCADE,
    code                   TEXT NOT NULL,
    name                   TEXT NOT NULL,
    type                   TEXT NOT NULL DEFAULT 'lecture',  -- lecture | lab | tutorial
    lectures_per_week      INTEGER DEFAULT 3,
    priority_first_period  BOOLEAN DEFAULT FALSE,
    external_fixed_days    TEXT[] DEFAULT ARRAY[]::TEXT[],    -- empty = solver chooses; 1+ days = admin picks any of these
    combine_divisions      INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    multi_faculty          BOOLEAN DEFAULT FALSE,
    syllabus_name          TEXT,
    syllabus_data          BYTEA
);

-- Which faculty teach which subject, to which division/batch(es) (many-to-many).
-- batch_ids empty = whole division; one id = a normal single batch; 2+ ids =
-- those batches are taught together as one merged session by this faculty
-- (e.g. a lab where two batches combine) — chosen directly here, at
-- allocation time, rather than as a separate subject-level setup step.
CREATE TABLE IF NOT EXISTS faculty_allocation (
    id           SERIAL PRIMARY KEY,
    subject_id   INTEGER REFERENCES subject(id) ON DELETE CASCADE,
    faculty_id   INTEGER REFERENCES faculty(id) ON DELETE CASCADE,
    division_id  INTEGER REFERENCES division(id) ON DELETE CASCADE,
    batch_ids    INTEGER[] DEFAULT ARRAY[]::INTEGER[]
);

-- The single source of truth every one of the five timetable views reads from
CREATE TABLE IF NOT EXISTS timetable_entry (
    id                SERIAL PRIMARY KEY,
    admin_id          INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    day               TEXT NOT NULL,
    period_index      INTEGER NOT NULL,
    subject_id        INTEGER REFERENCES subject(id) ON DELETE CASCADE,
    division_id       INTEGER REFERENCES division(id) ON DELETE CASCADE,
    batch_id          INTEGER REFERENCES batch(id) ON DELETE SET NULL,
    classroom_id      INTEGER REFERENCES classroom(id) ON DELETE CASCADE,
    faculty_ids       INTEGER[] DEFAULT ARRAY[]::INTEGER[],  -- usually 1, >1 for co-taught sessions
    session_group_id  TEXT,
    entry_type        TEXT DEFAULT 'lecture'
);
CREATE INDEX IF NOT EXISTS idx_tt_admin_day_period ON timetable_entry(admin_id, day, period_index);

-- =========================================================================
-- Migrations for installs created before this update. Safe to run every
-- time app.py starts (IF EXISTS / IF NOT EXISTS guards) — it upgrades an
-- existing database in place without touching any data you've already
-- entered.
-- =========================================================================

-- external_fixed_day (single TEXT) -> external_fixed_days (TEXT[], multi-choice)
ALTER TABLE subject ADD COLUMN IF NOT EXISTS external_fixed_days TEXT[] DEFAULT ARRAY[]::TEXT[];
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='subject' AND column_name='external_fixed_day') THEN
        UPDATE subject
           SET external_fixed_days = ARRAY[external_fixed_day]
         WHERE external_fixed_day IS NOT NULL
           AND (external_fixed_days IS NULL OR external_fixed_days = ARRAY[]::TEXT[]);
        ALTER TABLE subject DROP COLUMN external_fixed_day;
    END IF;
END $$;

-- faculty_allocation.batch_id (single) -> batch_ids (INTEGER[], 2+ ids = a
-- merged/combined-batch session). Picking which batches to merge now
-- happens directly on the allocation, so subject.combine_batches is retired.
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS batch_ids INTEGER[] DEFAULT ARRAY[]::INTEGER[];
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name='faculty_allocation' AND column_name='batch_id') THEN
        UPDATE faculty_allocation
           SET batch_ids = ARRAY[batch_id]
         WHERE batch_id IS NOT NULL
           AND (batch_ids IS NULL OR batch_ids = ARRAY[]::INTEGER[]);
        ALTER TABLE faculty_allocation DROP COLUMN batch_id;
    END IF;
END $$;
ALTER TABLE subject DROP COLUMN IF EXISTS combine_batches;

CREATE TABLE IF NOT EXISTS schedule_run (
    id            SERIAL PRIMARY KEY,
    admin_id      INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    ran_at        TIMESTAMP DEFAULT now(),
    placed_count  INTEGER DEFAULT 0,
    unplaced      JSONB DEFAULT '[]'::JSONB
);
ALTER TABLE subject ADD COLUMN IF NOT EXISTS lectures_per_week      INTEGER DEFAULT 3;
ALTER TABLE subject ADD COLUMN IF NOT EXISTS priority_first_period  BOOLEAN DEFAULT FALSE;
ALTER TABLE subject ADD COLUMN IF NOT EXISTS combine_divisions      INTEGER[] DEFAULT ARRAY[]::INTEGER[];
ALTER TABLE subject ADD COLUMN IF NOT EXISTS multi_faculty          BOOLEAN DEFAULT FALSE;
ALTER TABLE subject ADD COLUMN IF NOT EXISTS syllabus_name          TEXT;
ALTER TABLE subject ADD COLUMN IF NOT EXISTS syllabus_data          BYTEA;
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS batch_ids   INTEGER[] DEFAULT ARRAY[]::INTEGER[];

ALTER TABLE subject ADD COLUMN IF NOT EXISTS priority_slot TEXT DEFAULT 'any';
UPDATE subject SET priority_slot = 'first' WHERE priority_first_period = TRUE AND priority_slot = 'any';
UPDATE subject SET priority_slot = 'any'   WHERE priority_slot IS NULL;

-- Saved "template / last timetable" for the editable-PDF download: the uploaded
-- reference file plus the heading/footer wording used when generating from it.
CREATE TABLE IF NOT EXISTS pdf_template (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER UNIQUE REFERENCES admin(id) ON DELETE CASCADE,
    file_name   TEXT,
    file_data   BYTEA,
    college     TEXT,
    department  TEXT,
    title       TEXT,
    hod_name    TEXT,
    hod_dept    TEXT,
    updated_at  TIMESTAMP DEFAULT now()
);

-- Max lectures of ONE subject in a single day for a division (1, 2 or 3).
-- Default 2 matches the previous behaviour (spread out first, a 2nd same-day lecture only if needed).
ALTER TABLE subject ADD COLUMN IF NOT EXISTS max_per_day INTEGER DEFAULT 2;
UPDATE subject SET max_per_day = 2 WHERE max_per_day IS NULL;
-- priority_slot now also accepts: before_lunch | after_lunch (plus any | first | last)

-- =========================================================================
-- Student / Teacher portals
-- portal_user: logins for the two read-only portals. A teacher account is tied
-- to one faculty row (that is the ONLY personal timetable they can open); a
-- student account is tied to one division (their default view).
-- notification: one row every time the admin changes the timetable. Both
-- portals poll it, refresh their grids when it changes, and show the message
-- to whoever it concerns. Both arrays empty = broadcast to everybody.
-- =========================================================================
CREATE TABLE IF NOT EXISTS portal_user (
    id                         SERIAL PRIMARY KEY,
    admin_id                   INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    role                       TEXT NOT NULL,            -- student | teacher
    login_id                   TEXT UNIQUE NOT NULL,
    name                       TEXT NOT NULL,
    password_hash              TEXT NOT NULL,
    faculty_id                 INTEGER REFERENCES faculty(id) ON DELETE CASCADE,
    division_id                INTEGER REFERENCES division(id) ON DELETE SET NULL,
    last_seen_notification_id  INTEGER DEFAULT 0,
    created_at                 TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS notification (
    id            SERIAL PRIMARY KEY,
    admin_id      INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    message       TEXT NOT NULL,
    division_ids  INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    faculty_ids   INTEGER[] DEFAULT ARRAY[]::INTEGER[],
    created_at    TIMESTAMP DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notification_admin ON notification(admin_id, id);


-- Faculty workload sheet (Sr. No / Name / Designation / Class / Course / Theory / Tutorial / Practical / Divisions / Sum)
ALTER TABLE faculty ADD COLUMN IF NOT EXISTS designation TEXT;
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS class_label   TEXT;     -- "Class" cell exactly as written in the file
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS theory_hrs     INTEGER;  -- hours per week, as written in the file
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS tutorial_hrs   INTEGER;
ALTER TABLE faculty_allocation ADD COLUMN IF NOT EXISTS practical_hrs  INTEGER;


-- The admin's own "demo sheet" for the Faculty step (optional). When empty, the built-in demo sheet is served.
CREATE TABLE IF NOT EXISTS faculty_demo_sheet (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER UNIQUE REFERENCES admin(id) ON DELETE CASCADE,
    file_name   TEXT,
    file_data   BYTEA,
    updated_at  TIMESTAMP DEFAULT now()
);


-- Each division's assigned ("home") classroom, stored as the room number (e.g. CSAMCR-06). All of the
-- division's lectures are scheduled there; labs go to any lab. Resolved against classroom.room_no at generate time.
ALTER TABLE division ADD COLUMN IF NOT EXISTS home_room TEXT;

-- Roll call sheet uploaded by the administrator for a division (any format: pdf, xlsx, xls, csv, docx, image...).
-- One file per division; uploading again replaces it. Students of that division and teachers can view/download it.
CREATE TABLE IF NOT EXISTS division_rollcall (
    id          SERIAL PRIMARY KEY,
    admin_id    INTEGER REFERENCES admin(id) ON DELETE CASCADE,
    division_id INTEGER UNIQUE REFERENCES division(id) ON DELETE CASCADE,
    file_name   TEXT NOT NULL,
    mime_type   TEXT,
    file_data   BYTEA NOT NULL,
    updated_at  TIMESTAMP DEFAULT now()
);