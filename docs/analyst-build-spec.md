# WTM Content Council - Agent 1: Channel Analyst

Build spec v1 - for Claude Code
Repo: `wtm-content-council`
Owner: Edo (reviews and applies all migrations manually, dev before main)

---

## 1. Purpose

The Channel Analyst works out what performs on @whatthemenopause and why. It covers all content since January 2024, gives more weight to recent content, and judges each piece against the job it was meant to do.

It produces three reports:

1. **Performance report** - every cycle. This is the main input for the Strategist agent.
2. **Account history report** - a one-off baseline, then refreshed quarterly. It explains how the account has changed across its stages, including the drop in views.
3. **Partnership report** - every cycle. Covers brand partnership posts only. They are kept out of the main analysis.

### Core rule

All numbers are calculated in code (SQL or Python) and stored in tables. The language model is only used for two things:

- Tagging captions with the fixed taxonomy.
- Writing the report text, using only numbers from the calculated tables.

It never calculates, estimates or rounds a statistic itself. Every claim in a report must reference the post permalinks and table values behind it.

---

## 2. Data sources

| Source | What | Coverage | Notes |
|---|---|---|---|
| Metricool - Instagram reels | Caption, permalink, reach, views, saves, shares, comments, likes, view rate, average watch time, paid reach, spend | Jan 2024 - now | Retention (IGRE27) returns null, so use IGRE24 and IGRE28 instead. Do not use deprecated IGRE13 or IGRE15 |
| Metricool - Instagram posts (carousels and images) | Caption, permalink, type, reach, views, saves, shares, comments, likes, paid reach, spend | Jan 2024 - now | Metricool has no separate carousel connector. Carousels and images share the "posts" connector. Field mapping confirmed 23 Sep 2026, see section 4 |
| Metricool - Instagram stories | Reach, replies, exits, taps forward and back | From connection date onwards only | Excluded from all historical analysis. Reported as live data only |
| ig-attribution (Supabase, `wtm-attribution` project) | Designed to hold ManyChat keyword triggers and applications, linked to posts via `content_registry.media_id` | Effectively none yet. Checked 23 Sep 2026: one real post, `media_id` empty, no comment or application tables exist | Not usable for v1.1 conversion scoring until the ig-attribution system itself is built out further. That's separate work, not part of this build. See section 3 |

### Known data issues

1. **April to October 2025 is missing in Metricool.** This is a sync gap, not a content gap. Mark it as a gap period and draw no conclusions from it. No baseline window may span it.
2. **Response size cap.** The Metricool connector caps responses at about 25k tokens. Pull one calendar month per request, and split further if a month still fails.
3. **Metricool returns occasional server errors (502/500).** Retry with backoff, 3 attempts per request. If a month still fails, stop the run and report which month failed. Never continue with a hole in the data.

---

## 3. Step 0 - confirm before building

Status as of 23 Sep 2026: all three questions answered. Question 3's answer is that attribution data isn't usable yet, which pushes v1.1 conversion scoring out, but doesn't block v1 (this build).

1. **How Claude Code pulls the data - answered: Option B, the Metricool MCP connector.** Confirmed working in Claude Code (Terminal) on 23 Sep 2026: brand settings, reels pull and available-metrics calls all returned correct data for brand 3304707. No API token or plan upgrade needed for this data.

   **One build requirement this sets:** the tool's response must be written to disk by the code in the same step it's fetched, with no manual retyping or copy-paste in between, even by Claude itself. The test run had Claude Code retype the reels response by hand as a one-off, which worked for a 22-row test but is not an acceptable pattern for the real pull. Step 2 of the build (section 11) must write straight from the tool call to the file.
2. **Carousel fields - answered.** There is no separate carousel connector. Carousels and images both come through the Instagram "posts" connector, with IGPO07 (Type) distinguishing them. Full field list saved to `docs/metricool_posts_fields.json`. Mapping to use, confirmed 23 Sep 2026:

   | content_metrics_snapshots column | Metricool field |
   |---|---|
   | reach | IGPO14 |
   | views | IGPO28 |
   | saves | IGPO15 |
   | shares | IGPO27 |
   | comments | IGPO08 |
   | likes | IGPO13 |
   | reach_paid | IGPO19 |
   | spend | IGPO26 |

   Also usable: IGPO01-06 (date, content, post ID, image, URL), IGPO07 (type), IGPO10 (engagement), IGPO12 (interactions organic), IGPO29 (follows), IGPO17/22/23/24/25 (other paid fields).

   **Do not use** (Metricool marks these deprecated): IGPO09, IGPO11, IGPO16, IGPO18, IGPO20, IGPO21.
