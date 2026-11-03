-- 0002: scope folders to a system.
--
-- Folders live in the shared cytron_dashboard DB, but each master serves one
-- system, so without a system tag one system's folder also showed up in every
-- other system's dashboard. Add a `system` column.
--
-- No backfill: the product cannot know which system a deployment's existing
-- folders belonged to, and guessing would move real devices into the wrong
-- dashboard. A deployment upgrading from 0001 with folders already in place
-- tags them itself, once, in its own migration or by hand:
--
--     UPDATE folders SET system = '<slug>' WHERE system IS NULL;
--
-- Untagged folders are simply invisible to every master until tagged, which is
-- recoverable; a wrong tag is not obvious and is not.
ALTER TABLE folders ADD COLUMN IF NOT EXISTS system TEXT;

-- Root-folder names are unique PER SYSTEM now, so two systems may each have a
-- root folder of the same name (e.g. both "ground").
DROP INDEX IF EXISTS folders_root_name;
CREATE UNIQUE INDEX IF NOT EXISTS folders_root_name
    ON folders (system, name) WHERE parent_id IS NULL;
