"""Support for Pioneer Network Receivers."""
import logging
import telnetlib
import time

import voluptuous as vol

from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerDevice
from homeassistant.components.media_player.const import (
    SUPPORT_PAUSE,
    SUPPORT_PLAY,
    SUPPORT_SELECT_SOURCE,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_VOLUME_MUTE,
    SUPPORT_VOLUME_SET,
    SUPPORT_VOLUME_STEP,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
    CONF_TIMEOUT,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.exceptions import PlatformNotReady
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

CONF_SOURCES = "sources"
CONF_FAKEVOLUMESET = "fakevolumeset"

DEFAULT_NAME = "Pioneer AVR"
DEFAULT_PORT = 23  # telnet default. Some Pioneer AVRs use 8102
DEFAULT_TIMEOUT = None
DEFAULT_SOURCES = {}
DEFAULT_FAKEVOLUMESET = False

SUPPORT_PIONEER = (
    SUPPORT_PAUSE
    | SUPPORT_VOLUME_SET
    | SUPPORT_VOLUME_STEP
    | SUPPORT_VOLUME_MUTE
    | SUPPORT_TURN_ON
    | SUPPORT_TURN_OFF
    | SUPPORT_SELECT_SOURCE
    | SUPPORT_PLAY
)

MAX_VOLUME = 185
MAX_SOURCE_NUMBERS = 60
MAX_TRIES = 5
TRY_DELAY = 0.5

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_TIMEOUT): cv.socket_timeout,
        vol.Optional(CONF_SOURCES, default=DEFAULT_SOURCES): {cv.string: cv.string},
        vol.Optional(CONF_FAKEVOLUMESET, default=DEFAULT_FAKEVOLUMESET): cv.boolean,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the Pioneer platform."""
    pioneer = PioneerDevice(
        config[CONF_NAME],
        config[CONF_HOST],
        config[CONF_PORT],
        config[CONF_TIMEOUT],
        config[CONF_SOURCES],
        config[CONF_FAKEVOLUMESET],
    )

    if pioneer.update():
        add_entities([pioneer])
    else:
        raise PlatformNotReady


class PioneerDevice(MediaPlayerDevice):
    """Representation of a Pioneer device."""

    def __init__(self, name, host, port, timeout, sources, fakevolumeset):
        """Initialize the Pioneer device."""
        self._name = name
        self._host = host
        self._port = port
        self._timeout = timeout
        self._pwstate = "PWR1"
        self._volume = 0
        self._muted = False
        self._selected_source = ""
        self._source_name_to_number = sources
        self._source_number_to_name = {v: k for k, v in sources.items()}
        self._fakevolumeset = fakevolumeset
        self._vol_inc_steps = None

    @classmethod
    def telnet_request(cls, telnet, command, expected_prefix):
        """Execute `command` and return the response."""
        try:
            telnet.write(command.encode("ASCII") + b"\r")
        except telnetlib.socket.timeout:
            _LOGGER.debug("Pioneer command %s timed out", command)
            return None

        # The receiver will randomly send state change updates, make sure
        # we get the response we are looking for
        for _ in range(3):
            result = telnet.read_until(b"\r\n", timeout=0.2).decode("ASCII").strip()
            if result.startswith(expected_prefix):
                return result

        return None

    def telnet_command(self, command):
        """Establish a telnet connection and sends command."""
        tries = MAX_TRIES
        while tries > 0:
            tries = tries - 1
            try:
                try:
                    telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
                    telnet.write(command.encode("ASCII") + b"\r")
                    telnet.read_very_eager()  # skip response
                    telnet.close()
                    break
                except (ConnectionRefusedError, OSError):
                    _LOGGER.warning(
                        "telnet_command: Pioneer %s refused connection", self._name
                    )
                    time.sleep(TRY_DELAY)
                    continue
            except telnetlib.socket.timeout:
                _LOGGER.debug("Pioneer %s command %s timed out", self._name, command)
        if tries == 0:
            _LOGGER.warning(
                "Tried %d times, but Pioneer %s still refused connection",
                MAX_TRIES,
                self._name,
            )

    def update(self):
        """Get the latest details from the device."""
        tries = MAX_TRIES
        while tries > 0:
            tries = tries - 1
            try:
                telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
                break
            except (ConnectionRefusedError, OSError):
                _LOGGER.debug("update: Pioneer %s refused connection", self._name)
                time.sleep(TRY_DELAY)
                continue
        if tries == 0:
            _LOGGER.warning(
                "Tried %d times, but Pioneer %s still refused connection",
                MAX_TRIES,
                self._name,
            )
            return False

        pwstate = self.telnet_request(telnet, "?P", "PWR")
        if pwstate:
            self._pwstate = pwstate

        volume_str = self.telnet_request(telnet, "?V", "VOL")
        self._volume = int(volume_str[3:]) / MAX_VOLUME if volume_str else None

        muted_value = self.telnet_request(telnet, "?M", "MUT")
        self._muted = (muted_value == "MUT0") if muted_value else None

        # Build the source name dictionaries if necessary
        if not self._source_name_to_number:
            for i in range(MAX_SOURCE_NUMBERS):
                result = self.telnet_request(telnet, f"?RGB{str(i).zfill(2)}", "RGB")

                if not result:
                    continue

                source_name = result[6:]
                source_number = str(i).zfill(2)

                self._source_name_to_number[source_name] = source_number
                self._source_number_to_name[source_number] = source_name

        source_number = self.telnet_request(telnet, "?F", "FN")

        if source_number:
            self._selected_source = self._source_number_to_name.get(source_number[2:])
        else:
            self._selected_source = None

        telnet.close()
        return True

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        if self._pwstate == "PWR2":
            return STATE_OFF
        if self._pwstate == "PWR1":
            return STATE_OFF
        if self._pwstate == "PWR0":
            return STATE_ON

        return None

    @property
    def volume_level(self):
        """Volume level of the media player (0..1)."""
        return self._volume

    @property
    def is_volume_muted(self):
        """Boolean if volume is currently muted."""
        return self._muted

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_PIONEER

    @property
    def source(self):
        """Return the current input source."""
        return self._selected_source

    @property
    def source_list(self):
        """List of available input sources."""
        return list(self._source_name_to_number.keys())

    @property
    def media_title(self):
        """Title of current playing media."""
        return self._selected_source

    def turn_off(self):
        """Turn off media player."""
        self.telnet_command("PF")

    def volume_up(self):
        """Volume up media player."""
        self.telnet_command("VU")

    def volume_down(self):
        """Volume down media player."""
        self.telnet_command("VD")

    def set_volume_level(self, volume):
        """Set volume level, range 0..1."""
        if self._fakevolumeset:
            tries = MAX_TRIES
            while tries > 0:
                tries = tries - 1
                try:
                    telnet = telnetlib.Telnet(self._host, self._port, self._timeout)
                    target_steps = int(volume * MAX_VOLUME)
                    cmd = "VU" if (volume > self._volume) else "VD"

                    if self._vol_inc_steps == None:
                        # VU and VD increase/decrease volumes in fixed steps (usually 2 but not known for all devices), probe if unknown:
                        volume_str1 = self.telnet_request(telnet, "VU", "VOL")
                        volume_str2 = self.telnet_request(telnet, "VD", "VOL")
                        if volume_str1 and volume_str2:
                            vol1 = int(volume_str1[3:])
                            vol2 = int(volume_str2[3:])
                            self._vol_inc_steps = abs(vol2 - vol1)
                        else:
                            _LOGGER.error(
                                "No response from {} while probing step size".format(
                                    self._name
                                )
                            )
                            time.sleep(TRY_DELAY)
                            continue

                    while (
                        abs(target_steps - int(self._volume * MAX_VOLUME))
                        >= self._vol_inc_steps
                    ):
                        volume_str = self.telnet_request(telnet, cmd, "VOL")
                        if volume_str:
                            current_steps = int(volume_str[3:])
                            self._volume = current_steps / MAX_VOLUME
                        else:
                            _LOGGER.error(
                                "No response from {} while fake setting volume".format(
                                    self._name
                                )
                            )
                            break

                    self._volume = target_steps / MAX_VOLUME
                    telnet.close()
                    break
                except (ConnectionRefusedError, OSError):
                    _LOGGER.debug("Pioneer %s refused connection", self._name)
                    time.sleep(TRY_DELAY)
                    continue
            if tries == 0:
                _LOGGER.warning(
                    "Tried %d times, but Pioneer %s still refused connection",
                    MAX_TRIES,
                    self._name,
                )
        else:
            # 60dB max
            self.telnet_command(f"{round(volume * MAX_VOLUME):03}VL")

    def mute_volume(self, mute):
        """Mute (true) or unmute (false) media player."""
        self.telnet_command("MO" if mute else "MF")

    def turn_on(self):
        """Turn the media player on."""
        self.telnet_command("PO")

    def select_source(self, source):
        """Select input source."""
        self.telnet_command(f"{self._source_name_to_number.get(source)}FN")
