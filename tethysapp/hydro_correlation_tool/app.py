from tethys_sdk.base import TethysAppBase
from tethys_sdk.app_settings import CustomSetting, PersistentStoreDatabaseSetting

class App(TethysAppBase):
    """
    Tethys app class for Hydro Correlation Tool.
    """
    name = 'Hydro Correlation Tool'
    description = 'The Hydro Correlation Tool is a single-user scientific workbench for building and maintaining a high-quality cross-mapping table that links each active USGS streamflow gage to corresponding NWM v3 reach and GEOGLOWS v2 river.'
    package = 'hydro_correlation_tool'  # WARNING: Do not change this value
    index = 'home'
    icon = f'{package}/images/icon_gage_on_river.svg'
    root_url = 'hydro-correlation-tool'
    color = '#002E5D'
    tags = '"NWM","GEOGLOWS","USGS Gages"'
    enable_feedback = False
    feedback_emails = []

    def custom_settings(self):
        custom_settings = (
            CustomSetting(
                name='MapBox PK Token',
                type=CustomSetting.TYPE_STRING,
                description='The key for the mapbox vector tiles.',
                required=True
            ),
            CustomSetting(
                name='USGS API Token',
                type=CustomSetting.TYPE_STRING,
                description='The key for the USGS data. Optional, as it just raises rate limits.',
                required=False
            ),
            CustomSetting(
                name='NWM API Token',
                type=CustomSetting.TYPE_STRING,
                description='The key for the NWM API',
                required=True
            ),
            CustomSetting(
                name='Headwater Threshold',
                type=CustomSetting.TYPE_INTEGER,
                description='The lowest order stream order threshold, not inclusive. If you enter "2", the toggle shows 3 and up.',
                required=False,
                default=2
            )
        )
        return custom_settings
    
    def persistent_store_settings(self):
        """
        Define Persistent Store Settings.
        """
        ps_settings = (
            PersistentStoreDatabaseSetting(
                name='primary_db',
                description='primary database',
                initializer='hydro_correlation_tool.model.init_primary_db',
                required=True,
                spatial=True
            ),
        )
        return (ps_settings)

