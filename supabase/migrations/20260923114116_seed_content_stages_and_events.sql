-- Channel Analyst, Step 1: starting configuration (docs/analyst-build-spec.md, section 8).
-- Seeds account_stages and business_events only. Both stay editable.

insert into content.account_stages (id, name, start_date, end_date, goal_metrics) values
    (1, 'Cohort launch',                     '2024-01-01', '2024-06-30', 'Growth'),
    (2, 'Evergreen PPP',                     '2024-07-01', '2025-09-30', 'Growth and authority'),
    (3, 'Multi-product',                     '2025-10-01', '2026-05-31', 'Conversion across several offers'),
    (4, 'High-ticket (Rebuilt, VIP/Apply)',  '2026-06-01', null,         'Conversion');

-- Explicit ids were inserted, so move the identity sequence past them.
select setval(pg_get_serial_sequence('content.account_stages', 'id'), (select max(id) from content.account_stages));

-- Approximate dates are stored as the first day of the earliest month named;
-- date_label keeps the original wording.
insert into content.business_events (event_date, event_name, is_approximate, date_label, notes) values
    ('2024-01-01', 'PPP cohort 1',            true, 'Jan 2024',     null),
    ('2024-02-01', 'PPP cohort 2',            true, 'Feb/Mar 2024', null),
    ('2024-03-01', 'PPP cohort 3',            true, 'Mar/Apr 2024', null),
    ('2024-06-01', 'PPP moves to evergreen',  true, 'Jun/Jul 2024', null),
    ('2025-10-01', 'She Gets Lean launch',    true, 'Oct 2025',     null),
    ('2025-11-01', 'Learn to Lift launch',    true, 'Nov 2025',     null),
    ('2026-03-01', 'App launch',              true, 'Mar 2026',     'Placeholder - Edo to confirm'),
    ('2026-06-01', 'Rebuilt launch',          true, 'Jun 2026',     null);
