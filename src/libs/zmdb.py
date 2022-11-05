import glob
from configparser import ConfigParser
from datetime import datetime
from decimal import Decimal
from logging import getLogger
from pathlib import Path
from typing import Optional, Union, Tuple

from pydantic import Field, AnyUrl, validator, BaseSettings
from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.engine import Engine, Connection, CursorResult
from sqlalchemy.exc import SQLAlchemyError

from src.main import get_global_config

logger = getLogger("src")
lp = "ZMDB:"


def _rel_path(eid: int, mid: int, scheme: str, dt: Optional[datetime] = None) -> str:
    ret_val: str = ""
    lp: str = "zmes:rel path:"
    if scheme == "Deep":
        if dt:
            ret_val = f"{mid}/{dt.strftime('%y/%m/%d/%H/%M/%S')}"
        else:
            logger.error(f"{lp} no datetime for deep scheme path!")
    elif scheme == "Medium":
        ret_val = f"{mid}/{dt.strftime('%Y-%m-%d')}/{eid}"
    elif scheme == "Shallow":
        ret_val = f"{mid}/{eid}"
    else:
        logger.error(f"{lp} unknown scheme {scheme}")
    return ret_val


class ZMEnvVars(BaseSettings):
    conf_path: Path = Field(
        None, description="Path to ZoneMinder config files", env="CONF_PATH"
    )

    @validator("conf_path", pre=True, always=True)
    def conf_path(cls, v):
        if not v:
            v = "/etc/zm"
        v = Path(v)
        if not v.is_dir():
            raise ValueError(f"Config path {v} does not exist")
        return v

    class Config:
        env_prefix = "ZM_ML_"


class DBEnvVars(ZMEnvVars):
    host: AnyUrl = Field(None, description="Database host", env="DBHOST")
    user: str = Field(None, description="Database user", env="DBUSER")
    password: str = Field(None, description="Database password", env="DBPASSWORD")
    name: str = Field(None, description="Database name", env="DBNAME")
    driver: str = Field("mysql+pymysql", description="Database driver", env="DBDRIVER")


