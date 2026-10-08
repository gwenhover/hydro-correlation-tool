from sqlalchemy import Column, String, Float, BigInteger, DateTime, Date, text
from sqlalchemy.orm import declarative_base, sessionmaker
from geoalchemy2 import Geometry
from geoalchemy2.elements import WKTElement
import pandas as pd
import os
import hydroeval as he
from sqlalchemy.dialects.postgresql import JSONB


Base = declarative_base()

CACHE_URL = (
    "https://www.hydroshare.org/resource/8c1ebe560d06475e9804ce8107d84142"
    "/data/contents/hydro_correlation_retrospective_cache_2018_2022.parquet"
)
CACHE_PATH = "/tmp/hydro_correlation_retrospective_cache_2018_2022.parquet"

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

    if os.environ.get('HCT_SEED_CACHE', '1') == '1':
        try:
            seed_retrospective_cache(engine)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                'retrospective_cache seed skipped (%s); the app will fill the '
                'cache lazily instead', exc)

def _download_cache_parquet(path):
    """Download the HydroShare cache parquet unless a complete copy is present.

    Verifies against the server's Content-Length and the PAR1 magic bytes so a
    truncated download or an HTML error page is caught rather than handed to
    the reader.
    """
    import requests

    def complete(p):
        if not os.path.exists(p) or os.path.getsize(p) < 8:
            return False
        with open(p, 'rb') as f:
            head = f.read(4)
            f.seek(-4, os.SEEK_END)
            tail = f.read(4)
        return head == b'PAR1' and tail == b'PAR1'

    with requests.get(CACHE_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        remote_bytes = int(r.headers['Content-Length'])
        if os.path.exists(path) and os.path.getsize(path) == remote_bytes and complete(path):
            return
        with open(path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
    assert os.path.getsize(path) == remote_bytes, 'size mismatch after download'
    assert complete(path), 'not a complete parquet file'

def seed_retrospective_cache(engine, force=False):
    """Bulk-load retrospective_cache from the public HydroShare parquet.

    Idempotent: reaches already present are skipped (ON CONFLICT DO NOTHING) and,
    unless force is set, a table that already holds rows short-circuits before
    any download. Commits one parquet row group at a time so an interrupted run
    resumes. Returns (inserted, skipped).
    """
    import json
    import pyarrow.parquet as pq

    if not force:
        with engine.connect() as conn:
            if conn.execute(text('SELECT 1 FROM retrospective_cache LIMIT 1')).first():
                return (0, 0)

    _download_cache_parquet(CACHE_PATH)

    existing = set()
    with engine.connect() as conn:
        for row in conn.execute(text('SELECT network, reach_id FROM retrospective_cache')):
            existing.add((row[0], row[1]))

    insert_sql = text('''
        INSERT INTO retrospective_cache (network, reach_id, reach_data, start_date, end_date)
        VALUES (:network, :reach_id, CAST(:reach_data AS jsonb), :start_date, :end_date)
        ON CONFLICT (network, reach_id) DO NOTHING
    ''')

    inserted = 0
    skipped = 0
    parquet_file = pq.ParquetFile(CACHE_PATH)
    for batch in parquet_file.iter_batches(batch_size=500):
        params = []
        for row in batch.to_pylist():
            if (row['network'], row['reach_id']) in existing:
                skipped += 1
                continue
            reach_data = {
                'dates': [d.isoformat() for d in row['dates']],
                'units': row['units'],
                'values': row['values'],
            }
            params.append({
                'network': row['network'],
                'reach_id': row['reach_id'],
                'reach_data': json.dumps(reach_data),
                'start_date': row['start_date'],
                'end_date': row['end_date'],
            })
        if params:
            with engine.begin() as conn:
                conn.execute(insert_sql, params)
            inserted += len(params)
    return (inserted, skipped)

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

