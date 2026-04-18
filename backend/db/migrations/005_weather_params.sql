-- Migration to add year and month tracking for weather downloads
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS year INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS month INTEGER;
