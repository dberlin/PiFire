"""
==============================================================================
 PiFire Board Configuration Tool
==============================================================================

 Description: Tool to configure the board settings based on the settings.json
  configuration.  Currently supports only Raspberry Pi based platforms.

==============================================================================
"""

"""
==============================================================================
 Imported Modules
==============================================================================
"""

import argparse
import logging
import os
import json
import subprocess

from common.common import read_settings

"""
==============================================================================
 Globals
==============================================================================
"""

log_level = logging.DEBUG

"""
==============================================================================
 Main Functions
==============================================================================
"""


def set_pwm_gpio():
    result = "Setting the PWM pin: "
    changed = False
    try:
        settings = read_settings()
        pin = settings["platform"]["outputs"]["pwm"]
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all" or system_type == "prototype":
            # "dtoverlay=pwm-2chan,pin=13,func=4"
            pin = int(pin) if pin != None else None
            msg, changed = rpi_config_write("dtoverlay", "pwm-2chan", add_config={"func": "4"}, pin=pin, pin_type="pin")
            result += msg
        else:
            result += "NA - No system defined"
    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def set_onewire_gpio():
    result = "Setting the 1Wire pin: "
    changed = False
    try:
        settings = read_settings()
        pin = settings["platform"]["system"]["1WIRE"]
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all" or system_type == "prototype":
            # "dtoverlay=w1-gpio,pin=6"
            pin = int(pin) if pin != None else None
            msg, changed = rpi_config_write("dtoverlay", "w1-gpio", pin=pin, pin_type="gpiopin")
            result += msg
        else:
            result += "NA - No system defined"
    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def set_backlight():
    result = "Enabling Backlight Control for DSI Touch Display: "
    # A udev rule, not a config.txt/device-tree change -- never requires a reboot.
    changed = False
    try:
        settings = read_settings()
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all":
            lines = [
                'SUBSYSTEM=="backlight",RUN+="/bin/chmod 666 /sys/class/backlight/%k/brightness /sys/class/backlight/%k/bl_power"\n'
            ]
            file = "/etc/udev/rules.d/backlight-permissions.rules"
            result += create_file(file, lines)
    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def enable_spi():
    result = "Enabling SPI: "
    changed = False
    try:
        settings = read_settings()
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all" or system_type == "prototype":
            # "dtparam=spi=on"
            msg, changed = rpi_config_write("dtparam", "spi")
            result += msg
        else:
            result += "NA - No system defined"
    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def enable_i2c():
    result = "Enabling I2C: "
    changed = False
    try:
        settings = read_settings()
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all":
            # dtparam=i2c_arm=on
            msg, dtparam_changed = rpi_config_write("dtparam", "i2c_arm")
            result += msg
            # To enable userspace access to I2C ensure that /etc/modules contains "i2c-dev"
            msg, modules_changed = append_file("/etc/modules", "i2c-dev\n")
            result += msg
            changed = dtparam_changed or modules_changed
        else:
            result += "NA - No system defined"

    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def set_i2c_speed(baud=100000):
    result = f"Setting I2C speed ({baud} Baud): "
    changed = False
    try:
        settings = read_settings()
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all" or system_type == "prototype":
            # dtparam=i2c_arm_baudrate=100000
            msg, changed = rpi_config_write("dtparam", "i2c_arm_baudrate", param=baud)
            result += msg
        else:
            result += "NA - No system defined"

    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


def enable_gpio_shutdown():
    result = "Enabling the GPIO Shutdown pin: "
    changed = False
    try:
        settings = read_settings()
        pin = settings["platform"]["inputs"]["shutdown"]
        system_type = settings["platform"]["system_type"]
    except:
        result += "FAILED (error getting settings.json data) "
        return result, changed

    try:
        if system_type == "raspberry_pi_all" or system_type == "prototype":
            # dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up
            add_config = {"active_low": "1", "gpio_pull": "up"}
            pin = int(pin) if pin != None else None
            msg, changed = rpi_config_write(
                "dtoverlay", "gpio-shutdown", add_config=add_config, pin=pin, pin_type="gpio_pin"
            )
            result += msg
        else:
            result += "NA - No system defined"
    except:
        result += "FAILED (error making the configuration change) "

    return result, changed


