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

