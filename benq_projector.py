#!/usr/bin/env python3
"""
benq_projector.py - Control BenQ projectors (e.g. MH856UST) via LAN using the
RS232 command set over TCP (port 8000), with a built-in MQTT interface.

Usage:
  python3 benq_projector.py run                      # run the MQTT service
  python3 benq_projector.py send pow=? sour=?        # send commands directly (no MQTT)
  python3 benq_projector.py send --raw pow=?         # show every raw reply incl. echo
  python3 benq_projector.py status                   # print projector status as JSON
  python3 benq_projector.py -c /etc/benq-projector/benq_projector.ini run
  python3 benq_projector.py --host 192.168.0.59 send pow=?

Configuration file (first match wins):
  -c/--config  ->  ./benq_projector.ini  ->  /etc/benq-projector/benq_projector.ini

Exit codes (send/status): 0 = OK, 1 = projector error / no reply, 2 = connection error
"""
import argparse
import collections
import configparser
import json
import logging
import os
import queue
import re
import signal
import socket
import sys
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

__version__ = "2.0.0"

LOG = logging.getLogger("benq")

CONFIG_SEARCH_PATHS = ["./benq_projector.ini", "/etc/benq-projector/benq_projector.ini"]

DEFAULT_CONFIG = {
    "projector": {
        "host": "192.168.0.59",
        "port": "8000",
        "timeout": "3.0",           # seconds to wait for a reply
        "command_gap": "0.7",       # minimum pause between two commands
        "query_retries": "1",       # extra attempts for "=?" queries without reply
        "warmup_time": "60",        # seconds after power on in which commands are deferred
        "cooldown_time": "90",      # seconds after power off in which commands are blocked
        "reconnect_delay": "10",    # seconds to wait after a connection error
    },
    "mqtt": {
        "host": "localhost",
        "port": "1883",
        "username": "mqtt",
        "password": "mqtt",
        "client_id": "benq-projector",
        "base_topic": "projector/benq/mh856ust",
        "qos": "1",
        "keepalive": "60",
    },
    "polling": {
        "power_interval": "5",      # seconds between power state queries
        "full_interval": "30",      # seconds between full status polls (only when ON)
    },
    "logging": {
        "level": "INFO",
    },
}

# --------------------------------------------------------------------------- protocol
# ">*pow=?#" is the echo of a command, "*POW=OFF#" is the reply
TOKEN_RE = re.compile(r"(>?)\*([^#*>\r\n]*)#")
PROJECTOR_ERRORS = ("illegal format", "unsupported item", "block item")
SAFE_COMMAND_RE = re.compile(r"^[A-Za-z0-9=?:+.\-_]{1,40}$")
SAFE_VALUE_RE = re.compile(r"^[A-Za-z0-9:+.\-_]{1,20}$")


def normalize_command(cmd: str) -> str:
    return cmd.strip().lstrip(">").lstrip("*").rstrip("#").strip()


class Response:
    OK = "OK"
    ERROR = "ERROR"
    NO_RESPONSE = "NO_RESPONSE"
    CONNECTION_ERROR = "CONNECTION_ERROR"

    def __init__(self, status: str, command: str, value: Optional[str] = None,
                 raw: str = "", message: str = ""):
        self.status = status
        self.command = command
        self.value = value
        self.raw = raw
        self.message = message

    @property
    def ok(self) -> bool:
        return self.status == self.OK

    def as_dict(self) -> Dict[str, Optional[str]]:
        return {"command": self.command, "status": self.status, "value": self.value,
                "raw": self.raw, "message": self.message}

    def __repr__(self) -> str:
        return "Response({})".format(self.as_dict())


