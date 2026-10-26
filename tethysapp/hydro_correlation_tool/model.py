from sqlalchemy import Column, String, Float, BigInteger, DateTime, Date
from sqlalchemy.orm import declarative_base, sessionmaker
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
import pandas as pd
import os
import hydroeval as he
from sqlalchemy.dialects.postgresql import JSONB


Base = declarative_base()  

class hctTable(Base):
    __tablename__ = 'gage_mapping'
    usgs_id = Column(String, primary_key=True, nullable=False) #included the nullable just to remember and showcase that it is required
    gage_name = Column(String, nullable=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    geom = Column(Geometry('POINT', srid=4326), nullable=False)
    nwm_feature_id = Column(BigInteger, nullable=True)
    seeded_nwm_feature_id = Column(BigInteger, nullable=True)
    nwm_kge_rating = Column(Float, nullable=True)
    nwm_kge_shared_dates = Column(BigInteger, nullable=True)
    geoglows_river_id = Column(BigInteger, nullable=True)
    seeded_geoglows_river_id = Column(BigInteger, nullable=True)
    geoglows_kge_rating = Column(Float, nullable=True)
    geoglows_kge_shared_dates = Column(BigInteger, nullable=True)
    verification_status = Column(String, nullable=False, default='Unverified')
    last_modified_by = Column(String, nullable=True)
    last_modified_timestamp = Column(DateTime(timezone=True), nullable=True)

class cacheTable(Base):
    __tablename__ = 'retrospective_cache'
    network = Column(String, nullable=False, primary_key=True)
    reach_id = Column(BigInteger, nullable=False, primary_key=True)
    reach_data = Column(JSONB, nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)

    
MAPPING_CSV = os.path.join(os.path.dirname(__file__), 'public', 'data', 'gage_mapping.csv')

def init_primary_db(engine, first_time):
    
    Base.metadata.create_all(engine)

    if first_time:
        Session = sessionmaker(bind=engine)  
        session = Session()                   
        mapping = pd.read_csv(MAPPING_CSV, parse_dates=["last_modified_timestamp"], date_format='ISO8601')
        mapping = mapping.astype(object).where(mapping.notna(), None)
        for index, row in mapping.iterrows():
            session.add(create_row(row))       
        session.commit()                       
        session.close()                        

def create_row(row):
    geomPoint = WKTElement(f"POINT({row['longitude']} {row['latitude']})", srid=4326)
    if (pd.isna(row['nwm_feature_id'])):
        nwm_row = None
    else:
        nwm_row = int(row['nwm_feature_id'])
        
    if (pd.isna(row['geoglows_river_id'])):
        geo_row = None
    else:
        geo_row = int(row['geoglows_river_id'])
        
    if (pd.isna(row['seeded_nwm_feature_id'])):
        seeded_nwm_row = None
    else:
        seeded_nwm_row = int(row['seeded_nwm_feature_id'])
        
    if (pd.isna(row['seeded_geoglows_river_id'])):
        seeded_geo_row = None
    else:
        seeded_geo_row = int(row['seeded_geoglows_river_id'])
        
    if (pd.isna(row['nwm_kge_shared_dates'])):
        nwm_kge_shared = None
    else:
        nwm_kge_shared = int(row['nwm_kge_shared_dates'])
        
    if (pd.isna(row['geoglows_kge_shared_dates'])):
        geo_kge_shared = None
    else:
        geo_kge_shared = int(row['geoglows_kge_shared_dates'])
        
        
    new_row = hctTable(usgs_id=row['usgs_id'], gage_name=row['gage_name'], latitude=row['latitude'], 
                       longitude=row['longitude'], geom=geomPoint, nwm_feature_id=nwm_row, seeded_nwm_feature_id=seeded_nwm_row,
                       geoglows_river_id=geo_row, seeded_geoglows_river_id=seeded_geo_row, nwm_kge_shared_dates=nwm_kge_shared, geoglows_kge_shared_dates=geo_kge_shared,
                       nwm_kge_rating=row['nwm_kge_rating'], geoglows_kge_rating=row['geoglows_kge_rating'], verification_status=row['verification_status'],
                       last_modified_by=row['last_modified_by'], last_modified_timestamp=row['last_modified_timestamp'])
    return(new_row)



class UsgsDrainageArea(Base):
    # DA Analysis cache of USGS drainage areas, filled on first use (panel
    # click or CSV export) so the export doesn't call USGS for every gage.
    # A NULL area means USGS reports none for that gage; failed requests are
    # never stored, so they are retried next time.
    __tablename__ = 'usgs_drainage_area'
    usgs_id = Column(String, primary_key=True, nullable=False)
    drainage_area_sq_mi = Column(Float, nullable=True)
    drainage_area_km2 = Column(Float, nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)


# Verification reasons offered in the Save and Verify popup, one dropdown per
# model (NWM and GEOGLOWS). home.html renders this list, so the dropdowns and
# the server check can't drift apart.
VERIFICATION_OTHER = 'Other'
VERIFICATION_NO_MATCH = 'No matching reach'
VERIFICATION_REASONS = (
    'Matched KGE only',
    'Matched DA only',
    'Matched both KGE and DA',
    'Visual match (map location / hydrograph)',
    VERIFICATION_NO_MATCH,
    VERIFICATION_OTHER,
)
VERIFICATION_COMMENT_MAX = 500


def clean_verification_reason(reason, comment):
    # Checks one model's reason. Returns (reason, comment), or (None, None)
    # when the save must be refused:
    # the reason is required, and "Other" needs a typed comment. A comment is
    # only kept for "Other" -- the popup only offers the text box there.
    reason = (reason or '').strip()
    comment = (comment or '').strip()
    if reason not in VERIFICATION_REASONS:
        return None, None
    if reason != VERIFICATION_OTHER:
        return reason, None
    if not comment or len(comment) > VERIFICATION_COMMENT_MAX:
        return None, None
    return reason, comment


def clean_reach_id(raw_id, reason):
    # Checks one model's saved reach ID against that model's (already
    # cleaned) reason. Returns (ok, reach_id). A model with no matching reach
    # is saved with an empty ID, which is only accepted with the reason
    # "No matching reach" -- and that reason only with an empty ID.
    raw_id = str(raw_id or '').strip()
    if reason == VERIFICATION_NO_MATCH:
        return (raw_id == ''), None
    try:
        return True, int(raw_id)
    except ValueError:
        return False, None


def same_reach(seeded_id, saved_id):
    # True when a saved ID agrees with the seed; "no reach" agrees only with
    # a seed that had no reach either.
    if seeded_id is None or saved_id is None:
        return seeded_id is None and saved_id is None
    return int(seeded_id) == int(saved_id)


class VerificationLog(Base):
    # One row per Save and Verify, written in the same transaction as the
    # gage_mapping update, so the history of who verified what and why is
    # kept even when a gage is re-verified. Each model has its own reason,
    # since the NWM and GEOGLOWS reaches are judged separately. The export
    # uses the latest row.
    __tablename__ = 'verification_log'
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    usgs_id = Column(String, nullable=False, index=True)
    nwm_feature_id = Column(BigInteger, nullable=True)
    geoglows_river_id = Column(BigInteger, nullable=True)
    verification_status = Column(String, nullable=False)
    nwm_reason = Column(String, nullable=False)
    nwm_comment = Column(String, nullable=True)
    geoglows_reason = Column(String, nullable=False)
    geoglows_comment = Column(String, nullable=True)
    username = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False)
