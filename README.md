# camilladsp-controller

A controller for CamillaDSP that automatically changes the configuration
when the audio source's sample rate or format changes.
It works by listening for changes on an audio device
and then applying a new configuration to CamillaDSP.

It can provide new configurations in two ways:
by loading a specific config file for the new format,
or by adapting a general-purpose config.

## Configuration providers
The controller gets the CamillaDSP configurations from config providers.
One or more providers can be enabled at the same time.
The controller will try them in order, and the first one that can provide
a config for the new audio format will be used.

### "Specific" provider
The "Specific" provider loads a completely new configuration file when the audio format changes.
It's enabled with the `-s` or `--specific` command line parameter.
This parameter takes a path to a configuration file,
which can contain placeholders for the audio format parameters.

The available placeholders are:
- `{samplerate}`
- `{sampleformat}`
- `{channels}`

#### Example
Given the path `/path/to/configs/conf_{samplerate}_{channels}.yml`.
When the audio device changes to a 44100 Hz, 2-channel stream, the controller
will look for a file named `/path/to/configs/conf_44100_2.yml`.

### "Adapt" provider
The "Adapt" provider modifies a single base configuration file
to match the new audio format.
It's enabled with the `-a` or `--adapt` parameter,
which should point to the base configuration file.

This provider works in two ways depending on whether
the configuration file uses a resampler:
- **With a resampler**: It sets the `capture_samplerate` parameter
to match the new sample rate.
If the new sample rate is the same as the main `samplerate`,
the resampler is disabled (if it's a synchronous resampler).
- **Without a resampler**: It changes the main `samplerate` parameter
to the new sample rate.

It can also adapt the sample format if the `capture` device in the config
has a `format` parameter.

## Device Listeners
The controller can monitor changes on audio devices
to automatically trigger a configuration change.

### ALSA (Linux)
On Linux, the controller can monitor an ALSA device for sample rate and format changes.
This is useful for capturing audio from a loopback device where
other applications can play audio with varying sample rates.

#### Example
Start the controller to monitor the ALSA device `hw:Loopback,0`:
```sh
python controller.py -p 1234 -s "/home/user/camilladsp/configs/config_{samplerate}.yml" -r 44100 -d hw:Loopback,0
```
When a new stream starts playing to the loopback device,
the controller will detect the sample rate and load the corresponding config.

Example configs:

`config_44100.yml`
```yaml
devices:
  samplerate: 44100
  chunksize: 1024
  enable_rate_adjust: true
  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,0"
    format: S32LE
  playback:
    type: Alsa
    channels: 2
    device: "hw:MyDac"
```

`config_48000.yml`
```yaml
devices:
  samplerate: 48000
  chunksize: 1024
  enable_rate_adjust: true
  capture:
    type: Alsa
    channels: 2
    device: "hw:Loopback,0"
    format: S32LE
  playback:
    type: Alsa
    channels: 2
    device: "hw:MyDac"
```

### CoreAudio (macOS)
On macOS, the controller can monitor the sample rate of a CoreAudio device.

#### Example
Start the controller to monitor the device "BlackHole 2ch":
```sh
python controller.py -p 1234 -s "/path/to/config_{samplerate}.yml" -a "/path/to/config_with_resampler.yml" -d "BlackHole 2ch"
```
Here both the "Specific" and "Adapt" config providers are enabled.
The "Specific" one is tried first.
It will load a config for the new sample rate if a file for it exists.
If not, the "Adapt" provider will be used,
which will modify the `config_with_resampler.yml` file.

Example configs:

`config_44100.yml`
```yaml
devices:
  samplerate: 44100
  chunksize: 1024
  capture:
    type: CoreAudio
    channels: 2
    device: "BlackHole 2ch"
  playback:
    type: CoreAudio
    channels: 2
    device: "MyDAC"
```

`config_96000.yml`
```yaml
devices:
  samplerate: 96000
  chunksize: 1024
  capture:
    type: CoreAudio
    channels: 2
    device: "BlackHole 2ch"
  playback:
    type: CoreAudio
    channels: 2
    device: "MyDAC"
```

`config_with_resampler.yml`
```yaml
devices:
  samplerate: 96000
  capture_samplerate: 44100
  chunksize: 1024
  resampler:
    type: Synchronous
  capture:
    type: CoreAudio
    channels: 2
    device: "BlackHole 2ch"
  playback:
    type: CoreAudio
    channels: 2
    device: "MyDAC"
```
When a 44.1kHz stream is played, `capture_samplerate` is set to 44100.
When a 96kHz stream is played, `capture_samplerate` is set to 96000,
and the resampler is removed since it's not needed.
