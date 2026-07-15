"""
Imported Libraries
"""

import math

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from display.flexrect import Rect

"""
The following is a map of the FlexObject types to their respective classes.
"""

FlexObject_TypeMap = {
    "gauge": "GaugeCircle",
    "gauge_compact": "GaugeCompact",
    "mode_bar": "ModeBar",
    "control_panel": "ControlPanel",
    "status_icon": "StatusIcon",
    "menu_icon": "MenuIcon",
    "menu": "MenuGeneric",
    "qrcode": "MenuQRCode",
    "input_number": "InputNumber",
    "input_number_simple": "InputNumberSimple",
    "timer": "TimerStatus",
    "alert": "AlertMessage",
    "button": "FlexButton",
    "splus_control": "SPlusStatus",
    "p_mode_control": "PModeStatus",
    "hopper_status": "HopperStatus",
    "probe_card": "ProbeCard",
    "gauge_ember": "GaugeEmber",
    "system_card": "SystemCard",
    "duty_pill": "DutyPill",
    "cook_time_bar": "CookTimeBar",
    "hopper_vertical": "HopperVertical",
    "header_bar": "HeaderBar",
    "button_row": "ButtonRow",
}

"""
Accent palette resolver for flex dashboard themes
"""


def _hex(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 255)


_ACCENTS = {
    "Ember": {
        "accent": _hex("ff8a2b"),
        "glow": _hex("ff7a1a"),
        "arc": [_hex("ff5e1a")[:3], _hex("ff8a2b")[:3], _hex("ffc24b")[:3]],
    },
    "Ice": {
        "accent": _hex("3cc7d0"),
        "glow": _hex("2ec5d3"),
        "arc": [_hex("1f9fb8")[:3], _hex("35c7d0")[:3], _hex("7ef0d2")[:3]],
    },
    "Crimson": {
        "accent": _hex("ff6a5a"),
        "glow": _hex("ff5a4d"),
        "arc": [_hex("e11d48")[:3], _hex("ff5a4d")[:3], _hex("ff9f43")[:3]],
    },
}


def resolve_accent(name):
    return _ACCENTS.get(name, _ACCENTS["Ember"])


"""
Display Flex Object Class Definition 
"""


class FlexObject:
    def __init__(self, objectType, objectData, background):
        self.objectType = objectType
        self.objectData = objectData
        self.objectState = {"animation_active": False, "animation_start": False}
        self.background = background
        self._init_background()
        self.update_object_data(objectData)

    def _init_background(self):
        if self.background is not None:
            """ saves the slice of background image in PIL to be used for background """
            crop_region = (
                self.objectData["position"][0],
                self.objectData["position"][1],
                self.objectData["position"][0] + self.objectData["size"][0],
                self.objectData["position"][1] + self.objectData["size"][0],
            )
            self.objectBG = self.background.crop(crop_region)
        else:
            self.objectBG = Image.new("RGBA", self.objectData["size"])

    def update_object_data(self, updated_objectData=None):
        """If object was changed, update the objectData with the new values"""
        if updated_objectData is not None:
            if updated_objectData["animation_enabled"]:
                self.objectState["animation_active"] = True
                self.objectState["animation_start"] = True
                self.objectState["animation_lastData"] = {}
                for key, value in self.objectData.items():
                    self.objectState["animation_lastData"][key] = value
            for key, value in updated_objectData.items():
                self.objectData[key] = value

        """ If the object has input, process the input """
        self._process_input()

        """ If the object has animation, process the animation """
        if self.objectState["animation_active"]:
            self.objectCanvas = self._animate_object()
        else:
            self.objectCanvas = self._draw_object()

        """ Define the touch area - if applicable """
        self._define_touch_areas()

        return self.objectCanvas

    def get_object_data(self):
        current_objectData = dict(self.objectData)
        return current_objectData

    def get_object_canvas(self):
        return self.objectCanvas

    def get_object_state(self):
        current_objectState = self.objectState.copy()
        return current_objectState

    def _draw_text(self, text, font_name, font_point_size, color, rect=False, bg_fill=None):
        font = ImageFont.truetype(font_name, font_point_size)
        font_bbox = font.getbbox(str(text))  # Grab the width of the text
        font_canvas_size = (font_bbox[2], font_bbox[3])
        font_canvas = Image.new("RGBA", font_canvas_size)
        font_draw = ImageDraw.Draw(font_canvas)
        font_draw.text((0, 0), str(text), font=font, fill=color)
        font_canvas = font_canvas.crop(font_canvas.getbbox())
        if rect:
            font_canvas_size = font_canvas.size
            rect_canvas_size = (font_canvas_size[0] + 16, font_canvas_size[1] + 16)
            rect_canvas = Image.new("RGBA", rect_canvas_size)
            if bg_fill is not None:
                rect_canvas.paste(bg_fill, (0, 0) + rect_canvas.size)
            rect_draw = ImageDraw.Draw(rect_canvas)
            rect_draw.rounded_rectangle(
                (0, 0, rect_canvas_size[0], rect_canvas_size[1]), radius=8, outline=color, width=3
            )
            rect_canvas.paste(font_canvas, (8, 8), font_canvas)
            return rect_canvas
        elif bg_fill is not None:
            output_canvas = Image.new("RGBA", font_canvas.size)
            output_canvas.paste(bg_fill, (0, 0) + font_canvas.size)
            output_canvas.paste(font_canvas, (0, 0), font_canvas)
            return output_canvas
        else:
            return font_canvas

    def _create_icon(self, charid, font_size, color, bg_fill=None):
        # Get font and character size
        font = ImageFont.truetype("./static/font/FA-Free-Solid.otf", font_size)
        # Create canvas
        font_bbox = font.getbbox(charid)  # Grab the width of the text
        font_width = font_bbox[2]
        font_height = font_bbox[3]

        icon_canvas = Image.new("RGBA", (font_width, font_height))
        if bg_fill is not None:
            icon_canvas.paste(bg_fill, (0, 0) + icon_canvas.size)

        # Create drawing object
        draw = ImageDraw.Draw(icon_canvas)
        draw.text((0, 0), charid, font=font, fill=color)
        icon_canvas = icon_canvas.crop(icon_canvas.getbbox())
        return icon_canvas

    def _define_touch_areas(self):
        """
        Defines the touch area for the object.  This object may be
        overridden by subclasses.
        """
        touch_area = Rect(self.objectData["position"], self.objectData["size"])
        # Create button rectangle / touch area and append to list
        self.objectData["touch_areas"] = [touch_area]

    def _scale_touch_area(self, rectangle, screen_size_old, screen_size_new):
        """Scales a rectangle size and position according to the screen size change.

        Args:
            rectangle: A tuple of (x, y, width, height).
            screen_size_old: The old screen size.
            screen_size_new: The new screen size.

        Returns:
            A tuple of (x, y, width, height) of the scaled rectangle.
        """
        x, y, width, height = rectangle
        scaled_width = int(width * (screen_size_new[0] / screen_size_old[0]))
        scaled_height = int(height * (screen_size_new[1] / screen_size_old[1]))
        xlated_x = int(x * (screen_size_new[0] / screen_size_old[0]))
        xlated_y = int(y * (screen_size_new[1] / screen_size_old[1]))
        return (xlated_x, xlated_y, scaled_width, scaled_height)

    def _transform_touch_area(self, touch_area, origin):
        """Transforms the touch area to the correct place on the screen."""
        return (touch_area[0] + origin[0], touch_area[1] + origin[1], touch_area[2], touch_area[3])

    def _draw_object(self):
        """
        This function will draw the object and return the object canvas.
        The inheriting function will override this function.
        """
        return self.objectCanvas

    def _animate_object(self):
        """
        This function will animate the object and return the object canvas.
        The inheriting function will override this function.
        """
        return self._draw_object()

    def _process_input(self):
        """
        This function will process the input and return the object canvas.
        The inheriting function will override this function.
        """
        pass


