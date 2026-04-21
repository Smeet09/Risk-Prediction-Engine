-- Update manual_data_india check constraint
-- This allows the new 'coastline' type to be stored in the database.

ALTER TABLE manual_data_india 
DROP CONSTRAINT IF EXISTS manual_data_india_data_type_check;

ALTER TABLE manual_data_india 
ADD CONSTRAINT manual_data_india_data_type_check 
CHECK (data_type IN ('lulc', 'river', 'soil', 'fault', 'coastline'));