"""
==============================================================================
 Supporting Functions
==============================================================================
"""


def rpi_config_write(config_type, feature, add_config={}, pin=0, param="", pin_type="gpio_pin"):
    result = "SUCCESS"
    changed = False
    """ Check OS version, so we can get the correct location of config.txt """
    os_info = get_os_info()
    version = os_info.get("VERSION_ID", None)
    if version in ["12", "13"]:
        """ Version 12 Bookworm or Version 13 Trixie """
        config_filename = "/boot/firmware/config.txt"
    elif version == "11":
        """ Version 11 Bullseye """
        config_filename = "/boot/config.txt"
    else:
        """ Test Mode """
        config_filename = "./local/config.txt"

    """ Modify the configuration file """
    try:
        """ Open the configuration file """
        with open(config_filename, "r+") as config_txt:
            config_data = config_txt.readlines()

        original_config_data = list(config_data)

        # Remove old pwm overlay lines if adding new pwm-2chan overlay
        if config_type == "dtoverlay" and feature == "pwm-2chan":
            new_config_data = []
            for line in config_data:
                # Remove lines like: dtoverlay=pwm,pin=*,func=4 (with or without comments)
                if line.strip().startswith("dtoverlay=pwm,") and "func=4" in line:
                    continue  # skip this line
                new_config_data.append(line)
            config_data = new_config_data

        """ Look for the configuration line if it exists already """
        found = False
        for index in range(0, len(config_data)):
            if config_type in config_data[index] and feature in config_data[index]:
                found = True
                # Check for leading hashtag and remove
                config_line = remove_hashtag(config_data[index])

                # If the pin is marked as disabled / None, then comment out the line
                if pin == None:
                    config_data[index] = f"#{config_line}"
                else:
                    # Remove the preceding configuration type
                    config_line = config_line.replace(f"{config_type}=", "")

                    # Get dictionary of the components
                    config_dict = parse_config_line(config_line)

                    # For dtparams, turn on feature
                    if config_type == "dtparam":
                        if param == "":
                            config_dict[feature] = "on"
                        else:
                            config_dict[feature] = param

                    # For dtoverlay, edit gpio-pin and additional features
                    elif config_type == "dtoverlay":
                        # Modify pin number
                        if pin > 0:
                            for noun in ["gpio-pin", "gpiopin", "gpio_pin", "pin"]:
                                if noun in config_dict[feature].keys():
                                    config_dict[feature].pop(noun, None)
                                    config_dict[feature][pin_type] = str(pin)

                        # If function, add function number
                        if add_config != {}:
                            for key, value in add_config.items():
                                config_dict[feature][key] = value

                    """ Create the modified configuration line """
                    config_data[index] = build_config_line(config_type, config_dict)
                break

        if not found and pin is not None:
            config_dict = {}
            if config_type == "dtoverlay":
                config_dict[feature] = {}
                config_dict[feature][pin_type] = pin
                if add_config != {}:
                    for key, value in add_config.items():
                        config_dict[feature][key] = value
            elif config_type == "dtparam":
                config_dict[feature] = "on"

            config_data.append(build_config_line(config_type, config_dict))

        changed = config_data != original_config_data

        """ Write all data back to the file, only if something actually changed --
		this is what makes re-running the wizard with identical settings correctly
		report no reboot needed. """
        if changed:
            with open(config_filename, "w") as config_txt:
                config_txt.writelines(config_data)

    except:
        result = "FAILED "
        changed = False

    return result, changed


def parse_config_line(config_line):
    """
    (Format of the configuration line adheres to the Raspberry Pi config.txt formatting rules)
    This function parses a configuration line into component options.
    This function assumes that the preceding configuration option has been removed (i.e. dtparam=, dtoverlay=, etc.).
    This function removes comments.

    Args:
            config_line: The configuration line to be parsed

    Returns:
            Dictionary of configuration keys and values, sub-keys/values
    """
    if "#" in config_line:
        config_line = config_line.split("#")[0]

    split_line = config_line.split(",")
    config_dict = {}
    feature = None

    for item in split_line:
        item_split = item.split("=")
        item_dict = {}
        if len(item_split) > 1:
            if feature is not None:
                config_dict[feature][item_split[0]] = item_split[1]
            else:
                config_dict[item_split[0]] = item_split[1]
        else:
            config_dict[item_split[0]] = {}
            feature = item_split[0]
    return config_dict