class GaugeCircle(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        """
        Draws a gauge on an image canvas based on the object's data.

        Returns:
            Image: The image canvas with the gauge drawn on it.
        """
        output_size = self.objectData["size"]

        size = (400, 400)

        # Create drawing object
        gauge = Image.new("RGBA", (size[0], size[1]))
        draw = ImageDraw.Draw(gauge)

        # Get coordinates for the gauge arcs
        coords = (
            0 + int(size[0] * 0.05),
            0 + int(size[1] * 0.05),
            size[0] - int(size[0] * 0.05),
            size[1] - int(size[0] * 0.05),
        )

        # Draw Arc for Temperature (Percent)
        start_rad = 135

        # Determine the radian (0-270) for the current temperature
        temp_rad = 270 * min(self.objectData["temps"][0] / self.objectData["max_temp"], 1)
        end_rad = start_rad + temp_rad

        # Draw Temperature Arc
        draw.arc(coords, start=start_rad, end=end_rad, fill=self.objectData["fg_color"], width=30)

        # Draw Background Arc
        draw.arc(coords, start=end_rad, end=45, fill=self.objectData["bg_color"], width=30)

        # Current Temperature (Large Centered)
        cur_temp = str(self.objectData["temps"][0])[:5]
        if len(cur_temp) < 5:
            font_point_size = round(size[1] * 0.3)  # Font size as a ratio of the object size
        else:
            font_point_size = round(size[1] * 0.25)  # Font size as a ratio of the object size
        font = ImageFont.truetype(self.objectData["font"], font_point_size)
        font_bbox = font.getbbox(cur_temp)  # Grab the width of the text
        font_width = font_bbox[2] - font_bbox[0]
        font_height = font_bbox[3] - font_bbox[1]
        label_x = (size[0] // 2) - (font_width // 2)
        label_y = (size[1] // 2) - (font_height // 1.1)
        label_origin = (label_x, label_y)

        draw.text(label_origin, cur_temp, font=font, fill=self.objectData["fg_color"])

        # Units Label (Small Centered)
        unit_label = f"{self.objectData['units']}°"
        font_point_size = font_point_size = round((size[1] * 0.35) / 4)  # Font size as a ratio of the object size
        font = ImageFont.truetype(self.objectData["font"], font_point_size)
        font_bbox = font.getbbox(self.objectData["units"])  # Grab the width of the text
        font_width = font_bbox[2] - font_bbox[0]
        font_height = font_bbox[3] - font_bbox[1]

        label_x = (size[0] // 2) - (font_width // 2)
        label_y = round((size[1] * 0.60))
        label_origin = (label_x, label_y)
        draw.text(label_origin, unit_label, font=font, fill=self.objectData["fg_color"])

        # Gauge Label

        # Gauge Label Text
        if len(self.objectData["label"]) > 7:
            label_displayed = self.objectData["label"][0:7]
        else:
            label_displayed = self.objectData["label"]
        font_point_size = round((size[1] * 0.55) / 4)  # Font size as a ratio of the object size
        font = ImageFont.truetype(self.objectData["font"], font_point_size)
        font_bbox = font.getbbox(label_displayed)  # Grab the width of the text
        font_width = font_bbox[2] - font_bbox[0]
        font_height = font_bbox[3] - font_bbox[1]
        # print(f'Font bbox= {font_bbox}')

        label_x = (size[0] // 2) - (font_width // 2)
        label_y = round((size[1] * 0.75))
        label_origin = (label_x, label_y)
        draw.text(label_origin, label_displayed, font=font, fill=self.objectData["fg_color"])
        # Gauge Label Rectangle
        # rounded_rectangle = (label_x-6, label_y+4, label_x + font_width + 8, label_y + font_height + 16)
        rounded_rectangle = (
            label_x - 8,
            label_y + (font_bbox[1] - 8),
            label_x + font_width + 8,
            label_y + font_bbox[1] + font_height + 8,
        )
        draw.rounded_rectangle(rounded_rectangle, radius=8, outline=self.objectData["fg_color"], width=3)

        # Set Points Labels
        if self.objectData["temps"][1] > 0 and self.objectData["temps"][2] > 0:
            dual_label = 1
        else:
            dual_label = 0

        # Notify Point Label
        if self.objectData["temps"][1] > 0:
            notify_point_label = f"{self.objectData['temps'][1]}"
            font_point_size = round(
                (size[1] * (0.5 - (dual_label * 0.15))) / 4
            )  # Font size as a ratio of the object size
            font = ImageFont.truetype(self.objectData["font"], font_point_size)
            font_bbox = font.getbbox(notify_point_label)  # Grab the width of the text
            font_width = font_bbox[2] - font_bbox[0]
            font_height = font_bbox[3] - font_bbox[1]

            label_x = (size[0] // 2) - (font_width // 2) - (dual_label * ((font_width // 2) + 10))
            label_y = round((size[1] * (0.20 + (dual_label * 0.05))))
            label_origin = (label_x, label_y)
            draw.text(label_origin, notify_point_label, font=font, fill=self.objectData["np_color"])
            # Notify Point Label Rectangle
            rounded_rectangle = (
                label_x - 8,
                label_y + (font_bbox[1] - 8),
                label_x + font_width + 8,
                label_y + font_bbox[1] + font_height + 8,
            )
            # (label_x-6, label_y+2, label_x + font_width + 6, label_y + font_height + 4)
            draw.rounded_rectangle(rounded_rectangle, radius=8, outline=self.objectData["np_color"], width=3)

            # Draw Tic for notify point
            setpoint = 270 * min(self.objectData["temps"][1] / self.objectData["max_temp"], 1)
            setpoint += start_rad
            draw.arc(coords, start=setpoint - 1, end=setpoint + 1, fill=self.objectData["np_color"], width=30)

        # Set Point Label
        if self.objectData["temps"][2] > 0:
            set_point_label = f"{self.objectData['temps'][2]}"
            font_point_size = round(
                (size[1] * (0.5 - (dual_label * 0.15))) / 4
            )  # Font size as a ratio of the object size
            font = ImageFont.truetype(self.objectData["font"], font_point_size)
            font_bbox = font.getbbox(set_point_label)  # Grab the width of the text
            font_width = font_bbox[2] - font_bbox[0]
            font_height = font_bbox[3] - font_bbox[1]

            label_x = (size[0] // 2) - (font_width // 2) + (dual_label * ((font_width // 2) + 10))
            label_y = round((size[1] * (0.20 + (dual_label * 0.05))))
            label_origin = (label_x, label_y)
            draw.text(label_origin, set_point_label, font=font, fill=self.objectData["sp_color"])
            # Set Point Label Rectangle
            rounded_rectangle = (
                label_x - 8,
                label_y + (font_bbox[1] - 8),
                label_x + font_width + 8,
                label_y + font_bbox[1] + font_height + 8,
            )
            draw.rounded_rectangle(rounded_rectangle, radius=8, outline=self.objectData["sp_color"], width=3)

            # Draw Tic for set point
            setpoint = 270 * min(self.objectData["temps"][2] / self.objectData["max_temp"], 1)
            setpoint += start_rad
            draw.arc(coords, start=setpoint - 1, end=setpoint + 1, fill=self.objectData["sp_color"], width=30)

        # Create drawing object
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        gauge = gauge.resize(output_size)
        canvas.paste(gauge, (0, 0), gauge)

        return canvas

    def _animate_object(self):
        if self.objectState["animation_start"]:
            self.objectState["animation_start"] = False  # Run animation start only once
            self.objectState["animation_temps"] = self.objectData["temps"].copy()
            self.objectState["animation_temps"][0] = self.objectState["animation_lastData"]["temps"][0]
            target_temp = self.objectData["temps"][0]
            last_temp = self.objectState["animation_lastData"]["temps"][0]
            self.delta = target_temp - last_temp

            if self.delta == 0:
                self.objectState["animation_active"] = False
                self.step_value = 0
            elif self.delta > 0:
                self.step_value = int(self.delta / 3) if int(self.delta / 3) != 0 else 1
            else:
                self.step_value = int(self.delta / 3) if int(self.delta / 3) != 0 else -1

        if self.objectState["animation_temps"][0] != self.objectData["temps"][0]:
            self.objectState["animation_temps"][0] += self.step_value

            if self.objectState["animation_temps"][0] <= 0:
                self.objectState["animation_temps"][0] = self.objectData["temps"][0]
                self.objectState["animation_active"] = False  # if len(self.objectData['label']) <= 5 else True

            elif (self.delta >= 0) and (
                abs(self.objectState["animation_temps"][0]) >= abs(self.objectData["temps"][0])
            ):
                self.objectState["animation_temps"][0] = self.objectData["temps"][0]
                self.objectState["animation_active"] = False  # if len(self.objectData['label']) <= 5 else True

            elif (self.delta <= 0) and (
                abs(self.objectState["animation_temps"][0]) <= abs(self.objectData["temps"][0])
            ):
                self.objectState["animation_temps"][0] = self.objectData["temps"][0]
                self.objectState["animation_active"] = False  # if len(self.objectData['label']) <= 5 else True

        return self._draw_object()


class GaugeCompact(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 200)  # Working Canvas Size

        # Create drawing object
        gauge = Image.new("RGBA", size)
        draw = ImageDraw.Draw(gauge)

        # Gauge Background
        draw.rounded_rectangle((15, 15, size[0] - 15, size[1] - 15), radius=20, fill=(255, 255, 255, 100))

        # Draw Gauge Label on Top Portion of Box
        if len(self.objectData["label"]) > 11:
            label_displayed = self.objectData["label"][0:11]
        else:
            label_displayed = self.objectData["label"]

        gauge_label = self._draw_text(label_displayed, self.objectData["font"], 50, self.objectData["fg_color"])
        gauge.paste(gauge_label, (40, 30), gauge_label)

        # Draw Temperature Value
        current_temp = self._draw_text(
            self.objectData["temps"][0], self.objectData["font"], 100, self.objectData["fg_color"]
        )
        gauge.paste(current_temp, (40, 75), current_temp)

        # Determine if Displaying Notify Point AND Set Point
        dual_temp = True if self.objectData["temps"][1] != 0 and self.objectData["temps"][2] != 0 else False

        if dual_temp:
            font_size = 30
            y_position_offset = 0
        else:
            font_size = 50
            y_position_offset = 15

        if self.objectData["units"] == "F":
            x_position = 215
            y_position = 75
        else:
            """ Since Celcius can be a larger number, we need to adjust the positioning of the text """
            x_position = 250
            y_position = 5

        # Draw Notify Point Value
        if self.objectData["temps"][1]:
            notify_point_temp = self._draw_text(
                self.objectData["temps"][1], self.objectData["font"], font_size, self.objectData["np_color"], rect=True
            )
            gauge.paste(notify_point_temp, (x_position, y_position + y_position_offset), notify_point_temp)

        # Draw Set Point Value
        if self.objectData["temps"][2]:
            set_point_temp = self._draw_text(
                self.objectData["temps"][2], self.objectData["font"], font_size, self.objectData["sp_color"], rect=True
            )
            if dual_temp:
                y_position_offset = notify_point_temp.size[1] + 2
            gauge.paste(set_point_temp, (x_position, y_position + y_position_offset), set_point_temp)

        # Draw Units
        text = f"{self.objectData['units']}°"
        units_label = self._draw_text(text, "trebucbd.ttf", 50, self.objectData["fg_color"])
        # units_label_size = units_label.size()
        units_label_position = (330, (size[1] // 2))
        gauge.paste(units_label, units_label_position, units_label)

        # Draw Bar
        temp_bar = (40, 160, 360, 170)
        max_temp = self.objectData["max_temp"]
        current_temp_adjusted = (
            int((self.objectData["temps"][0] / max_temp) * 320) + 40 if self.objectData["temps"][0] > 0 else 40
        )
        if current_temp_adjusted > 360:
            current_temp_adjusted = 360
        current_temp_bar = (40, 160, current_temp_adjusted, 170)
        draw.rounded_rectangle(temp_bar, radius=10, fill=(0, 0, 0, 200))
        draw.rounded_rectangle(current_temp_bar, radius=10, fill=self.objectData["fg_color"])

        # Draw Notify Point Polygon
        if self.objectData["temps"][1]:
            notify_temp_adjusted = (
                int((self.objectData["temps"][1] / max_temp) * 320) + 40 if self.objectData["temps"][1] > 0 else 0
            )
            if notify_temp_adjusted > 360:
                notify_temp_adjusted = 360
            triangle_coords = [
                (notify_temp_adjusted, 168),
                (notify_temp_adjusted + 10, 150),
                (notify_temp_adjusted - 10, 150),
            ]
            draw.polygon(triangle_coords, fill=self.objectData["np_color"])

        # Draw Set Point Polygon
        if self.objectData["temps"][2]:
            set_temp_adjusted = (
                int((self.objectData["temps"][2] / max_temp) * 320) + 40 if self.objectData["temps"][2] > 0 else 0
            )
            if set_temp_adjusted > 360:
                set_temp_adjusted = 360
            triangle_coords = [(set_temp_adjusted, 168), (set_temp_adjusted + 10, 150), (set_temp_adjusted - 10, 150)]
            draw.polygon(triangle_coords, fill=self.objectData["sp_color"])

        # Create drawing object
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        gauge = gauge.resize(output_size)
        canvas.paste(gauge, (0, 0), gauge)

        return canvas


class ProbeCard(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 220)  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        name = data.get("name", "")
        temp = data.get("temp", 0)
        target = data.get("target", 0)
        units = self.objectData.get("units", "F")

        dim_color = (183, 172, 156, 255)
        light_color = (244, 237, 226, 255)
        cooking_color = (255, 210, 63, 255)
        done_color = (94, 201, 111, 255)
        ambient_color = (125, 114, 100, 255)

        done = target > 0 and temp >= target - 1

        card = Image.new("RGBA", size)
        draw = ImageDraw.Draw(card)

        # Card background + subtle border
        draw.rounded_rectangle((15, 15, size[0] - 15, size[1] - 15), radius=20, fill=(26, 22, 17, 255))
        draw.rounded_rectangle((15, 15, size[0] - 15, size[1] - 15), radius=20, outline=(255, 255, 255, 30), width=2)

        # Top row: probe name (left)
        name_label = self._draw_text(name.upper(), "./static/font/Barlow-SemiBold.ttf", 26, dim_color)
        card.paste(name_label, (35, 28), name_label)

        # Top row: target string (right aligned)
        if target > 0:
            target_text = f"{round(target)}°"
            target_color = done_color if done else cooking_color
        else:
            target_text = "AMBIENT"
            target_color = ambient_color
        target_label = self._draw_text(target_text, "./static/font/Barlow-SemiBold.ttf", 26, target_color)
        target_x = size[0] - 35 - target_label.size[0]
        card.paste(target_label, (target_x, 28), target_label)

        # Draw triangle to the left of target when target > 0
        if target > 0:
            # Triangle pointing right, positioned to the left of the target text
            triangle_size = 10  # px
            triangle_left_x = target_x - triangle_size - 4  # 4px spacing
            triangle_center_y = 28 + 13  # Center vertically with text (~half of font size)
            # Right-pointing triangle: point right, base on left
            triangle_points = [
                (triangle_left_x, triangle_center_y - triangle_size // 2),  # top-left
                (triangle_left_x, triangle_center_y + triangle_size // 2),  # bottom-left
                (triangle_left_x + triangle_size, triangle_center_y),  # right point
            ]
            draw.polygon(triangle_points, fill=target_color)

        # Big temperature
        temp_label = self._draw_text(round(temp), "./static/font/BarlowSemiCondensed-Bold.ttf", 90, light_color)
        card.paste(temp_label, (35, 75), temp_label)

        # Units, smaller/dim, following the big temp
        units_label = self._draw_text(f"°{units}", "./static/font/Barlow-SemiBold.ttf", 34, dim_color)
        units_x = 35 + temp_label.size[0] + 8
        units_y = 75 + temp_label.size[1] - units_label.size[1]
        card.paste(units_label, (units_x, units_y), units_label)

        # Progress bar near the bottom
        bar_track = (35, 178, size[0] - 35, 188)
        draw.rounded_rectangle(bar_track, radius=5, fill=(60, 54, 46, 255))

        if target > 0:
            fraction = max(0.0, min(1.0, temp / target))
        else:
            fraction = 0.0

        if fraction > 0:
            fill_color = done_color if done else accent["accent"]
            bar_width = bar_track[2] - bar_track[0]
            fill_x = bar_track[0] + int(bar_width * fraction)
            if fill_x > bar_track[0]:
                draw.rounded_rectangle((bar_track[0], bar_track[1], fill_x, bar_track[3]), radius=5, fill=fill_color)

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        card = card.resize(output_size)
        canvas.paste(card, (0, 0), card)

        return canvas


def _lerp_color(color_a, color_b, fraction):
    """Linearly interpolate between two RGB tuples."""
    return tuple(round(color_a[i] + (color_b[i] - color_a[i]) * fraction) for i in range(3))


def _gradient_color(stops, fraction):
    """Interpolate a color across a 3-stop gradient (0.0 - 1.0)."""
    fraction = max(0.0, min(1.0, fraction))
    if fraction <= 0.5:
        return _lerp_color(stops[0], stops[1], fraction * 2)
    return _lerp_color(stops[1], stops[2], (fraction - 0.5) * 2)


class GaugeEmber(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        """
        Draws the redesigned "ember" style center gauge: a 270 degree arc gauge
        with an approximated gradient sweep, a soft glow, a setpoint tick and
        centered temperature/mode readouts.

        Returns:
            Image: The image canvas with the gauge drawn on it.
        """
        output_size = self.objectData["size"]

        size = (500, 500)  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        mode_label = str(data.get("mode_label", "")).upper()

        temps = self.objectData["temps"]
        current_temp = temps[0]
        setpoint = temps[2] if len(temps) > 2 else 0
        max_temp = self.objectData["max_temp"] or 1
        units = self.objectData["units"]
        label = self.objectData["label"]
        glow_enabled = self.objectData.get("glow", True)

        track_color = (42, 36, 29, 255)
        label_color = (125, 114, 100, 255)
        light_color = (248, 242, 232, 255)
        dim_color = (138, 127, 112, 255)
        setpoint_color = (108, 200, 255, 255)

        gauge = Image.new("RGBA", size)
        draw = ImageDraw.Draw(gauge)

        # Card background + subtle border
        margin = round(size[0] * 0.03)
        radius = round(size[0] * 0.055)
        draw.rounded_rectangle(
            (margin, margin, size[0] - margin, size[1] - margin), radius=radius, fill=(26, 22, 17, 255)
        )
        draw.rounded_rectangle(
            (margin, margin, size[0] - margin, size[1] - margin), radius=radius, outline=(255, 255, 255, 30), width=2
        )

        # Arc geometry - matches the GaugeCircle 270 degree sweep starting at 135 degrees
        arc_width = round(size[0] * 0.075)
        arc_margin = round(size[0] * 0.09)
        coords = (arc_margin, arc_margin, size[0] - arc_margin, size[1] - arc_margin)
        start_deg = 135
        fraction = max(0.0, min(1.0, current_temp / max_temp))
        end_deg = start_deg + (270 * fraction)

        # Background/track arc (full sweep)
        draw.arc(coords, start=start_deg, end=start_deg + 270, fill=track_color, width=arc_width)

        # Soft glow behind the value arc, approximated with a blurred copy of the arc
        if glow_enabled and fraction > 0:
            glow_layer = Image.new("RGBA", size)
            glow_draw = ImageDraw.Draw(glow_layer)
            glow_color = tuple(accent["glow"][:3]) + (160,)
            glow_draw.arc(
                coords, start=start_deg, end=end_deg, fill=glow_color, width=arc_width + round(size[0] * 0.03)
            )
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=round(size[0] * 0.025)))
            gauge = Image.alpha_composite(gauge, glow_layer)
            draw = ImageDraw.Draw(gauge)

        # Value arc - approximate the ember gradient by drawing short interpolated segments
        if fraction > 0:
            segment_count = max(8, round(48 * fraction))
            for index in range(segment_count):
                seg_start = start_deg + (end_deg - start_deg) * (index / segment_count)
                seg_end = start_deg + (end_deg - start_deg) * ((index + 1) / segment_count) + 0.75
                seg_fraction = (index + 0.5) / segment_count
                seg_color = _gradient_color(accent["arc"], seg_fraction) + (255,)
                draw.arc(coords, start=seg_start, end=seg_end, fill=seg_color, width=arc_width)

        # Setpoint tick - short radial line at the setpoint angle
        if setpoint > 0:
            center = (size[0] / 2, size[1] / 2)
            radius_outer = (size[0] / 2) - arc_margin + (arc_width / 2) + round(size[0] * 0.02)
            radius_inner = (size[0] / 2) - arc_margin - (arc_width / 2) - round(size[0] * 0.02)

            set_fraction = max(0.0, min(1.0, setpoint / max_temp))
            angle_rad = math.radians(start_deg + (270 * set_fraction))
            x1 = center[0] + radius_inner * math.cos(angle_rad)
            y1 = center[1] + radius_inner * math.sin(angle_rad)
            x2 = center[0] + radius_outer * math.cos(angle_rad)
            y2 = center[1] + radius_outer * math.sin(angle_rad)
            draw.line((x1, y1, x2, y2), fill=setpoint_color, width=round(size[0] * 0.012))

        # Center content: label, big temp + units, SET line, mode pill - stacked and centered
        pieces = []

        label_text = label.upper()
        if len(label_text) > 10:
            label_text = label_text[0:10]
        label_canvas = self._draw_text(
            label_text, "./static/font/Barlow-SemiBold.ttf", round(size[0] * 0.046), label_color
        )
        pieces.append(label_canvas)

        temp_canvas = self._draw_text(
            round(current_temp), "./static/font/BarlowSemiCondensed-Bold.ttf", round(size[0] * 0.22), light_color
        )
        units_canvas = self._draw_text(
            f"°{units}", "./static/font/Barlow-SemiBold.ttf", round(size[0] * 0.072), dim_color
        )
        temp_row = Image.new(
            "RGBA",
            (
                temp_canvas.width + units_canvas.width + round(size[0] * 0.016),
                max(temp_canvas.height, units_canvas.height),
            ),
        )
        temp_row.paste(temp_canvas, (0, 0), temp_canvas)
        temp_row.paste(
            units_canvas,
            (temp_canvas.width + round(size[0] * 0.016), temp_row.height - units_canvas.height),
            units_canvas,
        )
        pieces.append(temp_row)

        if setpoint > 0:
            set_canvas = self._draw_text(
                f"SET {round(setpoint)}°", "./static/font/Barlow-SemiBold.ttf", round(size[0] * 0.06), setpoint_color
            )
            pieces.append(set_canvas)

        if mode_label:
            pill_text = self._draw_text(
                mode_label, "./static/font/Barlow-SemiBold.ttf", round(size[0] * 0.056), accent["accent"]
            )
            pad_x, pad_y = round(size[0] * 0.045), round(size[0] * 0.02)
            pill_size = (pill_text.width + 2 * pad_x, pill_text.height + 2 * pad_y)
            pill_canvas = Image.new("RGBA", pill_size)
            pill_draw = ImageDraw.Draw(pill_canvas)
            tint = tuple(accent["accent"][:3]) + (40,)
            pill_draw.rounded_rectangle(
                (0, 0, pill_size[0] - 1, pill_size[1] - 1),
                radius=pill_size[1] // 2,
                fill=tint,
                outline=accent["accent"],
                width=2,
            )
            pill_canvas.paste(pill_text, (pad_x, pad_y), pill_text)
            pieces.append(pill_canvas)

        gap = round(size[1] * 0.022)
        total_height = sum(piece.height for piece in pieces) + gap * (len(pieces) - 1)
        y = round((size[1] - total_height) / 2)
        for piece in pieces:
            x = round((size[0] - piece.width) / 2)
            gauge.paste(piece, (x, y), piece)
            y += piece.height + gap

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        gauge = gauge.resize(output_size)
        canvas.paste(gauge, (0, 0), gauge)

        return canvas


class SystemCard(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _row_specs(self):
        """Ordered row specs: (data key, label, FA glyph, active text, inactive text)."""
        return [
            ("fan", "FAN", "", "RUNNING", "IDLE"),  # Font Awesome Fan Icon
            ("auger", "AUGER", "", "FEEDING", "IDLE"),  # Font Awesome Right Chevron Arrows Icon
            ("igniter", "IGNITER", "", "HOT", "OFF"),  # Font Awesome Flame Icon
        ]

    def _draw_icon_glyph(self, char_id, color, box_size, rotation=0):
        """Renders a single FA glyph centered in a square box, optionally rotated
        using the same in-place rotate/crop approach as StatusIcon's fan-spin."""
        font_size = max(10, round(box_size * 0.62))
        icon = self._create_icon(char_id, font_size, color)
        icon_bbox = icon.getbbox()
        if rotation:
            icon = icon.rotate(rotation)
            icon = icon.crop(icon_bbox)
        canvas = Image.new("RGBA", (box_size, box_size))
        paste_x = (box_size - icon.size[0]) // 2
        paste_y = (box_size - icon.size[1]) // 2
        canvas.paste(icon, (paste_x, paste_y), icon)
        return canvas

    def _draw_object(self, rotation=0):
        output_size = self.objectData["size"]
        size = (300, 300)  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})

        card_fill = (26, 22, 17, 255)  # #1a1611
        row_fill = (20, 16, 12, 255)  # #14100c
        title_color = (125, 114, 100, 255)  # #7d7264 (dim label)
        label_color = (207, 198, 184, 255)  # #cfc6b8
        dim_color = (125, 114, 100, 255)  # #7d7264
        grey_icon = (87, 81, 74, 255)  # #57514a
        ignite_color = (255, 122, 26, 255)  # #ff7a1a
        dot_active = (94, 201, 111, 255)  # #5ec96f
        dot_inactive = (74, 68, 60, 255)  # #4a443c
        row_border_default = (255, 255, 255, 22)

        card = Image.new("RGBA", size)
        draw = ImageDraw.Draw(card)

        # Card background + subtle border
        draw.rounded_rectangle((10, 10, size[0] - 10, size[1] - 10), radius=18, fill=card_fill)
        draw.rounded_rectangle((10, 10, size[0] - 10, size[1] - 10), radius=18, outline=(255, 255, 255, 30), width=2)

        # Title
        title = self._draw_text("SYSTEM", "./static/font/Barlow-SemiBold.ttf", 20, title_color)
        card.paste(title, (26, 20), title)

        rows = self._row_specs()
        row_left = 20
        row_width = size[0] - (2 * row_left)
        row_gap = 10
        row_top = 20 + title.size[1] + 14
        bottom_margin = 18
        row_height = (size[1] - row_top - bottom_margin - (row_gap * (len(rows) - 1))) // len(rows)

        icon_col_width = 60
        text_left = row_left + icon_col_width + 12
        dot_radius = 5
        dot_right_margin = 18

        y = row_top
        for key, label, char_id, active_text, inactive_text in rows:
            active = bool(data.get(key, False))
            active_tint = ignite_color if key == "igniter" else accent["accent"]

            row_rect = (row_left, y, row_left + row_width, y + row_height)
            border_color = tuple(active_tint[:3]) + (140,) if active else row_border_default
            draw.rounded_rectangle(row_rect, radius=13, fill=row_fill, outline=border_color, width=2)

            # Icon (fan glyph rotates via the animation step when active)
            icon_color = active_tint if active else grey_icon
            icon_box_size = row_height - 16
            icon_rotation = rotation if (key == "fan" and active) else 0
            icon_canvas = self._draw_icon_glyph(char_id, icon_color, icon_box_size, rotation=icon_rotation)
            icon_x = row_left + (icon_col_width - icon_box_size) // 2
            icon_y = y + (row_height - icon_box_size) // 2
            card.paste(icon_canvas, (icon_x, icon_y), icon_canvas)

            # Label
            label_canvas = self._draw_text(label, "./static/font/Barlow-SemiBold.ttf", 17, label_color)
            label_y = y + 12
            card.paste(label_canvas, (text_left, label_y), label_canvas)

            # Status text
            status_text = active_text if active else inactive_text
            status_color = active_tint if active else dim_color
            status_canvas = self._draw_text(status_text, "./static/font/Barlow-SemiBold.ttf", 13, status_color)
            status_y = label_y + label_canvas.size[1] + 4
            card.paste(status_canvas, (text_left, status_y), status_canvas)

            # Status dot
            dot_color = dot_active if active else dot_inactive
            dot_cx = row_left + row_width - dot_right_margin
            dot_cy = y + (row_height // 2)
            draw.ellipse(
                (dot_cx - dot_radius, dot_cy - dot_radius, dot_cx + dot_radius, dot_cy + dot_radius), fill=dot_color
            )

            y += row_height + row_gap

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        card = card.resize(output_size)
        canvas.paste(card, (0, 0), card)

        return canvas

    def _animate_object(self):
        if self.objectState["animation_start"]:
            self.objectState["animation_start"] = False  # Run animation start only once
            self.objectState["animation_rotation"] = 0  # Set initial rotation

        # Fan spins while running, so increase rotation by 15 degrees on each step
        if self.objectData.get("data", {}).get("fan"):
            self.objectState["animation_rotation"] = (self.objectState.get("animation_rotation", 0) + 15) % 360
        else:
            self.objectState["animation_rotation"] = 0

        return self._draw_object(rotation=self.objectState["animation_rotation"])

    def _define_touch_areas(self):
        """Subdivides the card into three stacked touch rows (fan/auger/igniter),
        modeled on ControlPanel._define_touch_areas but split vertically."""
        row_count = len(self.objectData["button_list"])
        spacing = int(self.objectData["size"][1] / row_count)
        self.objectData["touch_areas"] = []
        for index in range(0, row_count):
            x_left = self.objectData["position"][0]
            y_top = self.objectData["position"][1] + (index * spacing)
            width = self.objectData["size"][0]
            height = spacing
            touch_area = Rect(x_left, y_top, width, height)
            # Create button rectangle / touch area and append to list
            self.objectData["touch_areas"].append(touch_area)


class ModeBar(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]

        size = (400, 60)

        # Create drawing object
        mode_bar = Image.new("RGBA", (size[0], size[1]))
        draw = ImageDraw.Draw(mode_bar)

        # Text Rectangle from top
        draw.rounded_rectangle(
            (10, -20, size[0] - 10, size[1] - 10),
            radius=8,
            outline=self.objectData["fg_color"],
            width=2,
            fill=self.objectData["bg_color"],
        )

        # Mode Text
        if len(self.objectData["text"]) > 16:
            label_displayed = self.objectData["text"][0:16]
        else:
            label_displayed = self.objectData["text"]
        font_point_size = round(size[1] * 0.80)  # Font size as a ratio of the object size
        font = ImageFont.truetype(self.objectData["font"], font_point_size)
        font_bbox = font.getbbox(label_displayed)  # Grab the width of the text
        font_width = font_bbox[2] - font_bbox[0]
        font_height = font_bbox[3] - font_bbox[1]

        label_x = (size[0] // 2) - (font_width // 2)
        label_y = (size[1] // 2) - (font_height // 2) - 18
        label_origin = (label_x, label_y)
        draw.text(label_origin, label_displayed, font=font, fill=self.objectData["fg_color"])

        # Create drawing object
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        mode_bar = mode_bar.resize(output_size)
        canvas.paste(mode_bar, (0, 0), mode_bar)

        return canvas

    def _define_touch_areas(self):
        pass


class ControlPanel(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        button_type = self.objectData["button_type"]
        active = self.objectData["button_active"]

        # Establish working size
        size = (400, 100)
        padding = 10

        # Create drawing object
        control_panel = Image.new("RGBA", (size[0], size[1]))
        draw = ImageDraw.Draw(control_panel)

        # Text Rectangle from top
        draw.rounded_rectangle(
            (padding, padding, size[0] - padding, size[1] - padding),
            radius=8,
            outline=(255, 255, 255, 255),
            width=2,
            fill=(0, 0, 0, 100),
        )

        spacing = int((size[0] - 20) / (len(button_type)))
        # Draw Dividing Lines
        for index in range(1, len(button_type) + 1):
            x_position = (index * spacing) + 10

            # Draw vertical dividing line unless on the last icon space
            if index < len(button_type):
                coords = (x_position, padding, x_position, size[1] - padding)
                draw.line(coords, fill=(255, 255, 255, 255), width=2)

            # Draw icon
            font_size = 40
            if button_type[index - 1] == active:
                font_color = (255, 255, 255, 255)  # Color for active button
            else:
                font_color = (255, 255, 255, 200)  # Color for inactive button

            if button_type[index - 1] == "Startup":
                char_id = "\uf04b"  # FontAwesome Play Icon
            elif button_type[index - 1] == "Prime":
                char_id = "\uf101"  # FontAwesome Double Arrow Right Icon
            elif button_type[index - 1] == "Monitor":
                char_id = "\uf530"  # FontAwesome Glasses Icon
            elif button_type[index - 1] == "Stop":
                char_id = "\uf04d"  # FontAwesome Stop Icon
            elif button_type[index - 1] == "Smoke":
                char_id = "\uf0c2"  # FontAwesome Cloud Icon
            elif button_type[index - 1] == "Hold":
                char_id = "\uf05b"  # FontAwesome Crosshairs Icon
            elif button_type[index - 1] == "Shutdown":
                char_id = "\uf11e"  # FontAwesome Finish Flag Icon
            elif button_type[index - 1] == "Next":
                char_id = "\uf051"  # FontAwesome Step Icon
            elif button_type[index - 1] == "None":
                char_id = "\uf068"  # FontAwesome Minus Icon
            else:
                char_id = "\uf071"  # FontAwesome Error Triangle Icon
            icon_canvas = self._create_icon(char_id, font_size, font_color)
            icon_size = icon_canvas.getbbox()
            control_panel.paste(
                icon_canvas,
                (x_position - (spacing // 2) - (icon_size[2] // 2), (size[1] // 2) - (icon_size[3] // 2)),
                icon_canvas,
            )

        # Create final canvas output object
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        control_panel = control_panel.resize(output_size)
        canvas.paste(control_panel, (0, 0), control_panel)

        return canvas

    def _define_touch_areas(self):
        spacing = int((self.objectData["size"][0]) / (len(self.objectData["button_list"])))
        # Draw Dividing Lines
        self.objectData["touch_areas"] = []
        for index in range(0, len(self.objectData["button_list"])):
            x_left = self.objectData["position"][0] + (index * spacing)
            y_top = self.objectData["position"][1]
            width = spacing
            height = self.objectData["size"][1]
            touch_area = Rect(x_left, y_top, width, height)
            # Create button rectangle / touch area and append to list
            self.objectData["touch_areas"].append(touch_area)
            # print(f'Index: {index}  Button: {self.objectData["button_list"][index]}  Touch Area: {touch_area}')


class StatusIcon(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self, rotation=0, breath_step=0):
        # Save output size
        output_size = self.objectData["size"]
        type = self.objectData["icon"]
        icon_color = self.objectData["active_color"] if self.objectData["active"] else self.objectData["inactive_color"]
        animation_breath_steps = [1, 0.95, 0.90, 0.80, 0.70, 0.80, 0.90, 0.95, 1]

        # Working Size
        size = (100, 100)

        if type == "Fan":
            char_id = "\uf863"  # Font Awesome Fan Icon

        elif type == "Auger":
            char_id = "\uf101"  # Font Awesome Right Chevron Arrows Icon

        elif type == "Igniter":
            char_id = "\uf46a"  # Font Awesome Flame Icon

        elif type == "SmokePlus":
            char_id = "\uf0c2"  # Font Awesome Icon for Cloud (Smoke)
            text = "\uf067"  # Font Awesome Icon for PLUS

        elif type == "Notify":
            char_id = "\uf0f3"  # Font Awesome Bell Icon

        elif type == "Recipe":
            char_id = "\uf46d"  # Font Awesome Clipboard Icon

        elif type == "Pause":
            char_id = "\uf04c"  # Font Awesome Pause Icon

        else:
            char_id = "\uf071"  # FontAwesome Error Triangle Icon

        if "animation_breathe" in self.objectState.keys():
            if self.objectState["animation_breathe"] >= len(animation_breath_steps):
                self.objectState["animation_breathe"] = breath_step = 0

        font_size = int(animation_breath_steps[breath_step] * 80)

        icon = self._create_icon(char_id, font_size, icon_color)

        # Determine Bounding Box of Icon
        icon_size = icon.getbbox()

        if rotation:
            icon = icon.rotate(rotation)

        icon = icon.crop(icon_size)
        # Upper Left Corner of Centered Icon
        center = ((size[0] // 2) - (icon_size[2] // 2), (size[1] // 2) - (icon_size[3] // 2))

        # Create final canvas output object
        canvas = Image.new("RGBA", size)
        canvas.paste(icon, center, icon)

        canvas = canvas.resize(output_size)

        return canvas

    def _animate_object(self):
        if self.objectState["animation_start"]:
            self.objectState["animation_start"] = False  # Run animation start only once
            self.objectState["animation_rotation"] = 0  # Set initial rotation
            self.objectState["animation_breathe"] = 0  # Set initial animation breath step

        # Fans Rotate, so increase rotation by 15 degrees on each step
        if self.objectData["icon"] == "Fan":
            self.objectState["animation_rotation"] += 15
            if self.objectState["animation_rotation"] > 360:
                self.objectState["animation_rotation"] = 0

        # Some Icons Breathe
        if self.objectData["icon"] in ["Auger", "Igniter", "Recipe"]:
            self.objectState["animation_breathe"] += 1

        return self._draw_object(
            rotation=self.objectState["animation_rotation"], breath_step=self.objectState["animation_breathe"]
        )

    def _define_touch_areas(self):
        pass


class MenuIcon(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        # Save output size
        output_size = self.objectData["size"]

        # Working Size
        size = (40, 40)
        if self.objectData["icon"] == "Hamburger":
            char_id = "\uf0c9"  # Font Awesome Hamburger Menu
        else:
            char_id = "\uf00d"  # Font Awesome Times for closing the window

        font_size = 30
        color = (255, 255, 255, 255)

        menu_icon = self._create_icon(char_id, font_size, color)

        menu_icon_size = menu_icon.getbbox()

        center_offset = (size[0] // 2) - (menu_icon_size[2] // 2), (size[1] // 2) - (menu_icon_size[3] // 2)

        # Create final canvas output object
        canvas = Image.new("RGBA", size)
        canvas.paste(menu_icon, center_offset, menu_icon)

        canvas = canvas.resize(output_size)

        return canvas


class MenuGeneric(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        size = (600, 400)  # Define working size
        canvas = Image.new("RGBA", size)  # Create canvas output object
        draw = ImageDraw.Draw(canvas)  # Create drawing object
        fg_color = self.objectData["color"]
        bg_color = (0, 0, 0, 255)

        # Clear any touch areas that might have been defined before
        self.objectData["touch_areas"] = []

        selected = self.objectData["data"].get("button_selected", None)
        if selected == None and len(self.objectData["button_list"]) > 1:
            self.objectData["data"]["button_selected"] = 1
            selected = 1

        # Rounded rectangle that fills the canvas size
        menu_padding = 10  # Define padding around outside of the menu rectangle
        draw.rounded_rectangle(
            (menu_padding, menu_padding, size[0] - menu_padding, size[1] - menu_padding),
            radius=8,
            outline=(0, 0, 0, 225),
            fill=(0, 0, 0, 250),
        )

        # Menu Title
        title = self._draw_text(self.objectData["title_text"], "trebuc.ttf", 35, fg_color)
        title_position = ((size[0] // 2) - (title.width // 2), 20)
        canvas.paste(title, title_position, title)

        # Index through button_list to create menu items

        number_of_buttons = len(self.objectData["button_list"])

        two_column_mode = True if number_of_buttons > 6 else False

        if two_column_mode:
            button_height = 50
            button_padding = 10
            button_width = size[0] // 2 - menu_padding - (button_padding * 2)
            column = 0
            button_area_position = (menu_padding + button_padding, 80)
            button_area_size = (
                size[0] - (menu_padding * 2) - (button_padding * 2),
                size[1] - button_area_position[1] - menu_padding - button_padding,
            )
            row_height = 60
        else:
            button_height = 50
            button_padding = 10
            button_width = size[0] - (menu_padding * 2) - (button_padding * 2)
            button_area_position = (menu_padding + button_padding, 60)
            button_area_size = (
                size[0] - (menu_padding * 2) - (button_padding * 2),
                size[1] - button_area_position[1] - menu_padding - button_padding,
            )
            row_height = button_area_size[1] // (number_of_buttons - 1)

        button_count = 0
        row = 0

        for index, button in enumerate(self.objectData["button_list"]):
            if "_close" in button and self.objectData["button_text"][index] == "Close Menu":
                # Close Icon Upper Right
                close_icon = self._create_icon("\uf00d", 34, (255, 255, 255))
                close_position = (size[0] - (menu_padding * 4), (menu_padding * 2))
                canvas.paste(close_icon, close_position, close_icon)
                close_touch_area = (close_position[0], close_position[1], close_icon.width, close_icon.height)
                scaled_touch_area = self._scale_touch_area(close_touch_area, size, self.objectData["size"])
                transformed_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
                self.objectData["touch_areas"].append(Rect(transformed_touch_area))
            else:
                if button_count > 10:
                    break  # Stop if at 11 items

                if two_column_mode:
                    if button_count in [0, 2, 4, 6, 8, 10]:
                        rect_position = (button_area_position[0], button_area_position[1] + (row * row_height))
                    else:
                        rect_position = (
                            button_area_position[0] + button_width + (button_padding * 2),
                            button_area_position[1] + (row * row_height),
                        )
                        row += 1
                else:
                    rect_position = (
                        button_area_position[0],
                        button_area_position[1]
                        + (button_count * row_height)
                        + ((row_height // 2) - (button_height // 2)),
                    )

                rect_size = (button_width, button_height)
                rect_coords = (
                    rect_position[0],
                    rect_position[1],
                    rect_position[0] + rect_size[0],
                    rect_position[1] + rect_size[1],
                )
                if selected == index:
                    # Reverse colors if selected
                    draw.rounded_rectangle(rect_coords, radius=8, outline=(255, 255, 255, 255), fill=fg_color)
                else:
                    draw.rounded_rectangle(rect_coords, radius=8, outline=(255, 255, 255, 255), fill=bg_color)

                # Put button text inside rectangle
                if len(self.objectData["button_text"][index]) > 25:
                    label_displayed = self.objectData["button_text"][index][0:25]
                else:
                    label_displayed = self.objectData["button_text"][index]
                if selected == index:
                    # Reverse colors if selected
                    label = self._draw_text(label_displayed, self.objectData["font"], 35, bg_color)
                else:
                    label = self._draw_text(label_displayed, self.objectData["font"], 35, fg_color)
                label_x = rect_position[0] + (rect_size[0] // 2) - (label.width // 2)
                label_y = rect_position[1] + (rect_size[1] // 2) - (label.height // 2)
                label_position = (label_x, label_y)
                canvas.paste(label, label_position, label)

                # Define touch area for button
                button_touch_area = rect_position + rect_size
                scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
                transformed_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
                touch_area = Rect(transformed_touch_area)

                # Create button rectangle / touch area and append to list
                self.objectData["touch_areas"].append(touch_area)

                button_count += 1

        # Resize for output
        canvas = canvas.resize(self.objectData["size"])

        return canvas

    def _define_touch_areas(self):
        pass


class MenuQRCode(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        size = (600, 400)  # Define working size
        canvas = Image.new("RGBA", size)  # Create canvas output object
        draw = ImageDraw.Draw(canvas)  # Create drawing object
        fg_color = self.objectData["color"]

        # Rounded rectangle that fills the canvas size
        menu_padding = 10  # Define padding around outside of the menu rectangle
        draw.rounded_rectangle(
            (menu_padding, menu_padding, size[0] - menu_padding, size[1] - menu_padding),
            radius=8,
            outline=(0, 0, 0, 225),
            fill=(0, 0, 0, 250),
        )

        # Draw Close Icon in upper right
        close_icon = self._create_icon("\uf00d", 34, (255, 255, 255))
        close_position = (size[0] - (menu_padding * 4), (menu_padding * 2))
        canvas.paste(close_icon, close_position, close_icon)

        # Menu Title
        title = self._draw_text(self.objectData["ip_address"], "trebuc.ttf", 35, fg_color)
        title_position = ((size[0] // 2) - (title.width // 2), 20)
        canvas.paste(title, title_position, title)

        # Draw QR Code
        img_qr = qrcode.make(f"http://{self.objectData['ip_address']}")
        img_qr = img_qr.resize((300, 300))
        position = (150, 60)
        canvas.paste(img_qr, position)
        canvas = canvas.resize(self.objectData["size"])

        return canvas


class InputNumber(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        size = (600, 400)  # Define working size
        canvas = Image.new("RGBA", size)  # Create canvas output object
        draw = ImageDraw.Draw(canvas)  # Create drawing object
        button_pushed = self.objectState.get("animation_input", "")
        self.objectData["touch_areas"] = []
        self.objectData["button_list"] = []

        # Rounded rectangle that fills the canvas size
        menu_padding = 10  # Define padding around outside of the menu rectangle
        draw.rounded_rectangle(
            (menu_padding, menu_padding, size[0] - menu_padding, size[1] - menu_padding),
            radius=8,
            outline=(0, 0, 0, 225),
            fill=(0, 0, 0, 250),
        )

        # Close Icon Upper Right
        close_icon = self._create_icon("\uf00d", 34, (255, 255, 255))
        close_position = (size[0] - (menu_padding * 4), (menu_padding * 2))
        canvas.paste(close_icon, close_position, close_icon)
        close_touch_area = (close_position[0], close_position[1], close_icon.width, close_icon.height)
        scaled_touch_area = self._scale_touch_area(close_touch_area, size, self.objectData["size"])
        transformed_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transformed_touch_area))
        self.objectData["button_list"].append("menu_close")

        # Menu Title
        title = self._draw_text(
            self.objectData["title_text"],
            self.objectData["font"],
            35,
            self.objectData["color"],
            rect=False,
            bg_fill=(0, 0, 0, 250),
        )
        title_x = (size[0] // 2) - (title.width // 2)
        title_y = 15
        canvas.paste(title, (title_x, title_y))

        # Number Display
        number_entry_position = (60, 75)
        number_entry_size = (240, 100)
        number_entry_coords = number_entry_position + (
            number_entry_position[0] + number_entry_size[0],
            number_entry_position[1] + number_entry_size[1],
        )
        number_entry_bg_color = (50, 50, 50)
        draw.rounded_rectangle(number_entry_coords, radius=8, fill=number_entry_bg_color)
        number_digits = self._draw_text(
            self.objectData["data"]["value"],
            self.objectData["font"],
            80,
            self.objectData["color"],
            bg_fill=number_entry_bg_color,
        )
        number_digits_position = (
            number_entry_position[0] + ((number_entry_size[0] // 2) - (number_digits.width // 2)),
            number_entry_position[1] + (number_entry_size[1] // 2) - (number_digits.height // 2),
        )
        canvas.paste(number_digits, number_digits_position)

        # Up Arrow
        if button_pushed == "up":
            bg_fill = (255, 255, 255, 255)
            fg_fill = (0, 0, 0, 255)
        else:
            bg_fill = (0, 0, 0, 255)
            fg_fill = self.objectData["color"]
        button_position = (60, 195)
        button_size = (110, 70)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=fg_fill, fill=bg_fill, width=3)
        button_icon = self._create_icon("\uf077", 35, fg_fill, bg_fill=bg_fill)
        button_icon_position = (
            button_position[0] + ((button_size[0] // 2) - (button_icon.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_icon.height // 2),
        )
        canvas.paste(button_icon, button_icon_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append("button_up")

        # Down Arrow
        if button_pushed == "down":
            bg_fill = (255, 255, 255, 255)
            fg_fill = (0, 0, 0, 255)
        else:
            bg_fill = (0, 0, 0, 255)
            fg_fill = self.objectData["color"]
        button_position = (190, 195)
        button_size = (110, 70)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=fg_fill, fill=bg_fill, width=3)
        button_icon = self._create_icon("\uf078", 35, fg_fill, bg_fill=bg_fill)
        button_icon_position = (
            button_position[0] + ((button_size[0] // 2) - (button_icon.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_icon.height // 2),
        )
        canvas.paste(button_icon, button_icon_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append("button_down")

        # Enter Button
        button_position = (60, 290)
        button_size = (240, 80)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=self.objectData["color"], width=3)
        button_text = self._draw_text("ENTER", self.objectData["font"], 60, self.objectData["color"], bg_fill=(0, 0, 0))
        button_text_position = (
            button_position[0] + ((button_size[0] // 2) - (button_text.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_text.height // 2),
        )
        canvas.paste(button_text, button_text_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append(self.objectData["command"])

        # Draw Number Pad
        pad_position = (250, 75)
        button_size = (70, 70)
        button_padding = 5
        button_position = [
            pad_position[0] - button_size[0] - button_padding,
            pad_position[1] - button_size[0] - button_padding,
        ]

        pad_button_list = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["DEL", "0", "."]]
        for row in pad_button_list:
            button_position[1] += button_size[1] + button_padding
            button_position[0] = pad_position[0]
            for col in row:
                if button_pushed == col:
                    bg_fill = (255, 255, 255, 255)
                    fg_fill = (0, 0, 0, 255)
                else:
                    bg_fill = (0, 0, 0, 255)
                    fg_fill = self.objectData["color"]
                button_position[0] += button_size[0] + button_padding
                if col == "DEL":
                    button_text = self._create_icon("\uf55a", 35, fg_fill, bg_fill=bg_fill)
                else:
                    button_text = self._draw_text(
                        col, self.objectData["font"], 35, fg_fill, rect=False, bg_fill=bg_fill
                    )
                # Draw Rectangle
                button_coords = tuple(button_position) + (
                    button_position[0] + button_size[0],
                    button_position[1] + button_size[1],
                )
                draw.rounded_rectangle(button_coords, radius=8, outline=fg_fill, fill=bg_fill, width=3)
                text_position = (
                    button_position[0] + (button_size[0] // 2) - (button_text.width // 2),
                    button_position[1] + (button_size[1] // 2) - (button_text.height // 2),
                )
                canvas.paste(button_text, text_position)
                button_touch_area = (
                    button_position[0] + self.objectData["position"][0],
                    button_position[1] + self.objectData["position"][1],
                ) + button_size
                scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
                self.objectData["touch_areas"].append(Rect(scaled_touch_area))
                self.objectData["button_list"].append(f"button_{col}")

        # Resize for output
        canvas = canvas.resize(self.objectData["size"])
        return canvas

    def _animate_object(self):
        if self.objectState["animation_start"]:
            self.objectState["animation_start"] = False  # Run animation start only once
            self.objectState["animation_counter"] = 0  # Setup a counter for number of frames to produce
            self.objectState["animation_input"] = self.objectData["data"]["input"]  # Save input from user
            self.objectData["data"]["input"] = ""  # Clear user input

        if self.objectState["animation_counter"] > 1:
            self.objectState["animation_active"] = False  # Disable animation after one frame
            self.objectState["animation_input"] = ""

        self.objectState["animation_counter"] += 1  # Increment the frame counter

        return self._draw_object()

    def _process_input(self):
        if self.objectData["data"]["input"] != "":
            """ Check first for up / down input """
            if self.objectData["data"]["input"] == "up":
                self.objectData["data"]["value"] += self.objectData["step"]

            if self.objectData["data"]["input"] == "down":
                self.objectData["data"]["value"] -= self.objectData["step"]
                if self.objectData["data"]["value"] < 0:
                    self.objectData["data"]["value"] = 0

            """ Convert value to list of characters """
            temp_string = str(self.objectData["data"]["value"])
            self.objectState["value"] = [char for char in temp_string]

            if self.objectData["data"]["input"] == "DEL":
                if len(self.objectState["value"]) > 1:
                    """ If a float, delete back to the decimal value """
                    if "." in self.objectState["value"] and self.objectState["value"][-2] == ".":
                        self.objectState["value"].pop()
                        self.objectState["value"].pop()
                    else:
                        self.objectState["value"].pop()
                else:
                    self.objectState["value"] = ["0"]

            if "." in self.objectState["value"] and self.objectData["data"]["input"] == ".":
                pass
            elif self.objectData["data"]["input"] in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "."]:
                if len(self.objectState["value"]) > 5:
                    pass
                if "." in self.objectState["value"]:
                    self.objectState["value"].pop()
                    self.objectState["value"].append(self.objectData["data"]["input"])
                elif len(self.objectState["value"]) == 3 and self.objectData["data"]["input"] == ".":
                    self.objectState["value"].append(self.objectData["data"]["input"])
                elif len(self.objectState["value"]) < 3:
                    self.objectState["value"].append(self.objectData["data"]["input"])

            """ Combine list of characters back to string and then back to a float or int """
            temp_string = "".join([str(i) for i in self.objectState["value"]])

            if "." in temp_string:
                self.objectData["data"]["value"] = float(temp_string)
            else:
                self.objectData["data"]["value"] = int(temp_string)

    def _define_touch_areas(self):
        pass


class InputNumberSimple(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        size = (600, 400)  # Define working size
        canvas = Image.new("RGBA", size)  # Create canvas output object
        draw = ImageDraw.Draw(canvas)  # Create drawing object
        button_pushed = self.objectState.get("animation_input", "")
        self.objectData["touch_areas"] = []
        self.objectData["button_list"] = []

        # Rounded rectangle that fills the canvas size
        menu_padding = 10  # Define padding around outside of the menu rectangle
        draw.rounded_rectangle(
            (menu_padding, menu_padding, size[0] - menu_padding, size[1] - menu_padding),
            radius=8,
            outline=(0, 0, 0, 225),
            fill=(0, 0, 0, 250),
        )

        # Close Icon Upper Right
        close_icon = self._create_icon("\uf00d", 34, (255, 255, 255))
        close_position = (size[0] - (menu_padding * 4), (menu_padding * 2))
        canvas.paste(close_icon, close_position, close_icon)
        close_touch_area = (close_position[0], close_position[1], close_icon.width, close_icon.height)
        scaled_touch_area = self._scale_touch_area(close_touch_area, size, self.objectData["size"])
        transformed_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transformed_touch_area))
        self.objectData["button_list"].append("menu_close")

        # Menu Title
        title = self._draw_text(
            self.objectData["title_text"],
            self.objectData["font"],
            35,
            self.objectData["color"],
            rect=False,
            bg_fill=(0, 0, 0, 250),
        )
        title_x = (size[0] // 2) - (title.width // 2)
        title_y = 15
        canvas.paste(title, (title_x, title_y))

        # Number Display
        number_entry_position = (40, 80)
        number_entry_size = (340, 200)
        number_entry_coords = number_entry_position + (
            number_entry_position[0] + number_entry_size[0],
            number_entry_position[1] + number_entry_size[1],
        )
        number_entry_bg_color = (50, 50, 50)
        draw.rounded_rectangle(number_entry_coords, radius=8, fill=number_entry_bg_color)
        number_digits = self._draw_text(
            self.objectData["data"]["value"],
            self.objectData["font"],
            160,
            self.objectData["color"],
            bg_fill=number_entry_bg_color,
        )
        number_digits_position = (
            number_entry_position[0] + ((number_entry_size[0] // 2) - (number_digits.width // 2)),
            number_entry_position[1] + (number_entry_size[1] // 2) - (number_digits.height // 2),
        )
        canvas.paste(number_digits, number_digits_position)

        # Up Arrow
        if button_pushed == "up":
            bg_fill = (255, 255, 255, 255)
            fg_fill = (0, 0, 0, 255)
        else:
            bg_fill = (0, 0, 0, 255)
            fg_fill = self.objectData["color"]
        button_position = (420, 80)
        button_size = (140, 80)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=fg_fill, fill=bg_fill, width=3)
        button_icon = self._create_icon("\uf077", 35, fg_fill, bg_fill=bg_fill)
        button_icon_position = (
            button_position[0] + ((button_size[0] // 2) - (button_icon.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_icon.height // 2),
        )
        canvas.paste(button_icon, button_icon_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append("button_up")

        # Down Arrow
        if button_pushed == "down":
            bg_fill = (255, 255, 255, 255)
            fg_fill = (0, 0, 0, 255)
        else:
            bg_fill = (0, 0, 0, 255)
            fg_fill = self.objectData["color"]
        button_position = (420, 200)
        button_size = (140, 80)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=fg_fill, fill=bg_fill, width=3)
        button_icon = self._create_icon("\uf078", 35, fg_fill, bg_fill=bg_fill)
        button_icon_position = (
            button_position[0] + ((button_size[0] // 2) - (button_icon.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_icon.height // 2),
        )
        canvas.paste(button_icon, button_icon_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append("button_down")

        # Enter Button
        button_position = (180, 300)
        button_size = (240, 80)
        button_coords = button_position + (button_position[0] + button_size[0], button_position[1] + button_size[1])
        draw.rounded_rectangle(button_coords, radius=8, outline=self.objectData["color"], width=3)
        button_text = self._draw_text("ENTER", self.objectData["font"], 60, self.objectData["color"], bg_fill=(0, 0, 0))
        button_text_position = (
            button_position[0] + ((button_size[0] // 2) - (button_text.width // 2)),
            button_position[1] + (button_size[1] // 2) - (button_text.height // 2),
        )
        canvas.paste(button_text, button_text_position)
        # Scale and Store Touch Area
        button_touch_area = button_position + button_size
        scaled_touch_area = self._scale_touch_area(button_touch_area, size, self.objectData["size"])
        transform_touch_area = self._transform_touch_area(scaled_touch_area, self.objectData["position"])
        self.objectData["touch_areas"].append(Rect(transform_touch_area))
        self.objectData["button_list"].append(self.objectData["command"])

        # Resize for output
        canvas = canvas.resize(self.objectData["size"])
        return canvas

    def _animate_object(self):
        if self.objectState["animation_start"]:
            self.objectState["animation_start"] = False  # Run animation start only once
            self.objectState["animation_counter"] = 0  # Setup a counter for number of frames to produce
            self.objectState["animation_input"] = self.objectData["data"]["input"]  # Save input from user
            self.objectData["data"]["input"] = ""  # Clear user input

        if self.objectState["animation_counter"] > 1:
            self.objectState["animation_active"] = False  # Disable animation after one frame
            self.objectState["animation_input"] = ""

        self.objectState["animation_counter"] += 1  # Increment the frame counter

        return self._draw_object()

    def _process_input(self):
        if self.objectData["data"]["input"] != "":
            """ Check first for up / down input """
            if self.objectData["data"]["input"] == "up":
                self.objectData["data"]["value"] += self.objectData["step"]

            if self.objectData["data"]["input"] == "down":
                self.objectData["data"]["value"] -= self.objectData["step"]
                if self.objectData["data"]["value"] < 0:
                    self.objectData["data"]["value"] = 0

            """ Convert value to list of characters """
            temp_string = str(self.objectData["data"]["value"])
            self.objectState["value"] = [char for char in temp_string]

            if self.objectData["data"]["input"] == "DEL":
                if len(self.objectState["value"]) > 1:
                    """ If a float, delete back to the decimal value """
                    if "." in self.objectState["value"] and self.objectState["value"][-2] == ".":
                        self.objectState["value"].pop()
                        self.objectState["value"].pop()
                    else:
                        self.objectState["value"].pop()
                else:
                    self.objectState["value"] = ["0"]

            if "." in self.objectState["value"] and self.objectData["data"]["input"] == ".":
                pass
            elif self.objectData["data"]["input"] in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "."]:
                if len(self.objectState["value"]) > 5:
                    pass
                if "." in self.objectState["value"]:
                    self.objectState["value"].pop()
                    self.objectState["value"].append(self.objectData["data"]["input"])
                elif len(self.objectState["value"]) == 3 and self.objectData["data"]["input"] == ".":
                    self.objectState["value"].append(self.objectData["data"]["input"])
                elif len(self.objectState["value"]) < 3:
                    self.objectState["value"].append(self.objectData["data"]["input"])

            """ Combine list of characters back to string and then back to a float or int """
            temp_string = "".join([str(i) for i in self.objectState["value"]])

            if "." in temp_string:
                self.objectData["data"]["value"] = float(temp_string)
            else:
                self.objectData["data"]["value"] = int(temp_string)

    def _define_touch_areas(self):
        pass


class TimerStatus(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 200)  # Working Canvas Size
        fg_color = self.objectData["fg_color"]
        bg_color = self.objectData["bg_color"]

        # Create drawing object
        canvas = Image.new("RGBA", size)
        draw = ImageDraw.Draw(canvas)

        # If not in use, display empty box
        if self.objectData["data"]["seconds"] > 0:
            # Timer Background
            draw.rounded_rectangle((15, 15, size[0] - 15, size[1] - 15), radius=20, fill=bg_color)

            # Draw Stopwatch Icon
            timer_icon = self._create_icon("\uf2f2", 35, fg_color)
            canvas.paste(timer_icon, (40, 30), timer_icon)

            # Draw Timer Label on Top Portion of Box
            if len(self.objectData["label"]) > 11:
                label_displayed = self.objectData["label"][0:11]
            else:
                label_displayed = self.objectData["label"]

            timer_label = self._draw_text(label_displayed, "trebuc.ttf", 50, fg_color)
            canvas.paste(timer_label, (80, 30), timer_label)

            # Draw Seconds Remaining
            seconds_remaining = f"{self.objectData['data']['seconds']}s"
            timer_text = self._draw_text(seconds_remaining, "trebuc.ttf", 100, fg_color)
            timer_text_position = ((size[0] // 2) - (timer_text.width // 2), 90)
            canvas.paste(timer_text, timer_text_position, timer_text)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))

        canvas.paste(resized, (0, 0), resized)

        return canvas

    def _define_touch_areas(self):
        pass


class AlertMessage(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 200)  # Working Canvas Size
        fg_color = self.objectData["fg_color"]
        bg_color = self.objectData["bg_color"]

        # Create canvas & drawing object
        canvas = Image.new("RGBA", size)
        draw = ImageDraw.Draw(canvas)

        if self.objectData["active"]:
            # Draw Rectangle
            draw.rounded_rectangle(
                (15, 15, size[0] - 15, size[1] - 15), radius=20, outline=fg_color, width=6, fill=bg_color
            )

            # Draw Alert Icon
            fg_color_alpha = list(fg_color)
            fg_color_alpha[3] = 125
            fg_color_alpha = tuple(fg_color_alpha)

            alert_icon = self._create_icon("\uf071", 100, fg_color_alpha)
            alert_icon_pos = ((size[0] // 2) - (alert_icon.width // 2), (size[1] // 2) - (alert_icon.height // 2))
            canvas.paste(alert_icon, alert_icon_pos)

            text_lines = self.objectData["data"]["text"]
            num_lines = len(text_lines)
            padding = 50
            line_height = (size[1] - padding) // num_lines
            font_size = int(line_height * 0.8)
            for index, text in enumerate(text_lines):
                if len(text) > 11:
                    text_displayed = text[0:11]
                else:
                    text_displayed = text

                text_line = self._draw_text(text_displayed, "trebuc.ttf", font_size, fg_color)
                text_pos = (
                    (size[0] // 2) - (text_line.width // 2),
                    (padding // 2) + (line_height * index) + (line_height // 2) - (text_line.height // 2),
                )
                canvas.paste(text_line, text_pos, text_line)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        canvas.paste(resized, (0, 0), resized)

        return canvas

    def _define_touch_areas(self):
        pass


class FlexButton(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (200, 200)  # Working Canvas Size

        # Create canvas & drawing object
        canvas = Image.new("RGBA", size)
        draw = ImageDraw.Draw(canvas)

        color = self.objectData["active_color"] if self.objectData["active"] else self.objectData["inactive_color"]

        # Draw Rectangle
        padding = 25
        draw.rounded_rectangle(
            (padding, padding, size[0] - padding, size[1] - padding), radius=20, outline=color, width=6
        )

        # Draw Icon
        icon_code = (
            self.objectData["data"].get("active_icon", "\uf071")
            if self.objectData["active"]
            else self.objectData["data"].get("inactive_icon", "\uf071")
        )
        icon = self._create_icon(icon_code, 85, color)
        icon_pos = ((size[0] // 2) - (icon.width // 2), (size[1] // 2) - (icon.height // 2))
        canvas.paste(icon, icon_pos, icon)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        canvas.paste(resized, (0, 0), resized)

        return canvas


class PModeStatus(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 200)  # Working Canvas Size
        fg_color = self.objectData["fg_color"]
        bg_color = self.objectData["bg_color"]

        # Create canvas & drawing object
        canvas = Image.new("RGBA", size)

        if self.objectData["active"]:
            draw = ImageDraw.Draw(canvas)
            # Draw Rectangle
            draw.rounded_rectangle(
                (15, 15, size[0] - 15, size[1] - 15), radius=20, outline=fg_color, width=6, fill=bg_color
            )

            # Draw PMode Icon
            fg_color_alpha = list(fg_color)
            fg_color_alpha[3] = 125
            fg_color_alpha = tuple(fg_color_alpha)

            pmode_icon = self._create_icon("\uf83e", 100, fg_color_alpha)
            pmode_icon_pos = ((size[0] // 2) - (pmode_icon.width // 2), (size[1] // 2) - 25)
            canvas.paste(pmode_icon, pmode_icon_pos)

            # Draw Title
            text_displayed = self.objectData["label"]
            font_size = 40
            text_line = self._draw_text(text_displayed, "trebuc.ttf", font_size, fg_color)
            text_pos = ((size[0] // 2) - (text_line.width // 2), 25)
            canvas.paste(text_line, text_pos, text_line)

            # Draw PMode Number
            text_displayed = self.objectData["data"]["pmode"]
            font_size = 100
            text_line = self._draw_text(text_displayed, "trebuc.ttf", font_size, fg_color)
            text_pos = ((size[0] // 2) - (text_line.width // 2), (size[1] // 2) - 25)
            canvas.paste(text_line, text_pos, text_line)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        canvas.paste(resized, (0, 0), resized)

        return canvas


class SPlusStatus(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (200, 200)  # Working Canvas Size

        # Create canvas & drawing object
        canvas = Image.new("RGBA", size)
        draw = ImageDraw.Draw(canvas)

        color = self.objectData["active_color"] if self.objectData["active"] else self.objectData["inactive_color"]

        # Draw Rectangle
        padding = 25
        draw.rounded_rectangle(
            (padding, padding, size[0] - padding, size[1] - padding), radius=20, outline=color, width=6
        )

        # Draw Smoke Plus Icon(s)
        cloud_icon = self._create_icon("\uf0c2", 85, color)
        cloud_icon_pos = ((size[0] // 2) - (cloud_icon.width // 2) - 8, (size[1] // 2) - (cloud_icon.height // 2))
        canvas.paste(cloud_icon, cloud_icon_pos, cloud_icon)

        plus_icon = self._create_icon("\uf067", 50, color)
        plus_icon_pos = (120, 50)
        # plus_icon_pos = ((size[0] // 2) - (plus_icon.width // 2), (size[1] // 2) - (plus_icon.height // 2))
        canvas.paste(plus_icon, plus_icon_pos, plus_icon)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        canvas.paste(resized, (0, 0), resized)

        return canvas


class HopperStatus(FlexObject):
    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (400, 200)  # Working Canvas Size
        # fg_color = self.objectData['color']

        color_index = int(self.objectData["data"]["level"] // (100 / len(self.objectData["color_levels"])))
        # print(f'color_index = {color_index-1} level={self.objectData["data"]["level"]}')
        fg_color = self.objectData["color_levels"][max(color_index - 1, 0)]

        # Create canvas & drawing object
        canvas = Image.new("RGBA", size)
        draw = ImageDraw.Draw(canvas)

        # Draw Transparent Rectangle
        bg_color = (255, 255, 255, 100) if color_index != 0 else fg_color
        bg_color = list(bg_color)
        bg_color[3] = 100
        bg_color = tuple(bg_color)
        draw.rounded_rectangle((15, 15, size[0] - 15, size[1] - 15), radius=20, fill=bg_color)

        # Draw Title
        text_displayed = self.objectData["label"]
        font_size = 40
        text_line = self._draw_text(text_displayed, "trebuc.ttf", font_size, fg_color)
        text_pos = ((size[0] // 2) - (text_line.width // 2), 25)
        canvas.paste(text_line, text_pos, text_line)

        # Draw Hopper Percentage
        text_displayed = str(self.objectData["data"]["level"]) + "%"
        font_size = 100
        text_line = self._draw_text(text_displayed, "trebuc.ttf", font_size, fg_color)
        text_pos = ((size[0] // 2) - (text_line.width // 2), (size[1] // 2) - 25)
        canvas.paste(text_line, text_pos, text_line)

        # Draw Bar
        level_bar = (40, 160, 360, 170)
        current_level_adjusted = (
            int((self.objectData["data"]["level"] / 100) * 320) + 40 if self.objectData["data"]["level"] > 0 else 40
        )
        if current_level_adjusted > 360:
            current_level_adjusted = 360
        current_level_bar = (40, 160, current_level_adjusted, 170)
        draw.rounded_rectangle(level_bar, radius=10, fill=(0, 0, 0, 200))
        draw.rounded_rectangle(current_level_bar, radius=10, fill=fg_color)

        # Resize and Prepare Output
        resized = canvas.resize(output_size)
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        canvas.paste(resized, (0, 0), resized)

        return canvas


class DutyPill(FlexObject):
    """A small labeled-value pill, e.g. P-MODE/SMOKE+ or AUGER/FAN DUTY.

    Presentational only - the label/value/highlight are computed upstream
    (base_flex) and passed in via objectData['data'].
    """

    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_letterspaced_text(self, text, font_name, font_size, color, spacing):
        """Draws text with extra spacing between glyphs, bottom-aligned per glyph."""
        space_width = max(4, round(font_size * 0.35))
        parts = []
        for char in text:
            if char == " ":
                parts.append(("space", space_width))
            else:
                parts.append(("glyph", self._draw_text(char, font_name, font_size, color)))

        max_height = max((glyph.size[1] for kind, glyph in parts if kind == "glyph"), default=0)
        total_width = sum((glyph.size[0] if kind == "glyph" else glyph) for kind, glyph in parts) + spacing * max(
            0, len(parts) - 1
        )

        if total_width <= 0 or max_height <= 0:
            return Image.new("RGBA", (1, 1))

        canvas = Image.new("RGBA", (total_width, max_height))
        x = 0
        for index, (kind, glyph) in enumerate(parts):
            if kind == "glyph":
                canvas.paste(glyph, (x, max_height - glyph.size[1]), glyph)
                x += glyph.size[0]
            else:
                x += glyph
            if index < len(parts) - 1:
                x += spacing
        return canvas

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (350, 160)  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        label = str(data.get("label", "")).upper()
        value = str(data.get("value", ""))
        highlight = bool(data.get("highlight", False))

        if highlight:
            bg_fill = (94, 201, 111, 36)  # #5ec96f @ ~0.14
            border_color = (94, 201, 111, 255)  # #5ec96f
            label_color = (143, 224, 154, 255)  # #8fe09a
            value_color = (143, 224, 154, 255)  # #8fe09a
        else:
            bg_fill = (26, 22, 17, 255)  # #1a1611
            border_color = (255, 255, 255, 13)  # rgba(255,255,255,0.05)
            label_color = (125, 114, 100, 255)  # #7d7264
            value_color = accent["accent"]

        pill = Image.new("RGBA", size)
        draw = ImageDraw.Draw(pill)

        margin = 8
        radius = (size[1] - (2 * margin)) // 2
        draw.rounded_rectangle((margin, margin, size[0] - margin, size[1] - margin), radius=radius, fill=bg_fill)
        draw.rounded_rectangle(
            (margin, margin, size[0] - margin, size[1] - margin), radius=radius, outline=border_color, width=2
        )

        label_canvas = self._draw_letterspaced_text(
            label, "./static/font/Barlow-SemiBold.ttf", 22, label_color, spacing=4
        )
        value_canvas = self._draw_text(value, "./static/font/BarlowSemiCondensed-Bold.ttf", 60, value_color)

        gap = 10
        content_height = label_canvas.size[1] + gap + value_canvas.size[1]
        content_top = (size[1] - content_height) // 2

        label_x = (size[0] - label_canvas.size[0]) // 2
        pill.paste(label_canvas, (label_x, content_top), label_canvas)

        value_x = (size[0] - value_canvas.size[0]) // 2
        value_y = content_top + label_canvas.size[1] + gap
        pill.paste(value_canvas, (value_x, value_y), value_canvas)

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        pill = pill.resize(output_size)
        canvas.paste(pill, (0, 0), pill)

        return canvas


class CookTimeBar(FlexObject):
    """Horizontal cook-time bar: a rounded-rectangle card with a small label
    pinned left and the time value pinned right - the pygame counterpart of the
    Qt CookTimeBar. Renders on a canvas that preserves the output box's aspect
    ratio, so a wide/short bar downscales uniformly instead of distorting the way
    a fixed square-ish canvas (e.g. DutyPill) does when squished into a wide box.

    Presentational only - base_flex feeds data={'label','value','highlight'} to
    the 'cook_time' object by name (see base_flex._cook_time_data). When the lid
    opens in Hold mode base_flex feeds label='Lid Pause' + a mm:ss countdown; the
    bar recolors red to serve as the lid-open alert (the ember dashboards have no
    separate lid_alert overlay - one full-width bar handles both states)."""

    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_object(self):
        output_size = self.objectData["size"]
        # Work at 2x the output (aspect-preserving) for a crisp, undistorted downscale.
        width = max(1, output_size[0] * 2)
        height = max(1, output_size[1] * 2)

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        label = str(data.get("label") or "COOK TIME").upper()
        value = str(data.get("value", ""))
        # base_flex._timer_seconds_and_label() uses the 'Lid Pause' label while the
        # lid is open - render the bar as a red alert in that state.
        lid_alert = "LID" in label

        if lid_alert:
            card_fill = (48, 22, 18, 255)  # dark red-tinted
            border_color = (255, 90, 77, 255)  # #ff5a4d
            label_color = (255, 138, 128, 255)
            value_color = (255, 90, 77, 255)
            border_w = 4
        else:
            card_fill = (26, 22, 17, 255)  # #1a1611
            border_color = (255, 255, 255, 15)  # rgba(255,255,255,0.06)
            label_color = (125, 114, 100, 255)  # #7d7264
            value_color = accent["accent"] if value else (138, 127, 112, 255)
            border_w = 2

        bar = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(bar)
        radius = round(height * 0.32)
        draw.rounded_rectangle(
            (0, 0, width - 1, height - 1), radius=radius, fill=card_fill, outline=border_color, width=border_w
        )

        pad = round(height * 0.55)
        label_canvas = self._draw_text(label, "./static/font/Barlow-SemiBold.ttf", round(height * 0.28), label_color)
        value_canvas = self._draw_text(
            value, "./static/font/BarlowSemiCondensed-Bold.ttf", round(height * 0.50), value_color
        )

        bar.paste(label_canvas, (pad, (height - label_canvas.size[1]) // 2), label_canvas)
        bar.paste(
            value_canvas, (width - pad - value_canvas.size[0], (height - value_canvas.size[1]) // 2), value_canvas
        )

        # Resize to the configured output size (uniform - aspect was preserved above).
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        bar = bar.resize(output_size)
        canvas.paste(bar, (0, 0), bar)

        return canvas


class HopperVertical(FlexObject):
    """Ember pellet-hopper card: header + big percentage, a tall vertical fill
    bar (bottom-anchored) and a threshold-colored status label."""

    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_letterspaced_text(self, text, font_name, font_size, color, spacing):
        """Draws text with extra spacing between glyphs, bottom-aligned per glyph."""
        space_width = max(4, round(font_size * 0.35))
        parts = []
        for char in text:
            if char == " ":
                parts.append(("space", space_width))
            else:
                parts.append(("glyph", self._draw_text(char, font_name, font_size, color)))

        max_height = max((glyph.size[1] for kind, glyph in parts if kind == "glyph"), default=0)
        total_width = sum((glyph.size[0] if kind == "glyph" else glyph) for kind, glyph in parts) + spacing * max(
            0, len(parts) - 1
        )

        if total_width <= 0 or max_height <= 0:
            return Image.new("RGBA", (1, 1))

        canvas = Image.new("RGBA", (total_width, max_height))
        x = 0
        for index, (kind, glyph) in enumerate(parts):
            if kind == "glyph":
                canvas.paste(glyph, (x, max_height - glyph.size[1]), glyph)
                x += glyph.size[0]
            else:
                x += glyph
            if index < len(parts) - 1:
                x += spacing
        return canvas

    def _threshold(self, level):
        """Returns (color, status_label) for the given pellet level (0-100)."""
        if level < 15:
            return (255, 90, 77, 255), "REFILL PELLETS"  # #ff5a4d
        elif level < 35:
            return (255, 176, 32, 255), "RUNNING LOW"  # #ffb020
        else:
            return (94, 201, 111, 255), "LEVEL OK"  # #5ec96f

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = (300, 300)  # Working Canvas Size

        self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        level = max(0, min(100, int(data.get("level", 0))))

        dim_color = (125, 114, 100, 255)  # #7d7264
        track_fill = (255, 255, 255, 36)  # translucent light fill (~0.14 alpha)
        threshold_color, status_text = self._threshold(level)

        card = Image.new("RGBA", size)
        draw = ImageDraw.Draw(card)

        # Card background + subtle border
        draw.rounded_rectangle((10, 10, size[0] - 10, size[1] - 10), radius=18, fill=(26, 22, 17, 255))
        draw.rounded_rectangle((10, 10, size[0] - 10, size[1] - 10), radius=18, outline=(255, 255, 255, 30), width=2)

        # Header row: "HOPPER" label (left)
        header_left = 26
        header_top = 22
        label_canvas = self._draw_text("HOPPER", "./static/font/Barlow-SemiBold.ttf", 20, dim_color)
        card.paste(label_canvas, (header_left, header_top), label_canvas)

        # Header row: big percentage (right aligned, threshold colored, no arrow)
        pct_text = f"{level}%"
        pct_canvas = self._draw_text(pct_text, "./static/font/BarlowSemiCondensed-Bold.ttf", 44, threshold_color)
        pct_x = size[0] - 26 - pct_canvas.size[0]
        pct_y = header_top - ((pct_canvas.size[1] - label_canvas.size[1]) // 2)
        card.paste(pct_canvas, (pct_x, pct_y), pct_canvas)

        header_bottom = max(header_top + label_canvas.size[1], pct_y + pct_canvas.size[1]) + 14

        # Status label at the bottom, uppercase letter-spaced
        status_canvas = self._draw_letterspaced_text(
            status_text, "./static/font/Barlow-SemiBold.ttf", 15, threshold_color, spacing=3
        )
        status_bottom_margin = 20
        status_y = size[1] - status_bottom_margin - status_canvas.size[1]
        status_x = (size[0] - status_canvas.size[0]) // 2
        card.paste(status_canvas, (status_x, status_y), status_canvas)

        # Vertical fill bar: tall rounded track between the header and the status label
        bar_top_margin = 14
        bar_bottom_margin = 14
        track_left = size[0] // 2 - 34
        track_right = size[0] // 2 + 34
        track_top = header_bottom + bar_top_margin
        track_bottom = status_y - bar_bottom_margin
        track_radius = (track_right - track_left) // 2

        draw.rounded_rectangle((track_left, track_top, track_right, track_bottom), radius=track_radius, fill=track_fill)

        track_height = max(0, track_bottom - track_top)
        fill_height = round(track_height * (level / 100))
        if fill_height > 0:
            fill_top = track_bottom - fill_height
            draw.rounded_rectangle(
                (track_left, fill_top, track_right, track_bottom), radius=track_radius, fill=threshold_color
            )

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        card = card.resize(output_size)
        canvas.paste(card, (0, 0), card)

        return canvas


class HeaderBar(FlexObject):
    """Top header bar: live dot, PiFire wordmark, CONTROLLER label, IP address,
    clock and a hamburger menu button. The hamburger button is the only touch
    target on the bar - the rest of the header does not open the menu."""

    WORKING_SIZE = (1280, 58)
    PADDING = 22
    HAMBURGER_SIZE = 44

    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _draw_letterspaced_text(self, text, font_name, font_size, color, spacing):
        """Draws text with extra spacing between glyphs, bottom-aligned per glyph."""
        space_width = max(4, round(font_size * 0.35))
        parts = []
        for char in text:
            if char == " ":
                parts.append(("space", space_width))
            else:
                parts.append(("glyph", self._draw_text(char, font_name, font_size, color)))

        max_height = max((glyph.size[1] for kind, glyph in parts if kind == "glyph"), default=0)
        total_width = sum((glyph.size[0] if kind == "glyph" else glyph) for kind, glyph in parts) + spacing * max(
            0, len(parts) - 1
        )

        if total_width <= 0 or max_height <= 0:
            return Image.new("RGBA", (1, 1))

        canvas = Image.new("RGBA", (total_width, max_height))
        x = 0
        for index, (kind, glyph) in enumerate(parts):
            if kind == "glyph":
                canvas.paste(glyph, (x, max_height - glyph.size[1]), glyph)
                x += glyph.size[0]
            else:
                x += glyph
            if index < len(parts) - 1:
                x += spacing
        return canvas

    def _hamburger_rect_working(self):
        """Returns (left, top, width, height) of the hamburger button in working-canvas coords."""
        box_size = self.HAMBURGER_SIZE
        left = self.WORKING_SIZE[0] - self.PADDING - box_size
        top = (self.WORKING_SIZE[1] - box_size) // 2
        return (left, top, box_size, box_size)

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = self.WORKING_SIZE  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        data = self.objectData.get("data", {})
        ip_address = str(data.get("ip", ""))
        clock_text = str(data.get("clock", ""))
        cooking = bool(data.get("cooking", False))

        light_color = (244, 237, 226, 255)  # #f4ede2
        dim_color = (125, 114, 100, 255)  # #7d7264
        ip_color = (138, 127, 112, 255)  # #8a7f70
        clock_color = (207, 198, 184, 255)  # #cfc6b8
        hairline_color = (255, 255, 255, 15)  # rgba(255,255,255,0.06)
        separator_color = (255, 255, 255, 20)  # rgba(255,255,255,0.08)
        live_active = (94, 201, 111, 255)  # #5ec96f
        live_idle = (125, 114, 100, 255)  # #7d7264
        hamburger_fill = (29, 24, 19, 255)  # #1d1813
        hamburger_bar_color = (207, 198, 184, 255)  # #cfc6b8

        card = Image.new("RGBA", size)
        draw = ImageDraw.Draw(card)

        # Bottom hairline
        draw.line([(0, size[1] - 1), (size[0], size[1] - 1)], fill=hairline_color, width=1)

        # --- Left cluster: live dot + wordmark + CONTROLLER label ---
        x = self.PADDING
        dot_diameter = 12
        dot_top = (size[1] - dot_diameter) // 2
        dot_color = live_active if cooking else live_idle
        draw.ellipse((x, dot_top, x + dot_diameter, dot_top + dot_diameter), fill=dot_color)
        x += dot_diameter + 12

        pi_label = self._draw_text("Pi", "./static/font/Barlow-SemiBold.ttf", 20, light_color)
        fire_label = self._draw_text("Fire", "./static/font/Barlow-SemiBold.ttf", 20, accent["accent"])
        wordmark_height = max(pi_label.size[1], fire_label.size[1])
        wordmark_y = (size[1] - wordmark_height) // 2
        card.paste(pi_label, (x, wordmark_y), pi_label)
        x += pi_label.size[0]
        card.paste(fire_label, (x, wordmark_y), fire_label)
        x += fire_label.size[0]

        x += 12
        separator_top = (size[1] // 2) - 7
        separator_bottom = (size[1] // 2) + 7
        draw.line([(x, separator_top), (x, separator_bottom)], fill=separator_color, width=1)
        x += 1 + 10

        controller_label = self._draw_letterspaced_text(
            "CONTROLLER", "./static/font/Barlow-SemiBold.ttf", 12, dim_color, spacing=2
        )
        controller_y = (size[1] - controller_label.size[1]) // 2
        card.paste(controller_label, (x, controller_y), controller_label)

        # --- Right cluster: IP, clock, hamburger button ---
        hamburger_left, hamburger_top, hamburger_size, _ = self._hamburger_rect_working()
        draw.rounded_rectangle(
            (hamburger_left, hamburger_top, hamburger_left + hamburger_size, hamburger_top + hamburger_size),
            radius=12,
            fill=hamburger_fill,
            outline=separator_color,
            width=1,
        )
        bar_width = 20
        bar_height = 2
        bar_gap = 4
        bars_total_height = (bar_height * 3) + (bar_gap * 2)
        bars_top = hamburger_top + (hamburger_size - bars_total_height) // 2
        bar_x = hamburger_left + (hamburger_size - bar_width) // 2
        for bar_index in range(3):
            bar_y = bars_top + bar_index * (bar_height + bar_gap)
            draw.rounded_rectangle(
                (bar_x, bar_y, bar_x + bar_width, bar_y + bar_height), radius=1, fill=hamburger_bar_color
            )

        clock_label = self._draw_text(clock_text, "./static/font/BarlowSemiCondensed-SemiBold.ttf", 22, clock_color)
        clock_right = hamburger_left - 18
        clock_x = clock_right - clock_label.size[0]
        clock_y = (size[1] - clock_label.size[1]) // 2
        card.paste(clock_label, (clock_x, clock_y), clock_label)

        ip_label = self._draw_text(ip_address, "./static/font/Barlow-SemiBold.ttf", 13, ip_color)
        ip_right = clock_x - 18
        ip_x = ip_right - ip_label.size[0]
        ip_y = (size[1] - ip_label.size[1]) // 2
        card.paste(ip_label, (ip_x, ip_y), ip_label)

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        card = card.resize(output_size)
        canvas.paste(card, (0, 0), card)

        return canvas

    def _define_touch_areas(self):
        """Restricts the touch area to the hamburger button only, so the rest of
        the header bar is not a menu tap target."""
        hamburger_rect = self._hamburger_rect_working()
        output_size = self.objectData["size"]
        scaled_rect = self._scale_touch_area(hamburger_rect, self.WORKING_SIZE, output_size)
        translated_rect = self._transform_touch_area(scaled_rect, self.objectData["position"])
        self.objectData["touch_areas"] = [Rect(translated_rect)]


class ButtonRow(FlexObject):
    """Row of N equal-width mode-dependent control buttons at the bottom of
    the center column (e.g. Set Temp / Hold / Stop / Shutdown).

    Presentational only - base_flex computes the button set for the current
    operating mode (button_type/button_list) and which one, if any, should
    be shown active (button_active); this widget renders the row and
    subdivides touch so each button maps to its own action.
    """

    WORKING_SIZE = (1200, 164)
    GAP = 24
    RADIUS = 32
    BORDER_WIDTH = 4

    DANGER_ACTIONS = ("cmd_stop", "cmd_shutdown")

    def __init__(self, objectType, objectData, background):
        super().__init__(objectType, objectData, background)

    def _border_color(self, label, action, active, accent):
        if action in self.DANGER_ACTIONS:
            return (255, 90, 77, 255)  # #ff5a4d - danger
        if active and label == active:
            return (94, 201, 111, 255)  # #5ec96f - ok / active
        return accent["accent"]

    def _draw_object(self):
        output_size = self.objectData["size"]
        size = self.WORKING_SIZE  # Working Canvas Size

        accent = self.objectData.get("accent", resolve_accent("Ember"))
        button_type = self.objectData.get("button_type", [])
        button_list = self.objectData.get("button_list", [])
        active = self.objectData.get("button_active", "")

        fill_color = (29, 24, 19, 255)  # #1d1813
        text_color = (232, 223, 209, 255)  # #e8dfd1
        active_fill = _lerp_color(fill_color[:3], (94, 201, 111), 0.16) + (255,)  # subtle green tint

        row = Image.new("RGBA", size)
        draw = ImageDraw.Draw(row)

        count = len(button_type)
        if count > 0:
            button_width = (size[0] - self.GAP * (count - 1)) // count

            for index in range(count):
                label = button_type[index]
                action = button_list[index] if index < len(button_list) else ""

                left = index * (button_width + self.GAP)
                right = left + button_width if index < count - 1 else size[0]

                border_color = self._border_color(label, action, active, accent)
                btn_fill = (
                    active_fill if (active and label == active and action not in self.DANGER_ACTIONS) else fill_color
                )

                draw.rounded_rectangle((left, 0, right, size[1]), radius=self.RADIUS, fill=btn_fill)
                draw.rounded_rectangle(
                    (left, 0, right, size[1]), radius=self.RADIUS, outline=border_color, width=self.BORDER_WIDTH
                )

                label_canvas = self._draw_text(str(label), "./static/font/Barlow-SemiBold.ttf", 46, text_color)
                label_x = left + ((right - left) - label_canvas.size[0]) // 2
                label_y = (size[1] - label_canvas.size[1]) // 2
                row.paste(label_canvas, (label_x, label_y), label_canvas)

        # Resize to configured output size
        canvas = Image.new("RGBA", (output_size[0], output_size[1]))
        row = row.resize(output_size)
        canvas.paste(row, (0, 0), row)

        return canvas

    def _define_touch_areas(self):
        """Subdivides the row into N evenly-spaced touch areas, one per
        button, so touch_areas[i] maps to button_list[i]."""
        button_list = self.objectData.get("button_list", [])
        count = len(button_list)
        self.objectData["touch_areas"] = []
        if count == 0:
            return

        spacing = int(self.objectData["size"][0] / count)
        for index in range(count):
            x_left = self.objectData["position"][0] + (index * spacing)
            y_top = self.objectData["position"][1]
            width = spacing
            height = self.objectData["size"][1]
            touch_area = Rect(x_left, y_top, width, height)
            self.objectData["touch_areas"].append(touch_area)
