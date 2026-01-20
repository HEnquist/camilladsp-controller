from datastructures import WaveFormat

class DeviceListener:
    """
    A base class for a device listener
    """

    def __init__(self, device):
        pass

    def run(self):
        """
        Start listening for events from the device.
        May do nothing if events are not supported.
        """
        pass

    def set_on_change(self, function):
        """
        Provide a callback function that gets called when some event occurs.
        The callback is called once per event, and will be called with a DeviceEvent as argument.
        """
        pass

    def read_wave_format(self):
        """
        Read and return the current values.
        """
        return WaveFormat(channels=None, sample_format=None, sample_rate=None)

    def is_active(self):
        """
        Check if the device is active, i.e. if sound is playing.
        The default implementation returns True.
        Override for devices that can provide this information.
        """
        return True