3. **Attribution join - answered, and the answer is "not yet usable".** Checked directly in the `wtm-attribution` Supabase project (read-only) on 23 Sep 2026:
   - **Only one table exists:** `content_registry`, with columns `id`, `slug`, `media_id`, `platform`, `content_type`, `description`, `post_date`, `created_at`.
   - **It holds two rows total:** one real post (`reel_2026-08-03_tierlist`, posted 3 Aug 2026) and one test row, both created the same day, nothing added since.
   - **The intended join field is `media_id`** (the numeric Meta ID), but it's empty on both rows, so nothing currently links to a Metricool post.
   - **No comment-trigger or application tables exist at all.**
   - **The project itself was created 3 Aug 2026**, so attribution data can't predate that regardless.

   **Conclusion:** attribution isn't ready to join against. v1.1 conversion scoring stays deferred until the ig-attribution system is built out with populated `media_id` values (or permalinks) and comment/application tables. That's separate work, tracked in [[ig-attribution]], not part of this Analyst build. Nothing in Steps 1-8 of this spec (section 11) depends on it.

---

## 4. Database tables

Create these tables in a `content` schema. Claude Code writes the migrations, Edo reviews and applies them, dev first.

### content_posts
One row per Instagram post.

| Column | Type | Notes |
|---|---|---|
| id | uuid | Primary key |
| permalink | text | Unique. Normalise the URL, because some old permalinks contain `/whatthemenopause/` |
| shortcode | text | Taken from the permalink |
| format | text | reel, carousel, image or story |
| posted_at | timestamptz | |
| caption | text | |
| cta_keyword | text | Found by code using the pattern "Comment[:]? WORD" (e.g. REBUILT, VIP, CREATINE). Null if none |
| is_partnership | bool | See section 6 |
| is_boosted | bool | True if paid reach > 0 or spend > 0 |
| duplicate_group_id | uuid | Null unless the post is a duplicate |
| in_data_gap | bool | True if the post falls inside a known gap period |
| stage_id | int | Links to account_stages |
| launch_window_id | int | Null unless the post falls inside a confirmed launch window |

### content_metrics_snapshots
One row per post per pull. Keep every snapshot, because metrics keep growing after posting.

| Column | Notes |
|---|---|
| post_id, captured_at | |
| reach, views, saves, shares, comments, likes | Organic values only |
| view_rate, avg_watch_time | Reels only |
| reach_paid, spend | Used for the boosted check |
| raw_json | The full source row, kept for auditing |

### content_tags
Holds the tagging results.

**Uniqueness (applied 23 Sep 2026):** unique on `(post_id, taxonomy_version, tagged_by)`. This lets an LLM tag and a human tag coexist for the same post and version, but blocks a second tag from the same source for the same post and version, for example an LLM retry, or a second human correction. **This means the tagging step and the human-override path in Step 4/5 must upsert (`insert ... on conflict (post_id, taxonomy_version, tagged_by) do update`), not plain insert**, or a retry will fail outright.

| Column | Notes |
|---|---|
| post_id, taxonomy_version | |
| content_type | growth, authority or conversion |
| topic, hook_type | Values from the taxonomy in section 5 |
| confidence | high, medium or low |
| tagged_by | llm or human |
| tagged_at | |
| is_override | true if a human has corrected the tag. Human tags always take priority |

### Other tables

| Table | What it holds |
|---|---|
| account_stages | Stage name, start date, end date, goal metrics, notes |
| business_events | Date, event name, whether the date is approximate |
| launch_windows | Start date, end date, related event, status (proposed or confirmed) |
| data_gaps | Start date, end date, reason |
| partnership_overrides | Manual corrections to the caption-based partnership detection in section 6. Keyed by permalink, no foreign key to content_posts by design (so an override can be set before a post is pulled, and survives a reload). Added during the build, 23 Sep 2026 |
| analyst_runs | Run date, settings used, report links, and a pass/fail result for each data check |
| pattern_scores | Run ID, what was grouped (e.g. topic), the group value, stage, post count, weighted post count, score for each metric, and class (evergreen, faded, emerging or early signal) |

