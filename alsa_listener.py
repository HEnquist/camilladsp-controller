import sys
import time
import select
import threading
import logging
from copy import deepcopy
from typing import Callable

from dataclasses import dataclass
from enum import Enum

from pyalsa import alsahcontrol

from device_listener import DeviceListener
from datastructures import WaveFormat, DeviceEvent

LOOPBACK_ACTIVE = "PCM Slave Active"
LOOPBACK_CHANNELS = "PCM Slave Channels"
LOOPBACK_FORMAT = "PCM Slave Format"
LOOPBACK_RATE = "PCM Slave Rate"
GADGET_CAP_RATE = "Capture Rate"

INTERFACE_PCM = alsahcontrol.interface_id["PCM"]
INTERFACE_MIXER = alsahcontrol.interface_id["MIXER"]


class SampleFormat(Enum):
    S8 = 0
    U8 = 1
    S16_LE = 2
    S16_BE = 3
    U16_LE = 4
    U16_BE = 5
    S24_LE = 6
    S24_BE = 7
    U24_LE = 8
    U24_BE = 9
    S32_LE = 10
    S32_BE = 11
    U32_LE = 12
    U32_BE = 13
    FLOAT_LE = 14
    FLOAT_BE = 15
    FLOAT64_LE = 16
    FLOAT64_BE = 17
    IEC958_SUBFRAME_LE = 18
    IEC958_SUBFRAME_BE = 19
    MU_LAW = 20
    A_LAW = 21
    IMA_ADPCM = 22
    MPEG = 23
    GSM = 24
    S20_LE = 25
    S20_BE = 26
    U20_LE = 27
    U20_BE = 28
    SPECIAL = 31
    S24_3LE = 32
    S24_3BE = 33
    U24_3LE = 34
    U24_3BE = 35
    S20_3LE = 36
    S20_3BE = 37
    U20_3LE = 38
    U20_3BE = 39
    S18_3LE = 40
    S18_3BE = 41
    U18_3LE = 42
    U18_3BE = 43
    G723_24 = 44
    G723_24_1B = 45
    G723_40 = 46
    G723_40_1B = 47
    DSD_U8 = 48
    DSD_U16_LE = 49
    DSD_U32_LE = 50
    DSD_U16_BE = 51
    DSD_U32_BE = 52


def alsa_format_to_cdsp(fmt):
    if fmt == SampleFormat.S16_LE:
        return "S16LE"
    if fmt == SampleFormat.S24_3LE:
        return "S24LE3"
    if fmt == SampleFormat.S24_LE:
        return "S24LE"
    if fmt == SampleFormat.S32_LE:
        return "S32LE"
    if fmt == SampleFormat.FLOAT_LE:
        return "FLOAT32LE"
    if fmt == SampleFormat.FLOAT64_LE:
        return "FLOAT64LE"


@dataclass
class Control:
    index: int | None
    element: alsahcontrol.Element | None
    value_transform_func: Callable | None


