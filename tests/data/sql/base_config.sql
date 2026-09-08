

-- ========= csipaus_control ========
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
    


-- ========= csipaus_control_response ========
--
-- Sent: #1, #5
--
-- Unsent (based on not_before):
-- 00:00    00:05   00:10
--   #2       #3    #6
--            #4
--     
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 1, 'aaa', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z', '2000-01-01T00:00:00Z');
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 2, 'aaa', '2026-01-01T00:00:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 3, 'aaa', '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (2, 1, 'bbb', '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (3, 1, 'ccc', '2026-01-01T00:10:00Z', '2025-01-01T00:00:00Z', '2000-01-01T00:00:00Z');
INSERT INTO csipaus_control_response (csipaus_control_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (3, 99, 'ccc', '2026-01-01T00:10:00Z', NULL, '2000-01-01T00:00:00Z');


-- ========= csipaus_default ========
--
-- A rolling history of "active" defaults. finished_at is set to the max date for the currently active record.
-- active_range is a generated column so it is never inserted directly.
--
-- 00:00       00:05       00:10        MAX_DATE
--   |   #1      |    #2     |            #3 (current)
INSERT INTO csipaus_default (started_at, finished_at, created_at, ramp_percent_max_second_hundredths, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES ('2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z', '2000-01-01T00:00:00Z', 11, TRUE, FALSE, 1001, 1002, 1003, 1004, 1005);
INSERT INTO csipaus_default (started_at, finished_at, created_at, ramp_percent_max_second_hundredths, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES ('2026-01-01T00:05:00Z', '2026-01-01T00:10:00Z', '2000-01-01T00:00:00Z', 21, FALSE, TRUE, 2001, 2002, 2003, 2004, 2005);
INSERT INTO csipaus_default (started_at, finished_at, created_at, ramp_percent_max_second_hundredths, connect, energize, import_limit_watts, export_limit_watts, load_limit_watts, generation_limit_watts, storage_target_watts)
VALUES ('2026-01-01T00:10:00Z', '9999-1-1T00:00:00Z', '2000-01-01T00:00:00Z', 31, TRUE, TRUE, 3001, 3002, 3003, 3004, 3005);


-- ========= csipaus_dynamic_price ========
--
-- Mirrors the csipaus_control layout above (no superseded_at on this entity - #7 is cancelled instead).
--
-- 00:00    00:05   00:10   00:15
--   |  #1    |   #2  |   #3  |
--   |            #4          |
--   |            #5          |   (NULL price_kwh)
--   |        X   #6          |   (cancelled 00:05)
--   |            #7  X       |   (cancelled 00:10)
--
-- value column for id N is N.000N
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (1, '1111', 300, '2026-01-01T00:00:00Z', NULL, '2000-01-01T00:00:00Z', 1.0001);
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (1, '2222', 300, '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z', 2.0002);
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (1, '3333', 300, '2026-01-01T00:10:00Z', NULL, '2000-01-01T00:00:00Z', 3.0003);
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (2, '4444', 900, '2026-01-01T00:00:00Z', NULL, '2000-01-01T00:00:00Z', 4.0004);
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (0, '5555', 900, '2026-01-01T00:00:00Z', NULL, '2000-01-01T00:00:00Z', NULL);

-- Cancelled at 5 minutes
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (0, '6666', 900, '2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z', '2000-01-01T00:00:00Z', 6.0006);

-- Cancelled at 10 minutes
INSERT INTO csipaus_dynamic_price (primacy, mrid, duration_seconds, started_at, cancelled_at, created_at, price_kwh)
VALUES (0, '7777', 900, '2026-01-01T00:00:00Z', '2026-01-01T00:10:00Z', '2000-01-01T00:00:00Z', 7.0007);


-- ========= csipaus_dynamic_price_response ========
--
-- Mirrors csipaus_control_response above.
--
-- Sent: #1, #5
--
-- Unsent (based on not_before):
-- 00:00    00:05   00:10
--   #2       #3    #6
--            #4
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 1, 'aaa', '2026-01-01T00:00:00Z', '2026-01-01T00:00:01Z', '2000-01-01T00:00:00Z');
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 2, 'aaa', '2026-01-01T00:00:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (1, 3, 'aaa', '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (2, 1, 'bbb', '2026-01-01T00:05:00Z', NULL, '2000-01-01T00:00:00Z');
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (3, 1, 'ccc', '2026-01-01T00:10:00Z', '2025-01-01T00:00:00Z', '2000-01-01T00:00:00Z');
INSERT INTO csipaus_dynamic_price_response (csipaus_dynamic_price_id, response_status, end_device_lfdi, not_before, sent_at, created_at)
VALUES (3, 99, 'ccc', '2026-01-01T00:10:00Z', NULL, '2000-01-01T00:00:00Z');


-- ========= ocpp_reading ========
--
-- reading_start is deliberately NOT in id order so the ORDER BY (reading_start ASC, id ASC) is exercised.
-- ids 1 & 6 share a reading_start so the id ASC tie-break matters.
--
-- id   reading_start
--  2   00:00
--  4   00:05
--  1   00:10
--  6   00:10
--  5   00:15
--  3   00:20
--
-- value columns for id N are numbered N01..N07
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:10:00Z', '2000-01-01T00:00:00Z', 101, 102, 103, 104, 105, 106, 107);
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:00:00Z', '2000-01-01T00:00:00Z', 201, 202, 203, 204, 205, 206, 207);
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:20:00Z', '2000-01-01T00:00:00Z', 301, 302, 303, 304, 305, 306, 307);
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:05:00Z', '2000-01-01T00:00:00Z', 401, 402, 403, 404, 405, 406, 407);
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:15:00Z', '2000-01-01T00:00:00Z', 501, 502, 503, 504, 505, 506, 507);
INSERT INTO ocpp_reading (reading_start, created_at, frequency_hz, import_active_power_watts, export_active_power_watts, import_reactive_power_var, export_reactive_power_var, soc_percent, voltage_volts)
VALUES ('2026-01-01T00:10:00Z', '2000-01-01T00:00:00Z', 601, 602, 603, 604, 605, 606, 607);


-- ========= ocpp_metadata ========
--
-- created_at is deliberately NOT in id order - fetch_ocpp_metadata returns the most recent created_at, which is id 2.
--
-- id   created_at
--  1   00:00
--  3   00:05
--  2   00:10  <- latest
--
-- value columns for id N are numbered N001..N006
INSERT INTO ocpp_metadata (created_at, max_voltage_volts, min_voltage_volts, max_power_watts, max_charge_rate_watts, max_discharge_rate_watts, set_grad_w)
VALUES ('2026-01-01T00:00:00Z', 1001, 1002, 1003, 1004, 1005, 1006);
INSERT INTO ocpp_metadata (created_at, max_voltage_volts, min_voltage_volts, max_power_watts, max_charge_rate_watts, max_discharge_rate_watts, set_grad_w)
VALUES ('2026-01-01T00:10:00Z', 2001, 2002, 2003, 2004, 2005, 2006);
INSERT INTO ocpp_metadata (created_at, max_voltage_volts, min_voltage_volts, max_power_watts, max_charge_rate_watts, max_discharge_rate_watts, set_grad_w)
VALUES ('2026-01-01T00:05:00Z', 3001, 3002, 3003, 3004, 3005, 3006);


-- ========= csipaus_config ========
--
-- created_at is deliberately NOT in id order - fetch_csipaus_config returns the most recent created_at, which is id 2.
--
-- id   created_at
--  1   00:00
--  3   00:05
--  2   00:10  <- latest (current)
INSERT INTO csipaus_config (created_at, is_aggregator, certificate_pem, key_pem, nmi, client_pen, dcap_uri, serca_pem, verify_hostname, verify_ssl)
VALUES ('2026-01-01T00:00:00Z', TRUE, '\x616161'::bytea, '\x626262'::bytea, 'NMI001', 1001, 'https://example.com/dcap1', '\x636363'::bytea, TRUE, TRUE);
INSERT INTO csipaus_config (created_at, is_aggregator, certificate_pem, key_pem, nmi, client_pen, dcap_uri, serca_pem, verify_hostname, verify_ssl)
VALUES ('2026-01-01T00:10:00Z', FALSE, '\x646464'::bytea, '\x656565'::bytea, 'NMI002', 2002, 'https://example.com/dcap2', '\x666666'::bytea, FALSE, FALSE);
INSERT INTO csipaus_config (created_at, is_aggregator, certificate_pem, key_pem, nmi, client_pen, dcap_uri, serca_pem, verify_hostname, verify_ssl)
VALUES ('2026-01-01T00:05:00Z', TRUE, NULL, NULL, NULL, NULL, NULL, NULL, TRUE, TRUE);

