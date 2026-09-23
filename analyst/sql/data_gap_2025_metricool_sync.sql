-- Known Metricool sync gap (spec section 2, data notes). Safe to re-run: inserts only once.
insert into content.data_gaps (start_date, end_date, reason)
select '2025-04-15', '2025-10-08', 'Metricool sync gap, not a content gap'
where not exists (
    select 1 from content.data_gaps
    where start_date = '2025-04-15' and end_date = '2025-10-08'
)
returning id, start_date, end_date, reason;
