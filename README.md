# GoPro Manager

A Python script to manage the activation of multiple GoPros in a race car environment. This script was written for and tested against GoPro Hero 7s (silver and black editions). There are numerous caveats, assumptions and prerequisites so please read all of the documentation prior to trying it out.

Huge thanks to the documentation at [KonradIT's Github repo](https://github.com/KonradIT/goprowifihack).

### Prerequisites

- A device running Linux (specifically, I'm using Raspian on a Raspberry Pi)
- A basic understanding of how to SSH in to your device, ensure file permissions, use a text editor and what `sudo` does.
- One wireless interface per camera (I'm using these [USB adapters](https://www.amazon.com/gp/product/B00EQT0YK2))
- Switched, hardwired power to GoPros. (Wake from deep sleep requires going from unplugged to plugged-in)
- 2.4Ghz wireless connections enabled in your GoPros.
- Optionally, connect a GPIO line to your Pi for triggering recording.

I tried to initially get this working by just switching between wireless networks on a single interface. That turned out to be unreliable and pretty slow, so I pivoted to just having a dedicated interface for each camera. Linux is just fine having two separate interfaces on two separate networks having the identical network configuration information (same IP address, subnet, same destination IP address), but requires an explicit route change to point to which interface you want to use to access that destination IP (10.5.5.9, GoPro's well-known static IP address for each camera that can't be changed). This script facilitates that. I'm sure you can get a lot fancier with some bridge interfaces and NAT, but this was the path of least resistance for me.

For GPIO, I've hooked one of my GPIO pins to a RaceCapture GPIO. The RaceCapture is configured as an output and the chosen pin is an open circuit when not enabled/`true`. When the output is enabled/`true`, then the pin is ground. The Raspberry Pi GPIO is configured as an input with a soft pull-up resistor that will trigger high when the RaceCapture side goes to ground. More specifics can be found in the [RaceCapture section](#racecapture) of this document.

### Setup

Overview:

1. Install the required system packages. (`sudo apt-get update; sudo apt-get install python3 python3-rpi.gpio python3-pycurl wakeonlan`)
1. Determine the Wifi and Bluetooth addresses of your GoPros. This is easier said than done. See below.
1. Configure your wifi to connect to your GoPros. Also see below.
1. Clone this checkout somewhere on the device. (e.g. `/usr/local/gopro-manager`)
1. Create a configuration file as `config.py` in that directory. (Copy `config.py.example` for a base)
1. Copy or move the `gopro_manager.service` from the `systemd` directory into `/etc/systemd/system`. If you don't use `/usr/local/gopro-manager` for the checkout location, update that path in the service file. With that in place, you can `sudo systemctl daemon-reload; sudo systemctl enable gopro-manager`.

##### How do I find what the wifi address of my GoPro is?

It's easiest to ensure your wireless connections are active by toggling `Wireless Connections` off and then on via the onscreen menus, then do a scan on your device with `sudo iw wlan0 scan`. You'll get a fair amount of output, look for the SSID if your GoPro and look at the top of the entry for the BSS address. That is your wifi address that you can plug in to the config.

##### How do I configure my device to connect to the GoPros' Wifi?

That's rather up to your OS, but on Raspian, you can edit `/etc/network/interfaces` to configure each interface to a specific SSID. Just add one of these blocks for each with the correct SSID and WPA key.

```
allow-hotplug wlan1
iface wlan1 inet dhcp
	wpa-ssid "My Hero 7"
	wpa-psk "dove1234"
```

The `allow-hotplug` keyword instead of `auto` will allow systemd to continue booting before the interfaces are up. After you've configured that, you can either restart networking with `sudo systemctl restart networking` or just reboot.

##### Bluetooth

The `bluez` Bluetooth user-land tools package that that's available with my version of Raspbian is pretty old and crusty, so upgrade it for better compatibility. First stop the existing bluetooth daemon and ensure prerequisite packages are installed:

```
sudo systemctl stop bluetooth; sudo apt-get update; sudo apt-get install -y build-essential
```

Then follow the instructions on [this page](https://learn.adafruit.com/install-bluez-on-the-raspberry-pi/installation) to build bluez from source. Be sure to use the latest version available and not the version shown in the examples as that too is quite out of date at this time. When running configure, use `./configure --enable-deprecated --enable-experimental` so that all tools will be built (namely gatttool is what we need). After building, use `sudo cp ./attrib/gatttool /usr/local/bin` to copy that out to a location in our PATH.

You'll also need to modify the existing service file for bluetooth at `/lib/systemd/system/bluetooth.service` to use our newly installed version. Use `sudo systemctl edit --full bluetooth.service` and replace the ExecStart line with this line:

```
ExecStart=/usr/local/libexec/bluetooth/bluetoothd --experimental
```

Then reboot. Now we can hopefully reliably pair the cameras. Use `bluetoothctl` to enter an interactive session with bluetooth. Use `power on` to ensure the controller is enabled, then power on and find your camera. See the next section for how to determine which address is your camera. Now, we can try to connect and pair it. On your camera, go to Preferences -> Connections -> Connect to GoPro App and leave it there. Back in bluetoothctl, use `connect <address>` to connect to the camera. Upon success, use `trust` to mark the device as trusted, and `pair` to try and pair the device. Upon success, you can finish up by using `set-alias "My Hero"` to give it a convenient name for identification in bluetoothctl. Finish up by using `disconnect`. You can now back out of all the menus on the GoPro. Repeat the process for all of your cameras. This pairing will allow the Pi to turn on the Wifi on the camera as long as Bluetooth LE is active. See [Things that will ruin your day](#things-that-will-ruin-your-day) below for more info.

##### How do I find what the Bluetooth address of my GoPro is?

Again, toggle `Wireless Connections` off and then on via the onscreen menus. Fire up `bluetoothctl` on your device and use the `scan on` command to start looking for devices. You'll see a lot of devices probably. List discovered devices with `devices` and look at each one individually with `info <id>` like `info C1:58:37:FD:3F:D2`. You'll know a device is a GoPro when the device info output contains the following line:

```
       UUID: GoPro, Inc.               (0000fea6-0000-1000-8000-00805f9b34fb)
```

Use that device address for the bluetooth address in the config. To exit `bluetoothctl`, use `quit`.

### Operation

- You can start the service after you copy the `gopro-manager.service` unit as described above. Run `sudo systemctl start gopro-manager` or just reboot after you've enabled it.
- You can view the logs of the script's output with `journalctl -fu gopro-manager`
- If you want to trigger from something other than the GPIO or just want to test, you can just create the trigger file as configured by `TRIGGER_PATH` (e.g. `touch /tmp/recording`) to start recording and remove the file to stop recording (e.g. `rm -f /tmp/recording`).
- If you want to keep the state of the cameras synchronized with the recording state of the script, specify the number of cycles you want before the synchronize happens with `CHECK_STATUS`. There's a 1 second sleep between cycles so if the script isn't busy trying to communicate with a GoPro, then it should be roughly that long in seconds between synchronization checks. Note, that this requires the cameras be on to get state, so it will keep turning them on if this is enabled and the cameras are configured to auto-shutdown. A bonus of this check is that it will also be able to determine if the SD card is in an "error" state and will power-cycle the camera in that event which can "fix" the issue. Setting `CHECK_STATUS` to `None` will disable all of this behavior.
- I'm setting my cameras to 5 minute auto-shutdown and setting `CHECK_STATUS` to 15. This means if the cameras aren't recording, then they'll turn off and back on every 5 minutes. They will continue to be checked that they're recording when they should be and will be re-started if they stop recording.
- I've really tried to ensure reliable operation, the script will send a wake packet and test with an essentially "no-op" command before doing an actual command.

### RaceCapture

I'm using this in conjunction with my [RaceCapture Pro](https://wiki.autosportlabs.com/RaceCapture-Pro_MK3) (though I have a Mk2). The GPIO is used to control a ground to complete a circuit when logging is active to essentially tell the Raspberry Pi that the car is moving and the cameras should be recording. To do this, setup the GPIO you want to use on the RaceCapture to be an output: in the RaceCapture app, Setup -> Digital In/Out -> GPIO you want to use -> Mode set to Output. Then in the Scripting tab of Setup, in your `onTick()` function, add `setGpio(0, isLogging() == 1)` somewhere. Remember the GPIO number is zero-indexed.

By binding the recording of the cameras to the logging of the RaceCapture, we're letting the RaceCapture worry about how to do the calculation of if we're moving. The RaceCapture provides a Setup section called *Automatic Control* with convenient thresholds to trigger logging and thereby our recording.

Now is a good time to remind you to disable the RaceCapture's built-in wifi client and automatic GoPro triggering if you're using this script to manage them instead.

### Things that will ruin your day

- GoPro's wireless connections behave pretty intentionally shitty. The camera will turn off wifi after 10 minutes of being powered on and not serving requests. Even when plugged in to power continuously, the camera will still shutdown wireless after 10 hours. There's a lot more detail on the trial and error [in this Github thread](https://github.com/KonradIT/goprowifihack/issues/101#issuecomment-493446712). I mentioned *switched* power to the GoPros in the prerequisites for this reason. In my race car, I have a master kill-switch. When I flip it on, power "plugs" into the GoPros waking them from deep sleep so that the wifi can be flipped on via BT LE. If you have a normal car setup, you need to plug your cameras in from an ignition switched source and not a constant source.
- Interacting with Bluetooth LE on the camera is flakey if you're using outdated bluez software. With the version available through my system packages in Raspian (1.43), I got a lot of errors from the networking stack when I try to use bluetooth most of the time. Things like `Function not implemented (38)` and `Device or resource busy (16)`. You need to compile bluez from source to get a more reliable experience when using Bluetooth LE (low-energy). See the [Bluetooth section](#bluetooth) under Setup above.
- Sometimes after powering on if the start recording command is received too quickly while the camera is still reading the SD card, it will turn the card to an error state or just simply not start recording. Having the `CHECK_STATUS` enabled will eventually correct this condition by power cycling the camera and trying again.
- I've had mixed luck with Bluetooth low-energy command write addresses, so the wifi-on command will be tried on both `0x2f` and `0x33` addresses.
