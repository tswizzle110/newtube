-- Repair: the channel-page fallback dates a video "now" when it believes the video is a brand-new upload. For
-- channels that had only ever had ONE video stored (e.g. one you opened), it wrongly believed that of the whole
-- first page, so up to 15 old videos per channel looked like they were uploaded seconds ago.
--
-- Such a date is recognisable: it lies within a minute BEFORE the moment the video was first seen, and the video
-- has never been opened (opening one stores its real date). Remove those dates (the video simply drops out of the
-- feed) and make the affected channels poll again soon, so RSS can supply the real dates where it works.
UPDATE channels SET rss_checked_at = NULL
 WHERE id IN (SELECT DISTINCT channel_id FROM videos
               WHERE detail_fetched_at IS NULL AND from_import = 0 AND published_at IS NOT NULL
                 AND published_at BETWEEN first_seen_at - 60 AND first_seen_at AND channel_id IS NOT NULL);

UPDATE videos SET published_at = NULL
 WHERE detail_fetched_at IS NULL AND from_import = 0 AND published_at IS NOT NULL
   AND published_at BETWEEN first_seen_at - 60 AND first_seen_at;

-- A channel counts as "already read once" only when we really read its uploads (rss_checked_at is set then);
-- migration 003 wrongly marked every channel that merely had a video.
UPDATE channels SET feed_seeded = 0 WHERE rss_checked_at IS NULL AND feed_seeded = 1;
