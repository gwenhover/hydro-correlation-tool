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
    nwm_kge_rating = Column(Float, nullable=True)
    nwm_kge_shared_dates = Column(BigInteger, nullable=True)
    geoglows_river_id = Column(BigInteger, nullable=True)
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

    
SEED_CSV = os.path.join(os.path.dirname(__file__), 'public', 'data', 'seed.csv')

def init_primary_db(engine, first_time):
    
    Base.metadata.create_all(engine)

    if first_time:
        Session = sessionmaker(bind=engine)  
        session = Session()                   
        seed = pd.read_csv(SEED_CSV)
        for index, row in seed.iterrows():
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

    new_row = hctTable(usgs_id=row['usgs_id'], gage_name=row['gage_name'], latitude=row['latitude'], 
                       longitude=row['longitude'], geom=geomPoint, nwm_feature_id=nwm_row,
                       geoglows_river_id=geo_row)
    return(new_row)