class AlsaControlListener(DeviceListener):
    def __init__(self, device, debounce_time=0.05):

        self._on_change = None

        self._debounce_time = debounce_time
        self._get_card_device_subdevice(device)

        self._hctl = alsahcontrol.HControl(
            self._card, mode=alsahcontrol.open_mode["NONBLOCK"]
        )

        self._all_device_controls = self._hctl.list()

        self._ctl_loopback_active = self._find_control(LOOPBACK_ACTIVE, INTERFACE_PCM)
        self._ctl_loopback_channels = self._find_control(LOOPBACK_CHANNELS, INTERFACE_PCM)
        self._ctl_loopback_format = self._find_control(
            LOOPBACK_FORMAT, INTERFACE_PCM, value_transform_func=SampleFormat
        )
        self._ctl_loopback_rate = self._find_control(LOOPBACK_RATE, INTERFACE_PCM)
        self._ctl_gadget_rate = self._find_control(GADGET_CAP_RATE, INTERFACE_PCM)

        self._poller = select.poll()
        self._hctl.register_poll(self._poller)

        self._poll_thread = None
        self._wave_format = self.read_wave_format()
        self._is_active = self.is_active()
        self._running = False

    def __del__(self):
        self.stop()


    def _find_control(self, name, interface, value_transform_func=None):
        index = self._find_element(name, interface)
        if index is None:
            return None
        element = alsahcontrol.Element(self._hctl, index)
        if element is None:
            return None
        return Control(
            index=index, element=element, value_transform_func=value_transform_func
        )

    def _get_card_device_subdevice(self, dev):
        parts = dev.split(",")
        if len(parts) >= 3:
            self._subdev_nbr = int(parts[2])
        else:
            self._subdev_nbr = 0
        if len(parts) >= 2:
            self._device_nbr = int(parts[1])
        else:
            self._device_nbr = 0
        self._card = parts[0]

    def _find_element(self, wanted_name, interface, device=None, subdevice=None):
        if device is None:
            device = self._device_nbr
        if subdevice is None:
            subdevice = self._subdev_nbr
        found = None
        for idx, iface, dev, subdev, name, _ in self._all_device_controls:
            if (
                name == wanted_name
                and dev == device
                and subdev == subdevice
                and iface == interface
            ):
                found = idx
                logging.debug("Found control '%s' with index %d", wanted_name, idx)
                break
        return found

    def _read_element_value(self, elem):
        if elem is None:
            return None
        info = alsahcontrol.Info(elem)
        val = alsahcontrol.Value(elem)
        values = val.get_tuple(info.type, info.count)
        val.set_tuple(info.type, values)
        val.read()
        logging.debug("Read element value %s", values)
        return values[0]

    def _read_control_value(self, ctl: Control | None):
        if ctl is None:
            return None
        value = self._read_element_value(ctl.element)
        if ctl.value_transform_func is not None:
            return ctl.value_transform_func(value)
        return value

    def is_active(self):
        gadget_rate = self._read_control_value(self._ctl_gadget_rate)
        if gadget_rate is not None:
            return gadget_rate > 0
        return self._read_control_value(self._ctl_loopback_active)

    def read_wave_format(self):
        loopback_rate = self._read_control_value(self._ctl_loopback_rate)
        loopback_channels = self._read_control_value(self._ctl_loopback_channels)
        loopback_format = self._read_control_value(self._ctl_loopback_format)
        gadget_rate = self._read_control_value(self._ctl_gadget_rate)
        if gadget_rate is not None:
            return WaveFormat(
                sample_format=None, channels=None, sample_rate=gadget_rate
            )
        return WaveFormat(
            sample_format=alsa_format_to_cdsp(loopback_format),
            channels=loopback_channels,
            sample_rate=loopback_rate,
        )

    def _determine_action(self):
        new_wave_format = self.read_wave_format()
        new_active = self.is_active()
        if not self._is_active and new_active:
            logging.debug("Device became active")
            self._is_active = True
            event = DeviceEvent.STARTED
            event.set_data(deepcopy(new_wave_format))
            self._emit_event(event)
        elif self._is_active and not new_active:
            logging.debug("Device became inactive")
            self._is_active = False
            event = DeviceEvent.STOPPED
            self._emit_event(event)
        elif self._is_active and new_active and self._wave_format != new_wave_format:
            logging.debug("Device remained active but changed wave format")
            stop_event = DeviceEvent.STOPPED
            self._emit_event(stop_event)
            start_event = DeviceEvent.STARTED
            start_event.set_data(deepcopy(new_wave_format))
            self._emit_event(start_event)
        self._wave_format = new_wave_format

    def _emit_event(self, event):
        if self._on_change is not None:
            self._on_change(event)

    def _pollingloop(self):
        while self._running:
            pollres = self._poller.poll()
            if pollres:
                time.sleep(self._debounce_time)
                self._hctl.handle_events()
                self._determine_action()

    def run(self):
        logging.info("Starting Alsa listener")
        self._running = True
        self._poll_thread = threading.Thread(target=self._pollingloop, daemon=True)
        self._poll_thread.start()

    def stop(self):
        logging.info("Stopping Alsa listener")
        if self._running:
            self._running = False
            if self._poll_thread is not None:
                self._poll_thread.join()

    def set_on_change(self, function):
        self._on_change = function


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    device = sys.argv[1]
    listener = AlsaControlListener(device, debounce_time=0.05)

    def notifier(params):
        logging.info("%s %s", params, params.data)

    listener.set_on_change(notifier)
    listener.run()
    while True:
        time.sleep(10)
