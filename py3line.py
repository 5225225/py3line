#!/usr/bin/env python3

import sys
import time
import json
import subprocess
import socket
import re

import requests
import asyncio


from asyncio.tasks import iscoroutine

UPDATE_QUEUE = asyncio.Queue()


class block_base:
    def start(self):
        asyncio.async(self.updater())

    @asyncio.coroutine
    def updater(self):
        while True:
            # if the update method is proven to be asynchronous, e.g. it's
            # running a subprocess, or doing some network activity, then
            # we'll get the result by yielding from it
            future_or_result = self.update()

            if iscoroutine(future_or_result):
                result = yield from future_or_result
            else:
                result = future_or_result
            self.cachestr = result

            # tell the printing coroutine that it's time to update
            yield from UPDATE_QUEUE.put(True)

            # put this coroutine to sleep until it's time to update again
            # sleep for a minimum of one second so we don't update as fast as possible.
            yield from asyncio.sleep(self.cachetime or 1)


class block_time(block_base):
    def __init__(self, formatstr="%H:%M"):
        self.timeformat = formatstr
        self.cachetime = 0

    def update(self):
        return json.dumps({
            "full_text": time.strftime(self.timeformat)
        })


class block_reddit(block_base):
    def __init__(self, username, formatstr="L:{link_karma} C:{comment_karma}"):
        self.redditurl = "www.reddit.com/user/" + username + "/about.json"
        self.cachetime = 60
        self.formatstring = formatstr

    def update(self):
        # FIXME: do this request using the aiohttp library
        response = requests.get(self.redditurl)
        userdata = json.loads(response.text)["data"]
        return json.dumps({
            "full_text": self.formatstring.format_map(userdata)
        })


class block_text(block_base):
    def __init__(self, text="Hello world!"):
        self.text = text
        self.cachetime = 0

    @asyncio.coroutine
    def update(self):
        return json.dumps({
            "full_text": self.text
        })


class block_ip(block_base):
    def __init__(self):
        self.cachetime = 3600

    @asyncio.coroutine
    def update(self):
        # FIXME: do this request using the aiohttp library
        ip = requests.get("http://ifconfig.me/ip").text.strip()
        return json.dumps({
            "full_text": ip
        })


class block_subprocess(block_base):
    def __init__(self, command):
        self.command = command
        self.cachetime = 0

    @asyncio.coroutine
    def update(self):
        loop = asyncio.get_event_loop()

        protocol = asyncio.subprocess.SubprocessStreamProtocol(limit=2**16, loop=loop)
        future = loop.subprocess_shell(lambda: protocol, self.command)
        transport, protocol = yield from future

        # wait for the process to start
        yield from protocol.waiter

        # create a higher level object so we can wait for the process to finish
        process = asyncio.subprocess.Process(transport, protocol, loop)

        # wait until the process finishes
        yield from process.wait()

        # red the output from the process
        output = yield from process.stdout.read()

        return json.dumps({
            "full_text": output.decode("UTF-8").strip()
        })


class block_load(block_base):
    def __init__(self):
        self.loadfilename = "/proc/loadavg"
        self.cachetime = 0

        self.warnload = 2
        self.warncolour = "#FFFF00"

        self.critload = 4
        self.critcolour = "FF0000"

    @asyncio.coroutine
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


class block_mpd(block_base):
    def __init__(self, hostname="localhost", port=6600):
        self.hostname = hostname
        self.port = port
        self.cachetime = 0

    @asyncio.coroutine
    def update(self):
        # FIXME: ideally this would use asynchronous streams. Not required, but
        # would make a lot of sense.
        # See http://docs.python.org/3.4/library/asyncio-stream.html#asyncio.open_connection for details.
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
            yield from json.dumps({"full_text": ""})

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


@asyncio.coroutine
def main():
    blocks = eval(open("blocks").read().strip())

    for item in blocks:
        item.cachestr = json.dumps({"full_text": ""})
        item.start()

    headerstring = """{"version":1}
    [
    """

    sys.stdout.write(headerstring)

    while True:
        starttime = time.time()

        yield from UPDATE_QUEUE.get()

        outstr = "["
        for item in blocks:
            outstr = outstr + item.cachestr + ","
        outstr = outstr[:-1]
        outstr = outstr + "],"
        sys.stdout.write(outstr + "\n")
        sys.stdout.flush()
        sys.stderr.flush()

        try:
            time.sleep(1 - (time.time() - starttime))
        except ValueError:
            pass
            # All of them took more than 1 second combined, forget the pause.


if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
