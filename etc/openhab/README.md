# openHAB Integration

Example configuration for openHAB 5 using the MQTT Binding and the MQTT interface of `benq_projector.py`.

| File | Destination |
|---|---|
| `things/benq.things` | `$OPENHAB_CONF/things/` |
| `items/benq.items` | `$OPENHAB_CONF/items/` |
| `sitemaps/benq.sitemap` | `$OPENHAB_CONF/sitemaps/` |
| `transform/*.map` | `$OPENHAB_CONF/transform/` |
| `automation/python/benq.py` | `$OPENHAB_CONF/automation/python/` |

`$OPENHAB_CONF` is `/etc/openhab` for package installations and `/openhab/conf` in Docker.

## Required add-ons

- **MQTT Binding**
- **MAP Transformation**
- **JSONPath Transformation**
- **Python Scripting** (only for the rules)

## Connection

The broker on the projector Pi accepts unencrypted connections only from localhost (`listener 1883 127.0.0.1`). openHAB therefore connects via **TLS on port 8883**:

- `host` must match a name or IP address in the server certificate. `scripts/create-certs.sh` includes `<hostname>.local`, `<hostname>`, `localhost` and the IP address of the Pi.
- `certificatepin=true` pins the broker certificate on the first successful connection. After renewing the certificate, clear the `certificate` parameter of the broker Thing (UI: Things → broker → Configuration) so the new one is pinned.
- Username and password are those created with `mosquitto_passwd`.

If openHAB runs on the projector Pi itself, use `host="localhost"`, `port=1883`, `secure=false`.

## Items overview

| Item | Type | Description |
|---|---|---|
| `Beamer_Presentation` | Switch | Virtual: power on + source + picture mode in one step (rule) |
| `Beamer_Power` | Switch | Power |
| `Beamer_Source` | String | `HDMI`, `HDMI2`, `RGB`, `RGB2`, `VID`, `SVID` |
| `Beamer_Mute`, `Beamer_Blank`, `Beamer_Freeze` | Switch | Audio mute, blank screen, freeze picture |
| `Beamer_Volume` | Number | Target volume (the program steps up/down) |
| `Beamer_PictureMode`, `Beamer_LampMode`, `Beamer_Aspect`, `Beamer_ColorTemp` | String | Picture settings |
| `Beamer_Brightness`, `Beamer_Contrast` | Number | Picture settings |
| `Beamer_Menu` | String | OSD menu: `ON`, `UP`, `DOWN`, `LEFT`, `RIGHT`, `ENTER`, `OFF` |
| `Beamer_LampHours` | Number | Lamp hours |
| `Beamer_LampWarning` | Switch | Virtual: set by rule when the lamp hour limit is reached |
| `Beamer_Phase` | String | `STABLE`, `WARMUP`, `COOLDOWN` |
| `Beamer_Connection` | String | TCP connection to the projector: `ONLINE` / `OFFLINE` |
| `Beamer_Raw` / `Beamer_RawResponse` | String | Any projector command and the raw replies |
| `Beamer_Error` / `Beamer_ErrorCommand` | String | Last error message and the command that caused it |
| `Beamer_Refresh` | String | Any command: poll all values now |

## Rules (`automation/python/benq.py`)

| Rule | Function |
|---|---|
| `beamer_presentation` | `Beamer_Presentation` ON: power on, source, picture mode, blank off; OFF: power off |
| `beamer_power_sync` | Keeps `Beamer_Presentation` in sync when the projector is switched with the remote |
| `beamer_lamp_hours` | Sets `Beamer_LampWarning` when `LAMP_WARNING_HOURS` is reached |
| `beamer_connection` | Logs when the projector becomes unreachable / reachable |
| `beamer_error` | Logs projector errors (`Block item`, …) |
| `beamer_auto_off` | Switches the projector off at 22:00 if it was left on |

Commands may be sent immediately after power on: `benq_projector.py` queues them until the warm-up phase is over, so the rules need no timers.
