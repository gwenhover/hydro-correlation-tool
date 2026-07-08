import os
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