-- Bootstrap: the catalog the example data product publishes to (env.demoCatalog).
-- The example reads samples.tpch.orders, which every workspace has, so there is no data to load.
-- Idempotent: IF NOT EXISTS, so a re-run never strips the tags, comments and grants the publish
-- pipeline applied later. No tags, comments or grants here: the publish pipeline owns those.
-- bootstrap.sh splits statements on a semicolon at the end of a line, and puts env.demoCatalog
-- in place of sales_dev.

-- A metastore with no storage root (default storage) rejects the catalogs REST API, so the
-- catalog is created with SQL.
CREATE CATALOG IF NOT EXISTS sales_dev;
