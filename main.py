"""
Formaldehyde Exposure Monitor (detector v3)

Name:        Gordon Sweeney
Course:      CHEM096 - Formaldehyde Badge Research
Date:        October 2026
Hardware:    UNIHIKER K10 (ESP32-S3) + Gravity SFA40 formaldehyde sensor
Language:    MicroPython, saved on the board as main.py

Description:
    Reads formaldehyde concentration from an SFA40 sensor twice per second
    and shows it on the K10's screen in parts per billion and parts per
    million. The user picks an alarm limit from published occupational
    standards (NIOSH, WHO, ACGIH, OSHA) with button A. When the reading
    goes over that limit the screen flashes, the RGB LEDs flash red, and
    the speaker beeps. Every reading is saved to a CSV file on the board
    so a whole session can be graphed afterward.

Algorithm:
    1. Load the saved alarm limit from settings.txt
    2. Check for an off file, so the program can be disabled for editing
    3. Find the SFA40 on the I2C bus and start its measurement
    4. Open a new log file and write the column headings
    5. Repeat forever:
         a. Read formaldehyde, temperature and humidity from the sensor
         b. Verify the checksum and discard the reading if it fails
         c. Treat the first two minutes as warm-up (readings not valid yet)
         d. Check the buttons: A changes the limit, B marks the log
         e. Compare the reading with the limit and set the alarm state
         f. Update the screen, the LEDs and the speaker
         g. Save a line to the log file once per second

Notes:
    The SFA40 commands and conversion formulas follow the Sensirion SFA40
    datasheet; the sensor routines are adapted from DFRobot's MIT-licensed
    DFRobot_SFA40 library. The K10's own speaker functions do not work in
    this firmware, so the alarm writes audio to the amplifier directly.

"""

from machine import I2C, Pin
from unihiker_k10 import screen, rgb, button
import time
import os

# ---------------- Settings ----------------
READ_EVERY_S = 0.5     # how often to read the sensor (sensor updates ~0.7 s)
LOG_EVERY_S = 1        # seconds between saved readings
DEFAULT_LIMIT = 500    # alarm limit in ppb (OSHA action level = 500 ppb)

# Alarm limits come from published standards, so nobody has to guess what
# counts as harmful. Each one is shown with a plain-English explanation.
# (ppb, who set it, explanation line 1, explanation line 2)
LIMIT_CHOICES = [
    (1, "TEST setting",
     "Alarms right away,", "for testing only"),
    (16, "NIOSH advice",
     "US health agency's", "safest advice (10 hr)"),
    (80, "WHO guideline",
     "World Health Org.", "home air, 30 min"),
    (100, "NIOSH ceiling",
     "Never go above this", "for even 15 min"),
    (300, "ACGIH ceiling",
     "Hygienists' limit,", "never go above"),
    (500, "OSHA action level",
     "Employer must start", "monitoring workers"),
    (750, "OSHA legal limit",
     "Legal max average", "over an 8 hr shift"),
    (2000, "OSHA short-term",
     "Legal max for any", "15 minute period"),
]

ACTION_LEVEL = 0.5     # OSHA action level (ppm)
TWA_LIMIT = 0.75       # OSHA 8-hour limit (ppm)
SENSOR_MAX_PPB = 2000  # SFA40 upper limit (Sensirion datasheet)

SETTINGS_FILE = "settings.txt"
OFF_FILE = "off.txt"

# ---------------- SFA40 sensor ----------------
ADDR = 0x5D
CMD_START = 0x00AC
CMD_STOP = 0x50D2
CMD_READ = 0xC0EB
CMD_READ_B4 = 0xE06D
CMD_ID = 0x02CE
CMD_ID_B4 = 0x0559

i2c = I2C(0, scl=Pin(48), sda=Pin(47), freq=100000)
read_cmd = CMD_READ
status_index = 9


def crc8(two_bytes):
    """Return the checksum byte the sensor uses to verify its data."""
    crc = 0xFF
    for b in two_bytes:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def crc_ok(data):
    """Return True if every group of bytes passes its checksum."""
    for i in range(0, len(data), 3):
        if crc8(data[i:i + 2]) != data[i + 2]:
            return False
    return True


