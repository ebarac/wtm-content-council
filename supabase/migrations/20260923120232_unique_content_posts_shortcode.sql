-- Make content_posts.shortcode unique, as a guard against permalink
-- normalisation bugs (two permalinks for the same post). The unique
-- constraint brings its own index, so the plain index from
-- 20260923114111 is dropped as redundant.

alter table content.content_posts
    add constraint content_posts_shortcode_key unique (shortcode);

drop index content.content_posts_shortcode_idx;
