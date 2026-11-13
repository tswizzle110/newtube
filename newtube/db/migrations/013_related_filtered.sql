-- Up next now keeps only results about the same subject as the video (services/relevance.py). Every list stored so far came
-- from an unfiltered title search, so forget them: each video fetches a fresh, filtered list the next time it is opened.
-- (The recommender's "related to videos you like" candidates refill the same way.)
DELETE FROM related_videos;
DELETE FROM related_fetched;
