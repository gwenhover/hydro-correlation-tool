import os
import geoglows
from dataretrieval import waterdata
import requests
import pandas as pd
import io

def get_usgs_daily_discharge(usgs_id, start, end, api_key=None):
    if api_key:
        os.environ["API_USGS_PAT"] = api_key.strip()
    try:
        data, meta = waterdata.get_daily(
            monitoring_location_id=usgs_id,
            parameter_code="00060",
            time=(start,end)
        )
    except KeyError:
        print("No data")
        return {"dates": [], "values": [], "units": None}
    
    except Exception as e:
        # network error, bad id, api down, etc. Log it so failures aren't silent,
        # but still return an empty result so the front end shows an empty state.
        print("USGS daily discharge fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None, "error": "transient"}
    
    if data is None or data.empty:
        print("No data")
        return {"dates": [], "values": [], "units": None}
    
    data = data.drop_duplicates(subset="time", keep="first")
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
    series = (
        _get_geoglows_daily_ds()["Q"]
        .sel(river_id=river_id)
        .sel(time=slice(start, end))
        .to_series()
        .dropna()
    )
    return (
        tuple(series.index.strftime("%Y-%m-%d")),
        tuple(float(v) for v in series),
    )


def get_geoglows_retrospective(river_id, start, end):
    try:
        dates, values = _geoglows_daily_series(int(river_id), start, end)
    except KeyError:
        print("No data")
        return {"dates": [], "values": [], "units": None}
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
    df["time"] = pd.to_datetime(df["time"])
    daily = df.set_index("time")["streamflow"].resample("D").mean().dropna()
    
    return (
        tuple(daily.index.strftime("%Y-%m-%d")),
        tuple(daily),
    )
def get_nwm_retrospective(river_id, start, end, api_key):

    try: 
        dates, daily = _nwm_daily_series(river_id, start, end, api_key)
    
    except KeyError:
            print("No data")
            return {"dates": [], "values": [], "units": None}
        
    except Exception as e:
        print("NWM retrospective fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None, "error": "transient"}
    
    return {"dates": list(dates), "values": list(daily), "units": "m^3/s"}