def build_config_line(config_type, config_dict):
    """
    (Format of the configuration line adheres to the Raspberry Pi config.txt formatting rules)
    This function parses a configuration dictionary into a configuration string/line.

    Args:
            config_type: String of the type 'dtparam', 'dtoverlay', etc.
            config_dict: The configuration dictionary to be parsed

    Returns:
            String of the configuration line
    """

    config_line = f"{config_type}="
    comma = False
    for key, value in config_dict.items():
        if comma:
            config_line += ","
        if isinstance(value, dict):
            config_line += f"{key}"
            for subkey, subvalue in value.items():
                if subvalue is not None:
                    config_line += f",{subkey}={subvalue}"
                else:
                    config_line += f",{subkey}"
        else:
            config_line += f"{key}={value}"
        comma = True

    config_line += "  # Modified by PiFire Board Configuration Utility"
    config_line += "\n"

    return config_line


def create_file(filename, lines):
    result = f"\n - Attempting to write data to {filename}: "
    try:
        with open(filename, "w") as file:
            for line in lines:
                file.write(line)
        result += f" SUCCESS (creating file {filename}) "
    except:
        result += f" FAILED (creating file {filename}) "
    return result


def append_file(filename, lines):
    result = f"\n - Attempting to append data to {filename}: "
    if isinstance(lines, str):
        lines = [lines]
    changed = False
    try:
        try:
            with open(filename, "r") as file:
                existing_lines = file.read().splitlines()
        except FileNotFoundError:
            existing_lines = []

        missing_lines = [line for line in lines if line.rstrip("\n") not in existing_lines]

        if missing_lines:
            with open(filename, "a+") as file:
                for line in missing_lines:
                    file.write(line)
            changed = True
            result += f" SUCCESS (appending file {filename}) "
        else:
            result += f" SUCCESS (no change, already present in {filename}) "
    except:
        result += f" FAILED (appending file {filename}) "
    return result, changed


def remove_hashtag(text):
    """Removes a preceding hashtag character from a string if it exists,
    including any leading spaces.

    Args:
            text: The string to process.

    Returns:
            The string with the hashtag and leading spaces removed if it existed,
            otherwise the original string.
    """
    if text:
        # Strip leading spaces
        stripped_text = text.lstrip()
        if stripped_text and stripped_text[0] == "#":
            return stripped_text[1:]
        else:
            return text
    else:
        return text


def read_generic_json(filename):
    try:
        json_file = os.fdopen(os.open(filename, os.O_RDONLY))
        json_data = json_file.read()
        dictionary = json.loads(json_data)
        json_file.close()
    except:
        dictionary = {}
        event = f"An error occurred loading {filename} "
        logger.error(event)
    return dictionary


def write_generic_json(dictionary, filename):
    try:
        json_data_string = json.dumps(dictionary, indent=2, sort_keys=True)
        with open(filename, "w") as json_file:
            json_file.write(json_data_string)
    except:
        event = f"Error writing generic json file ({filename})"
        logger.error(event)


def create_logger(
    name, filename="./logs/pifire.log", messageformat="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
):
    """Create or Get Existing Logger"""
    logger = logging.getLogger(name)
    """ 
		If the logger does not exist, create one. Else return the logger. 
		Note: If the a log-level change is needed, the developer should directly set the log level on the logger, instead of using 
		this function.  
	"""
    if not logger.hasHandlers():
        logger.setLevel(level)
        formatter = logging.Formatter(fmt=messageformat, datefmt="%Y-%m-%d %H:%M:%S %z")
        # datefmt='%Y-%m-%d %H:%M:%S'
        handler = logging.FileHandler(filename)
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger


