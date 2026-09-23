-- Second known gap (step 3C): posts published on Instagram that Metricool never captured.
-- Safe to re-run: inserts only once.
insert into content.data_gaps (start_date, end_date, reason)
select '2026-04-26', '2026-06-02',
       'Metricool did not capture these posts, though Edo confirmed they were published on Instagram; '
       'content_posts for this window is incomplete and cannot be backfilled from Metricool.'
where not exists (
    select 1 from content.data_gaps
    where start_date = '2026-04-26' and end_date = '2026-06-02'
)
returning id, start_date, end_date, reason;
