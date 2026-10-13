ALTER TABLE speech_intents
ADD COLUMN custom_phrases_json TEXT NOT NULL DEFAULT '[]';

UPDATE speech_intents
SET custom_phrases_json = phrases_json;