class BenQProjector:
    """Serialised access to the projector over one persistent TCP connection.

    Lessons from the real device:
      * commands arriving while the projector is busy are silently dropped
        -> strictly one command at a time with a minimum gap
      * the echo (">*cmd#") is not always sent -> replies are matched by key
      * during standby/warm-up/cool-down some commands get no reply at all
      * error replies ("*Block item#") carry no key -> belong to the pending command
    """

    def __init__(self, host: str, port: int = 8000, timeout: float = 3.0,
                 command_gap: float = 0.7, query_retries: int = 1,
                 on_token: Optional[Callable[[bool, str], None]] = None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.command_gap = command_gap
        self.query_retries = query_retries
        self.on_token = on_token
        self._sock = None  # type: Optional[socket.socket]
        self._buf = ""
        self._lock = threading.RLock()
        self._last_io = 0.0

    # ------------------------------------------------------------- connection
    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        self.close()
        LOG.debug("Connecting to projector %s:%s", self.host, self.port)
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        LOG.info("Connected to projector %s:%s", self.host, self.port)
        self._sock = sock
        self._buf = ""

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buf = ""

    # ---------------------------------------------------------------- reading
    def _read(self, max_wait: float) -> bool:
        self._sock.settimeout(max(0.01, max_wait))
        try:
            chunk = self._sock.recv(1024)
        except socket.timeout:
            return False
        if not chunk:
            raise ConnectionError("connection closed by projector")
        self._buf += chunk.decode("ascii", errors="replace")
        if len(self._buf) > 4096:
            self._buf = self._buf[-1024:]
        return True

    def _tokens(self) -> List[Tuple[bool, str]]:
        tokens = []
        end = 0
        for match in TOKEN_RE.finditer(self._buf):
            tokens.append((match.group(1) == ">", match.group(2).strip()))
            end = match.end()
        rest = self._buf[end:]
        idx = rest.rfind("*")
        if idx > 0 and rest[idx - 1] == ">":
            idx -= 1
        self._buf = rest[idx:] if idx >= 0 else ""
        for is_echo, token in tokens:
            LOG.debug("<- %s*%s#", ">" if is_echo else "", token)
            if self.on_token is not None:
                try:
                    self.on_token(is_echo, token)
                except Exception:  # never let a callback break the protocol handling
                    LOG.exception("on_token callback failed")
        return tokens

    def _drain(self) -> None:
        """Consume anything unsolicited that arrived since the last command."""
        while self._read(0.02):
            pass
        self._tokens()

    # -------------------------------------------------------------- commands
    def execute(self, command: str) -> Response:
        cmd = normalize_command(command)
        if not SAFE_COMMAND_RE.match(cmd):
            return Response(Response.ERROR, cmd, message="invalid command")
        attempts = 1 + (self.query_retries if cmd.endswith("=?") else 0)
        with self._lock:
            resp = None  # type: Optional[Response]
            reconnected = False
            done = 0
            while done < attempts:
                resp = self._execute_once(cmd)
                if resp.status == Response.CONNECTION_ERROR and not reconnected:
                    reconnected = True  # stale socket: one immediate reconnect
                    continue
                if resp.status != Response.NO_RESPONSE:
                    break
                done += 1
            return resp

    def _execute_once(self, cmd: str) -> Response:
        key = cmd.split("=", 1)[0].lower() if "=" in cmd else None
        try:
            if self._sock is None:
                self.connect()
            self._drain()
            wait = self.command_gap - (time.monotonic() - self._last_io)
            if wait > 0:
                time.sleep(wait)
            LOG.debug("-> *%s#", cmd)
            self._sock.sendall("\r*{}#\r".format(cmd).encode("ascii"))
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return Response(Response.NO_RESPONSE, cmd,
                                    message="no reply within {:.1f}s".format(self.timeout))
                if not self._read(min(remaining, 0.5)):
                    continue
                for is_echo, token in self._tokens():
                    # echo: marked with ">" or identical to the (lower case) command
                    if is_echo or (token == cmd and token != token.upper()):
                        continue
                    if token.lower() in PROJECTOR_ERRORS:
                        return Response(Response.ERROR, cmd, raw=token, message=token)
                    tkey, sep, tval = token.partition("=")
                    if key is None or (sep and tkey.strip().lower() == key):
                        return Response(Response.OK, cmd,
                                        value=tval.strip() if sep else token, raw=token)
                    LOG.debug("Ignoring unrelated reply %r while waiting for %r", token, cmd)
        except (OSError, ConnectionError) as exc:
            self.close()
            return Response(Response.CONNECTION_ERROR, cmd, message=str(exc))
        finally:
            self._last_io = time.monotonic()

    def get(self, key: str) -> Response:
        return self.execute("{}=?".format(key))

    def set(self, key: str, value: str) -> Response:
        return self.execute("{}={}".format(key, value))


# ------------------------------------------------------------------ properties
class Prop:
    """One projector property exposed as <base>/<name>/state and <base>/<name>/command."""

    def __init__(self, name: str, key: str, kind: str, readable: bool = True,
                 writable: bool = True, poll_once: bool = False):
        self.name = name
        self.key = key
        self.kind = kind            # switch | enum | step | number | text | menu
        self.readable = readable
        self.writable = writable
        self.poll_once = poll_once


PROPERTIES = [
    Prop("power", "pow", "switch"),
    Prop("source", "sour", "enum"),
    Prop("mute", "mute", "switch"),
    Prop("volume", "vol", "step"),
    Prop("blank", "blank", "switch"),
    Prop("freeze", "freeze", "switch"),
    Prop("picturemode", "appmod", "enum"),
    Prop("lampmode", "lampm", "enum"),
    Prop("aspect", "asp", "enum"),
    Prop("colortemp", "ct", "enum"),
    Prop("brightness", "bri", "step"),
    Prop("contrast", "con", "step"),
    Prop("lamphours", "ltim", "number", writable=False),
    Prop("model", "modelname", "text", writable=False, poll_once=True),
    Prop("menu", "menu", "menu", readable=False),
]
PROP_BY_NAME = {p.name: p for p in PROPERTIES}
PROP_BY_KEY = {p.key: p for p in PROPERTIES}

MENU_COMMANDS = {"ON": "menu=on", "OFF": "menu=off", "UP": "up", "DOWN": "down",
                 "LEFT": "left", "RIGHT": "right", "ENTER": "enter"}


def normalize_state(prop: Prop, value: Optional[str]) -> str:
    value = (value or "").strip()
    if prop.kind in ("step", "number"):
        digits = re.sub(r"[^\d]", "", value)
        return digits or value.upper()
    if prop.kind == "text":
        return value
    return value.upper()


# ----------------------------------------------------------------- MQTT service
class ProjectorService:
    def __init__(self, cfg: configparser.ConfigParser):
        pc, mc, pol = cfg["projector"], cfg["mqtt"], cfg["polling"]
        self.projector = make_projector(cfg, on_token=self._on_token)
        self.warmup_time = pc.getfloat("warmup_time")
        self.cooldown_time = pc.getfloat("cooldown_time")
        self.reconnect_delay = pc.getfloat("reconnect_delay")
        self.power_interval = pol.getfloat("power_interval")
        self.full_interval = pol.getfloat("full_interval")
        self.mqtt_cfg = mc
        self.base = mc.get("base_topic").strip().strip("/")
        self.qos = mc.getint("qos")

        self.commands = queue.Queue()                   # type: queue.Queue
        self.poll_queue = collections.deque()           # type: collections.deque
        self.deferred = []                              # type: List[Tuple[str, str]]
        self.state = {}                                 # type: Dict[str, str]
        self.transition = None                          # type: Optional[str]
        self.transition_until = 0.0
        self.offline_until = 0.0
        self.next_power = 0.0
        self.next_full = 0.0
        self.stop_event = threading.Event()
        self.client = None

    # ------------------------------------------------------------------ MQTT
    def topic(self, sub: str) -> str:
        return "{}/{}".format(self.base, sub)

    def _setup_mqtt(self) -> None:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            LOG.error("paho-mqtt is missing. Install it with: "
                      "sudo apt install python3-paho-mqtt  (or: pip3 install paho-mqtt)")
            sys.exit(3)
        mc = self.mqtt_cfg
        client_id = mc.get("client_id")
        try:  # paho-mqtt >= 2.0
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except AttributeError:  # paho-mqtt 1.x
            client = mqtt.Client(client_id=client_id)
        if mc.get("username"):
            client.username_pw_set(mc.get("username"), mc.get("password") or None)
        client.will_set(self.topic("availability"), "offline", qos=self.qos, retain=True)
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        LOG.info("Connecting to MQTT broker %s:%s", mc.get("host"), mc.getint("port"))
        client.connect_async(mc.get("host"), mc.getint("port"), keepalive=mc.getint("keepalive"))
        client.loop_start()
        self.client = client

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        failed = rc.is_failure if hasattr(rc, "is_failure") else rc != 0
        if failed:
            LOG.error("MQTT connection refused: %s", rc)
            return
        LOG.info("Connected to MQTT broker, base topic '%s'", self.base)
        client.subscribe(self.topic("+/command"), qos=self.qos)
        client.publish(self.topic("availability"), "online", qos=self.qos, retain=True)
        for name, value in self.state.items():
            client.publish(self.topic(name + "/state"), value, qos=self.qos, retain=True)

    def _on_disconnect(self, client, userdata, *args) -> None:
        LOG.warning("Disconnected from MQTT broker (%s), reconnecting ...",
                    args[-2] if len(args) >= 2 else args[0] if args else "?")

    def _on_message(self, client, userdata, msg) -> None:
        if msg.retain:
            LOG.warning("Ignoring retained command on %s (clear it with an empty retained message)",
                        msg.topic)
            return
        parts = msg.topic[len(self.base) + 1:].split("/")
        if len(parts) != 2 or parts[1] != "command":
            return
        payload = msg.payload.decode("utf-8", errors="replace").strip()
        if payload:
            self.commands.put((parts[0], payload))

    def _publish(self, sub: str, payload: str, retain: bool = True) -> None:
        if self.client is not None:
            self.client.publish(self.topic(sub), payload, qos=self.qos, retain=retain)

    def _on_token(self, is_echo: bool, token: str) -> None:
        self._publish("raw/response", "{}*{}#".format(">" if is_echo else "", token), retain=False)

    def _publish_error(self, data: Dict[str, Optional[str]]) -> None:
        self._publish("error", json.dumps(data), retain=False)

    # ----------------------------------------------------------------- state
    def _set_value(self, name: str, value: str) -> None:
        if not value or self.state.get(name) == value:
            return
        LOG.info("%s: %s -> %s", name, self.state.get(name), value)
        self.state[name] = value
        self._publish(name + "/state", value)
        self._publish("state", json.dumps(self.state, sort_keys=True))

    def _set_prop(self, prop: Prop, raw_value: str) -> None:
        if raw_value in ("+", "-", "?", ""):
            return
        self._set_value(prop.name, normalize_state(prop, raw_value))

    def _start_transition(self, kind: str) -> None:
        duration = self.warmup_time if kind == "warmup" else self.cooldown_time
        self.transition = kind
        self.transition_until = time.monotonic() + duration
        self.poll_queue.clear()
        self._set_value("phase", "WARMUP" if kind == "warmup" else "COOLDOWN")
        LOG.info("Projector %s for %.0f s", "warming up" if kind == "warmup" else "cooling down",
                 duration)

    def _check_transition(self) -> None:
        if self.transition is None or time.monotonic() < self.transition_until:
            return
        kind = self.transition
        self.transition = None
        self._set_value("phase", "STABLE")
        self.next_power = 0.0
        if kind == "warmup":
            self._schedule_full_poll()
        if self.deferred:
            LOG.info("Executing %d deferred command(s)", len(self.deferred))
            for item in self.deferred:
                self.commands.put(item)
            self.deferred = []

    # --------------------------------------------------------------- execute
    def _exec(self, cmd: str, report: bool = True) -> Response:
        resp = self.projector.execute(cmd)
        if resp.status == Response.CONNECTION_ERROR:
            if self.state.get("connection") != "OFFLINE":
                LOG.warning("Projector not reachable: %s", resp.message)
            self._set_value("connection", "OFFLINE")
            self.offline_until = time.monotonic() + self.reconnect_delay
            if report:
                self._publish_error(resp.as_dict())
            return resp
        self._set_value("connection", "ONLINE")
        if resp.ok:
            prop = PROP_BY_KEY.get(cmd.split("=", 1)[0].lower())
            if prop is not None and prop.readable:
                self._set_prop(prop, resp.value)
        elif report:
            LOG.warning("%s -> %s %s", cmd, resp.status, resp.message)
            self._publish_error(resp.as_dict())
        else:
            LOG.debug("%s -> %s %s", cmd, resp.status, resp.message)
        return resp

    def _power(self, target: str, cmd: str) -> None:
        """target: 'ON' or 'OFF'; handles the missing replies seen during standby."""
        old = self.state.get("power")
        resp = self._exec(cmd)
        if resp.status == Response.NO_RESPONSE:
            LOG.info("No reply to %s, assuming power %s (verified by polling)", cmd, target)
            self._set_value("power", target)
        elif not resp.ok:
            return
        new = self.state.get("power")
        if new == "ON" and old != "ON":
            self._start_transition("warmup")
        elif new == "OFF" and old != "OFF":
            self._start_transition("cooldown")

    def _step_to(self, prop: Prop, target: int) -> None:
        for _ in range(40):
            resp = self._exec(prop.key + "=?", report=False)
            if not resp.ok:
                break
            try:
                current = int(normalize_state(prop, resp.value))
            except ValueError:
                break
            if current == target:
                return
            if not self._exec("{}={}".format(prop.key, "+" if target > current else "-")).ok:
                break
        LOG.warning("Could not set %s to %d", prop.name, target)

    def _apply(self, prop: Prop, payload: str) -> None:
        up = payload.strip().upper()
        if prop.kind == "switch":
            if up == "TOGGLE":
                up = "OFF" if self.state.get(prop.name) == "ON" else "ON"
            if up in ("ON", "TRUE", "1"):
                value = "on"
            elif up in ("OFF", "FALSE", "0"):
                value = "off"
            else:
                raise ValueError("expected ON, OFF or TOGGLE")
            cmd = "{}={}".format(prop.key, value)
            if prop.name == "power":
                self._power(value.upper(), cmd)
            else:
                self._exec(cmd)
        elif prop.kind == "enum":
            if not SAFE_VALUE_RE.match(payload.strip()):
                raise ValueError("invalid value")
            self._exec("{}={}".format(prop.key, payload.strip().lower()))
        elif prop.kind == "step":
            if up in ("UP", "+", "INCREASE"):
                self._exec(prop.key + "=+")
                self._exec(prop.key + "=?", report=False)
            elif up in ("DOWN", "-", "DECREASE"):
                self._exec(prop.key + "=-")
                self._exec(prop.key + "=?", report=False)
            elif re.fullmatch(r"\d{1,3}(\.\d+)?", up):  # openHAB may send "8.0"
                self._step_to(prop, int(round(float(up))))
            else:
                raise ValueError("expected UP, DOWN or a number")
        elif prop.kind == "menu":
            if up not in MENU_COMMANDS:
                raise ValueError("expected one of " + ", ".join(MENU_COMMANDS))
            self._exec(MENU_COMMANDS[up])
        else:
            raise ValueError("property is read-only")

    def _handle_raw(self, payload: str) -> None:
        cmd = normalize_command(payload)
        lower = cmd.lower()
        if lower in ("pow=on", "pow=off"):
            self._power(lower[4:].upper(), lower)
            return
        resp = self._exec(cmd)
        if resp.status in (Response.NO_RESPONSE, Response.CONNECTION_ERROR):
            self._publish("raw/response", resp.status, retain=False)

    def _handle_command(self, name: str, payload: str) -> None:
        LOG.info("Command %s <- %r", name, payload)
        if name == "refresh":
            self.next_power = 0.0
            self._schedule_full_poll(force=True)
            return
        prop = PROP_BY_NAME.get(name)
        if name != "raw" and (prop is None or not prop.writable):
            self._publish_error({"command": name, "status": "INVALID",
                                 "message": "unknown or read-only property"})
            return
        is_power = name == "power" or (name == "raw" and payload.lower().startswith("pow="))
        if self.transition == "warmup" or (self.transition == "cooldown" and is_power):
            LOG.info("Projector in %s, deferring %s=%s", self.transition, name, payload)
            self.deferred.append((name, payload))
            return
        if self.transition == "cooldown":
            LOG.warning("Projector cooling down, dropping %s=%s", name, payload)
            self._publish_error({"command": name, "status": "BLOCKED",
                                 "message": "projector is cooling down"})
            return
        try:
            if name == "raw":
                self._handle_raw(payload)
            else:
                self._apply(prop, payload)
        except ValueError as exc:
            self._publish_error({"command": name, "status": "INVALID", "message": str(exc)})

    # --------------------------------------------------------------- polling
    def _schedule_full_poll(self, force: bool = False) -> None:
        for prop in PROPERTIES:
            if not prop.readable or prop.name == "power" or prop.name in self.poll_queue:
                continue
            if prop.poll_once and prop.name in self.state and not force:
                continue
            self.poll_queue.append(prop.name)

    def _poll_power(self) -> None:
        old = self.state.get("power")
        if self._exec("pow=?", report=False).ok:
            new = self.state.get("power")
            if new == "ON" and old != "ON" and self.transition is None:
                self._schedule_full_poll()  # switched on externally (remote control)

    def _poll_next(self) -> None:
        if self.state.get("power") != "ON" or self.transition is not None:
            self.poll_queue.clear()
            return
        prop = PROP_BY_NAME[self.poll_queue.popleft()]
        self._exec(prop.key + "=?", report=False)

    # ------------------------------------------------------------------ loop
    def run(self) -> int:
        self._setup_mqtt()
        self._set_value("phase", "STABLE")
        while not self.stop_event.is_set():
            self._check_transition()
            now = time.monotonic()
            try:
                item = self.commands.get_nowait()
            except queue.Empty:
                item = None
            if item is not None:
                self._handle_command(*item)
                continue
            if now < self.offline_until:
                wait = self.offline_until - now
            elif self.transition is not None:
                # no polling during warm-up/cool-down: the projector does not answer anyway
                wait = self.transition_until - now
            elif now >= self.next_power:
                self.next_power = now + self.power_interval
                self._poll_power()
                continue
            else:
                if now >= self.next_full:
                    self.next_full = now + self.full_interval
                    self._schedule_full_poll()
                if self.poll_queue:
                    self._poll_next()
                    continue
                wait = min(self.next_power, self.next_full) - now
            try:
                self._handle_command(*self.commands.get(timeout=max(0.05, min(wait, 1.0))))
            except queue.Empty:
                pass
        self._shutdown()
        return 0

    def _shutdown(self) -> None:
        LOG.info("Shutting down")
        if self.client is not None:
            info = self.client.publish(self.topic("availability"), "offline",
                                       qos=self.qos, retain=True)
            try:
                info.wait_for_publish(timeout=2)
            except TypeError:  # old paho without timeout parameter
                time.sleep(0.5)
            except Exception:
                pass
            self.client.disconnect()
            self.client.loop_stop()
        self.projector.close()


# ------------------------------------------------------------------------ CLI
def load_config(path: Optional[str]) -> Tuple[configparser.ConfigParser, Optional[str]]:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_dict(DEFAULT_CONFIG)
    if path:
        if not os.path.isfile(path):
            sys.exit("Config file not found: {}".format(path))
        cfg.read(path, encoding="utf-8")
        return cfg, path
    for candidate in CONFIG_SEARCH_PATHS:
        if os.path.isfile(candidate):
            cfg.read(candidate, encoding="utf-8")
            return cfg, candidate
    return cfg, None


def make_projector(cfg: configparser.ConfigParser,
                   on_token: Optional[Callable[[bool, str], None]] = None) -> BenQProjector:
    pc = cfg["projector"]
    return BenQProjector(pc.get("host"), pc.getint("port"), pc.getfloat("timeout"),
                         pc.getfloat("command_gap"), pc.getint("query_retries"), on_token)


def cmd_send(cfg: configparser.ConfigParser, commands: List[str], raw: bool) -> int:
    on_token = None
    if raw:
        def on_token(is_echo: bool, token: str) -> None:
            print("{}*{}#".format(">" if is_echo else "", token))
    projector = make_projector(cfg, on_token)
    rc = 0
    try:
        for command in commands:
            resp = projector.execute(command)
            if resp.ok:
                if not raw:
                    print(resp.value)
            else:
                print("{}: {} {}".format(resp.command, resp.status, resp.message).strip(),
                      file=sys.stderr)
                if resp.status == Response.CONNECTION_ERROR:
                    return 2
                rc = 1
    finally:
        projector.close()
    return rc


def cmd_status(cfg: configparser.ConfigParser) -> int:
    projector = make_projector(cfg)
    result = {}  # type: Dict[str, Optional[str]]
    try:
        for prop in PROPERTIES:
            if not prop.readable:
                continue
            resp = projector.execute(prop.key + "=?")
            if resp.status == Response.CONNECTION_ERROR:
                print("Connection error: {}".format(resp.message), file=sys.stderr)
                return 2
            result[prop.name] = normalize_state(prop, resp.value) if resp.ok else None
    finally:
        projector.close()
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Control BenQ projectors via LAN (RS232 over TCP) with MQTT interface")
    parser.add_argument("-c", "--config", help="path to configuration file")
    parser.add_argument("--host", help="projector IP address (overrides config)")
    parser.add_argument("--port", type=int, help="projector TCP port (overrides config)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="mode")
    sub.add_parser("run", help="run the MQTT service")
    send = sub.add_parser("send", help="send commands directly, e.g. pow=? sour=hdmi")
    send.add_argument("--raw", action="store_true", help="print every raw reply incl. echo")
    send.add_argument("commands", nargs="+")
    sub.add_parser("status", help="print the projector status as JSON")
    args = parser.parse_args()

    if not args.mode:
        parser.print_help()
        return 1

    cfg, cfg_path = load_config(args.config)
    if args.host:
        cfg["projector"]["host"] = args.host
    if args.port:
        cfg["projector"]["port"] = str(args.port)

    if args.verbose:
        level = logging.DEBUG
    elif args.mode == "run":
        level = getattr(logging, cfg["logging"].get("level", "INFO").upper(), logging.INFO)
    else:
        level = logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s")
    LOG.info("benq_projector %s, config: %s", __version__, cfg_path or "built-in defaults")

    if args.mode == "send":
        return cmd_send(cfg, args.commands, args.raw)
    if args.mode == "status":
        return cmd_status(cfg)

    service = ProjectorService(cfg)

    def stop(signum, frame):
        LOG.info("Received signal %s", signum)
        service.stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
