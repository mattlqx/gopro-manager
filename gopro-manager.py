#!/usr/bin/env python3

import json
import logging
import os
import re
import subprocess
import sys
import time
from io import BytesIO

import pycurl
import RPi.GPIO as GPIO
from config import *

# Logging fun
root = logging.getLogger()
root.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
root.addHandler(handler)

# Setup GPIO communication
GPIO.setmode(GPIO.BCM)
GPIO.setup(GPIO_BCM_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

# Gotta use pycurl instead of requests to control source interface
def curl(url, iface=None):
    c = pycurl.Curl()
    buffer = BytesIO()
    c.setopt(pycurl.URL, url)
    c.setopt(pycurl.TIMEOUT, 5)
    c.setopt(pycurl.WRITEFUNCTION, buffer.write)
    if iface:
        c.setopt(pycurl.INTERFACE, iface)
    c.perform()

    code = c.getinfo(pycurl.HTTP_CODE)
    try:
        resp = json.loads(buffer.getvalue().decode('UTF-8'))
    except json.decoder.JSONDecodeError:
        resp = None

    buffer.close()
    c.close()
    return (code, resp)


class GoProManager(object):
    def __init__(self):
        self.gopros = []
        self.recording = False

    def add_gopro(self, iface, ssid, wifi_mac, bt_mac):
        gp = GoPro(iface, ssid, wifi_mac, bt_mac)
        self.gopros.append(gp)

    def change_route(self, iface):
        subprocess.call('sudo ip route replace 10.5.5.9 dev {} proto dhcp scope link'.format(iface), shell=True)

    def start_monitor(self):
        i = 0
        while True:
            triggered = not bool(GPIO.input(GPIO_BCM_PIN)) or os.path.exists(TRIGGER_PATH)
            if triggered != self.recording:
                for gp in self.gopros:
                    self.change_route(gp.iface)
                    if self.recording:
                        logging.info("Stopping capture on {}.".format(gp.ssid))
                        gp.stop_capture()
                    else:
                        logging.info("Starting capture on {}.".format(gp.ssid))
                        gp.start_capture()
                self.recording = not self.recording
            time.sleep(1)
            i = i + 1

            # Check to ensure status every so often, doing this won't let cameras sleep
            if CHECK_STATUS is not None and i >= CHECK_STATUS:
                for gp in self.gopros:
                    self.change_route(gp.iface)
                    if gp.is_capturing() != self.recording:
                        logging.warning("{} recording status does not match desired state.".format(gp.ssid))
                        if self.recording:
                            logging.info("Starting capture on {}.".format(gp.ssid))
                            gp.start_capture()
                        else:
                            logging.info("Stopping capture on {}.".format(gp.ssid))
                            gp.stop_capture()
                i = 0


class GoPro(object):
    def __init__(self, iface, ssid, wifi_mac, bt_mac):
        self.iface = iface
        self.ssid = ssid
        self.wifi_mac = wifi_mac
        self.bt_mac = bt_mac

    def gatttool_write(self, command):
        return subprocess.call("sudo gatttool -t random -b {bt} --char-write-req -a 0x33 -n {val}; \
                                sudo gatttool -t random -b {bt} --char-write-req -a 0x2f -n {val}".format(bt=self.bt_mac, val=command), shell=True, timeout=10)

    def power_on(self):
        if self.is_wifi_connected():
            for attempt in range(1, 10):
                try:
                    wake_on_lan = subprocess.call("sudo wakeonlan -p 9 -i 10.5.5.9 {}".format(self.wifi_mac), shell=True)
                    # logging.debug("Wake-on-lan sent to {}".format(self.ssid))
                    r, data = curl("http://10.5.5.9/gp/gpControl/command/system/locate?p=0", iface=self.iface)
                except:
                    r = 0
                if r == 200: return True
                time.sleep(2)
        else:
            logging.info("Wifi is not connected to {}. Sending wifi enable over Bluetooth LE.".format(self.ssid))
            try:
                wifi_on = self.gatttool_write('03170101')
                logging.debug("Wifi enable over Bluetooth LE returned {}".format(wifi_on))
            except TimeoutExpired:
                logging.warn("{} unreachable over Bluetooth LE. Is camera in deep sleep?")
                wifi_on = None

            for attempt in range(1, 10):
                logging.info("Waiting for wifi to associate to {}".format(self.ssid))
                time.sleep(1)
                if self.is_wifi_connected(): break

        wake_on_lan = subprocess.call("sudo wakeonlan -p 9 -i 10.5.5.9 {}".format(self.wifi_mac), shell=True)
        logging.debug("Wake-on-lan sent to {}".format(self.ssid))
        for attempt in range(1, 3):
            logging.debug("Waiting for response from GoPro over HTTP")
            try:
                r, data = curl("http://10.5.5.9/gp/gpControl/command/system/locate?p=0", iface=self.iface)
            except:
                r = 0
            if r == 200: return True
            time.sleep(1)

    def is_wifi_connected(self):
        out = subprocess.check_output('iw dev {} link'.format(self.iface), shell=True).split(b"\n")
        for line in out:
            if re.match('^Connected to {}'.format(self.wifi_mac.lower()).encode(), line) is not None:
                return True
        return False

    def ensure_connection(self):
        for attempt in range(1, 5):
            logging.debug('Attempt number {} to connect to {}'.format(attempt, self.ssid))
            try:
                if self.power_on():
                    return True
            except:
                pass
        return False

    def power_off(self):
        if self.ensure_connection():
            r, data = curl("http://10.5.5.9/gp/gpControl/command/system/sleep", iface=self.iface)
            return r

    def is_capturing(self):
        if self.ensure_connection():
            try:
                r, data = curl("http://10.5.5.9/gp/gpControl/status", iface=self.iface)
            except:
                return None

            # Card error, cycle camera
            if data['status'].get('33') == 3:
                logging.warning("{} has card error condition. Shutting it down.".format(self.ssid))
                self.power_off()
                return False

            # True if recording
            return data['status'].get('8') == 1

    def start_capture(self):
        if self.ensure_connection():
            r, data = curl("http://10.5.5.9/gp/gpControl/command/shutter?p=1", iface=self.iface)
            return r

    def stop_capture(self):
        if self.ensure_connection():
            r, data = curl("http://10.5.5.9/gp/gpControl/command/shutter?p=0", iface=self.iface)
            return r

if __name__ == '__main__':
    gpm = GoProManager()
    for gp in GOPROS:
        gpm.add_gopro(gp[0], gp[1], gp[2], gp[3])
    logging.info("Starting to monitor trigger...")
    gpm.start_monitor()
