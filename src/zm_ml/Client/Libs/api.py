"""
ZM API

Important:

  Make sure you have the following settings in ZM:

  - ``AUTH_RELAY`` is set to hashed
  - A valid ``AUTH_HASH_SECRET`` is provided (not empty)
  - ``AUTH_HASH_IPS`` is disabled
  - ``OPT_USE_APIS`` is enabled
  - If you are using any version lower than ZM 1.34, ``OPT_USE_GOOG_RECAPTCHA`` is disabled
  - If you are NOT using authentication at all in ZM, that is ``OPT_USE_AUTH`` is disabled, then make sure you
  also disable authentication in zmNinja, otherwise it will keep waiting for auth keys.
  - I don't quite know why, but on some devices, connection issues are caused because ZoneMinder's CSRF code
   causes issues. See `this <https://forums.zoneminder.com/viewtopic.php?f=33&p=115422#p115422>`__ thread, for
   example. In this case, try turning off CSRF checks by going to  ``ZM->Options->System`` and disable
   "Enable CSRF magic".

"""

import datetime
import inspect
import re
import logging
from typing import Dict, List, Optional, Union, Tuple

from requests import Response, Session
from requests.exceptions import HTTPError
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

from ..Models.config import ZMAPISettings, MonitorsSettings

GRACE: int = 60 * 5  # 5 mins
lp: str = "api::"
logger = logging.getLogger("ML-Client")
g = None


def version_tuple(version_str: str) -> tuple:
    # https://stackoverflow.com/a/11887825/1361529
    return tuple(map(int, (version_str.split("."))))


def find_whole_word(w: str):
    """Still figuring this out BOI, hold over from @pliablepixels code
    The call parentheses are omitted due to the way this function is used, meaning, the user must use find_whole_word()

    :param str w: The word to search using the ``re`` module
    :return: IDK man
    """
    return re.compile(r"\b({0})\b".format(w), flags=re.IGNORECASE).search


