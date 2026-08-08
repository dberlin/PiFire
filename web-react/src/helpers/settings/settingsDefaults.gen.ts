/* eslint-disable */
// GENERATED from schema/settings.schema.json — do not edit. Regenerate: bun run gen:types

export const SETTINGS_DEFAULTS = {
  "controller": {
    "config": {},
    "selected": "pid"
  },
  "cycle_data": {
    "HoldCycleTime": 25,
    "LidOpenDetectEnabled": false,
    "LidOpenPauseTime": 60,
    "LidOpenThreshold": 15,
    "PMode": 2,
    "SmokeOffCycleTime": 45,
    "SmokeOnCycleTime": 15,
    "u_max": 0.9,
    "u_min": 0.1
  },
  "dashboard": {
    "current": "Default",
    "dashboards": {}
  },
  "display": {
    "config": {},
    "selected": "none",
    "sleep_timeout": 300
  },
  "globals": {
    "augerrate": 0.3,
    "boot_to_monitor": false,
    "debug_mode": false,
    "disp_rotation": 0,
    "ext_data": false,
    "first_time_setup": true,
    "grill_name": "",
    "prime_ignition": false,
    "python_exec": ".venv/bin/python",
    "units": "F",
    "updated_message": false,
    "uv": true,
    "venv": true
  },
  "history_page": {
    "autorefresh": "on",
    "clearhistoryonstart": true,
    "datapoints": 10000,
    "fidelity_degrees": 2,
    "minutes": 15,
    "probe_config": {}
  },
  "keep_warm": {
    "s_plus": false,
    "temp": 165
  },
  "modules": {
    "display": "none",
    "dist": "none",
    "grillplat": "prototype"
  },
  "notify_services": {
    "apprise": {
      "enabled": false,
      "locations": []
    },
    "ifttt": {
      "APIKey": "",
      "enabled": false
    },
    "influxdb": {
      "bucket": "",
      "enabled": false,
      "org": "",
      "token": "",
      "url": ""
    },
    "mqtt": {
      "broker": "homeassistant.local",
      "enabled": false,
      "homeassistant_autodiscovery_topic": "homeassistant",
      "id": "PiFire",
      "password": "",
      "port": "1883",
      "update_sec": "30",
      "username": ""
    },
    "onesignal": {
      "app_id": "",
      "devices": {},
      "enabled": false,
      "uuid": ""
    },
    "pushbullet": {
      "APIKey": "",
      "PublicURL": "",
      "enabled": false
    },
    "pushover": {
      "APIKey": "",
      "PublicURL": "",
      "UserKeys": "",
      "enabled": false
    },
    "wled": {
      "device_address": "wled.local",
      "enabled": false,
      "event_presets": {
        "Grill_Error": 1,
        "Pellet_Level_Low": 1,
        "Recipe_Next": 1,
        "Temp_Achieved": 1,
        "Timer_Expired": 1
      },
      "mode_presets": {
        "Hold": 1,
        "Prime": 1,
        "Reignite": 1,
        "Shutdown": 1,
        "Smoke": 1,
        "Startup": 1,
        "Stop": 1
      },
      "notify_duration": 120,
      "profile_numbers": {
        "booting": 201,
        "cooking": 203,
        "cooldown": 204,
        "error_fault": 210,
        "idle": 200,
        "low_pellets": 208,
        "night_mode": 211,
        "overshoot_alarm": 206,
        "preheat": 202,
        "probe_alarm": 207,
        "target_reached": 205,
        "timer_done": 209
      },
      "suggested_config": {
        "cooking_color": "blue",
        "idle_brightness": 20,
        "led_count": 6,
        "night_mode": false
      },
      "use_profiles": true,
      "use_suggested_presets": false
    }
  },
  "pelletlevel": {
    "empty": 22,
    "full": 4,
    "warning_enabled": true,
    "warning_level": 25,
    "warning_time": 20
  },
  "platform": {
    "buttonslevel": "HIGH",
    "current": "custom",
    "dc_fan": false,
    "devices": {
      "display": {
        "dc": 24,
        "led": 5,
        "rst": 25
      },
      "distance": {
        "address": null,
        "device": "/dev/ttyACM0",
        "echo": 27,
        "i2c_bus": {
          "kind": "basic"
        },
        "trig": 23
      },
      "input": {
        "down_dt": 20,
        "enter_sw": 21,
        "up_clk": 16
      }
    },
    "fan_controller": {
      "address": "0x4c",
      "chip": "emc2101",
      "i2c_bus": {
        "kind": "basic"
      }
    },
    "ft232h": {
      "url": "1"
    },
    "inputs": {
      "selector": 17,
      "shutdown": 17
    },
    "mcp2221": {
      "serial": ""
    },
    "numato": {
      "baudrate": 921600,
      "device": "/dev/ttyACM0"
    },
    "outputs": {
      "auger": 14,
      "dc_fan": 26,
      "fan": 15,
      "igniter": 18,
      "power": 4,
      "pwm": 13
    },
    "real_hw": true,
    "standalone": true,
    "system": {
      "1WIRE": null,
      "SPI0": {
        "CE0": 8,
        "CE1": 7
      }
    },
    "system_type": "prototype",
    "triggerlevel": "HIGH"
  },
  "probe_settings": {
    "probe_map": {
      "probe_devices": [],
      "probe_info": []
    },
    "probe_profiles": {}
  },
  "pwm": {
    "frequency": 25000,
    "max_duty_cycle": 100,
    "min_duty_cycle": 20,
    "profiles": [
      {
        "duty_cycle": 20
      },
      {
        "duty_cycle": 35
      },
      {
        "duty_cycle": 50
      },
      {
        "duty_cycle": 75
      },
      {
        "duty_cycle": 100
      }
    ],
    "pwm_control": false,
    "temp_range_list": [
      3,
      7,
      10,
      15
    ],
    "update_time": 10
  },
  "recipe": {
    "probe_map": {
      "food": [],
      "primary": ""
    }
  },
  "safety": {
    "allow_manual_changes": false,
    "manual_override_time": 30,
    "maxstartuptemp": 100,
    "maxtemp": 550,
    "minstartuptemp": 75,
    "reigniteretries": 1,
    "startup_check": true
  },
  "schema_version": 8,
  "shutdown": {
    "auto_power_off": false,
    "shutdown_duration": 240
  },
  "smoke_plus": {
    "duty_cycle": 75,
    "enabled": false,
    "fan_ramp": false,
    "max_temp": 220,
    "min_temp": 160,
    "off_time": 5,
    "on_time": 5
  },
  "startup": {
    "duration": 240,
    "prime_on_startup": 0,
    "pwm_duty_cycle": 100,
    "smartstart": {
      "enabled": false,
      "exit_temp": 120,
      "profiles": [
        {
          "augerontime": 15,
          "p_mode": 0,
          "startuptime": 360
        },
        {
          "augerontime": 15,
          "p_mode": 1,
          "startuptime": 360
        },
        {
          "augerontime": 15,
          "p_mode": 3,
          "startuptime": 240
        },
        {
          "augerontime": 15,
          "p_mode": 5,
          "startuptime": 240
        }
      ],
      "temp_range_list": [
        60,
        80,
        90
      ]
    },
    "start_to_mode": {
      "after_startup_mode": "Smoke",
      "primary_setpoint": 165,
      "start_to_hold_prompt": false
    },
    "startup_exit_temp": 0
  }
} as const;
