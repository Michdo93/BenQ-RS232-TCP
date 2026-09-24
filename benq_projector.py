#!/usr/bin/env python3
"""
Control a BenQ projector (e.g., MH856UST) via LAN – RS232 protocol on TCP port 8000.

As CLI:
  python3 benq_projector.py pow=?                 # Single command -> "OFF"
  python3 benq_projector.py pow=on sour=hdmi      # Multiple sequential commands
  python3 benq_projector.py --status              # Status as JSON
  python3 benq_projector.py --raw pow=?           # Complete raw response
  python3 benq_projector.py                       # Interactive mode
  python3 benq_projector.py --host 192.168.0.59 --port 8000 pow=?

As a Library:
  from benq_projector import BenQProjector
  p = BenQProjector("192.168.0.59")
  p.power_on(); print(p.get("sour"))

Exit codes: 0 = OK, 1 = Projector reported error (Block/Illegal/Unsupported), 2 = Connection error
"""
import argparse
import json
import re
import socket
import sys
import time
from typing import Dict, List, Optional

DEFAULT_HOST = "192.168.0.59"
DEFAULT_PORT = 8000

TOKEN_RE = re.compile(r"\*([^#\r\n]*)#")
ERRORS = ("illegal format", "unsupported item", "block item")

STATUS_KEYS = ["pow", "sour", "mute", "vol", "appmod", "lampm", "ltim",
               "blank", "freeze", "asp", "ct", "bri", "con", "modelname"]


class BenQError(Exception):
    """Projector responded with Illegal format / Unsupported item / Block item."""


class BenQProjector:
    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 timeout: float = 3.0, pause: float = 0.2):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.pause = pause  # Delay between commands; the projector is not the fastest

    # ---------------------------------------------------------------- Low-Level
    @staticmethod
    def _normalize(cmd: str) -> str:
        return cmd.strip().lstrip("*").rstrip("#").strip()

    def send_raw(self, cmd: str) -> str:
        """Sends a command and returns the complete raw response (without CR/LF)."""
        cmd = self._normalize(cmd)
        sent_token = cmd.lower()
        buf = b""
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.sendall("\r*{}#\r".format(cmd).encode("ascii"))
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(1024)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                text = buf.decode("ascii", errors="replace")
                tokens = TOKEN_RE.findall(text)
                # Done as soon as a response is present that is not just the echo
                if len(tokens) >= 2 or any(t.lower() != sent_token for t in tokens):
                    break
        time.sleep(self.pause)
        return buf.decode("ascii", errors="replace").replace("\r", " ").replace("\n", " ").strip()

    def send(self, cmd: str) -> str:
        """Sends a command and returns only the response value (e.g., 'ON')."""
        cmd = self._normalize(cmd)
        raw = self.send_raw(cmd)
        tokens = TOKEN_RE.findall(raw)
        # Remove the first token if it is only the echo of the command
        if len(tokens) >= 2 and tokens[0].lower() == cmd.lower():
            tokens = tokens[1:]
        for token in tokens:
            if token.strip().lower() in ERRORS:
                raise BenQError("{} -> {}".format(cmd, token.strip()))
        if not tokens:
            if not raw:
                raise ConnectionError("No response to '{}'".format(cmd))
            return raw
        answer = tokens[-1]
        return answer.split("=", 1)[1].strip() if "=" in answer else answer.strip()

    def get(self, key: str) -> str:
        return self.send("{}=?".format(key))

    def set(self, key: str, value: str) -> str:
        return self.send("{}={}".format(key, value))

    # --------------------------------------------------------------- Convenience
    def power_on(self) -> str:
        return self.set("pow", "on")

    def power_off(self) -> str:
        return self.set("pow", "off")

    def is_on(self) -> bool:
        return self.get("pow").upper() == "ON"

    def source(self, src: Optional[str] = None) -> str:
        """src: hdmi, hdmi2, RGB, RGB2, vid, svid – without argument: current source."""
        return self.set("sour", src) if src else self.get("sour")

    def mute(self, enable: Optional[bool] = None) -> str:
        return self.get("mute") if enable is None else self.set("mute", "on" if enable else "off")

    def volume_up(self) -> str:
        return self.set("vol", "+")

    def volume_down(self) -> str:
        return self.set("vol", "-")

    def blank(self, enable: Optional[bool] = None) -> str:
        return self.get("blank") if enable is None else self.set("blank", "on" if enable else "off")

    def lamp_hours(self) -> int:
        return int(re.sub(r"\D", "", self.get("ltim")) or 0)

    def status(self, keys: Optional[List[str]] = None) -> Dict[str, Optional[str]]:
        result = {}  # type: Dict[str, Optional[str]]
        for key in keys or STATUS_KEYS:
            try:
                result[key] = self.get(key)
            except BenQError as e:
                result[key] = None if "block" in str(e).lower() else "ERROR: {}".format(e)
            except (OSError, ConnectionError) as e:
                result[key] = "ERROR: {}".format(e)
        return result


# ------------------------------------------------------------------------ CLI
def main() -> int:
    arg_parser = argparse.ArgumentParser(description="Control a BenQ projector via LAN (TCP 8000)")
    arg_parser.add_argument("--host", default=DEFAULT_HOST)
    arg_parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    arg_parser.add_argument("--timeout", type=float, default=3.0)
    arg_parser.add_argument("--raw", action="store_true", help="output complete raw response")
    arg_parser.add_argument("--status", action="store_true", help="output status as JSON")
    arg_parser.add_argument("commands", nargs="*", help="e.g., pow=? pow=on sour=hdmi")
    args = arg_parser.parse_args()

    projector = BenQProjector(args.host, args.port, args.timeout)

    try:
        if args.status:
            print(json.dumps(projector.status(), indent=2, ensure_ascii=False))
            return 0

        if args.commands:
            return_code = 0
            for cmd in args.commands:
                try:
                    print(projector.send_raw(cmd) if args.raw else projector.send(cmd))
                except BenQError as e:
                    print(e, file=sys.stderr)
                    return_code = 1
            return return_code

        print('Enter commands without * and # (e.g., "pow=?"), "status", or "exit".')
        while True:
            try:
                cmd = input(">> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if cmd in ("exit", "quit"):
                return 0
            if not cmd:
                continue
            try:
                if cmd == "status":
                    print(json.dumps(projector.status(), indent=2, ensure_ascii=False))
                else:
                    print(projector.send_raw(cmd) if args.raw else projector.send(cmd))
            except BenQError as e:
                print("Error:", e)
    except (OSError, ConnectionError) as e:
        print("Connection error: {}".format(e), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