def send(cmd):
    """Send a two-byte command to the sensor."""
    i2c.writeto(ADDR, bytes([cmd >> 8, cmd & 0xFF]))


def request(cmd, n):
    """Send a command, then read n bytes back from the sensor."""
    send(cmd)
    time.sleep_ms(5)
    return i2c.readfrom(ADDR, n)


def sensor_begin():
    """Find the sensor and work out which protocol version it uses.

    Returns True if the sensor answers on the I2C bus.
    """
    global read_cmd, status_index
    if ADDR not in i2c.scan():
        return False
    send(CMD_STOP)
    time.sleep_ms(30)
    try:
        if crc_ok(request(CMD_ID, 9)):
            read_cmd, status_index = CMD_READ, 9
            return True
    except OSError:
        pass
    try:
        if crc_ok(request(CMD_ID_B4, 15)):
            read_cmd, status_index = CMD_READ_B4, 10
    except OSError:
        pass
    return True


def sensor_read():
    """Read one measurement from the sensor.

    Returns (status, ppb, temperature_c, humidity), where status is
    0 ready, 1 warming up, 2 settling, 3 read error.
    """
    try:
        d = request(read_cmd, 12)
    except OSError:
        return 3, None, None, None
    if not crc_ok(d):
        return 3, None, None, None
    hcho = (d[0] * 256 + d[1]) / 10.0
    hum = 125.0 * ((d[3] * 256 + d[4]) / 65535.0) - 6
    hum = max(0, min(100, hum))
    temp = 175.0 * ((d[6] * 256 + d[7]) / 65535.0) - 45
    if d[status_index] & 0x01:
        return 1, hcho, temp, hum
    if d[status_index] & 0x02:
        return 2, hcho, temp, hum
    return 0, hcho, temp, hum


# ---------------- Files ----------------
def file_exists(name):
    """Return True if a file with this name is on the board."""
    return name in os.listdir()


