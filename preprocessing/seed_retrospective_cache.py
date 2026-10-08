"""Force-seed the retrospective_cache table from the public HydroShare parquet.

Thin CLI wrapper over model.seed_retrospective_cache for a one-off run against a
live install (e.g. inside a running app container, where Django and the
persistent store service are already configured):

    python seed_retrospective_cache.py

It connects through the app itself (App.get_persistent_store_database), so there
are no credentials here, and force=True seeds even when the table already has
rows. The same loader runs automatically on provision via
model.init_primary_db; this script is for seeding an existing deployment now.
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tethys_portal.settings")
django.setup()

from tethysapp.hydro_correlation_tool.app import App
from tethysapp.hydro_correlation_tool.model import seed_retrospective_cache

if __name__ == "__main__":
    engine = App.get_persistent_store_database("primary_db")
    inserted, skipped = seed_retrospective_cache(engine, force=True)
    print(f"done -- inserted {inserted}, skipped {skipped}")