class ZMDB:
    engine: Optional[Engine]
    connection: Optional[Connection]
    meta: Optional[MetaData]
    connection_str: str
    db_config: DBEnvVars

    def __init__(self):
        self._db_create()

    def _db_create(self):
        """A private function to interface with the ZoneMinder DataBase"""
        # From @pliablepixels SQLAlchemy work - all credit goes to them.
        lp: str = "ZM-DB:"
        db_config = DBEnvVars()
        files = []
        conf_path = db_config.conf_path
        if conf_path.is_dir():
            for fi in glob.glob(f"{conf_path}/conf.d/*.conf"):
                files.append(fi)
            files.sort()
            files.insert(0, f"{conf_path}/zm.conf")
            config_file = ConfigParser(interpolation=None, inline_comment_prefixes="#")
            try:
                for f in files:
                    with open(f, "r") as zm_conf_file:
                        # This adds [zm_root] section to the head of each zm .conf.d config file,
                        # not physically only in memory
                        config_file.read_string(f"[zm_root]\n{zm_conf_file.read()}")
            except Exception as exc:
                logger.error(f"{lp} error opening ZoneMinder .conf files! -> {files}")
            else:
                conf_data = config_file["zm_root"]
                if not db_config.user:
                    db_config.user = conf_data["ZM_DB_USER"]
                if not db_config.password:
                    db_config.password = conf_data["ZM_DB_PASS"]
                if not db_config.host:
                    db_config.host = conf_data["ZM_DB_HOST"]
                if not db_config.name:
                    db_config.name = conf_data["ZM_DB_NAME"]
                self.db_config = db_config

            self.connection_str = (
                f"{db_config.driver}://{db_config.user}"
                f":{db_config.password}@{db_config.host}"
                f"/{db_config.name}"
            )

            try:
                self._check_conn()
            except SQLAlchemyError as e:
                logger.error(f"DB configs - {self.connection_str}")
                logger.error(f"Could not connect to DB, message was: {e}")

    def _check_conn(self):
        if not self.engine:
            self.engine = create_engine(self.connection_str, pool_recycle=3600)
        if not self.connection:
            self.connection = self.engine.connect()
        if not self.meta:
            self._refresh_meta()

    def _refresh_meta(self):
        self.meta = MetaData(self.engine)
        self.meta.reflect(only=["Events", "Monitors", "Monitor_Status", "Storage"])

    def grab_all(self, eid: int) -> Tuple[int, str, int, int, Decimal, str, str]:
        #         return mid, mon_name, mon_post, mon_pre, mon_fps, reason, event_path
        self._check_conn()
        g = get_global_config()
        mid: Optional[Union[str, int]] = None
        mon_name: Optional[str] = None
        mon_post: Optional[Union[str, int]] = None
        mon_pre: Optional[Union[str, int]] = None
        mon_fps: Optional[Union[float, Decimal]] = None
        reason: Optional[str] = None
        notes: Optional[str] = None
        scheme: Optional[str] = None
        storage_id: Optional[int] = None
        start_datetime: Optional[datetime] = None
        storage_path: Optional[str] = None
        event_path: Optional[str] = None

        e_select: select = select([self.meta.tables["Events"].c.MonitorId]).where(
            self.meta.tables["Events"].c.Id == eid
        )
        mid_result: CursorResult = self.connection.execute(e_select)

        for row in mid_result:
            mid = row[0]
        mid_result.close()
        if mid:
            mid = int(mid)
            logger.debug(f"{lp} ZoneMinder DB returned Monitor ID: {mid}")
            g.mid = mid
        else:
            logger.warning(
                f"{lp} the database query did not return a monitor ID for this event?"
            )

        mid_name_select: select = select([self.meta.tables["Monitors"].c.Name]).where(
            self.meta.tables["Monitors"].c.Id == mid
        )
        pre_event_select: select = select(
            [self.meta.tables["Monitors"].c.PreEventCount]
        ).where(self.meta.tables["Monitors"].c.Id == mid)

        # Get Monitor 'Name'

        mid_name_result: CursorResult = self.connection.execute(mid_name_select)
        for mon_row in mid_name_result:
            mon_name = mon_row[0]
        mid_name_result.close()
        if mon_name:
            logger.debug(f"{lp} ZoneMinder DB returned monitor name ('{mon_name}')")
        else:
            logger.warning(
                f"{lp} the database query did not return a monitor name ('Name') for monitor ID {mid}"
            )

        # Get Monitor Pre/Post Event Count

        pre_event_result: CursorResult = self.connection.execute(pre_event_select)
        for mon_row in pre_event_result:
            mon_pre = mon_row[0]
        pre_event_result.close()
        if mon_pre:
            mon_pre = int(mon_pre)
            logger.debug(
                f"{lp} ZoneMinder DB returned monitor PreEventCount ('{mon_pre}')"
            )
        else:
            logger.warning(
                f"{lp} the database query did not return monitor pre-event count ('PreEventCount') for monitor ID {mid}"
            )
        # PostEventCount
        post_event_select: select = select(
            [self.meta.tables["Monitors"].c.PostEventCount]
        ).where(self.meta.tables["Monitors"].c.Id == mid)
        select_result: CursorResult = self.connection.execute(post_event_select)

        for mon_row in select_result:
            mon_post = mon_row[0]
        select_result.close()
        if mon_post:
            mon_post = int(mon_post)
            logger.debug(
                f"{lp} ZoneMinder DB returned monitor PostEventCount ('{mon_post}')"
            )
        else:
            logger.warning(
                f"{lp} the database query did not return monitor post-event count ('PostEventCount') for monitor ID {mid}"
            )
        # Get Monitor capturing FPS
        ms_select: select = select(
            [self.meta.tables["Monitor_Status"].c.CaptureFPS]
        ).where(self.meta.tables["Monitor_Status"].c.MonitorId == mid)
        select_result: CursorResult = self.connection.execute(ms_select)
        for mons_row in select_result:
            mon_fps = float(mons_row[0])
        select_result.close()
        if mon_fps:
            mon_fps = Decimal(mon_fps)
            logger.debug(f"{lp} ZoneMinder DB returned monitor FPS ('{mon_fps}')")
        else:
            logger.warning(
                f"{lp} the database query did not return monitor FPS ('CaptureFPS') for monitor ID {mid}"
            )

        reason_select: select = select([self.meta.tables["Events"].c.Cause]).where(
            self.meta.tables["Events"].c.Id == eid
        )
        notes_select: select = select([self.meta.tables["Events"].c.Notes]).where(
            self.meta.tables["Events"].c.Id == eid
        )
        scheme_select: select = select([self.meta.tables["Events"].c.Scheme]).where(
            self.meta.tables["Events"].c.Id == eid
        )
        storage_id_select: select = select(
            [self.meta.tables["Events"].c.StorageId]
        ).where(self.meta.tables["Events"].c.Id == eid)
        start_datetime_select: select = select(
            [self.meta.tables["Events"].c.StartDateTime]
        ).where(self.meta.tables["Events"].c.Id == eid)
        reason_result: CursorResult = self.connection.execute(reason_select)
        notes_result: CursorResult = self.connection.execute(notes_select)
        scheme_result: CursorResult = self.connection.execute(scheme_select)
        storage_id_result: CursorResult = self.connection.execute(storage_id_select)
        start_datetime_result: CursorResult = self.connection.execute(
            start_datetime_select
        )
        for row in notes_result:
            g.notes = row[0]
        notes_result.close()
        for row in reason_result:
            reason = row[0]
        reason_result.close()
        for row in scheme_result:
            scheme = row[0]
        scheme_result.close()
        for row in storage_id_result:
            storage_id = row[0]
        storage_id_result.close()
        for row in start_datetime_result:
            start_datetime = row[0]
        start_datetime_result.close()

        if storage_id:
            storage_path_select: select = select(
                [self.meta.tables["Storage"].c.Path]
            ).where(self.meta.tables["Storage"].c.Id == storage_id)
            storage_path_result: CursorResult = self.connection.execute(
                storage_path_select
            )
            for row in storage_path_result:
                storage_path = row[0]
            storage_path_result.close()
        else:
            logger.debug(f"{lp} no storage ID for event {eid}")

        if start_datetime:
            if storage_path:
                event_path = (
                    f"{storage_path}/{_rel_path(eid, mid, scheme, start_datetime)}"
                )
            else:
                if storage_id:
                    logger.error(
                        f"{lp} no storage path for StorageId {storage_id}, the StorageId could "
                        f"of been removed/deleted/disabled"
                    )
                else:
                    logger.error(f"{lp} no StorageId for event {eid}!")
        else:
            logger.debug(f"{lp} no StartDateTime for event {eid}")

        if event_path:
            logger.debug(
                f"{lp} storage path for event ID: {eid} has been calculated as '{event_path}'"
            )
        else:
            logger.warning(
                f"{lp} the database could not calculate the storage path for this event!"
            )

        if reason:
            logger.debug(f"{lp} ZoneMinder DB returned event cause as '{reason}'")
        else:
            logger.warning(
                f"{lp} the database query did not return a 'reason' ('Cause') for this event!"
            )

            # Get Monitor 'ImageBufferCount'
        buffer_select: select = select(self.meta.tables["Monitors"].c.ImageBufferCount).where(
            self.meta.tables["Monitors"].c.Id == g.mid)
        width_select: select = select(self.meta.tables["Monitors"].c.Width).where(self.meta.tables["Monitors"].c.Id == g.mid)
        height_select: select = select(self.meta.tables["Monitors"].c.Height).where(self.meta.tables["Monitors"].c.Id == g.mid)
        colours_select: select = select(self.meta.tables["Monitors"].c.Colours).where(
            self.meta.tables["Monitors"].c.Id == g.mid)
        buffer_result: CursorResult = self.connection.execute(buffer_select)
        for mon_row in buffer_result:
            g.mon_image_buffer_count = mon_row[0]
        buffer_result.close()

        width_result: CursorResult = self.connection.execute(width_select)
        for mon_row in width_result:
            g.mon_width = mon_row[0]
        width_result.close()
        # height
        height_result: CursorResult = self.connection.execute(height_select)
        for mon_row in height_result:
            g.mon_height = mon_row[0]
        height_result.close()
        # colours
        colours_result: CursorResult = self.connection.execute(colours_select)
        for mon_row in colours_result:
            g.mon_colorspace = mon_row[0]
        colours_result.close()

        if g.mon_image_buffer_count:
            print(f"Got ImageBufferCount for monitor {g.mid} -=> {g.mon_image_buffer_count = }")
        else:
            print(
                f"{lp} the database query did not return ImageBufferCount for monitor {g.mid}"
            )
        if g.mon_width:
            print(f"Got Width for monitor {g.mid} -=> {g.mon_width}")
        else:
            print(
                f"{lp} the database query did not return Width for monitor {g.mid}"
            )
        if g.mon_height:
            print(f"Got Height for monitor {g.mid} -=> {g.mon_height}")
        else:
            print(
                f"{lp} the database query did not return Height for monitor {g.mid}"
            )
        if g.mon_colorspace:
            print(f"Got Colours for monitor {g.mid} -=> {g.mon_colorspace}")
        else:
            print(
                f"{lp} the database query did not return Colours for monitor {g.mid}"
            )

        g.mon_name = mon_name
        g.mon_post = mon_post
        g.mon_pre = mon_pre
        g.mon_fps = mon_fps
        g.reason = reason
        g.event_path = event_path
        return mid, mon_name, mon_post, mon_pre, mon_fps, reason, event_path