def load_limit():
    """Read the saved alarm limit, or use the default if none is saved."""
    try:
        with open(SETTINGS_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return DEFAULT_LIMIT


def save_limit(value):
    """Save the alarm limit so it survives a power cycle."""
    try:
        with open(SETTINGS_FILE, "w") as f:
            f.write(str(value))
    except Exception as e:
        print("Could not save limit:", e)


def new_log_name():
    """Return the first unused log file name, such as log7.csv."""
    files = os.listdir()
    n = 1
    while "log{}.csv".format(n) in files:
        n += 1
    return "log{}.csv".format(n)


def log_line(text):
    """Add one line to the log file and close it again straight away."""
    with open(log_name, "a") as f:
        f.write(text + "\n")


def clock(seconds):
    """Turn a number of seconds into minutes:seconds, like 7:05."""
    return "{}:{:02d}".format(seconds // 60, seconds % 60)


# ---------------- Alarm hardware ----------------
# Sound: the K10's own speaker functions do not work in this firmware
# (play_tone writes to the microphone's bus and errors; the WAV players
# accept a file and stay silent). So the alarm drives the speaker
# amplifier directly over I2S instead.
# K10 audio pins: BCLK 0, LRCK 38, data-out 45.
USE_SOUND = True
BEEP_FREQ = 2000       # Hz
BEEP_LENGTH = 0.15     # seconds of tone per beep

# Vibration: the K10 has no vibration motor. Plug a Gravity vibration
# module into P0 and set USE_VIBRATION to True to use one.
USE_VIBRATION = False

audio = None
beep_block = None

if USE_SOUND:
    try:
        from machine import I2S
        import math
        import struct

        rate = 16000
        samples_per_cycle = rate // BEEP_FREQ

        # Build one cycle of a sine wave, then repeat it to make a beep
        cycle = bytearray()
        for i in range(samples_per_cycle):
            value = int(12000 * math.sin(2 * math.pi * i / samples_per_cycle))
            cycle = cycle + struct.pack("<h", value)
        repeats = int(rate * BEEP_LENGTH) // samples_per_cycle
        beep_block = bytes(cycle) * repeats

        audio = I2S(0, sck=Pin(0), ws=Pin(38), sd=Pin(45),
                    mode=I2S.TX, bits=16, format=I2S.MONO,
                    rate=rate, ibuf=8000)
        print("Sound ready")
    except Exception as e:
        print("No sound:", e)
        audio = None


def beep():
    """Play one short tone through the speaker amplifier."""
    if audio is None:
        return
    try:
        audio.write(beep_block)
    except Exception:
        pass


# Vibration motor (optional), on pin P0
vib = None
if USE_VIBRATION:
    try:
        from unihiker_k10 import pin
        vib = pin(0)
        vib.write_digital(value=0)
    except Exception as e:
        print("No vibration pin:", e)


def vibrate(on):
    """Switch the optional vibration motor on or off."""
    if vib is None:
        return
    try:
        vib.write_digital(value=1 if on else 0)
    except Exception:
        pass


# ---------------- Buttons ----------------
bt_a = button(button.a)
bt_b = button(button.b)
a_pressed = False
b_pressed = False


def on_a():
    """Button A was pressed."""
    global a_pressed
    a_pressed = True


def on_b():
    """Button B was pressed."""
    global b_pressed
    b_pressed = True


bt_a.event_pressed = on_a
bt_b.event_pressed = on_b

# ---------------- Screen ----------------
WHITE = 0xFFFFFF
BLACK = 0x000000
GREEN = 0x00A000
ORANGE = 0xFF8000
RED = 0xFF0000
BLUE = 0x0000FF
GRAY = 0x808080


def show(y, text, color, bg=WHITE):
    """Erase one row of the screen and draw text on it."""
    screen.draw_rect(x=0, y=y, w=240, h=28, bcolor=bg, fcolor=bg)
    screen.draw_text(text=text, x=10, y=y, font_size=24, color=color)


def clear_screen():
    """Erase every row the program draws on.

    Painting one big rectangle did not reliably clear old text on this
    screen, but erasing row by row does.
    """
    rows = [10, 45, 80, 110, 145, 178, 200, 235]
    for y in rows:
        screen.draw_rect(x=0, y=y, w=240, h=30, bcolor=WHITE, fcolor=WHITE)
    screen.show_draw()


def show_small(y, text, color):
    """Same as show(), in a smaller font for explanation lines."""
    screen.draw_rect(x=0, y=y, w=240, h=22, bcolor=WHITE, fcolor=WHITE)
    screen.draw_text(text=text, x=10, y=y, font_size=16, color=color)


screen.init(dir=2)
screen.show_bg(color=WHITE)
rgb.brightness(3)

# ---------------- Disabled mode ----------------
# Hold A at startup -> board stays idle (survives restarts) so Thonny is free.
# Hold B at startup -> re-enable.
if file_exists(OFF_FILE):
    show(10, "DISABLED", RED)
    show(45, "Hold B to enable", GRAY)
    screen.show_draw()
    for _ in range(30):
        if bt_b.status() == 1:
            os.remove(OFF_FILE)
            show(45, "Enabled. Restarting", GREEN)
            screen.show_draw()
            time.sleep(1)
            break
        time.sleep_ms(100)
    else:
        raise SystemExit

show(10, "HCHO Detector v3", BLUE)
show_small(45, "Hold A = off (for Thonny)", GRAY)
screen.show_draw()

limit = load_limit()
limit_index = 0
for i in range(len(LIMIT_CHOICES)):
    if LIMIT_CHOICES[i][0] == limit:
        limit_index = i
limit = LIMIT_CHOICES[limit_index][0]

# Hold A in the first 4 seconds to switch the board off, so Thonny can connect
for i in range(40):
    if bt_a.status() == 1:
        f = open(OFF_FILE, "w")
        f.write("off")
        f.close()
        show(45, "DISABLED for Thonny", RED)
        screen.show_draw()
        raise SystemExit
    time.sleep_ms(100)
a_pressed = False
b_pressed = False

show(45, "Finding sensor...", BLACK)
screen.show_draw()

while not sensor_begin():
    print("SFA40 not found. Check the cable.")
    show(45, "Sensor NOT found", RED)
    screen.show_draw()
    time.sleep(2)

clear_screen()
show(10, "HCHO Detector v3", BLUE)

log_name = new_log_name()
log_line("elapsed_s,elapsed,status,hcho_ppb,temp_c,humidity_pct,"
         "limit_ppb,alarm,marker")
print("SFA40 found. Limit {} ppb ({}). Logging to {}".format(
    limit, LIMIT_CHOICES[limit_index][1], log_name))
send(CMD_START)
time.sleep(1)

# ---------------- Main loop ----------------
start = time.ticks_ms()
last_log_s = -LOG_EVERY_S
marker_count = 0
alarm = False
flash = False

while True:
    elapsed_s = time.ticks_diff(time.ticks_ms(), start) // 1000
    status, ppb, temp, hum = sensor_read()

    # First 2 minutes after startup the sensor ramps up from ~0
    if elapsed_s < 120 and status == 0:
        status = 1

    if status == 3:
        print("Read failed")
        show(45, "Read error", RED)
        screen.show_draw()
        time.sleep(1)
        continue

    # Button A steps through the alarm limits, right on this screen
    if a_pressed:
        a_pressed = False
        limit_index = limit_index + 1
        if limit_index >= len(LIMIT_CHOICES):
            limit_index = 0
        limit = LIMIT_CHOICES[limit_index][0]
        save_limit(limit)
        print("Limit set to {} ppb ({})".format(
            limit, LIMIT_CHOICES[limit_index][1]))

    marker = ""
    if b_pressed:
        b_pressed = False
        marker_count += 1
        marker = "M{}".format(marker_count)
        print("Marker", marker, "at", clock(elapsed_s))


    # ---- Alarm (with a small gap so it doesn't chatter on and off) ----
    # No alarm during warm-up, because those readings are not real yet
    was_alarm = alarm
    if status == 1:
        alarm = False
    elif ppb >= limit:
        alarm = True
    elif ppb < limit * 0.9:
        alarm = False
    if alarm and not was_alarm:
        marker = marker or "ALARM"
        print("ALARM at {:.1f} ppb".format(ppb))

    ppm = ppb / 1000
    if alarm:
        color = RED
    elif ppm >= TWA_LIMIT:
        color = RED
    elif ppm >= ACTION_LEVEL:
        color = ORANGE
    else:
        color = GREEN

    # ---- Screen ----
    if alarm:
        flash = not flash
        show(45, "!! OVER LIMIT !!" if flash else "", RED)
    elif status == 1:
        show(45, "Warming up (2 min)", GRAY)
    elif status == 2:
        show(45, "Settling (<10 min)", ORANGE)
    else:
        show(45, "Ready", GREEN)

    if ppb >= SENSOR_MAX_PPB:
        show(80, "HCHO: ABOVE 2 ppm", RED)
        show(110, "(sensor max)", RED)
    else:
        show(80, "HCHO: {:.0f} ppb".format(ppb), color)
        show(110, "      {:.3f} ppm".format(ppm), color)

    show(145, "Limit: {} ppb".format(limit), BLACK)
    show_small(178, LIMIT_CHOICES[limit_index][1] + " (A changes)", GRAY)
    show(200, "{:.1f} C   {:.0f} %RH".format(temp, hum), BLACK)
    show_small(235, "{}  {}".format(log_name, clock(elapsed_s)), GRAY)
    screen.show_draw()

    # ---- Alarm outputs ----
    if alarm:
        rgb.write(num=-1, color=RED if flash else 0x000000)
        vibrate(flash)
        if flash:
            beep()
    else:
        rgb.write(num=-1, color=(GRAY if status == 1 else color))
        vibrate(False)

    # ---- Save to file ----
    if elapsed_s - last_log_s >= LOG_EVERY_S or marker:
        if not marker:
            last_log_s = elapsed_s
        line = "{},{},{},{:.1f},{:.2f},{:.1f},{},{},{}".format(
            elapsed_s, clock(elapsed_s), status, ppb,
            temp, hum, limit, 1 if alarm else 0, marker)
        log_line(line)
        print(line)

    time.sleep(READ_EVERY_S)