"""
BenQ MH856UST - openHAB rules (Python Scripting add-on, openHAB 5)

Place this file in $OPENHAB_CONF/automation/python/, e.g.
    /etc/openhab/automation/python/benq.py

Notes:
  * Commands can be sent right after power ON: benq_projector.py queues them
    until the warm-up phase is over, so no timers are needed here.
  * Adjust the constants below to your setup.
"""
from openhab import rule, Registry
from openhab.triggers import when

DEFAULT_SOURCE = "HDMI"         # source selected by the presentation mode
DEFAULT_PICTURE_MODE = "PRESET"
LAMP_WARNING_HOURS = 3500       # depends on lamp mode, see the projector manual
AUTO_OFF_ENABLED = True         # switch the projector off in the evening


def _state(item_name):
    return str(Registry.getItem(item_name).getState())


# --------------------------------------------------------------------------
# Presentation mode: one switch for power, source and picture mode
# --------------------------------------------------------------------------
@rule()
@when("Item Beamer_Presentation received command")
def beamer_presentation(module, input):
    command = input["event"].getItemCommand().toString()
    if command == "ON":
        beamer_presentation.logger.info(
            f"Presentation mode on: power ON, source {DEFAULT_SOURCE}, picture mode {DEFAULT_PICTURE_MODE}")
        Registry.getItem("Beamer_Power").sendCommand("ON")
        # queued by benq_projector.py until the warm-up is finished
        Registry.getItem("Beamer_Source").sendCommand(DEFAULT_SOURCE)
        Registry.getItem("Beamer_PictureMode").sendCommand(DEFAULT_PICTURE_MODE)
        Registry.getItem("Beamer_Blank").sendCommand("OFF")
    else:
        beamer_presentation.logger.info("Presentation mode off: power OFF")
        Registry.getItem("Beamer_Power").sendCommand("OFF")


@rule()
@when("Item Beamer_Power changed")
def beamer_power_sync(module, input):
    """Keep the presentation switch in sync if the projector is switched with the remote."""
    state = input["event"].getItemState().toString()
    if state in ("ON", "OFF") and _state("Beamer_Presentation") != state:
        Registry.getItem("Beamer_Presentation").postUpdate(state)


# --------------------------------------------------------------------------
# Lamp hours warning
# --------------------------------------------------------------------------
@rule()
@when("Item Beamer_LampHours changed")
def beamer_lamp_hours(module, input):
    try:
        hours = int(float(input["event"].getItemState().toString()))
    except ValueError:
        return
    due = "ON" if hours >= LAMP_WARNING_HOURS else "OFF"
    if _state("Beamer_LampWarning") != due:
        Registry.getItem("Beamer_LampWarning").postUpdate(due)
        if due == "ON":
            beamer_lamp_hours.logger.warn(
                f"Projector lamp has {hours} h (limit {LAMP_WARNING_HOURS} h), replacement due")


# --------------------------------------------------------------------------
# Monitoring
# --------------------------------------------------------------------------
@rule()
@when("Item Beamer_Connection changed")
def beamer_connection(module, input):
    state = input["event"].getItemState().toString()
    if state == "OFFLINE":
        beamer_connection.logger.warn("Projector not reachable via LAN (TCP 8000)")
    elif state == "ONLINE":
        beamer_connection.logger.info("Projector reachable again")


@rule()
@when("Item Beamer_Error received update")
def beamer_error(module, input):
    beamer_error.logger.info(
        f"Projector error: {_state('Beamer_ErrorCommand')} -> {input['event'].getItemState().toString()}")


# --------------------------------------------------------------------------
# Automatic power off in the evening (22:00) if the projector was left on
# --------------------------------------------------------------------------
@rule()
@when("Time cron 0 0 22 * * ?")
def beamer_auto_off(module, input):
    if AUTO_OFF_ENABLED and _state("Beamer_Power") == "ON":
        beamer_auto_off.logger.info("Projector still on at 22:00, switching off")
        Registry.getItem("Beamer_Power").sendCommand("OFF")
