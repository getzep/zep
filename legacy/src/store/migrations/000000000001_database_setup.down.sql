-- normally the down migration is the opposite of the up migration
-- but in this case we don't want to drop everything. if the user wants to
-- start fresh, they should manually drop the database.
SELECT 1;