def get_os_info(filepath="os_info.json", loggername="events"):
    """Get operating system information"""
    os_info = {}

    try:
        # Get OS release info
        with open("/etc/os-release", "r") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    # Remove quotes if present
                    value = value.strip('"')
                    os_info[key] = value

        # Get architecture using uname -m
        arch = subprocess.check_output(["/bin/uname", "-m"]).decode().strip()
        os_info["ARCHITECTURE"] = arch

        # Save to JSON file
        write_generic_json(os_info, filepath)
        return os_info

    except Exception as e:
        event = f"Error getting OS info: {str(e)}"
        logger.error(event)
        return os_info


def _print_results_and_reboot_flag(results, reboot_flags, logger):
    """Print the human-readable results block, then a final REBOOT_REQUIRED=<bool>
    sentinel line that wizard.py's command-execution loop parses to decide whether a
    reboot is actually needed. Returns the aggregated bool."""
    if len(results) == 0:
        print("No Arguments Found. Use --help to see available arguments")
    else:
        print("Results:")
        for item in results:
            print(f" - {item}")
            logger.info(f"{item}")

    reboot_required = any(reboot_flags)
    sentinel = f"REBOOT_REQUIRED={str(reboot_required).lower()}"
    print(sentinel)
    logger.info(sentinel)
    return reboot_required


"""
==============================================================================
 Main
==============================================================================
"""
if __name__ == "__main__":
    logger = create_logger("board_config", filename="./logs/board_config.log", level=log_level)

    print("PiFire Board Configuration Tool v1.0.1")
    print("Ben Parmeter - 2025 - MIT License")
    print(" --help, -h for command details\n")

    parser = argparse.ArgumentParser(
        description="This tool performs board specific configuration for certain system level features.  Use the below options to enable/disable and configure these features.  System settings are read from the settings.json file."
    )
    parser.add_argument("-pwm", "--pwm", action="store_true", required=False, help="Set PWM GPIO.")
    parser.add_argument("-ow", "--onewire", action="store_true", required=False, help="Set 1Wire GPIO.")
    parser.add_argument("-bl", "--backlight", action="store_true", required=False, help="Enable backlight permissions.")
    parser.add_argument(
        "-ov", "--osversion", action="store_true", required=False, help="Get OS Version. Saves to os_info.json."
    )
    parser.add_argument("-s", "--spi", action="store_true", required=False, help="Enable SPI.")
    parser.add_argument("-i", "--i2c", action="store_true", required=False, help="Enable I2C.")
    parser.add_argument(
        "-is",
        "--i2cspeed",
        metavar="BAUD",
        type=int,
        required=False,
        help="Set the I2C baud rate. BAUD should be an integer, i.e. 100000",
    )
    parser.add_argument("-gs", "--gpioshutdown", action="store_true", required=False, help="Enable GPIO shutdown.")

    args = parser.parse_args()

    results = []
    reboot_flags = []

    if args.pwm:
        msg, changed = set_pwm_gpio()
        results.append(msg)
        reboot_flags.append(changed)

    if args.onewire:
        msg, changed = set_onewire_gpio()
        results.append(msg)
        reboot_flags.append(changed)

    if args.backlight:
        msg, changed = set_backlight()
        results.append(msg)
        reboot_flags.append(changed)

    if args.spi:
        msg, changed = enable_spi()
        results.append(msg)
        reboot_flags.append(changed)

    if args.i2c:
        msg, changed = enable_i2c()
        results.append(msg)
        reboot_flags.append(changed)

    if args.i2cspeed:
        msg, changed = set_i2c_speed(baud=args.i2cspeed)
        results.append(msg)
        reboot_flags.append(changed)

    if args.gpioshutdown:
        msg, changed = enable_gpio_shutdown()
        results.append(msg)
        reboot_flags.append(changed)

    if args.osversion:
        os_info = get_os_info(loggername="board_config")
        event = "OS Version Information: "
        results.append(event)
        for key, value in os_info.items():
            event = f"   {key} : {value}"
            results.append(event)

    _print_results_and_reboot_flag(results, reboot_flags, logger)
