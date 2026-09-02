

-- csipaus_control
--
-- 00:00    00:05   00:10   00:15
--   |  #1    |   #2  |   #3  |
--   |            #4          |
--   |            #5          |
--   |        X   #6          |
--   |            #7  X       |
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (1, '1111', 300, '2026-01-01T00:00:00Z', NULL, NULL, '2000-01-01T00:00:00Z', 100, NULL, NULL, 101, 102, 103, 104, 105);
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (1, '2222', 300, '2026-01-01T00:05:00Z', NULL, NULL, '2000-01-01T00:00:00Z', 200, NULL, NULL, 201, 202, 203, 204, 205);
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (1, '3333', 300, '2026-01-01T00:10:00Z', NULL, NULL, '2000-01-01T00:00:00Z', 300, NULL, NULL, 301, 302, 303, 304, 305);
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (2, '4444', 900, '2026-01-01T00:00:00Z', NULL, NULL, '2000-01-01T00:00:00Z', 400, NULL, NULL, 401, 402, 403, 404, 405);
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (0, '5555', 900, '2026-01-01T00:00:00Z', NULL, NULL, '2000-01-01T00:00:00Z', 500, NULL, NULL, NULL, NULL, NULL, NULL, NULL);

-- Cancelled at 5 minutes
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (0, '6666', 900, '2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z', 600, NULL, NULL, 601, 602, 603, 604, 605);

-- Superseded at 10 minutes
INSERT INTO csipaus_control (primacy, mrid, duration_seconds, started_at, cancelled_at, superseded_at, created_at, ramp_time_seconds, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES (0, '7777', 900, '2026-01-01T00:00:00Z', NULL, '2026-01-01T00:10:00Z', '2000-01-01T00:00:00Z', 700, NULL, NULL, 701, 702, 703, 704, 705);
    