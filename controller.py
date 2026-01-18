import time
from copy import deepcopy
import yaml
import argparse
import platform
from os.path import isfile

from camilladsp import CamillaClient, ProcessingState, StopReason, CamillaError

from datastructures import DeviceEvent

if platform.system() == "Linux":
    from alsa_listener import AlsaControlListener
if platform.system() == "Darwin":
    from ca_listener import CAListener

RUNNING_STATES = (
    ProcessingState.RUNNING,
    ProcessingState.PAUSED,
    ProcessingState.STALLED,
    ProcessingState.STARTING,
)


class CamillaController:

    def __init__(self, host, port, config_providers, listener):
        self.listener = listener
        self.host = host
        self.port = port
        self.config_providers = config_providers
        self.events = []
        self.cdsp = CamillaClient(self.host, self.port)
        self.cdsp.connect()
        if self.listener is not None:
            self.listener.set_on_change(self.queue_event)
            self.listener.run()
        self.expected_running = None
        self.error_on_start = False
        self.get_config_for_new_wave_format()

    def queue_event(self, params):
        self.events.append(params)

    def debounce_event_queue(self):
        # If the queue contains a stop event, remove any start and stop events before this
        events_to_remove = []
        stop_found = False
        # Iterate through the list from the end
        for n, event in enumerate(reversed(self.events)):
            if not stop_found and event == DeviceEvent.STOPPED:
                # This is the first stop event we encounter
                stop_found = True
            elif stop_found and event in (DeviceEvent.STARTED, DeviceEvent.STOPPED):
                # This start or stop event is followed by a stop, mark it for removal
                events_to_remove.append(n)
        orig_len = len(self.events)
        for rev_idx in events_to_remove:
            idx = orig_len - rev_idx - 1
            # the indexes are sorted in decreasing order, safe to just pop
            self.events.pop(idx)

    def main_loop(self):
        while True:
            time.sleep(0.2)

            # Handle any change events from the device
            if len(self.events) > 1:
                self.debounce_event_queue()
            while len(self.events) > 0:
                event = self.events.pop(0)
                # handle each event
                print(event)
                if event == DeviceEvent.STARTED:
                    wave_format = event.data
                    # re-read wave format here!
                    if self.listener is not None:
                        wave_format = listener.read_wave_format()
                    print("Device started with wave format", wave_format)
                    self.get_config_for_new_wave_format(
                        sample_rate=wave_format.sample_rate,
                        sample_format=wave_format.sample_format,
                        channels=wave_format.channels,
                    )
                    self.stop_cdsp()
                    self.start_cdsp()
                elif event == DeviceEvent.STOPPED:
                    print("Device stopped")
                    self.stop_cdsp()

            # Query CamillaDSP for status
            state = self.cdsp.general.state()
            if state == ProcessingState.INACTIVE:
                # print("CamillaDSP is inactive")
                stop_reason = self.cdsp.general.stop_reason()
                if stop_reason == StopReason.CAPTUREFORMATCHANGE:
                    if not self.error_on_start:
                        print("CamillaDSP stopped because the capture format changed")
                        new_rate = stop_reason.data
                        # re-read wave format here!
                        if self.listener is not None:
                            wave_format = listener.read_wave_format()
                            print("Updated", wave_format)
                            if wave_format.sample_rate is not None:
                                new_rate = wave_format.sample_rate
                        if new_rate > 0:
                            self.get_config_for_new_wave_format(sample_rate=new_rate)
                            self.stop_cdsp()
                            self.start_cdsp()
                        else:
                            print(
                                "Sample rate changed, new value is unknown. Unable to get get a new config"
                            )
                elif stop_reason == StopReason.DONE:
                    print("Capture is done, no action")
                elif stop_reason == StopReason.NONE:
                    # print("Initial start")
                    if not self.error_on_start:
                        self.start_cdsp()
                elif stop_reason in (
                    StopReason.CAPTUREERROR,
                    StopReason.PLAYBACKERROR,
                ):
                    if not self.error_on_start:
                        print("Stopped due to error, trying to restart", stop_reason)
                        self.start_cdsp()
                elif stop_reason == StopReason.PLAYBACKFORMATCHANGE:
                    print("Playback format changed, ")

    def run(self):
        try:
            self.main_loop()
        except KeyboardInterrupt:
            print("Shutting down...")

    def stop_cdsp(self):
        print("Stopping CamillaDSP")
        self.cdsp.general.stop()
        self.expected_running = False
        self.error_on_start = False

    def start_cdsp(self):
        if self.config is not None:
            print("Starting CamillaDSP with new config")
            try:
                self.cdsp.config.set_active(self.config)
                self.expected_running = True
                self.error_on_start = False
                print("Started")
            except CamillaError as e:
                print("Unable to start, error:", e)
                self.expected_running = True
                self.error_on_start = True
        else:
            print("No config available, ignoring start request")

        # else:
        #    print("No new config is available, not starting")

    def get_config_for_new_wave_format(
        self, sample_rate=None, sample_format=None, channels=None
    ):
        print(
            f"Getting new config for rate: {sample_rate}, format: {sample_format}, channels: {channels}"
        )
        for provider in self.config_providers:
            try:
                provider.change_wave_format(
                    sample_rate=sample_rate,
                    sample_format=sample_format,
                    channels=channels,
                )
                self.config = provider.get_config()
                if self.config is not None:
                    print(f"Using new config from {provider.name} provider")
                    return
            except Exception as e:
                print(
                    f"Provider {provider.name} is unable to supply a new config for this wave format"
                )
        print(
            f"No config available for rate: {sample_rate}, format: {sample_format}, channels: {channels}"
        )
        self.config = None


