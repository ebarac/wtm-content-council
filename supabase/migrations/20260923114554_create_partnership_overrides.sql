-- Channel Analyst: manual partnership overrides (docs/analyst-build-spec.md, section 6).
-- Corrects posts the caption-based partnership detection gets wrong, in either
-- direction. An override here beats the detected value in content_posts.is_partnership.
--
-- Keyed by the normalised permalink, the same unique key as content_posts.
-- Deliberately no foreign key: an override can be added before the post is
-- pulled, and survives content_posts being cleared and reloaded.

create table content.partnership_overrides (
    permalink       text primary key,   -- normalised, same format as content_posts.permalink
    is_partnership  boolean not null,
    reason          text not null,
    added_by        text not null,
    added_at        timestamptz not null default now()
);

alter table content.partnership_overrides enable row level security;

grant all on content.partnership_overrides to service_role;