Later, the ledger's `content_items` table (ideas through to posted pieces) will link to `content_posts` by permalink. That link is not needed for v1.

---

## 5. Tag taxonomy (version 1)

The taxonomy has a version number. If the taxonomy changes, every post is re-tagged under the new version. Old tags are kept.

### Content type (the job the piece was meant to do)

Apply these rules in order and stop at the first match:

1. **conversion** - the post contains a sales ask: a comment keyword leading to a DM, a mention of apply, or an offer mention with a call to action. Educational content that ends in "Comment REBUILT" counts as conversion.
2. **authority** - mainly educational or science-led, and has no sales ask.
3. **growth** - everything else: relatable, shareable, humour, community, personal story with no ask.

### Topic

| Topic | Notes |
|---|---|
| symptoms_signs | |
| weight_metabolism | Includes blood sugar and insulin |
| nervous_system_mood | Includes rage, anxiety, window of tolerance |
| nutrition_supplements | |
| strength_training_exercise | Added for the PPP and Learn to Lift periods |
| recipes_meals | Added for the She Gets Lean period |
| client_transformation | |
| personal_story | |
| science_research | |
| cardiovascular_health | |
| family_identity_midlife | |
| other | Tagger must write a short free-text description. These are reviewed each quarter and added to the list if they recur |

### Hook type

| Hook type | Example |
|---|---|
| direct_question | "Did perimenopause catch you off guard?" |
| numbered_list | |
| myth_bust_reframe | |
| personal_confession | |
| transformation_reveal | |
| pattern_interrupt | "You're not Googling Ozempic because..." |
| comment_bait_short | A one-line caption asking for comments |
| research_hook | Opens with a study or science claim |
| product_intro | Sponsored content only |
| other | Free-text description required |

### Filming style

Not tagged for historical posts, because it can't be worked out reliably from captions. From now on the Producer agent records it for new content.

A possible v2 addition: estimate filming style for old posts from the thumbnail image field (IGRE05) using image analysis. This would need a separate accuracy check before use.

### Tagging method

1. Only posts that have no tag for the current taxonomy version are tagged.
2. Captions are sent in batches of 20.
3. The output must match a fixed JSON format and be checked against it. Anything that fails the check is retried once, then marked for human review.
4. Low-confidence tags are listed in the report for human review.

---

## 6. Flags and exclusions

