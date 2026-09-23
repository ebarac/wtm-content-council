-- At most one tag per post, taxonomy version and source. An LLM tag and a
-- human override can coexist for the same post and version, but a second
-- LLM tag (e.g. from a retry) or a second human tag cannot; writers must
-- update the existing row instead.
--
-- The constraint's index leads with (post_id, taxonomy_version), so the
-- plain index on those two columns from 20260923114111 is dropped as redundant.

alter table content.content_tags
    add constraint content_tags_post_version_source_key unique (post_id, taxonomy_version, tagged_by);

drop index content.content_tags_post_version_idx;
