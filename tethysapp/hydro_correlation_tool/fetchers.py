import os
import geoglows
from dataretrieval import waterdata
import requests
import pandas as pd
import io
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as pads
import pyarrow.parquet as pq
try:
    import fcntl  # cross-process lock for the one-time DA download; Unix only
except ImportError:
    fcntl = None

def get_usgs_daily_discharge(usgs_id, start, end, api_key=None):
    if api_key:
        os.environ["API_USGS_PAT"] = api_key.strip()
    try:
        data, meta = waterdata.get_daily(
            monitoring_location_id=usgs_id,
            parameter_code="00060",
            time=(start,end),
            statistic_id="00003"
        )
    except Exception as e:
        # network error, bad id, api down, etc. Log it so failures aren't silent,
        # but still return an empty result so the front end shows an empty state.
        print("USGS daily discharge fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None, "error": "transient"}
    
    if data is None or data.empty:
        print("No data")
        return {"dates": [], "values": [], "units": None, "error": "no_data"}
    
    return {
        "dates": data["time"].dt.strftime("%Y-%m-%d").tolist(),
        "values": (data["value"] * 0.0283168).tolist(),
        "units": "m^3/s"
    }
    
    
# Lazily-opened, module-lifetime handle to the GEOGLOWS daily retrospective
# zarr on S3. format='xarray' returns a lazy (dask-backed) view of the whole
# dataset without downloading it; each per-river select below then pulls only
# the few chunks that river needs (~0.2-1s) instead of the full record (~4s
# for the hourly product the old code fetched). Opening costs ~1.5s, so pay
# it once on the first click, not on every click.
_geoglows_daily_ds = None
NWM_URL = "https://nwm-api-v2-9f6idmxh.uc.gateway.dev/retrospectives"


def _get_geoglows_daily_ds():
    global _geoglows_daily_ds
    if _geoglows_daily_ds is None:
        _geoglows_daily_ds = geoglows.data.retro_daily(format="xarray")
        _geoglows_daily_ds = _geoglows_daily_ds.isel(
            time=_geoglows_daily_ds["time"].notnull()
        )
    return _geoglows_daily_ds



def _geoglows_daily_series(river_id, start, end):
    series = _get_geoglows_daily_ds()["Q"]
    try:
        series = series.sel(river_id=river_id)
    except KeyError as e:
        print ("Invalid GEOGLOWS reach ID: " + repr(e))
        return (None, "error")
    series = series.sel(time=slice(start, end)).to_series().dropna()
    
    return (
        tuple(series.index.strftime("%Y-%m-%d")),
        tuple(float(v) for v in series),
    )


def get_geoglows_retrospective(river_id, start, end):
    try:
        dates, values = _geoglows_daily_series(int(river_id), start, end)
        if dates is None or values == "error":
            return {"dates": [], "values": [], "units": None, "error": "invalid id"}
    except Exception as e:
        # bad/unknown river_id, network error, AWS down, etc. Log it, then
        # return the same empty shape as the USGS fetcher so the front end
        # can show its empty state.
        print("GEOGLOWS retrospective fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None, "error": "transient"}

    return {
        "dates": list(dates),
        "values": list(values),
        "units": "m^3/s",   # GEOGLOWS is always cms; no units column to read
    }
    

def _nwm_daily_series(river_id, start, end, api_key):
    nwm_params = {
        'reach_id': river_id,
        'start_time': start,
        'end_time': end,
        'output_format': 'csv'}
    
    r=requests.get(NWM_URL,
                       params=nwm_params,
                       headers={'x-api-key': api_key},
                       timeout=60)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if ("time" not in df.columns):
        return (None, "error")
    df["time"] = pd.to_datetime(df["time"])
    daily = df.set_index("time")["streamflow"].resample("D").mean().dropna()
    
    return (
        tuple(daily.index.strftime("%Y-%m-%d")),
        tuple(daily),
    )
def get_nwm_retrospective(river_id, start, end, api_key):

    try: 
        dates, daily = _nwm_daily_series(river_id, start, end, api_key)
        
    except Exception as e:
        print("NWM retrospective fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None, "error": "transient"}
    
    if (daily == "error"):
        return {"dates": [], "values": [], "units": None, "error": "no data or invalid id"}
        
    return {"dates": list(dates), "values": list(daily), "units": "m^3/s"}


# --- Drainage area (DA Analysis) ---------------------------------------------
#
# NWM and GEOGLOWS drainage areas come from two public reference tables that
# are downloaded once into the app workspace. Only the three columns DA
# Analysis needs are kept, sorted by reach id and written in small row groups,
# so a lookup reads a few kB from disk instead of every server process holding
# ~9.5M rows in memory. Formulas and unit conversions live in da_analysis.py;
# these functions return the source values untouched.
#
# Pre-download both tables (do this when setting up a server, so no user
# waits on the ~500 MB download):
#     python -m tethysapp.hydro_correlation_tool.fetchers

# NHDPlusV2 value-added attributes -- the same file pynhd.nhdplus_vaa() reads.
# NWM v3 feature_id == NHDPlusV2 COMID.
NHDPLUS_VAA_URL = "https://www.hydroshare.org/resource/6092c8a62fac45be97a09bfd0b0bf726/data/contents/nhdplusVAA.parquet"
# GEOGLOWS v2 model table -- the same file geoglows.data.metadata_table() reads.
# GEOGLOWS v2 river_id == LINKNO.
GEOGLOWS_TABLE_URL = "https://geoglows-v2.s3-us-west-2.amazonaws.com/tables/v2-model-table.parquet"

DA_REFERENCE_TABLES = {
    "NWM": {
        "url": NHDPLUS_VAA_URL,
        "file": "nhdplus_vaa_da.parquet",
        "id": "comid",
        "columns": ["comid", "totdasqkm", "areasqkm"],
    },
    "GEOGLOWS": {
        "url": GEOGLOWS_TABLE_URL,
        "file": "geoglows_v2_da.parquet",
        "id": "LINKNO",
        "columns": ["LINKNO", "USContArea", "DSContArea"],
    },
}
USGS_DA_BATCH_SIZE = 200

_da_download_thread_lock = threading.Lock()
_da_background_download = None


def da_cache_dir():
    # The Tethys app workspace. HCT_DA_CACHE_DIR overrides it. Resolved from
    # settings rather than get_app_workspace() so the command-line pre-download
    # (no request, app not necessarily registered yet) lands in the same place.
    override = os.environ.get("HCT_DA_CACHE_DIR")
    if override:
        return Path(override)
    from django.conf import settings
    if getattr(settings, "USE_OLD_WORKSPACES_API", True):
        root = Path(__file__).parent / "workspaces" / "app_workspace"
    else:
        root = Path(settings.TETHYS_WORKSPACES_ROOT) / "hydro_correlation_tool"
    return root / "da_reference"


def _da_reference_path(network):
    return da_cache_dir() / DA_REFERENCE_TABLES[network]["file"]


def da_reference_ready():
    return all(_da_reference_path(n).exists() for n in DA_REFERENCE_TABLES)


@contextmanager
def _da_download_lock(cache_dir):
    # One download at a time: a thread lock within this process, plus a file
    # lock across server processes (several workers may hit it at once).
    with _da_download_thread_lock:
        if fcntl is None:
            yield
            return
        with open(cache_dir / ".download.lock", "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)


def _build_da_reference(network):
    spec = DA_REFERENCE_TABLES[network]
    cache_dir = da_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = _da_reference_path(network)
    if dest.exists():
        return dest
    with _da_download_lock(cache_dir):
        if dest.exists():  # another process finished it while we waited
            return dest
        raw_fd, raw_path = tempfile.mkstemp(dir=cache_dir, suffix=".download")
        trimmed_fd, trimmed_path = tempfile.mkstemp(dir=cache_dir, suffix=".partial")
        os.close(trimmed_fd)
        try:
            print(f"DA: downloading {network} reference table from {spec['url']}")
            with os.fdopen(raw_fd, "wb") as raw, requests.get(spec["url"], stream=True, timeout=60) as r:
                r.raise_for_status()
                expected = r.headers.get("Content-Length")
                written = 0
                for chunk in r.iter_content(chunk_size=1 << 20):
                    raw.write(chunk)
                    written += len(chunk)
            if expected is not None and written != int(expected):
                raise IOError(f"{network} download incomplete: got {written} of {expected} bytes")
            table = pq.read_table(raw_path, columns=spec["columns"]).sort_by(spec["id"])
            pq.write_table(table, trimmed_path, row_group_size=50_000, compression="zstd")
            # Rename only once complete, so an interrupted download never
            # leaves a file that looks finished.
            os.replace(trimmed_path, dest)
            # mkstemp files are owner-only; the server may run as a different
            # user than whoever ran the pre-download.
            os.chmod(dest, 0o644)
            print(f"DA: {network} reference table ready ({table.num_rows:,} reaches) -> {dest}")
        finally:
            for leftover in (raw_path, trimmed_path):
                if os.path.exists(leftover):
                    os.remove(leftover)
    return dest


def ensure_da_reference_tables():
    return {network: _build_da_reference(network) for network in DA_REFERENCE_TABLES}


def start_da_reference_download():
    # Non-blocking version for the panel endpoint: a first click starts the
    # download and gets NO DATA back instead of waiting minutes for it.
    global _da_background_download
    if _da_background_download is not None and _da_background_download.is_alive():
        return

    def run():
        try:
            ensure_da_reference_tables()
        except Exception as e:
            print("DA reference download failed:", repr(e))

    _da_background_download = threading.Thread(target=run, daemon=True)
    _da_background_download.start()


def lookup_da_reference(network, reach_ids):
    # Source values for the given reach ids, indexed by reach id. Ids not in
    # the table are simply absent from the result.
    spec = DA_REFERENCE_TABLES[network]
    ids = sorted({int(i) for i in reach_ids if not pd.isna(i)})
    dataset = pads.dataset(_da_reference_path(network), format="parquet")
    if not ids:
        return dataset.schema.empty_table().to_pandas().set_index(spec["id"])
    id_type = dataset.schema.field(spec["id"]).type
    table = dataset.to_table(filter=pc.field(spec["id"]).isin(pa.array(ids, type=id_type)))
    return table.to_pandas().drop_duplicates(spec["id"]).set_index(spec["id"])


def get_usgs_drainage_areas(usgs_ids, api_key=None):
    # Returns ({usgs_id: drainage area in sq mi, or None when USGS has none},
    # [ids whose request failed]). Failed ids are kept apart so the caller
    # doesn't cache a network error as "no drainage area".
    if api_key:
        os.environ["API_USGS_PAT"] = api_key.strip()
    usgs_ids = list(usgs_ids)
    found = {}
    failed = []
    for start in range(0, len(usgs_ids), USGS_DA_BATCH_SIZE):
        batch = usgs_ids[start:start + USGS_DA_BATCH_SIZE]
        try:
            data, meta = waterdata.get_monitoring_locations(
                monitoring_location_id=batch,
                properties=["monitoring_location_id", "drainage_area"],
            )
        except Exception as e:
            print("USGS drainage area fetch failed:", repr(e))
            failed.extend(batch)
            continue
        values = {}
        if data is not None and not data.empty:
            for uid, area in zip(data["monitoring_location_id"], data["drainage_area"]):
                values[uid] = None if pd.isna(area) else float(area)
        for uid in batch:
            found[uid] = values.get(uid)
    return found, failed


if __name__ == "__main__":
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tethys_portal.settings")
    import django
    django.setup()
    for network, path in ensure_da_reference_tables().items():
        print(f"{network}: {path}")
