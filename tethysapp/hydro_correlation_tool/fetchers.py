import os
import geoglows
from dataretrieval import waterdata

def get_usgs_daily_discharge(usgs_id, start, end, api_key=None):
    if api_key:
        os.environ["API_USGS_PAT"] = api_key.strip()
    try:
        data, meta = waterdata.get_daily(
            monitoring_location_id=usgs_id,
            parameter_code="00060",
            time=(start,end)
        )
    except Exception as e:
        # network error, bad id, api down, etc. Log it so failures aren't silent,
        # but still return an empty result so the front end shows an empty state.
        print("USGS daily discharge fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None}
    
    if data is None or data.empty:
        return {"dates": [], "values": [], "units": None}
    

    return {
        "dates": data["time"].dt.strftime("%Y-%m-%d").tolist(),
        "values": data["value"].tolist(),
        "units": data["unit_of_measure"].iloc[0]
    }
    
    
def get_geoglows_retrospective(river_id, start, end):
    try:
        df = geoglows.data.retrospective(int(river_id))
    except Exception as e:
        # bad/unknown river_id, network error, AWS down, etc. Log it, then
        # return the same empty shape as the USGS fetcher so the front end
        # can show its empty state.
        print("GEOGLOWS retrospective fetch failed:", repr(e))
        return {"dates": [], "values": [], "units": None}

    if df is None or df.empty:
        return {"dates": [], "values": [], "units": None}

    # Hourly since 1940 -> daily means, then take the single value column
    # (named after the river_id, so select by position: scalar 0 -> Series)
    # and slice to the requested window by date label.
    daily = df.resample("D").mean()
    series = daily.iloc[:, 0].loc[start:end]

    return {
        "dates": series.index.strftime("%Y-%m-%d").tolist(),
        "values": series.tolist(),
        "units": "m^3/s",   # GEOGLOWS is always cms; no units column to read
    }