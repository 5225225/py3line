#!/usr/bin/env python3

import sys
import time
import json
import subprocess
import socket
import re

import requests


class block_time():
    def __init__(self):
        self.timeformat = "%H:%M"
        self.cachetime = 0

    def update(self):
        return json.dumps({
            "full_text": time.strftime(self.timeformat)
        })


class block_reddit():
    def __init__(self):
        self.redditurl = "http://www.reddit.com/user/5225225/about.json"
        self.cachetime = 60
        self.formatstring = "L:{link_karma} C:{comment_karma}"

    def update(self):
        response = requests.get(self.redditurl)
        userdata = json.loads(response.text)["data"]
        return json.dumps({
            "full_text": self.formatstring.format_map(userdata)
        })


class block_text():
    def __init__(self, text="Hello world!"):
        self.text = text
        self.cachetime = 0

    def update(self):
        return json.dumps({
            "full_text": self.text
        })


class block_load():
    def __init__(self):
        self.loadfilename = "/proc/loadavg"
        self.cachetime = 0

        self.warnload = 2
        self.warncolour = "#FFFF00"

        self.critload = 4
        self.critcolour = "FF0000"

    def update(self):
        loadfile = open(self.loadfilename)
        load = loadfile.read()
        loadfile.close()

        loadlist = load.split(" ")

        colourout = False
        if float(loadlist[0]) > self.warnload:
            colour = self.warncolour
            colourout = True

        if float(loadlist[0]) > self.critload:
            colour = self.critcolour
            colourout = True

        if colourout:
            return json.dumps({
                "full_text": str(loadlist[0]),
                "color": colour
            })

        else:
            return json.dumps({
                "full_text": str(loadlist[0])
            })


class block_mpd():
    def __init__(self, hostname="localhost", port=6600):
        self.hostname = hostname
        self.port = port
        self.cachetime = 0

    def update(self):
        self.mpdsoc = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.mpdsoc.connect((self.hostname, self.port))
        except ConnectionRefusedError:
            return json.dumps({"full_text": ""})
        okay = self.mpdsoc.recv(2**12)
        assert okay == b"OK MPD 0.16.0\n"

        self.mpdsoc.send("currentsong\n".encode("UTF-8"))
        out = self.mpdsoc.recv(2**12).decode("UTF-8")
        data = {}

        for item in out.split("\n"):
            if ":" in item:
                itemkey, sep, itemvalue = item.partition(":")
                data[itemkey.lower()] = itemvalue.strip()

        if data == {}:
            #MPD isn't playing anything, but it's running. Return nothing.
            return json.dumps({"full_text": ""})

        elif data["file"].startswith("http://"):
            #  Playing a radio station
            name = data["name"]
            try:
                if " " in data["title"]:
                    title = data["title"]
                else:
                    title = re.sub("_{1,}", " ", data["title"])
            except KeyError:
                # MPD hasn't set a title for the station
                # Just use the name
                # It's not good looking, but it doesn't crash.
                title = data["name"]
            # Workaround for the spaces in Radio Reddit's stream being
            #   replaced by underscores.
            return json.dumps({
                "full_text": "{}: {}".format(name, title)
            })

        else:
            #  Playing a local file
            artist = data["artist"]
            title = data["title"]
            return json.dumps({
                "full_text": "{} - {}".format(artist, title)
            })

blocks = [block_mpd(), block_load(), block_time()]

for item in blocks:
    item.ct = 0

headerstring = """{"version":1, "click_events": true}
[
"""

sys.stdout.write(headerstring)

while True:
    outstr = "["
    for item in blocks:
        if item.ct == 0:
            stime = time.time()
            item.cachestr = item.update()
            sys.stderr.write("\t{}: {} SEC\n".format(
                str(item),
                time.time() - stime))
            item.ct = item.cachetime
        else:
            item.ct -= 1
        outstr = outstr + item.cachestr + ","
    outstr = outstr[:-1]
    outstr = outstr + "],"
    sys.stdout.write(outstr + "\n")
    sys.stdout.flush()
    sys.stderr.flush()
    time.sleep(1)
