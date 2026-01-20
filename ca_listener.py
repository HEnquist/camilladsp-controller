import cffi

from datastructures import WaveFormat, DeviceEvent
from device_listener import DeviceListener

try:
    from _ca_listener import ffi, lib
except ImportError:
    print("Compiling bindings, this will only be done on the first run.")
    import ca_listener_build

    ca_listener_build.run_build()
    from _ca_listener import ffi, lib


def to_int(bytes):
    return int.from_bytes(bytes, byteorder="big")


kAudioObjectPropertyElementMaster = 0
kAudioObjectPropertyScopeGlobal = to_int(b"glob")
kAudioObjectPropertyName = to_int(b"lnam")
kAudioObjectSystemObject = 1
kAudioDevicePropertyNominalSampleRate = to_int(b"nsrt")
kAudioHardwarePropertyDevices = to_int(b"dev#")
kAudioStreamPropertyPhysicalFormat = to_int(b"pft ")
kCFStringEncodingUTF8 = 0x08000100


class CAListener(DeviceListener):
    def __init__(self, device):

        self.device_name = device
        self.device_id = self._get_capture_device_id(device)

        self.sr_prop = self._property_address(kAudioDevicePropertyNominalSampleRate)
        self.asbd_prop = self._property_address(kAudioStreamPropertyPhysicalFormat)

        self.on_change = None
        self.listening = False

        self.self_ref = ffi.new_handle(self)

    def __del__(self):
        self.stop()

    def _property_address(
        self,
        selector,
        scope=kAudioObjectPropertyScopeGlobal,
        element=kAudioObjectPropertyElementMaster,
    ):
        prop = ffi.new(
            "AudioObjectPropertyAddress*",
            {"mSelector": selector, "mScope": scope, "mElement": element},
        )
        return prop

    def _read_property(self, id, prop_address, ctype):
        size = ffi.new("UInt32*")
        res = lib.AudioObjectGetPropertyDataSize(id, prop_address, 0, ffi.NULL, size)
        if res != 0:
            return None

        num_values = int(size[0] // ffi.sizeof(ctype))

        prop_data = ffi.new(ctype + "[]", num_values)
        res = lib.AudioObjectGetPropertyData(
            id, prop_address, 0, ffi.NULL, size, prop_data
        )
        if res != 0:
            return None
        return prop_data

    def _CFString_to_str(self, cfstr_ptr):
        str_length = lib.CFStringGetLength(cfstr_ptr[0])
        str_buffer = ffi.new("char[]", 4 * str_length + 1)
        success = lib.CFStringGetCString(
            cfstr_ptr[0], str_buffer, str_length + 1, kCFStringEncodingUTF8
        )
        if not success:
            return None
        return ffi.string(str_buffer).decode()

    def _get_capture_device_id(self, wanted_name):
        id_prop = self._property_address(kAudioHardwarePropertyDevices)
        device_ids = self._read_property(
            kAudioObjectSystemObject, id_prop, "AudioObjectID"
        )
        name_prop = self._property_address(kAudioObjectPropertyName)
        for id in device_ids:
            name_ptr = self._read_property(id, name_prop, "CFStringRef")
            name = self._CFString_to_str(name_ptr)
            print(name, id)
            if name == wanted_name:
                print("found it!", id, name)
                return id
        raise ValueError(f"Cannot find device with name {wanted_name}")

    def run(self):
        if self.listening:
            raise RuntimeError("Already listening to events")

        res = lib.AudioObjectAddPropertyListener(
            self.device_id, self.sr_prop, lib.property_listener, self.self_ref
        )
        if res != 0:
            raise RuntimeError(f"Unable to register listener, code: {res}")
        self.listening = True
        print("Listening...")

    def _emit_event(self, event):
        if self.on_change is not None:
            self.on_change(event)

    def stop(self):
        if not self.listening:
            raise RuntimeError("Not listening to events")
        res = lib.AudioObjectRemovePropertyListener(
            self.device_id, self.sr_prop, lib.property_listener, self.self_ref
        )
        if res != 0:
            raise RuntimeError(f"Unable to remove listener, code: {res}")
        self.listening = False
        print("Stopped listening")

    def set_on_change(self, function):
        self.on_change = function

    def read_wave_format(self):
        asbd = self._read_property(
            self.device_id, self.asbd_prop, "AudioStreamBasicDescription"
        )
        return WaveFormat(
            channels=asbd[0].mChannelsPerFrame,
            sample_rate=int(asbd[0].mSampleRate),
            sample_format=None,
        )


@ffi.def_extern()
def property_listener(inObjectID, _inNumberAddresses, _inAddresses, inClientData):
    self = ffi.from_handle(inClientData)
    wave_format = self.read_wave_format()
    stop_event = DeviceEvent.STOPPED
    self._emit_event(stop_event)
    start_event = DeviceEvent.STARTED
    start_event.set_data(wave_format)
    self._emit_event(start_event)
    return 0


def demo(device):
    import time

    listener = CAListener(device)

    def dummy_callback(params):
        print(params, params.data)

    print(listener.read_wave_format())
    listener.set_on_change(dummy_callback)
    listener.run()
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        print("Exit requested")


if __name__ == "__main__":
    import sys

    device = sys.argv[1]
    demo(device)