class CamillaConfig:
    """
    Base class for a config provider.
    """

    name = "base class for config provider"

    def __init__(self):
        self.config = None

    def get_config(self):
        """
        Return the config for the current set of wave format parameters.
        Returns None if no config can be provided.
        """
        return self.config

    def read_config(self, filename):
        """
        Helper method to read and parse a yaml file
        """
        with open(filename) as f:
            config = yaml.safe_load(f)
            return config

    def change_wave_format(self, sample_rate=None, sample_format=None, channels=None):
        """
        Update the values for sample rate, sample format and number of channels.
        Only the values that are not None will be updated.
        This method should be overriden in the child class.
        """
        pass

    def check_if_exists(self, filepath):
        """
        Helper method to check it a file exists.
        """
        return isfile(filepath)



class AdaptConfig(CamillaConfig):
    """
    Modify a single config file for different wave formats.
    If the config has resampling, change only 'capture_samplerate', and disable resamplng if it's not needed.
    If no resampler, change 'samplerate'.
    """

    name = "Adapt"

    def __init__(self, config_path):
        self.base_config = self.read_config(config_path)
        self.config = self.base_config

    def _change_sample_rate(self, config, rate):
        if config["devices"].get("resampler") is None:
            print(f"No resampler defined, change 'samplerate' to {rate}")
            config["devices"]["samplerate"] = rate
            return

        resampler_type = config["devices"]["resampler"]["type"]
        config["devices"]["capture_samplerate"] = rate
        print(f"Config has a resampler, change 'capture_samplerate' to {rate}")

        if (
            config["devices"]["capture_samplerate"] == config["devices"]["samplerate"]
            and resampler_type == "Synchronous"
        ):
            print("No need for a 1:1 sync resampler, removing")
            config["devices"]["resampler"] = None

    def _change_sample_format(self, config, fmt):
        if config["devices"]["capture"].get("format") is not None:
            print(f"Change capture sample format to {fmt}")
            config["devices"]["capture"]["format"] = fmt
        else:
            print(f"Capture sample format is automatic, no need to change")

    def _change_channels(self, config, channels):
        raise NotImplementedError("Changing channels is not implemented")

    def change_wave_format(self, sample_rate=None, sample_format=None, channels=None):
        # adjust base_config and store as self.config
        config = deepcopy(self.base_config)
        # handle rate
        if sample_rate is not None:
            self._change_sample_rate(config, sample_rate)
        if sample_format is not None:
            self._change_sample_format(config, sample_format)
        if channels is not None:
            self._change_channels(config, channels)
        self.config = config



