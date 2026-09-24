# BenQ-RS232-TCP
A Python program for controlling BenQ projectors via their LAN interface using RS232 commands (RS232 over TCP).

Many BenQ business and education projectors accept their RS232 command set not only on the D-Sub 9 serial port but also over the network on **TCP port 8000**. This project uses that interface, so no USB-to-serial adapter, null-modem cable or dedicated controller is required. Any machine on the same network can control the projector.

The program can be used as a **command line tool**, in an **interactive shell** or as a **Python library** (e.g. for openHAB, Home Assistant, cron jobs or your own scripts).

---

## Table of Contents

- [Features](#features)
- [Tested Hardware](#tested-hardware)
- [Requirements](#requirements)
- [Installation](#installation)
- [Projector Setup](#projector-setup)
- [Configuration (IP and Port)](#configuration-ip-and-port)
- [Usage](#usage)
  - [Command Line](#command-line)
  - [Interactive Mode](#interactive-mode)
  - [Python Library](#python-library)
- [Command Reference](#command-reference)
- [Responses, Errors and Exit Codes](#responses-errors-and-exit-codes)
- [Manual Testing with netcat](#manual-testing-with-netcat)
- [openHAB Integration](#openhab-integration)
- [Troubleshooting](#troubleshooting)
- [Why Not the Serial Port?](#why-not-the-serial-port)
- [License](#license)

---

## Features

- Sends BenQ RS232 commands over TCP (`<CR>*command#<CR>`); framing is added automatically
- Strips the command echo and returns only the value (e.g. `ON`, `OFF`, `HDMI`)
- Detects the projector error replies `Illegal format`, `Unsupported item` and `Block item`
- Meaningful exit codes for use in scripts and home automation rules
- `--status` option that returns the complete projector state as JSON
- `--raw` option that shows the unprocessed reply for debugging
- Interactive mode for exploring the command set
- One TCP connection per command for robust operation
- **No external dependencies**, only the Python 3 standard library

## Tested Hardware

| Model | Status |
|---|---|
| BenQ MH856UST | Tested |

Other BenQ projectors that support "RS232 via LAN" on port 8000 should work as well, for example MW855UST, MX854UST and the "+" variants. The available commands differ between models; check the RS232 Control Guide for your projector on the [BenQ support website](https://www.benq.eu/en-uk/support/downloads-faq.html).

## Requirements

- Python **3.6 or newer** (works on older Raspberry Pi OS releases as well)
- A BenQ projector with RS232-over-LAN support, connected to your network via Ethernet
- Network access from the controlling machine to the projector on TCP port 8000
- Optional: `netcat` for manual testing
- Optional: `git` for cloning the repository

## Installation

### 1. Install system packages

Debian / Ubuntu / Raspberry Pi OS:

```bash
sudo apt update
sudo apt install -y python3 git netcat-openbsd
```

Check your Python version:

```bash
python3 --version
```

### 2. Clone the repository

```bash
git clone https://github.com/Michdo93/BenQ-RS232-TCP.git
cd BenQ-RS232-TCP
```

### 3. Make the script executable

```bash
chmod +x benq_projector.py
```

### 4. Optional: install system-wide

This lets you call the tool from anywhere as `benq`:

```bash
sudo cp benq_projector.py /usr/local/bin/benq
sudo chmod +x /usr/local/bin/benq
benq --help
```

To use it as a library from other Python scripts, either copy `benq_projector.py` next to your script or add the repository directory to your `PYTHONPATH`:

```bash
export PYTHONPATH="$PYTHONPATH:/path/to/BenQ-RS232-TCP"
```

No `pip install` is required, because the program only uses the standard library.

## Projector Setup

1. **Connect the projector** to your network with an Ethernet cable (RJ45 port on the projector).
2. **Find the IP address** in the projector's OSD menu under the network settings (wired LAN). Assigning a **static IP address** or a DHCP reservation in your router is strongly recommended.
3. **Enable network control in standby.** Without this, the projector does not respond on the network while it is in standby, and `pow=on` will not work. You can enable it either in the OSD (standby settings, network: *On*) or with the tool itself while the projector is switched on:

   ```bash
   python3 benq_projector.py --host 192.168.0.59 standbynet=on
   ```

4. **Test the connection:**

   ```bash
   ping -c 2 192.168.0.59
   python3 benq_projector.py --host 192.168.0.59 pow=?
   ```

## Configuration (IP and Port)

The projector's address can be set in two ways.

### Option A: command line arguments

| Argument | Default | Description |
|---|---|---|
| `--host` | `192.168.0.59` | IP address or hostname of the projector |
| `--port` | `8000` | TCP port of the RS232-over-LAN interface |
| `--timeout` | `3.0` | Timeout in seconds per command |

```bash
python3 benq_projector.py --host 192.168.1.50 --port 8000 --timeout 5 pow=?
```

### Option B: change the defaults in the script

If you always control the same projector, edit the constants at the top of `benq_projector.py`:

```python
DEFAULT_HOST = "192.168.0.59"
DEFAULT_PORT = 8000
```

After that you can omit `--host` and `--port`:

```bash
python3 benq_projector.py pow=?
```

### Option C: as a library

```python
from benq_projector import BenQProjector

projector = BenQProjector(host="192.168.1.50", port=8000, timeout=3.0)
```

## Usage

### Command Line

```text
usage: benq_projector.py [-h] [--host HOST] [--port PORT] [--timeout TIMEOUT]
                         [--raw] [--status] [commands ...]
```

Commands are written **without** the leading `*` and trailing `#`. The program adds the framing.

```bash
# Query power state
python3 benq_projector.py pow=?
# -> OFF

# Power on
python3 benq_projector.py pow=on
# -> ON

# Several commands in sequence
python3 benq_projector.py pow=on sour=hdmi

# Lamp hours
python3 benq_projector.py ltim=?

# Show the complete raw reply including echo
python3 benq_projector.py --raw pow=?
# -> >*pow=?#  *POW=OFF#

# Complete status as JSON
python3 benq_projector.py --status
```

Example output of `--status` (values that are blocked in the current state, e.g. in standby, are shown as `null`):

```json
{
  "pow": "ON",
  "sour": "HDMI",
  "mute": "OFF",
  "vol": "5",
  "appmod": "PRESET",
  "lampm": "ECO",
  "ltim": "1234",
  "blank": "OFF",
  "freeze": "OFF",
  "asp": "AUTO",
  "ct": "NORMAL",
  "bri": "50",
  "con": "50",
  "modelname": "MH856UST"
}
```

### Interactive Mode

Start the program without commands:

```bash
python3 benq_projector.py
```

```text
Enter commands without * and # (e.g. "pow=?"), "status" or "exit".
>> pow=?
OFF
>> pow=on
ON
>> status
{ ... }
>> exit
```

### Python Library

```python
from benq_projector import BenQProjector, BenQError

p = BenQProjector("192.168.0.59")

# Generic
p.send("pow=?")          # -> "OFF"
p.get("sour")            # -> "HDMI"
p.set("sour", "hdmi2")   # -> "HDMI2"
p.send_raw("pow=?")      # -> ">*pow=?#  *POW=OFF#"

# Convenience methods
p.power_on()
p.power_off()
p.is_on()                # -> True / False
p.source()               # current source
p.source("hdmi")         # switch source
p.mute(True)             # mute on
p.mute(False)            # mute off
p.mute()                 # query mute state
p.volume_up()
p.volume_down()
p.blank(True)            # blank screen
p.lamp_hours()           # -> 1234 (int)
p.status()               # -> dict with the complete state

# Error handling
try:
    p.source("hdmi")
except BenQError as e:
    print("Projector refused command:", e)   # e.g. "Block item" while in standby
except (OSError, ConnectionError) as e:
    print("Projector not reachable:", e)
```

## Command Reference

The following commands are supported by the **MH856UST** according to the BenQ RS232 Control Guide. Other models may support a different set.

| Function | Commands |
|---|---|
| Power | `pow=on`, `pow=off`, `pow=?` |
| Source | `sour=hdmi`, `sour=hdmi2`, `sour=RGB`, `sour=RGB2`, `sour=vid`, `sour=svid`, `sour=?` |
| Audio | `mute=on`, `mute=off`, `mute=?`, `vol=+`, `vol=-`, `vol=?` |
| Audio source | `audiosour=off`, `audiosour=RGB`, `audiosour=vid`, `audiosour=hdmi`, `audiosour=?` |
| Picture mode | `appmod=preset`, `appmod=srgb`, `appmod=bright`, `appmod=cine`, `appmod=user1`, `appmod=user2`, `appmod=?` |
| Picture | `con=+`, `con=-`, `con=?`, `bri=+`, `bri=-`, `bri=?`, `color=+`, `color=-`, `color=?`, `sharp=+`, `sharp=-`, `sharp=?` |
| Color temperature | `ct=warm`, `ct=normal`, `ct=cool`, `ct=native`, `ct=?` |
| Aspect ratio | `asp=4:3`, `asp=16:9`, `asp=16:10`, `asp=AUTO`, `asp=?` |
| Zoom / auto | `zoomI`, `zoomO`, `auto` |
| BrilliantColor | `BC=on`, `BC=off`, `BC=?` |
| Projector position | `pp=FT`, `pp=RE`, `pp=RC`, `pp=FC`, `pp=?` |
| Quick auto search | `QAS=on`, `QAS=off`, `QAS=?` |
| Power behavior | `directpower=on/off/?`, `autopower=on/off/?`, `ins=on/off/?` |
| Standby settings | `standbynet=on/off/?`, `standbymnt=on/off/?` |
| Lamp | `ltim=?`, `lampm=lnor`, `lampm=eco`, `lampm=seco`, `lampm=?` |
| Screen | `blank=on`, `blank=off`, `blank=?`, `freeze=on`, `freeze=off`, `freeze=?` |
| Menu navigation | `menu=on`, `menu=off`, `up`, `down`, `left`, `right`, `enter` |
| 3D | `3d=off`, `3d=auto`, `3d=tb`, `3d=fs`, `3d=fp`, `3d=sbs`, `3d=da`, `3d=iv`, `3d=?` |
| Information | `modelname=?`, `macaddr=?` |
| Miscellaneous | `Highaltitude=on/off/?`, `amxdd=on/off/?`, `baud=?` |

Commands are case-insensitive.

## Responses, Errors and Exit Codes

A typical reply from the projector consists of the echo of the command followed by the answer:

```text
>*pow=?#
*POW=OFF#
```

The program removes the echo and returns only the value (`OFF`).

The projector can reply with the following errors:

| Reply | Meaning |
|---|---|
| `Illegal format` | The command syntax is wrong |
| `Unsupported item` | The command is valid but not supported by this model |
| `Block item` | The command cannot be executed in the current state (e.g. `sour=?` while in standby) |

Exit codes of the command line tool:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | The projector returned an error (`Illegal format`, `Unsupported item`, `Block item`) |
| `2` | Connection error (projector not reachable, timeout, no reply) |

## Manual Testing with netcat

You can talk to the projector without this program:

```bash
printf '\r*pow=?#\r' | nc -w 2 192.168.0.59 8000 | cat -v
```

The `| cat -v` part is important: the projector ends its replies with a carriage return (`\r`), which moves the cursor back to the start of the line. Without `cat -v`, your shell prompt overwrites the reply and it looks as if nothing was returned.

## openHAB Integration

The easiest way is the **Exec Binding**.

1. Install the Exec Binding in openHAB.
2. Copy `benq_projector.py` to a location readable by the `openhab` user, e.g. `/opt/benq/benq_projector.py`.
3. Whitelist the commands in `$OPENHAB_CONF/misc/exec.whitelist` (e.g. `/etc/openhab/misc/exec.whitelist`):

   ```text
   python3 /opt/benq/benq_projector.py pow=on
   python3 /opt/benq/benq_projector.py pow=off
   python3 /opt/benq/benq_projector.py pow=?
   python3 /opt/benq/benq_projector.py %2$s
   ```

4. Define things, for example in `things/benq.things`:

   ```java
   Thing exec:command:beamer_power_state [
       command="python3 /opt/benq/benq_projector.py pow=?",
       interval=60, timeout=5, autorun=false ]

   Thing exec:command:beamer_cmd [
       command="python3 /opt/benq/benq_projector.py %2$s",
       interval=0, timeout=10, autorun=true ]
   ```

5. Link the `output` channel of `beamer_power_state` to a String item, and the `input` channel of `beamer_cmd` to a String item to which you send commands like `pow=on` or `sour=hdmi`.

Alternatively, you can import the `BenQProjector` class into a Python-based bridge (e.g. MQTT or the openHAB REST API) for a more integrated solution.

## Troubleshooting

**No reply / connection timeout**

- Check that the projector is reachable: `ping <IP>`
- Check that the port is open: `nc -zv <IP> 8000`
- If this only fails in standby, enable network standby (`standbynet=on` or in the OSD).
- Make sure no other client (e.g. Crestron RoomView, another control system) is holding a connection to port 8000.

**`Block item` replies**

- Many commands are only accepted while the projector is switched on. After `pow=on`, wait roughly 30 to 60 seconds before sending further commands.
- Right after `pow=off`, the projector is in its cooling phase and also blocks most commands.

**`Unsupported item` replies**

- The command is not available on your model. Check your model's RS232 Control Guide.

**It looks like the reply is empty in the shell**

- See [Manual Testing with netcat](#manual-testing-with-netcat): the reply is overwritten by the prompt because of the carriage return.

**IP address changes**

- Assign a static IP or a DHCP reservation to the projector, or use a hostname with `--host`.

## Why Not the Serial Port?

The same commands also work on the projector's D-Sub 9 RS232 port (crossover cable; 9600 to 115200 baud, 8N1, no flow control). In practice, cheap USB-to-serial adapters, especially those based on the CH340/HL-340 chip, often lack a proper RS232 level converter. The result is corrupted replies in which individual `0` bits are read as `1` (e.g. `*P_W}_NN'` instead of `*POW=OFF#`).

If the projector is connected to the network anyway, the LAN interface avoids all of these problems. If you do need the serial port, use an adapter with an FTDI chip and a real RS232 level converter (MAX232/MAX3232), or a MAX3232 module on the Raspberry Pi UART.

## License

See [LICENSE](LICENSE).
