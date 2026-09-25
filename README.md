# BenQ-RS232-TCP
A Python program for controlling BenQ projectors via their LAN interface using RS232 commands (RS232 over TCP).

Many BenQ business and education projectors accept their RS232 command set not only on the D-Sub 9 serial port but also over the network on **TCP port 8000**. This program uses that interface and exposes the projector via **MQTT**, so it can be integrated into openHAB, Home Assistant, Node-RED or any other MQTT-capable system. No USB-to-serial adapter or null-modem cable is required.

One single program does everything:

- `run`: MQTT service (runs permanently, e.g. as a systemd service)
- `send`: send commands directly from the command line
- `status`: print the complete projector status as JSON

---

## Table of Contents

- [Features](#features)
- [Tested Hardware](#tested-hardware)
- [How It Works](#how-it-works)
- [Requirements](#requirements)
- [Step 1: Install and Configure Mosquitto](#step-1-install-and-configure-mosquitto)
- [Step 2: Prepare the Projector](#step-2-prepare-the-projector)
- [Step 3: Install the Program](#step-3-install-the-program)
- [Step 4: Configuration](#step-4-configuration)
- [Step 5: First Test](#step-5-first-test)
- [Step 6: Run as a systemd Service](#step-6-run-as-a-systemd-service)
- [MQTT Interface](#mqtt-interface)
- [Command Line Usage](#command-line-usage)
- [openHAB Integration](#openhab-integration)
- [Projector Command Reference](#projector-command-reference)
- [Troubleshooting](#troubleshooting)
- [Why Not the Serial Port?](#why-not-the-serial-port)
- [License](#license)

---

## Features

- One persistent TCP connection to the projector with automatic reconnect
- Strictly serialised command queue with a configurable minimum gap between commands
- Replies are matched to commands by key, so a missing echo does not cause errors
- Detection of `Illegal format`, `Unsupported item` and `Block item`
- Warm-up and cool-down handling: commands sent while the projector warms up are queued and executed afterwards
- Periodic polling; states are only published when they change (retained)
- One MQTT topic per property (`…/power/state`, `…/power/command`, …) plus an aggregated JSON state
- Raw command and raw response topics for debugging and for every command without its own topic
- Availability topic with MQTT Last Will
- Configuration file, command line overrides, systemd service
- Works with Python 3.6 or newer and with paho-mqtt 1.x and 2.x

## Tested Hardware

| Model | Status |
|---|---|
| BenQ MH856UST | Tested |

Other BenQ projectors that support "RS232 via LAN" on port 8000 should work as well. The available commands differ between models; check the RS232 Control Guide for your model on the [BenQ support website](https://www.benq.eu/en-uk/support/downloads-faq.html).

## How It Works

```text
openHAB / Home Assistant / Node-RED
              │  MQTT
              ▼
     Mosquitto (MQTT broker)
              │  MQTT
              ▼
      benq_projector.py run
              │  TCP port 8000, RS232 command set
              ▼
        BenQ projector
```

Tests with the real device showed some peculiarities that the program takes care of:

| Behaviour of the projector | Handling in the program |
|---|---|
| Commands that arrive while the projector is still processing the previous one are silently dropped | Strict command queue, one command at a time, minimum gap (`command_gap`) |
| The echo (`>*pow=?#`) is not always sent | Replies are matched by key (`*POW=OFF#` belongs to `pow=…`) |
| In standby, during warm-up and cool-down some commands get no reply at all | Status `NO_RESPONSE`, retries for queries, no polling during transitions |
| Error replies (`*Block item#`) contain no key | Assigned to the pending command |
| Replies end with a carriage return (`\r`) | Replies are parsed token by token (`*…#`) |

## Requirements

- Raspberry Pi or any other Linux machine (examples use Raspberry Pi OS / Debian / Ubuntu)
- Python **3.6 or newer**
- `paho-mqtt` (1.x or 2.x)
- Mosquitto MQTT broker (installed in Step 1, can run on the same machine)
- BenQ projector with RS232-over-LAN support, connected via Ethernet

## Step 1: Install and Configure Mosquitto

The following steps install Mosquitto on the same machine as this program. If you already have a broker, skip this step and enter its address in the configuration.

### 1.1 Install

```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
```

`mosquitto-clients` provides `mosquitto_pub` and `mosquitto_sub` for testing.

Check the version:

```bash
mosquitto -h | head -1
```

### 1.2 Create a user with password

This guide uses the default credentials **user `mqtt`, password `mqtt`**, which are also the defaults of the program. **Change them for anything beyond a lab setup** and adjust the configuration file accordingly.

```bash
sudo mosquitto_passwd -c -b /etc/mosquitto/passwd mqtt mqtt
```

For a real password, use the interactive variant instead, so the password does not end up in your shell history:

```bash
sudo mosquitto_passwd -c /etc/mosquitto/passwd mqtt
```

`-c` creates a new file and overwrites an existing one. To add further users (e.g. for openHAB), leave out `-c`:

```bash
sudo mosquitto_passwd -b /etc/mosquitto/passwd openhab openhab
```

**Set the owner and permissions.** Mosquitto runs as user `mosquitto` and cannot read the file otherwise (error: `Unable to open pwfile`):

```bash
sudo chown mosquitto:mosquitto /etc/mosquitto/passwd
sudo chmod 0700 /etc/mosquitto/passwd
```

### 1.3 Configure the listener

Create `/etc/mosquitto/conf.d/local.conf`:

```bash
sudo nano /etc/mosquitto/conf.d/local.conf
```

Content:

```text
# Listen on all interfaces, port 1883
listener 1883

# Require authentication
allow_anonymous false
password_file /etc/mosquitto/passwd
```

- `listener 1883` accepts connections from the whole network, which is needed if openHAB runs on another machine. If only local programs connect, use `listener 1883 127.0.0.1` instead.
- The main configuration `/etc/mosquitto/mosquitto.conf` already enables persistence and includes `conf.d`. Do not repeat `persistence` or `log_dest` in `local.conf`, otherwise Mosquitto may refuse to start because of duplicate options. Check with:

  ```bash
  grep -E "include_dir|persistence|log_dest" /etc/mosquitto/mosquitto.conf
  ```

### 1.4 Optional: access control list (ACL)

If several users share the broker, you can restrict what each user may do. Add to `local.conf`:

```text
acl_file /etc/mosquitto/acl
```

Create `/etc/mosquitto/acl`:

```text
# The projector service
user mqtt
topic readwrite projector/#

# openHAB
user openhab
topic readwrite #
```

```bash
sudo chown mosquitto:mosquitto /etc/mosquitto/acl
sudo chmod 0700 /etc/mosquitto/acl
```

### 1.5 Restart and check

```bash
sudo systemctl restart mosquitto
sudo systemctl status mosquitto --no-pager
sudo journalctl -u mosquitto -n 30 --no-pager
sudo tail -n 30 /var/log/mosquitto/mosquitto.log
```

The status must be `active (running)`. On Debian-based systems Mosquitto writes its log to `/var/log/mosquitto/mosquitto.log` (`log_dest file` in `mosquitto.conf`); errors during startup can also appear in the journal.

Check that Mosquitto listens on port 1883:

```bash
sudo ss -tlnp | grep 1883
```

### 1.6 Test the broker

Terminal 1 (subscribe):

```bash
mosquitto_sub -h localhost -u mqtt -P mqtt -t 'test/#' -v
```

Terminal 2 (publish):

```bash
mosquitto_pub -h localhost -u mqtt -P mqtt -t test/hello -m "it works"
```

Terminal 1 must show `test/hello it works`. Without credentials the connection must be refused:

```bash
mosquitto_pub -h localhost -t test/hello -m "anonymous"
# -> Connection error: Connection Refused: not authorised.
```

### 1.7 Firewall (only if enabled)

If `ufw` is active and other machines (e.g. openHAB) should connect:

```bash
sudo ufw allow 1883/tcp
```

## Step 2: Prepare the Projector

1. Connect the projector to your network with an Ethernet cable.
2. Look up the IP address in the OSD network settings. Assign a **static IP address** or a DHCP reservation in your router.
3. Enable **network control in standby** (OSD standby settings, network: *On*). Without it, the projector does not respond while in standby and cannot be switched on via the network. Alternatively, while the projector is switched on (after Step 3):

   ```bash
   python3 /opt/benq-projector/benq_projector.py --host 192.168.0.59 send standbynet=on
   ```

4. Check that the projector is reachable:

   ```bash
   ping -c 2 192.168.0.59
   nc -zv 192.168.0.59 8000
   ```

## Step 3: Install the Program

### 3.1 System packages

```bash
sudo apt update
sudo apt install -y python3 python3-paho-mqtt git netcat-openbsd
```

If `python3-paho-mqtt` is not available for your distribution, install it with pip instead:

```bash
sudo apt install -y python3-pip
sudo pip3 install paho-mqtt
# Raspberry Pi OS Bookworm / Debian 12 and newer:
sudo pip3 install --break-system-packages paho-mqtt
```

Check:

```bash
python3 --version
python3 -c "import paho.mqtt; print(paho.mqtt.__version__)"
```

### 3.2 Get the program

```bash
git clone https://github.com/Michdo93/BenQ-RS232-TCP.git
cd BenQ-RS232-TCP
```

### 3.3 Install to /opt and /etc

```bash
# Program
sudo mkdir -p /opt/benq-projector
sudo cp benq_projector.py /opt/benq-projector/
sudo chmod 755 /opt/benq-projector/benq_projector.py

# System user for the service (no login, no home directory)
sudo useradd --system --no-create-home --shell /usr/sbin/nologin benq

# Configuration (readable by the service user only, it contains the MQTT password)
sudo mkdir -p /etc/benq-projector
sudo cp benq_projector.ini /etc/benq-projector/
sudo chown root:benq /etc/benq-projector/benq_projector.ini
sudo chmod 640 /etc/benq-projector/benq_projector.ini
```

Optional: a short command for the command line:

```bash
sudo ln -s /opt/benq-projector/benq_projector.py /usr/local/bin/benq
```

Since the configuration is only readable by the `benq` user and root, run command line tests with `sudo` or pass the projector address with `--host`.

## Step 4: Configuration

Edit the configuration file:

```bash
sudo nano /etc/benq-projector/benq_projector.ini
```

The program looks for its configuration in this order: `-c/--config`, `./benq_projector.ini`, `/etc/benq-projector/benq_projector.ini`. Missing values fall back to the built-in defaults.

### [projector]

| Key | Default | Description |
|---|---|---|
| `host` | `192.168.0.59` | IP address or hostname of the projector |
| `port` | `8000` | TCP port of the RS232-over-LAN interface |
| `timeout` | `3.0` | Seconds to wait for a reply |
| `command_gap` | `0.7` | Minimum pause between two commands in seconds |
| `query_retries` | `1` | Extra attempts for `=?` queries without reply |
| `warmup_time` | `60` | Seconds after power on in which commands are queued |
| `cooldown_time` | `90` | Seconds after power off in which commands are rejected (power on is queued) |
| `reconnect_delay` | `10` | Seconds to wait after a connection error |

### [mqtt]

| Key | Default | Description |
|---|---|---|
| `host` | `localhost` | MQTT broker address |
| `port` | `1883` | MQTT broker port |
| `username` | `mqtt` | MQTT user (empty = anonymous) |
| `password` | `mqtt` | MQTT password |
| `client_id` | `benq-projector` | MQTT client ID (must be unique per broker) |
| `base_topic` | `projector/benq/mh856ust` | Prefix for all topics |
| `qos` | `1` | QoS for publish and subscribe |
| `keepalive` | `60` | MQTT keepalive in seconds |

### [polling]

| Key | Default | Description |
|---|---|---|
| `power_interval` | `5` | Seconds between power state queries |
| `full_interval` | `30` | Seconds between full status polls (only while the projector is on) |

### [logging]

| Key | Default | Description |
|---|---|---|
| `level` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

### Command line overrides

`--host` and `--port` override the projector address from the configuration file:

```bash
python3 benq_projector.py --host 192.168.1.50 --port 8000 send pow=?
```

## Step 5: First Test

Direct commands (no MQTT involved):

```bash
cd /opt/benq-projector
python3 benq_projector.py --host 192.168.0.59 send pow=?
python3 benq_projector.py --host 192.168.0.59 send --raw modelname=?
python3 benq_projector.py --host 192.168.0.59 status
```

Run the MQTT service in the foreground with debug output:

```bash
sudo -u benq python3 /opt/benq-projector/benq_projector.py \
    -c /etc/benq-projector/benq_projector.ini -v run
```

In a second terminal, watch all topics:

```bash
mosquitto_sub -h localhost -u mqtt -P mqtt -t 'projector/#' -v
```

In a third terminal, send commands:

```bash
mosquitto_pub -h localhost -u mqtt -P mqtt -t projector/benq/mh856ust/power/command -m ON
mosquitto_pub -h localhost -u mqtt -P mqtt -t projector/benq/mh856ust/source/command -m HDMI
```

Stop the foreground service with `Ctrl+C`.

## Step 6: Run as a systemd Service

```bash
sudo cp benq-projector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now benq-projector
```

Check:

```bash
sudo systemctl status benq-projector --no-pager
sudo journalctl -u benq-projector -f
```

After changes to the configuration:

```bash
sudo systemctl restart benq-projector
```

The service starts after Mosquitto and restarts automatically after errors.

> **Note:** While the service is running it holds the TCP connection to the projector. Many BenQ projectors accept only one connection at a time, so `send` and `status` from the command line may not get a reply. Use the MQTT `raw/command` topic instead, or stop the service temporarily (`sudo systemctl stop benq-projector`).

## MQTT Interface

All topics start with `base_topic` (default `projector/benq/mh856ust`). States are published **retained** and only when they change. Commands must **not** be retained; retained commands are ignored so that they are not executed again after a reconnect.

### Topics

| Topic | Direction | Payload |
|---|---|---|
| `availability` | state | `online` / `offline` (Last Will) |
| `connection/state` | state | `ONLINE` / `OFFLINE` (TCP connection to the projector) |
| `phase/state` | state | `STABLE` / `WARMUP` / `COOLDOWN` |
| `power/state` | state | `ON` / `OFF` |
| `power/command` | command | `ON` / `OFF` / `TOGGLE` |
| `source/state` | state | e.g. `HDMI`, `HDMI2`, `RGB`, `RGB2`, `VID`, `SVID` |
| `source/command` | command | `HDMI`, `HDMI2`, `RGB`, `RGB2`, `VID`, `SVID` |
| `mute/state` | state | `ON` / `OFF` |
| `mute/command` | command | `ON` / `OFF` / `TOGGLE` |
| `volume/state` | state | number |
| `volume/command` | command | `UP` / `DOWN` / target number (e.g. `8`) |
| `blank/state` | state | `ON` / `OFF` |
| `blank/command` | command | `ON` / `OFF` / `TOGGLE` |
| `freeze/state` | state | `ON` / `OFF` |
| `freeze/command` | command | `ON` / `OFF` / `TOGGLE` |
| `picturemode/state` | state | e.g. `PRESET`, `BRIGHT`, `SRGB`, `CINE`, `USER1` |
| `picturemode/command` | command | `PRESET`, `BRIGHT`, `SRGB`, `CINE`, `USER1`, `USER2` |
| `lampmode/state` | state | `LNOR` / `ECO` / `SECO` |
| `lampmode/command` | command | `LNOR` / `ECO` / `SECO` |
| `aspect/state` | state | e.g. `AUTO`, `16:9` |
| `aspect/command` | command | `AUTO`, `4:3`, `16:9`, `16:10` |
| `colortemp/state` | state | e.g. `NORMAL` |
| `colortemp/command` | command | `WARM`, `NORMAL`, `COOL`, `NATIVE` |
| `brightness/state` | state | number |
| `brightness/command` | command | `UP` / `DOWN` / target number |
| `contrast/state` | state | number |
| `contrast/command` | command | `UP` / `DOWN` / target number |
| `lamphours/state` | state | number |
| `model/state` | state | e.g. `MH856UST` |
| `menu/command` | command | `ON`, `OFF`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER` |
| `refresh/command` | command | any payload: poll all values now |
| `raw/command` | command | any projector command without `*` and `#`, e.g. `pow=?`, `3d=auto` |
| `raw/response` | event | every received token, e.g. `>*pow=?#` (echo), `*POW=OFF#`, `NO_RESPONSE` |
| `error` | event | JSON, e.g. `{"command": "sour=hdmi", "status": "ERROR", "message": "Block item", …}` |
| `state` | state | JSON with all known values |

Command payloads are case-insensitive.

### Behaviour

- **Power on:** `power/state` changes to `ON` and `phase/state` to `WARMUP` for `warmup_time` seconds. Commands received during warm-up are queued and executed afterwards, so `power ON` followed immediately by `source HDMI` works as expected.
- **Power off:** `phase/state` changes to `COOLDOWN` for `cooldown_time` seconds. Other commands are rejected (`error` with status `BLOCKED`), a power-on command is queued.
- **Polling:** the power state is queried every `power_interval` seconds, all other values every `full_interval` seconds, but only while the projector is on and stable.
- **Remote control:** if the projector is switched on with the remote control, the next power poll detects it and all values are read.

### Examples

```bash
B=projector/benq/mh856ust
PUB="mosquitto_pub -h localhost -u mqtt -P mqtt"

$PUB -t $B/power/command -m ON
$PUB -t $B/source/command -m HDMI2
$PUB -t $B/volume/command -m 8
$PUB -t $B/mute/command -m TOGGLE
$PUB -t $B/menu/command -m ON
$PUB -t $B/raw/command -m "ltim=?"
$PUB -t $B/refresh/command -m 1
$PUB -t $B/power/command -m OFF
```

Watch only replies and errors:

```bash
mosquitto_sub -h localhost -u mqtt -P mqtt -v \
    -t 'projector/benq/mh856ust/raw/response' \
    -t 'projector/benq/mh856ust/error'
```

If a command topic was accidentally published retained, clear it with an empty retained message:

```bash
mosquitto_pub -h localhost -u mqtt -P mqtt -t projector/benq/mh856ust/power/command -r -n
```

## Command Line Usage

```text
benq_projector.py [-h] [-c CONFIG] [--host HOST] [--port PORT] [-v] [--version]
                  {run,send,status} ...
```

| Command | Description |
|---|---|
| `run` | Run the MQTT service |
| `send CMD [CMD ...]` | Send commands directly, print the value of each reply |
| `send --raw CMD [CMD ...]` | Print every raw token including the echo |
| `status` | Query all readable values and print them as JSON |

```bash
python3 benq_projector.py send pow=?            # -> OFF
python3 benq_projector.py send pow=on sour=hdmi
python3 benq_projector.py send --raw pow=?      # -> >*pow=?#  and  *POW=OFF#
python3 benq_projector.py status
python3 benq_projector.py -v send pow=?         # with debug output
```

Exit codes for `send` and `status`:

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Projector error (`Block item`, `Illegal format`, `Unsupported item`) or no reply |
| `2` | Connection error |
| `3` | paho-mqtt missing (`run` only) |

### Use as a Python library

```python
from benq_projector import BenQProjector

p = BenQProjector("192.168.0.59")
r = p.execute("pow=?")
print(r.status, r.value)      # OK OFF
p.set("sour", "hdmi")
p.close()
```

## openHAB Integration

Requires the **MQTT Binding**. Replace `192.168.0.10` with the IP address of the machine running Mosquitto.

`things/benq.things`:

```java
Bridge mqtt:broker:beamerpi "Mosquitto Beamer-Pi" [ host="192.168.0.10", port=1883, secure=false,
                                                   username="mqtt", password="mqtt", clientID="openhab-benq" ] {
    Thing topic mh856ust "BenQ MH856UST" [ availabilityTopic="projector/benq/mh856ust/availability",
                                          payloadAvailable="online", payloadNotAvailable="offline" ] {
    Channels:
        Type switch : power       "Power"        [ stateTopic="projector/benq/mh856ust/power/state",       commandTopic="projector/benq/mh856ust/power/command" ]
        Type string : source      "Source"       [ stateTopic="projector/benq/mh856ust/source/state",      commandTopic="projector/benq/mh856ust/source/command" ]
        Type switch : mute        "Mute"         [ stateTopic="projector/benq/mh856ust/mute/state",        commandTopic="projector/benq/mh856ust/mute/command" ]
        Type number : volume      "Volume"       [ stateTopic="projector/benq/mh856ust/volume/state",      commandTopic="projector/benq/mh856ust/volume/command" ]
        Type switch : blank       "Blank"        [ stateTopic="projector/benq/mh856ust/blank/state",       commandTopic="projector/benq/mh856ust/blank/command" ]
        Type switch : freeze      "Freeze"       [ stateTopic="projector/benq/mh856ust/freeze/state",      commandTopic="projector/benq/mh856ust/freeze/command" ]
        Type string : picturemode "Picture Mode" [ stateTopic="projector/benq/mh856ust/picturemode/state", commandTopic="projector/benq/mh856ust/picturemode/command" ]
        Type string : lampmode    "Lamp Mode"    [ stateTopic="projector/benq/mh856ust/lampmode/state",    commandTopic="projector/benq/mh856ust/lampmode/command" ]
        Type number : lamphours   "Lamp Hours"   [ stateTopic="projector/benq/mh856ust/lamphours/state" ]
        Type string : phase       "Phase"        [ stateTopic="projector/benq/mh856ust/phase/state" ]
        Type string : connection  "Connection"   [ stateTopic="projector/benq/mh856ust/connection/state" ]
        Type string : menu        "Menu"         [ commandTopic="projector/benq/mh856ust/menu/command" ]
        Type string : raw         "Raw Command"  [ commandTopic="projector/benq/mh856ust/raw/command" ]
        Type string : rawResponse "Raw Response" [ stateTopic="projector/benq/mh856ust/raw/response" ]
        Type string : error       "Last Error"   [ stateTopic="projector/benq/mh856ust/error" ]
    }
}
```

`items/benq.items`:

```java
Group   gBeamer            "Beamer"
Switch  Beamer_Power       "Power"              (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:power" }
String  Beamer_Source      "Source [%s]"        (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:source" }
Switch  Beamer_Mute        "Mute"               (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:mute" }
Number  Beamer_Volume      "Volume [%d]"        (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:volume" }
Switch  Beamer_Blank       "Blank"              (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:blank" }
Switch  Beamer_Freeze      "Freeze"             (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:freeze" }
String  Beamer_PictureMode "Picture Mode [%s]"  (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:picturemode" }
String  Beamer_LampMode    "Lamp Mode [%s]"     (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:lampmode" }
Number  Beamer_LampHours   "Lamp Hours [%d h]"  (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:lamphours" }
String  Beamer_Phase       "Phase [%s]"         (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:phase" }
String  Beamer_Connection  "Connection [%s]"    (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:connection" }
String  Beamer_Menu        "Menu"               (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:menu" }
String  Beamer_Raw         "Raw Command"        (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:raw" }
String  Beamer_RawResponse "Raw Response [%s]"  (gBeamer) { channel="mqtt:topic:beamerpi:mh856ust:rawResponse" }
```

Sitemap example:

```java
Frame label="Beamer" {
    Switch    item=Beamer_Power
    Text      item=Beamer_Phase
    Selection item=Beamer_Source      mappings=[HDMI="HDMI 1", HDMI2="HDMI 2", RGB="Computer 1", RGB2="Computer 2", VID="Video"]
    Switch    item=Beamer_Mute
    Setpoint  item=Beamer_Volume      minValue=0 maxValue=20 step=1
    Switch    item=Beamer_Blank
    Selection item=Beamer_PictureMode mappings=[PRESET="Presentation", BRIGHT="Bright", SRGB="sRGB", CINE="Cinema", USER1="User 1"]
    Switch    item=Beamer_Menu        mappings=[ON="Menu", UP="▲", DOWN="▼", LEFT="◀", RIGHT="▶", ENTER="OK", OFF="Close"]
    Text      item=Beamer_LampHours
}
```

The `Beamer_Volume` setpoint sends a target value; the program steps the volume up or down until it is reached.

## Projector Command Reference

Commands for the **MH856UST** according to the BenQ RS232 Control Guide, usable with `send` and the `raw/command` topic. Other models may support a different set.

| Function | Commands |
|---|---|
| Power | `pow=on`, `pow=off`, `pow=?` |
| Source | `sour=hdmi`, `sour=hdmi2`, `sour=RGB`, `sour=RGB2`, `sour=vid`, `sour=svid`, `sour=?` |
| Audio | `mute=on`, `mute=off`, `mute=?`, `vol=+`, `vol=-`, `vol=?` |
| Audio source | `audiosour=off`, `audiosour=RGB`, `audiosour=vid`, `audiosour=hdmi`, `audiosour=?` |
| Picture mode | `appmod=preset`, `appmod=srgb`, `appmod=bright`, `appmod=cine`, `appmod=user1`, `appmod=user2`, `appmod=?` |
| Picture | `con=+/-/?`, `bri=+/-/?`, `color=+/-/?`, `sharp=+/-/?` |
| Color temperature | `ct=warm`, `ct=normal`, `ct=cool`, `ct=native`, `ct=?` |
| Aspect ratio | `asp=4:3`, `asp=16:9`, `asp=16:10`, `asp=AUTO`, `asp=?` |
| Zoom / auto | `zoomI`, `zoomO`, `auto` |
| BrilliantColor | `BC=on`, `BC=off`, `BC=?` |
| Projector position | `pp=FT`, `pp=RE`, `pp=RC`, `pp=FC`, `pp=?` |
| Quick auto search | `QAS=on`, `QAS=off`, `QAS=?` |
| Power behaviour | `directpower=on/off/?`, `autopower=on/off/?`, `ins=on/off/?` |
| Standby settings | `standbynet=on/off/?`, `standbymnt=on/off/?` |
| Lamp | `ltim=?`, `lampm=lnor`, `lampm=eco`, `lampm=seco`, `lampm=?` |
| Screen | `blank=on/off/?`, `freeze=on/off/?` |
| Menu | `menu=on`, `menu=off`, `up`, `down`, `left`, `right`, `enter` |
| 3D | `3d=off`, `3d=auto`, `3d=tb`, `3d=fs`, `3d=fp`, `3d=sbs`, `3d=da`, `3d=iv`, `3d=?` |
| Information | `modelname=?`, `macaddr=?` |
| Miscellaneous | `Highaltitude=on/off/?`, `baud=?` |

Replies of the projector:

| Reply | Meaning |
|---|---|
| `>*pow=?#` | Echo of the command (not always sent) |
| `*POW=OFF#` | Answer |
| `*Illegal format#` | Wrong command syntax |
| `*Unsupported item#` | Command not supported by this model |
| `*Block item#` | Command not possible in the current state (e.g. in standby) |

For manual tests without this program:

```bash
printf '\r*pow=?#\r' | nc -w 2 192.168.0.59 8000 | cat -v
```

`| cat -v` is important: replies end with a carriage return, and without it the shell prompt overwrites the reply.

## Troubleshooting

**Mosquitto does not start**

- `sudo journalctl -u mosquitto -n 50 --no-pager` and `sudo tail -n 50 /var/log/mosquitto/mosquitto.log`
- `Unable to open pwfile`: owner or permissions of `/etc/mosquitto/passwd` are wrong, see [1.2](#12-create-a-user-with-password).
- `Duplicate … value`: an option from `mosquitto.conf` was repeated in `conf.d/local.conf`.

**The service cannot connect to MQTT (`MQTT connection refused: Not authorized`)**

- Check `username` and `password` in the configuration.
- Test with `mosquitto_sub -h localhost -u mqtt -P mqtt -t '#' -v`.

**`connection/state` is `OFFLINE`**

- `ping 192.168.0.59` and `nc -zv 192.168.0.59 8000`
- Another client (e.g. Crestron RoomView, a second instance, a manual `nc` session) is holding the connection.
- In standby: network standby is disabled, see [Step 2](#step-2-prepare-the-projector).

**Commands are ignored or answered with `Block item`**

- Check `phase/state`: during `WARMUP` commands are queued, during `COOLDOWN` they are rejected.
- Increase `warmup_time` if commands are still blocked after the warm-up phase.
- Many commands only work while the projector is on.

**Values are missing or occasionally wrong**

- Increase `command_gap` (e.g. `1.0`) and `timeout` (e.g. `5`).
- Run with `-v` and check the raw replies.

**`Unsupported item`**

- The command is not available on your model.

**Debug output**

```bash
sudo systemctl stop benq-projector
sudo -u benq python3 /opt/benq-projector/benq_projector.py -c /etc/benq-projector/benq_projector.ini -v run
```

## Why Not the Serial Port?

The same commands also work on the D-Sub 9 RS232 port (crossover cable, 9600 to 115200 baud, 8N1, no flow control). In practice, cheap USB-to-serial adapters, especially those based on the CH340/HL-340 chip, often lack a proper RS232 level converter. The result is corrupted replies in which individual `0` bits are read as `1` (e.g. `*P_W}_NN'` instead of `*POW=OFF#`).

If the projector is connected to the network anyway, the LAN interface avoids all of these problems. If you need the serial port, use an adapter with an FTDI chip and a real RS232 level converter (MAX232/MAX3232), or a MAX3232 module on the Raspberry Pi UART.

## License

See [LICENSE](LICENSE).