class ZMApi:
    def import_zones(self):
        """A function to import zones that are defined in the ZoneMinder web GUI instead of defining
        zones in the per-monitor section of the configuration file.


        :return:
        """
        imported_zones = []
        lp: str = "api::import zones::"
        mid_cfg = None
        existing_zones = {}
        if g.config.detection_settings.import_zones:
            mid_cfg = g.config.monitors.get(g.mid)
            if mid_cfg:
                existing_zones: Dict = mid_cfg.zones
            monitor_resolution: Tuple[int, int] = (int(g.mon_width), int(g.mon_height))
            match_reason: bool = False
            logger.debug(f"{lp} import_zones() called")

            if match_reason := g.config.detection_settings.match_origin_zone:
                logger.debug(
                    f"{lp}match origin zone:: only triggering on ZM zones that are "
                    f"listed in the event 'Cause' [{g.event_cause}]",
                )
            url = f"{self.get_portalbase()}/api/zones/forMonitor/{g.mid}.json"
            r = self.make_request(url)
            # Now lets look at reason to see if we need to honor ZM motion zones
            if r:
                logger.debug(f"{lp} RESPONSE from zone API call => {r}")
                zones = r.get("zones")
                if zones:
                    logger.debug(f"{lp} {len(zones)} ZM zones found, checking for 'Inactive' zones")
                    for zone in zones:
                        zone_name: str = zone.get("Zone", {}).get("Name", "")
                        zone_type: str = zone.get("Zone", {}).get("Type", "")
                        zone_points: str = zone.get("Zone", {}).get("Coords", "")
                        logger.debug(f"{lp} BEGINNING OF ZONE LOOP - {zone_name=} -- {zone_type=} -- {zone_points=}")
                        if zone_type.casefold() == "inactive":
                            logger.debug(
                                f"{lp} skipping '{zone_name}' as it is set to 'Inactive'"
                            )
                            continue
                        if match_reason:
                            if not re.compile(r"\b({0})\b".format(zone_name)).search(
                                g.event_cause
                            ):
                                logger.debug(
                                    f"{lp}match origin zone:: skipping '{zone_name}' as it is not in event "
                                    f"cause -> '{g.event_cause}' and 'match_origin_zone' is enabled"
                                )
                                continue
                            logger.debug(f"{lp}match origin zone:: '{zone_name}' is in event cause -> '{g.event_cause}'")

                        from ..Models.config import MonitorZones

                        if not mid_cfg:
                            logger.debug(
                                f"{lp} no monitor configuration found for monitor {g.mid}, "
                                f"creating a new one"
                            )
                            mid_cfg = MonitorsSettings(
                                models=None,
                                object_confirm=None,
                                static_objects=None,
                                filters=None,
                                zones={zone_name: MonitorZones(
                                        points=zone_points,
                                        resolution=monitor_resolution,
                                    )},
                            )
                            g.config.monitors[g.mid] = mid_cfg

                        if existing_zones is None:
                            existing_zones = {}

                        if mid_cfg:
                            logger.debug(f"{lp} monitor configuration found for monitor {g.mid}")
                            if existing_zones is not None:
                                logger.debug(f"{lp} existing zones found: {existing_zones}")
                                if not (existing_zone := existing_zones.get(zone_name)):
                                    new_zone = MonitorZones(
                                        points=zone_points,
                                        resolution=monitor_resolution,
                                    )
                                    g.config.monitors[g.mid].zones[zone_name] = new_zone
                                    imported_zones.append({zone_name: new_zone})
                                    logger.debug(
                                        f"{lp} '{zone_name}' has been constructed into a model and imported"
                                    )
                                else:
                                    logger.debug(
                                        f"{lp} '{zone_name}' already exists in the monitor configuration {existing_zone}"
                                    )
                                    # only update if points are not set
                                    if not existing_zone.points:
                                        logger.debug(f"{lp} updating points for '{zone_name}'")
                                        ex_z_dict = existing_zone.dict()
                                        logger.debug(f"{lp} existing zone AS DICT: {ex_z_dict}")
                                        ex_z_dict["points"] = zone_points
                                        ex_z_dict["resolution"] = monitor_resolution
                                        logger.debug(f"{lp} updated zone AS DICT: {ex_z_dict}")
                                        existing_zone = MonitorZones(**ex_z_dict)
                                        logger.debug(f"{lp} updated zone AS MODEL: {existing_zone}")
                                        g.config.monitors[g.mid].zones[zone_name] = existing_zone
                                        imported_zones.append({zone_name: existing_zone})
                                        logger.debug(
                                            f"{lp} '{zone_name}' already exists, updated points and resolution"
                                        )
                                    else:
                                        logger.debug(
                                            f"{lp} '{zone_name}' HAS POINTS SET which is interpreted "
                                            f"as already existing, skipping"
                                        )
                        logger.debug(f"{lp}DBG>>> END OF ZONE LOOP for '{zone_name}'")
        else:
            logger.debug(f"{lp} import_zones() is disabled, skipping")
        logger.debug(f"{lp} ALL ZONES with imported zones => {imported_zones}")
        exit(1)
        return imported_zones

    def __init__(self, options: ZMAPISettings):
        global g
        from ..main import get_global_config

        g = get_global_config()
        lp: str = "api:init:"
        if options is None:
            raise ValueError(f"{lp} options is None")
        self.options = options
        self.api_url: Optional[str] = options.api

        self.auth_enabled: bool = False
        self.access_token: Optional[str] = ""
        self.refresh_token: Optional[str] = ""
        self.refresh_token_datetime: Optional[Union[datetime, str]] = None
        self.access_token_datetime: Optional[Union[datetime, str]] = None
        self.api_version: Optional[str] = ""
        self.zm_version: Optional[str] = ""
        self.zm_tz: Optional[str] = None

        self.session: Session = Session()
        self.username: Optional[str] = options.user
        self.password: Optional[str] = options.password

        # Sanitize logs of urls, passwords, tokens, etc. Makes for easier copy+paste
        if self.options.ssl_verify is False:
            self.session.verify = False
            logger.debug(
                f"{lp} SSL certificate verification disabled (encryption enabled, vulnerable to MITM attacks)",
            )
            disable_warnings(category=InsecureRequestWarning)

        self._login()

    def get_session(self):
        return self.session

    def tz(self, force=False):
        if not self.zm_tz or self.zm_tz and force:
            url = f"{self.api_url}/host/gettimezone.json"
            try:
                r = self.make_request(url=url)
            except HTTPError as err:
                logger.error(
                    f"{lp} timezone API not found, relative timezones will be local time",
                )
                logger.debug(f"{lp} EXCEPTION>>> {err}")
            else:
                self.zm_tz = r.get("tz")

        return self.zm_tz

    # called in _make_request to avoid 401s if possible
    def _refresh_tokens_if_needed(self):

        lp = f"api::_refresh_tokens_if_needed::"
        # stack = inspect.stack()
        # idx = min(len(stack), 1)
        # caller = inspect.getframeinfo(stack[idx][0])
        # stack.reverse()
        # stack = [f"{s[3]}()" for s in stack]
        # stack = " -> ".join(stack)
        # logger.debug(f"{lp} caller: {caller} line_no: {caller.lineno} stack: {stack}")

        # global GRACE
        if not self.access_token_datetime and not self.refresh_token_datetime:
            logger.warning(
                f"{lp} no token expiration times to check, not evaluating if access token needs a refresh"
            )
            return
        sec_remain = (
            self.access_token_datetime - datetime.datetime.now()
        ).total_seconds()
        if sec_remain >= GRACE:  # grace for refresh lifetime
            logger.debug(
                f"{lp} access token still has {sec_remain/60:.2f} minutes remaining"
            )

            return
        else:
            logger.debug(f"{lp} access token has expired, checking if refreshable")
            self._re_login()

    def _re_login(self):
        """Used for 401. I could use _login too but decided to do a simpler fn"""
        lp = f"api::_re_login::"

        time_remaining = (
            self.refresh_token_datetime - datetime.datetime.now()
        ).total_seconds()
        if time_remaining >= GRACE:
            logger.debug(
                f"{lp} using refresh token to get a new auth, as refresh still has {time_remaining / 60} "
                f"minutes remaining",
            )

            url = f"{self.api_url}/host/login.json"
            login_data = {"token": self.refresh_token}
            try:
                response = self.session.post(url, data=login_data)
                response.raise_for_status()

                resp_json = response.json()
                self.api_version = resp_json.get("apiversion")
                self.zm_version = resp_json.get("version")
                if resp_json:
                    if resp_json.get("access_token"):
                        # there is a JSON response and there is data in the access_token field
                        logger.debug(
                            f"{lp} there is a JSON response from REFRESH attempt and an access_token "
                            f"has been supplied"
                        )
                        if version_tuple(self.api_version) >= version_tuple("2.0"):
                            logger.debug(
                                f"{lp} detected API ver 2.0+, using token system",
                            )
                            self.access_token = resp_json.get("access_token", "")
                            if self.access_token:
                                access_token_expires = int(
                                    resp_json.get("access_token_expires")
                                )
                                self.access_token_datetime = (
                                    datetime.datetime.now()
                                    + datetime.timedelta(seconds=access_token_expires)
                                )
                                logger.debug(
                                    f"{lp} access token expires in {access_token_expires} seconds "
                                    f"on: {self.access_token_datetime}",
                                )

            except HTTPError as err:
                logger.error(f"{lp} got API login error: {err}")
                raise err
        else:
            logger.debug(
                f"{lp} refresh token only has {time_remaining}s of lifetime, need to re-login (user/pass)",
            )
        self._login()

    def _login(self):
        """This is called by the constructor. You are not expected to call this directly.

        Raises:
            err: reason for failure
        """
        lp: str = "api::login::"
        login_data: dict = {}
        url = f"{self.api_url}/host/login.json"

        if self.options.user and self.options.password:
            logger.debug(
                f"{lp} no token found, user/pass has been supplied, trying credentials...",
            )
            login_data = {
                "user": self.options.user,
                "pass": self.options.password.get_secret_value(),
            }
        else:
            logger.debug(f"{lp} not using auth (no user/password was supplied)")
            url = f"{self.api_url}/host/getVersion.json"
        try:
            response = self.session.post(url, data=login_data)
            response.raise_for_status()

            resp_json = response.json()
            self.api_version = resp_json.get("apiversion")
            self.zm_version = resp_json.get("version")
            if resp_json:
                if resp_json.get("access_token"):
                    # there is a JSON response and there is data in the access_token field
                    logger.debug(
                        f"{lp} there is a JSON response from login attempt and an access_token "
                        f"has been supplied"
                    )
                    self.auth_enabled = True

                    if version_tuple(self.api_version) >= version_tuple("2.0"):
                        logger.debug(
                            f"{lp} detected API ver 2.0+, using token system",
                        )
                        self.access_token = resp_json.get("access_token", "")
                        self.refresh_token = resp_json.get("refresh_token")
                        if self.access_token:
                            access_token_expires = int(
                                resp_json.get("access_token_expires")
                            )
                            self.access_token_datetime = (
                                datetime.datetime.now()
                                + datetime.timedelta(seconds=access_token_expires)
                            )
                            logger.debug(
                                f"{lp} access token expires in {access_token_expires} seconds "
                                f"on: {self.access_token_datetime}",
                            )
                        if self.refresh_token:
                            refresh_token_expires = int(
                                resp_json.get("refresh_token_expires")
                            )
                            self.refresh_token_datetime = (
                                datetime.datetime.now()
                                + datetime.timedelta(seconds=refresh_token_expires)
                            )
                            logger.debug(
                                f"{lp} refresh token expires in {refresh_token_expires} seconds "
                                f"on: {self.refresh_token_datetime}",
                            )
                else:
                    self.auth_enabled = False
                    logger.debug(f"{lp} it is assumed 'OPT_USE_AUTH' is disabled!")

        except HTTPError as err:
            logger.error(f"{lp} got API login error: {err}")
            code_ = err.response.status_code
            if code_ == 401 or code_ == 403 or code_ == 404:
                logger.error(f"{lp} got 401, trying to re-login")
                self._login()
            elif code_ == 521:
                logger.error(
                    f"{lp} CloudFlare reports that the origin server is down, sleeping for 5 "
                    f"secs and retrying"
                )
                from time import sleep

                sleep(5)
                self._re_login()
            self.authenticated = False
            raise err

    def get_all_event_data(
        self,
        event_id: int,
    ):
        """Returns the data from an 'Event' API call.
            ZoneMinder returns 3 structures in the JSON response.
        - Monitor data - A dict containing data about the event' monitor.
        - Event data - A dict containing all info about the current event.
        - Frame data - A list whose length is the current amount of frames in the frame buffer for the event, also contains data about the frames.

        :param event_id: (int) the event ID to query.
        """
        lp: str = "api::get_all_event_data::"
        event: Optional[Dict] = None
        monitor: Optional[Dict] = None
        frame: Optional[List] = None
        event_tot_frames: Union[int, float, None] = None
        events_url = f"{self.api_url}/events/{event_id}.json"
        api_event_response = self.make_request(url=events_url, quiet=True)
        event = api_event_response.get("event", {}).get("Event")
        monitor = api_event_response.get("event", {}).get("Monitor")
        frame = api_event_response.get("event", {}).get("Frame")
        event_tot_frames = len(frame)
        return event, monitor, frame, event_tot_frames

    def get_portalbase(self):
        """Returns the portal base URL"""
        return self.api_url[:-4]

    def get_monitor_data(
        self,
        mon_id: int,
    ):
        """Returns the data from a 'Monitor' API call."""
        lp: str = "api::get_monitor_data::"

        monitor: Optional[Dict] = None
        monitor_url = f"{self.api_url}/monitors/{mon_id}.json"
        try:
            api_monitor_response = self.make_request(url=monitor_url, quiet=True)
        except Exception as e:
            logger.error(f"{lp} Error during Event data retrieval: {str(e)}")
            logger.debug(f"{lp} EXCEPTION>>> {e}")
        else:
            monitor = api_monitor_response.get("monitor", {}).get("Monitor")
        finally:
            # logger.debug(f" DEBUG Monitor data: {monitor}", caller=caller)
            return monitor

    def get_image(self, fid: Union[int, str]):
        pass

    def make_request(
        self,
        url: Optional[str] = None,
        query: Optional[Dict] = None,
        payload: Optional[Dict] = None,
        type_action: str = "get",
        re_auth: bool = True,
        quiet: bool = False,
    ) -> Union[Dict, Response]:
        lp: str = "api:make_req:"
        if payload is None:
            payload = {}
        if query is None:
            query = {}

        self._refresh_tokens_if_needed()
        type_action = type_action.casefold()
        if self.auth_enabled:
            query["token"] = self.access_token

        try:
            r: Response
            logger.debug(
                f"{lp} '{type_action}'->{url} payload={payload} query={query}",
            ) if not quiet else None
            if type_action == "get":
                r = self.session.get(url, params=query, timeout=240)
            elif type_action == "post":
                r = self.session.post(url, data=payload, params=query)
            elif type_action == "put":
                r = self.session.put(url, data=payload, params=query)
            elif type_action == "delete":
                r = self.session.delete(url, data=payload, params=query)
            else:
                logger.error(f"{lp} unsupported request type: {type_action}")
                raise ValueError(f"Unsupported request type: {type_action}")
            r.raise_for_status()
            # Empty response, e.g. to DELETE requests, can't be parsed to json
            # even if the content-type says it is application/json
            content_type = r.headers.get("content-type", "")
            content_length = r.headers.get("content-length", "")

            if content_type.startswith("application/json") and r.text:
                return r.json()
            elif content_type.startswith("image/"):
                # return raw image data
                return r
            else:
                logger.debug(
                    f"{lp} {content_type = } -- {content_length = } >>> {r.text = }"
                )
                # A non 0 byte response will usually mean it's an image eid request that needs re-login
                if content_length:
                    if content_length != "0":
                        if r.text.lower().startswith("no frame found"):
                            #  r.text = 'No Frame found for event(69129) and frame id(280)']
                            logger.warning(
                                f"{lp} Frame was not found by API! >>> {r.text}"
                            )
                        else:
                            logger.debug(
                                f"{lp} raising RE_LOGIN ValueError -> Non 0 byte response: {r.text}"
                            )
                            raise ValueError("RE_LOGIN")
                    elif content_length == "0":
                        # ZM returns 0 byte body if index not found (no frame ID or out of bounds)
                        logger.debug(f"{lp} {content_length = } >>> {r.text = }")
                        logger.debug(
                            f"{lp} raising BAD_IMAGE ValueError -> Content-Length = {content_length}"
                        )
                        raise ValueError("BAD_IMAGE")

        except HTTPError as http_err:
            if code := http_err.response.status_code == 401 and re_auth:
                logger.debug(
                    f"{lp} Got 401 (Unauthorized) -> {http_err.response.json()}"
                )
                raise ValueError("RE_LOGIN")
            elif code == 404:
                # ZM returns 404 when an image cannot be decoded or the requested event does not exist
                err_json: Optional[dict] = http_err.response.json()
                if err_json:
                    logger.error(f"{lp} 404 to JSON ERROR response >>> {err_json}")
                    if err_json.get("success") is False:
                        # get the reason instead of guessing
                        err_name = err_json.get("data").get("message")
                        err_message = err_json.get("data").get("message")
                        err_url = err_json.get("data").get("url")
                        if err_name == "Invalid event":
                            raise ValueError("INVALID_EVENT")
                        else:
                            raise ValueError("IMAGE_MISSING")
            else:
                logger.debug(f"{lp} HTTP error: {http_err}")
        # If RE_LOGIN is raised, it will be caught by the caller
        except ValueError as val_err:
            err_msg = str(val_err)
            if err_msg == "RE_LOGIN":
                if re_auth:
                    logger.debug(f"{lp} retrying login once")
                    self._refresh_tokens_if_needed()
                    logger.debug(f"{lp} retrying failed request again...")
                    return self.make_request(
                        url, query, payload, type_action, re_auth=False
                    )
            else:
                raise val_err
