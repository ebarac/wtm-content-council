-- Step 3D: stage 4 "High-ticket" starts 2026-06-28, the first "Comment REBUILT"
-- post (reel DaJdAp6M1bi), not the 2026-06-01 placeholder seeded in 20260923114116.
-- Stage 3 now runs to 2026-06-27, and posts from 1-27 June 2026 move back to it.
-- Both changes are in one migration so stages and posts never disagree.

update content.account_stages
   set end_date = '2026-06-27', updated_at = now()
 where id = 3 and end_date = '2026-05-31';

update content.account_stages
   set start_date = '2026-06-28', updated_at = now()
 where id = 4 and start_date = '2026-06-01';

update content.content_posts
   set stage_id = 3, updated_at = now()
 where stage_id = 4
   and (posted_at at time zone 'Europe/London')::date between '2026-06-01' and '2026-06-27';

-- Fail the migration (and roll it back) if anything is left inconsistent.
do $$
begin
    if (select start_date from content.account_stages where id = 4) <> '2026-06-28'
       or (select end_date from content.account_stages where id = 3) <> '2026-06-27' then
        raise exception 'account_stages boundary not updated as expected';
    end if;
    if exists (
        select 1 from content.content_posts p
        join content.account_stages st on st.id = p.stage_id
        where (p.posted_at at time zone 'Europe/London')::date
              not between st.start_date and coalesce(st.end_date, 'infinity'::date)
    ) then
        raise exception 'a post is outside its stage''s date range';
    end if;
end $$;
