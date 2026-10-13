ALTER TABLE agenda_items
ADD COLUMN finish_check_enabled INTEGER
CHECK(finish_check_enabled IN (0, 1));