class SpecificConfigs(CamillaConfig):
    """
    Load separate config files for different rates.
    The file path is generated by subsituting the tokens
    {sampleformat}, {channels} and {samplerate} with their current values.
    Example: test_{sampleformat}_{channels}_{samplerate}.yml => test_S16LE_2_44100.yml
    """
    name = "Specific"

    def __init__(self, config_path, initial_rate, initial_format, initial_channels):
        self.config_path = config_path
        self.rate = initial_rate
        self.format = initial_format
        self.channels = initial_channels
        missing = []
        if "{samplerate}" in config_path and initial_rate is None:
            missing.append("sample rate")
        if "{sampleformat}" in config_path and initial_format is None:
            missing.append("sample format")
        if "{channels}" in config_path and initial_channels is None:
            missing.append("channels")
        if len(missing) > 0:
            raise ValueError(f"Missing initial values for {', '.join(missing)}")
        try:
            self.config = self.read_config(self._filename())
        except FileNotFoundError:
            self.config = None

    def _filename(self):
        name = self.config_path
        if self.rate is not None:
            name = name.replace("{samplerate}", str(self.rate))
        if self.channels is not None:
            name = name.replace("{channels}", str(self.channels))
        if self.format is not None:
            name = name.replace("{sampleformat}", self.format)
        return name

    def change_wave_format(self, sample_rate=None, sample_format=None, channels=None):
        if sample_rate is not None:
            self.rate = sample_rate
        if sample_format is not None:
            self.format = sample_format
        if channels is not None:
            self.channels = channels
        print("New config path:", self._filename())
        self.config = self.read_config(self._filename())


def parse_args():
    parser = argparse.ArgumentParser(description="CamillaDSP controller")
    if platform.system() in ("Linux", "Darwin"):
        parser.add_argument("-d", "--device", help="Name of capture device to monitor")
    parser.add_argument(
        "-s",
        "--specific",
        help="Template for paths to config files for specific wave formats",
    )
    parser.add_argument(
        "-a",
        "--adapt",
        help="Path to a config file that can be adapted to new sample rates",
    )
    # Example: add an argument for a custom config provider
    # parser.add_argument(
    #     "-c",
    #     "--custom",
    #     help="Argument for a custom config provider",
    # )
    parser.add_argument(
        "-p", "--port", help="CamillaDSP websocket port", type=int, required=True
    )
    parser.add_argument("--host", help="CamillaDSP websocket host", default="localhost")
    parser.add_argument("-f", "--format", help="Initial value for sample format")
    parser.add_argument("-c", "--channels", help="Initial value for number of channels")
    parser.add_argument("-r", "--rate", help="Initial value for sample rate")

    args = parser.parse_args()

    if args.specific is None and args.adapt is None:
        parser.error("At least one of '--specific' and '--adapt' must be provided")

    return parser, args


def get_listener(args):
    if platform.system() == "Linux" and args.device is not None:
        listener = AlsaControlListener(args.device)
    elif platform.system() == "Darwin" and args.device is not None:
        listener = CAListener(args.device)
    else:
        listener = None

    return listener


def get_config_providers(parser, args, wave_format=None):
    configs = []
    sample_rate = args.rate
    sample_format = args.format
    channels = args.channels
    if wave_format is not None:
        if wave_format.sample_rate is not None:
            sample_rate = wave_format.sample_rate
        if wave_format.sample_format is not None:
            sample_format = wave_format.sample_format
        if wave_format.channels is not None:
            channels = wave_format.channels
    # Example: instantiate a custom config provider
    # if args.custom is not None:
    #     try:
    #         config = CustomConfigs(
    #             args.custom, sample_rate, sample_format, channels
    #         )
    #         configs.append(config)
    #     except Exception as e:
    #         parser.error(str(e))
    if args.specific is not None:
        try:
            config = SpecificConfigs(
                args.specific, sample_rate, sample_format, channels
            )
            configs.append(config)
        except Exception as e:
            parser.error(str(e))
    if args.adapt is not None:
        try:
            config = AdaptConfig(args.adapt)
            configs.append(config)
        except Exception as e:
            parser.error(str(e))
    return configs


if __name__ == "__main__":
    parser, args = parse_args()

    listener = get_listener(args)

    if listener is not None:
        # Try to get the current wave format
        wave_format = listener.read_wave_format()
        print(wave_format)
    else:
        wave_format = None

    configs = get_config_providers(parser, args, wave_format=wave_format)

    controller = CamillaController(args.host, args.port, configs, listener)
    controller.run()