| Flag | How it's detected | What happens |
|---|---|---|
| Partnership | Caption contains `#ad`, a hashtag ending in "partner" (e.g. #humannpartner, #ritualpartner), or "Partner" after a brand name (e.g. MitoQPartner). A manual override table handles missed cases | Removed from the main analysis. Goes to the partnership report only |
| Boosted | Paid reach > 0 or spend > 0 | Removed from the main analysis and listed in the data-quality section. Expected to be zero |
| Duplicate | Same caption after normalising (lowercase, whitespace trimmed) posted within 7 days | Merged into one record using the average of the copies' metrics, and flagged. The group is listed in the data-quality section as a repost test or posting error |
| Data gap | Post date falls inside a data_gaps period | Excluded from baselines and pattern scores |
| Too new | Posted less than 7 days before the run | Shown under "early reads". Not included in pattern scores |

### Examples from real data (use as test cases)

- **Partnership:** 22 Sep 2026 (Humann), 11 Sep 2026 (MitoQ), 27 Aug 2026 (Ritual)
- **Duplicates:** the pair from 5 Sep 2026, and the three copies from 28 Aug 2026

---

## 7. Scoring

All settings live in `analyst.config.yaml`, so they can be changed without touching code.

### 7.1 Scoring each post against its own period

For each post and each metric:

- **baseline** = the median of that metric for all eligible posts of the same format (reel or carousel) within ±15 days of the post.
- For posts in the last 15 days, where future data doesn't exist yet, use the previous 30 days instead.
- If there are fewer than 5 posts in the window, widen it to ±30 days. If there are still fewer than 5, leave the score empty.
- **score** = the post's value divided by the baseline. A score of 2.0 means twice a typical post from that period.
- Also store log2(score), which is used for averaging.

### 7.2 Recency weighting

- **weight** = 0.5 ^ (age in days ÷ half-life)
- **Half-life = 182 days** (6 months). This is set in the config file.

### 7.3 Pattern scores

For each group (content type, topic, hook type, format, and pairs of these), within each stage and across all stages:

- **Pattern score** = the weighted average of log2(score), converted back into a normal score. Averaging logs stops one viral post from dominating the result.
- **Post count** and **weighted post count** are stored alongside it.

Reporting rules:

| Post count | Label |
|---|---|
| 8 or more | Finding |
| 3 to 7 | Early signal |
| Under 3 | Not reported |

### 7.4 Which metrics matter for each content type

| Content type | Main metrics | Secondary metrics |
|---|---|---|
| Growth | Reach, shares | Views, view rate |
| Authority | Saves, shares | Average watch time, view rate |
| Conversion | Comments. From v1.1, keyword triggers and applications per 1,000 reach | Reach |

### 7.5 Evergreen, faded and emerging

Compare a group's score in older content (more than 12 months old) with its score in recent content (last 6 months). Each side needs at least 5 posts.

| Class | Rule |
|---|---|
| Evergreen | Score of 1.2 or more in both older and recent content |
| Faded | 1.2 or more in older content, under 1.0 in recent content |
| Emerging | 1.2 or more in recent content, and under 1.0 in older content or not enough older posts |
| Neutral | Anything else |

---

## 8. Account stages

### Starting configuration (editable in account_stages)

| Stage | Start | End | Goal metrics |
|---|---|---|---|
| 1. Cohort launch | 2024-01-01 | 2024-06-30 | Growth |
| 2. Evergreen PPP | 2024-07-01 | 2025-09-30 | Growth and authority |
| 3. Multi-product | 2025-10-01 | 2026-05-31 | Conversion across several offers |
| 4. High-ticket (Rebuilt, VIP/Apply) | 2026-06-01 | Open | Conversion |

### Business events (business_events)

| Event | Date | Exact? |
|---|---|---|
| PPP cohort 1 | Jan 2024 | Approximate |
| PPP cohort 2 | Feb/Mar 2024 | Approximate |
| PPP cohort 3 | Mar/Apr 2024 | Approximate |
| PPP moves to evergreen | Jun/Jul 2024 | Approximate |
| She Gets Lean launch | Oct 2025 | Approximate |
| Learn to Lift launch | Nov 2025 | Approximate |
| App launch | Mar 2026 | Placeholder - Edo to confirm |
| Rebuilt launch | Jun 2026 | Approximate |

### How the Analyst checks the stages

1. **Proposing boundaries.** From the tags, the Analyst builds a monthly breakdown of content type mix, posting frequency, and when each CTA keyword first appeared. Where the data points to a different boundary from the table above, it proposes a change. Edo confirms before anything is updated.
2. **Finding launch windows.** A week is proposed as part of a launch window if its share of conversion posts is at least twice that stage's average. Nearby weeks are merged into one window, and each window is matched to the closest business event. Windows are saved as "proposed" until Edo confirms them.
3. **The June 2024 peak.** The 6.4M reach reel on 14 June 2024 sits on the boundary between Stage 1 and Stage 2. The account history report must say which stage it fits better and why.

---

## 9. Reports

All reports are saved to Google Drive at `WTM Content Council / Cycle YYYY-MM-DD / 02 Research /`. A JSON copy of each performance report is saved for the Strategist agent to read.

### Report 1 - Performance report (every cycle)

1. **Headline findings.** At most 5. Each one has its numbers and example post links.
2. **Pattern table.** Grouped by content type, topic, hook type and format. Shows score, post count and class. Sorted separately within each content type.
3. **Last cycle's posts.** Every post, with its score for each metric and its tags. Posts less than 7 days old are listed separately under "early reads".
4. **Actual mix vs target.**
   - Formats: target 5 reels and 2 carousels per week.
   - Reel content types: target 50% growth, 30% authority, 20% conversion.
5. **Reels vs carousels test.** Compares the two on the same content types and topics.
6. **Stories.** Live data only: reach, replies, exits and taps for each story set.
7. **Data quality.**
   - Did every month pull successfully?
   - Duplicate groups.
   - Boosted posts (expected: none).
   - Low-confidence tags.
   - Metrics that came back empty.

### Report 2 - Account history (one-off baseline, refreshed quarterly)

1. **Monthly timeline.** Reach, number of posts, format mix and content type mix per month, with stages and business events marked.
2. **Stage summaries.** For each stage: what performed best against that stage's own goal, and which patterns held across stages.
3. **Views drop analysis.** Uses all history with no recency weighting. It separates two causes:
   - **Content mix change.** Recalculate expected reach using Stage 1 median reach for each content type, applied to each later stage's actual content mix. The difference between that and Stage 1 reach is the effect of shifting towards conversion content.
   - **Real performance change.** Compare the scores of growth-tagged content across stages. If growth content itself scores lower, that is a real decline, not just a mix effect.
4. **Evergreen, faded and emerging patterns**, with the evidence for each.
5. **Stage boundary and launch window proposals** for Edo to confirm.

### Report 3 - Partnership report (every cycle)

- Every partnership post, with its score against normal posts from the same period.
- Average partnership score compared with the average for normal posts.
- Posts listed by brand.

---

## 10. Guardrails

1. **Numbers come from code only.** The language model writes report text from table values and never produces figures itself.
2. **Minimum post counts are enforced** for every finding (section 7.3).
3. **Nothing is concluded from gap periods or from stories before the connection date.**
4. **A failed data pull stops the run.** It is reported and nothing continues on partial data.
5. **Human tags always win.** A corrected tag is never overwritten by the model.
6. **No personal data is stored.** Only post content and metrics.

---

## 11. Build steps and checks

Each step has a check that must pass before moving on.

| Step | Work | Check before moving on |
|---|---|---|
| 0 | Answer the section 3 questions | Done 23 Sep 2026, all three answered. Question 3's answer means v1.1 conversion scoring is deferred, not that anything here is blocked |
| 1 | Migrations for the section 4 tables (dev) | Done 23 Sep 2026. All 10 tables applied to `wtm-attribution` (content schema), seed data confirmed matching section 8, shortcode and content_tags uniqueness constraints added and applied. Public schema and content_registry untouched throughout |
| 2 | Pull all history from Jan 2024, one month at a time | Monthly post counts roughly match Metricool's web app. The gap months show as gaps. No duplicate permalinks |
| 3 | Add the section 6 flags | Every example case in section 6 is flagged correctly |
| 4 | Tag a test sample: the model tags 40 posts that Edo or Jessica have already tagged by hand | Content type matches the human tag at least 80% of the time. If not, adjust the rules and run the sample again |
| 5 | Tag the full history | No failed format checks remain unreviewed. Low-confidence tags are listed |
| 6 | Build the scoring (section 7), with unit tests on a small fixed dataset | Tests pass. The 12 Sep 2026 vagus nerve reel scores well above 1.0 on reach for its period |
| 7 | Stage checks and launch window proposals (section 8) | Edo confirms the stages and windows |
| 8 | First full run: all three reports | Edo reviews them. Any feedback on tags or settings is fed back |
| 9 | Write the Analyst as a Claude Code subagent that runs steps 2-8 in order on each cycle, only pulling the last 45 days (so newer posts' metrics can keep updating) | Two cycles run in a row give consistent results |

### Tag test cases from real data

- 12 Sep 2026, vagus nerve reel: **growth**, nervous_system_mood
- 1 Sep 2026, Ozempic reel: **conversion**, weight_metabolism, pattern_interrupt
- 16 Sep 2026, numbered symptoms list: **authority**, symptoms_signs, numbered_list

---

## 12. Open items

1. **App launch date.** Edo to confirm. Currently a March 2026 placeholder.
2. ~~Metricool API access.~~ Resolved 23 Sep 2026: the MCP connector covers everything needed, on the current plan, no upgrade required.
3. ~~Attribution coverage dates.~~ Resolved 23 Sep 2026: attribution isn't usable yet (see section 3). v1.1 conversion scoring stays deferred until ig-attribution is built out further. Not a blocker for this spec.
4. **Human-tagged test sample.** 40 posts need tagging by hand before step 4 can run.
