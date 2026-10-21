"""
Formaldehyde Exposure Monitor (detector v4)

Name:        Gordon Sweeney
Course:      CHEM096 - Formaldehyde Badge Research
Date:        October 2026
Hardware:    UNIHIKER K10 (ESP32-S3) + Gravity SFA40 formaldehyde sensor
Language:    MicroPython, saved on the board as main.py

Description:
    Reads formaldehyde concentration from an SFA40 sensor twice per second
    and shows it on the K10's screen. It alarms in three separate ways:

      1. Instantaneous: a single reading crosses a warning level the user
         picks with button A. This is the fast "get away from it" alert.
      2. STEL: the rolling 15-minute average crosses OSHA's short-term
         limit of 2 ppm.
      3. TWA: the rolling 8-hour average crosses OSHA's permissible
         exposure limit of 0.75 ppm.

    The OSHA limits are defined as averages over those periods, not as
    values a single reading may never touch, so comparing one reading
    against them would not measure what the regulation describes. The
    rolling averages do. Every reading, plus both running averages, is
    saved to a CSV file on the board so a session can be graphed later.

Algorithm:
    1. Load the saved alarm limit from settings.txt
    2. Check for an off file, so the program can be disabled for editing
    3. Find the SFA40 on the I2C bus and start its measurement
    4. Open a new log file and write the column headings
    5. Repeat forever:
         a. Read formaldehyde, temperature and humidity from the sensor
         b. Verify the checksum and discard the reading if it fails
         c. Treat the first two minutes as warm-up (readings not valid yet)
         d. Add the reading to the 15-minute and 8-hour rolling averages
         e. Check the buttons: A changes the warning level, B marks the log
         f. Set the three alarm states and update screen, LEDs and speaker
         g. Save a line to the log file once per second

Notes:
    The SFA40 commands and conversion formulas follow the Sensirion SFA40
    datasheet; the sensor routines are adapted from DFRobot's MIT-licensed
    DFRobot_SFA40 library. The K10's own speaker functions do not work in
    this firmware, so the alarm writes audio to the amplifier directly.

IF YOU ARE HERE TO WRITE CODE

    Six jobs are marked in this file, JOB 1 to JOB 6. Search the file
    for the word JOB and you will land on each one.

    JOB 1, 2 and 3 are changing numbers and words that are already
    there. There is no new code to write.

    JOB 4, 5 and 6 are three short functions to fill in. Each one says
    what it has to do and shows the shape of the answer.

    Everything else in this file already works. The device runs right
    now. Your jobs change how it sounds, what it says, and what colour
    it shows.

    You cannot break anything for good. The original is on GitHub.

"""

from machine import I2C, Pin
from unihiker_k10 import screen, rgb, button
import time
import os

# ---------------- Settings ----------------
# Everything in this section is a number you can change without touching
# any of the logic. This is the friendliest place to start.
#
# TRY THIS: set LOG_EVERY_S to 5 and see how much smaller the log file
# gets, or set READ_EVERY_S to 2 and watch the screen update slowly.

READ_EVERY_S = 0.5     # how often to read the sensor (sensor updates ~0.7 s)
LOG_EVERY_S = 1        # seconds between saved readings
DEFAULT_LIMIT = 500    # warning level in ppb used before one is saved

# Alarm limits come from published standards, so nobody has to guess what
# counts as harmful. Each one is shown with a plain-English explanation.
# These are INSTANTANEOUS warning levels: one reading crossing the value
# raises the fast alarm. The 15-minute and 8-hour OSHA limits are handled
# separately by the rolling averages further down.
# (ppb, who set it, explanation line 1, explanation line 2)
# JOB 3
# This is the list of warning levels that button A flips through.
# Each row has four parts: the number in ppb, who set it, and two short
# lines of explanation that appear on the screen.
# Copy a row, paste it in, and change the four parts to add a level of
# your own. Keep the rows in order, lowest number first, and keep the
# brackets and commas exactly where they are.
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

# The two OSHA limits below are averages over a period of time, not
# numbers a single reading may never touch. That distinction is the whole
# reason the rolling averages further down exist. ppb means parts per
# billion; 1000 ppb = 1 ppm (parts per million).
STEL_LIMIT_PPB = 2000     # 2 ppm averaged over any 15 minutes
STEL_WINDOW_S = 15 * 60   # 15 minutes
TWA_LIMIT_PPB = 750       # 0.75 ppm averaged over an 8 hour shift
TWA_WINDOW_MIN = 8 * 60   # 8 hours, stored as one value per minute

ACTION_LEVEL = 0.5     # OSHA action level (ppm)
SENSOR_MAX_PPB = 2000  # SFA40 upper limit (Sensirion datasheet)

SETTINGS_FILE = "settings.txt"
OFF_FILE = "off.txt"

# ---------------- SFA40 sensor ----------------
# The sensor is a separate chip on the end of a cable. The board talks to
# it over I2C, which is a two-wire bus: one wire for a clock, one for
# data. Every device on the bus has an address, and this sensor's is 0x5D
# (the 0x means the number is written in hexadecimal, so 0x5D = 93).
#
# The numbers below are commands the sensor understands. They come from
# the manufacturer's datasheet; there is no way to guess them.
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
    """Return the checksum byte the sensor uses to verify its data.

    A checksum is a small number calculated from the data. The sensor
    sends both, and if our calculation does not match the number it
    sent, the data got scrambled on the wire and we throw it away.
    You do not need to follow the bit-shifting to use this function.
    """
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


# ---------------- Rolling exposure averages ----------------
# This is the heart of the project.
#
# What harms a person is dose: how much they breathed, multiplied by how
# long. Ten seconds beside an open bottle is not the same as an hour in a
# badly ventilated room, even though the ten-second number looks worse.
#
# OSHA writes its limits as averages over a period:
#     2 ppm    averaged over any 15 minutes   (the "STEL")
#     0.75 ppm averaged over an 8 hour shift  (the "TWA")
# Note the word averaged. "2 ppm" does not mean a single reading may
# never touch 2 ppm. So the device keeps two rolling averages.
#
# A rolling average keeps the most recent readings in a list. A new one
# goes on the end, the oldest falls off the front, and the list always
# describes the last 15 minutes or the last 8 hours.
#
# Each list also keeps a running total, so finding the average is one
# division instead of adding up 900 numbers twice a second.

stel_values = []          # one reading per second, last 15 minutes
stel_total = 0.0
twa_values = []           # one average per minute, last 8 hours
twa_total = 0.0

second_total = 0.0        # readings collected during the current second
second_count = 0
minute_total = 0.0        # seconds collected during the current minute
minute_count = 0


def add_second(value):
    """Add one second's reading to the 15-minute and 8-hour buffers."""
    global stel_total, twa_total, minute_total, minute_count

    stel_values.append(value)
    stel_total = stel_total + value
    if len(stel_values) > STEL_WINDOW_S:
        stel_total = stel_total - stel_values.pop(0)

    minute_total = minute_total + value
    minute_count = minute_count + 1
    if minute_count >= 60:
        minute_average = minute_total / minute_count
        minute_total = 0.0
        minute_count = 0
        twa_values.append(minute_average)
        twa_total = twa_total + minute_average
        if len(twa_values) > TWA_WINDOW_MIN:
            twa_total = twa_total - twa_values.pop(0)


def stel_average():
    """Average of the 15-minute buffer, in ppb."""
    if len(stel_values) == 0:
        return 0.0
    return stel_total / len(stel_values)


def twa_average():
    """Average of the 8-hour buffer, in ppb."""
    if len(twa_values) == 0:
        return 0.0
    return twa_total / len(twa_values)


def stel_ready():
    """True once a full 15 minutes of readings has been collected.

    Until then the average covers a shorter period, so it is not a
    15-minute average and must not raise the short-term alarm. Ten
    seconds at 3000 ppb is only about 33 ppb across a real 15 minutes.
    """
    return len(stel_values) >= STEL_WINDOW_S


def twa_ready():
    """True once at least 15 minutes of readings has been collected."""
    return len(twa_values) >= 15


def safe(value, fallback):
    """Used while JOBS 4 to 6 are still empty.

    An empty function gives back nothing, and this swaps that for
    something sensible so the detector keeps working. Leave it alone.
    """
    if value is None:
        return fallback
    return value


def percent_of(value, limit):
    """JOB 4

    This gives back a percentage. If the reading is 375 and the limit
    is 750, it should give back 50, because 375 is half of 750.

    Write one line:

        return int(100 * value / limit)

    int() chops off the decimal, so you get 50 instead of 50.0.

    Then delete the word pass below.
    """
    pass


def pick_color(ppb, limit):
    """JOB 5

    This picks what colour the big number on the screen is shown in.

    ppb is the reading right now. limit is the warning level.

    The rules:
        reading is at or above the limit        -> RED
        reading is at or above half the limit   -> ORANGE
        anything lower                          -> GREEN

    Write it like this:

        if ppb >= limit:
            return RED
        if ppb >= limit / 2:
            return ORANGE
        return GREEN

    Then delete the word pass below.
    """
    pass


def status_word(percent):
    """JOB 6

    This turns a percentage into a word, so somebody can glance at the
    screen and know where they stand without reading numbers.

    The rules:
        100 or more  -> "OVER"
        50 or more   -> "MEDIUM"
        anything less -> "LOW"

    Same shape as JOB 5. The words go in quote marks.

    Change the words if you like. They appear on the screen, so
    "FINE", "WATCH IT" and "GET OUT" would work just as well.

    Then delete the word pass below.
    """
    pass


# ---------------- Alarm hardware ----------------
# A worker will not be staring at the screen, so the alarm also has to
# make noise and light up. Both are set up here.
#
# Sound: the K10's own speaker functions do not work in this firmware
# (play_tone writes to the microphone's bus and errors; the WAV players
# accept a file and stay silent). So the alarm drives the speaker
# amplifier directly over I2S instead.
# K10 audio pins: BCLK 0, LRCK 38, data-out 45.
USE_SOUND = True

# JOB 1
# BEEP_FREQ is the pitch of the alarm beep. Bigger number, higher pitch.
# BEEP_LENGTH is how long each beep lasts, in seconds.
# Change either number, put the file back on the board, and listen.
# Try 1000 for a low beep or 3000 for a sharp one. There is no wrong
# answer here; pick what you would want to hear in a lab.
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
# The K10 has two buttons, A and B. Instead of checking them constantly,
# the board calls these functions the moment a button goes down, and they
# set a flag. The main loop checks the flag and clears it. That way a
# press is never missed while the program is busy drawing the screen.
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
# Colors are written as 0xRRGGBB: two hex digits each for red, green and
# blue. 0xFF0000 is full red, 0x00A000 is a medium green.
#
# TRY THIS: change ORANGE to something else and see it on screen.
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
# The program starts automatically when the board powers up, which makes
# it hard to edit: the board is busy, so Thonny cannot connect. Holding A
# at startup writes a file called off.txt and quits. While that file
# exists the program stops immediately every time, so you can edit in
# peace. Holding B at startup deletes it and the detector runs again.
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
log_line("elapsed_s,elapsed,status,hcho_ppb,stel_15min_ppb,twa_8hr_ppb,"
         "temp_c,humidity_pct,warn_level_ppb,alarm_now,alarm_stel,"
         "alarm_twa,marker")
print("SFA40 found. Limit {} ppb ({}). Logging to {}".format(
    limit, LIMIT_CHOICES[limit_index][1], log_name))
send(CMD_START)
time.sleep(1)

# ---------------- Main loop ----------------
# Everything above was preparation. This loop runs forever, about twice a
# second, and each pass does the same seven things:
#
#   read the sensor -> update the averages -> check the buttons ->
#   decide the alarms -> draw the screen -> sound and lights -> save a line
#
# If you are adding a feature, it almost certainly belongs in here, next
# to whichever of those steps it relates to.
start = time.ticks_ms()
last_log_s = -LOG_EVERY_S
last_second = -1
marker_count = 0
alarm_now = False      # one reading over the warning level
alarm_stel = False     # 15-minute average over the OSHA short-term limit
alarm_twa = False      # 8-hour average over the OSHA 8-hour limit
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

    # ---- Feed the rolling averages, once the readings are valid ----
    # Readings are collected into whole seconds first, so the 15-minute
    # buffer holds one value per second however fast the sensor is read.
    if status != 1:
        second_total = second_total + ppb
        second_count = second_count + 1
        if elapsed_s != last_second:
            last_second = elapsed_s
            add_second(second_total / second_count)
            second_total = 0.0
            second_count = 0

    stel_now = stel_average()
    twa_now = twa_average()
    stel_done = stel_ready()
    twa_done = twa_ready()

    # ---- Buttons ----
    # A steps through the instantaneous warning levels
    if a_pressed:
        a_pressed = False
        limit_index = limit_index + 1
        if limit_index >= len(LIMIT_CHOICES):
            limit_index = 0
        limit = LIMIT_CHOICES[limit_index][0]
        save_limit(limit)
        print("Warning level set to {} ppb ({})".format(
            limit, LIMIT_CHOICES[limit_index][1]))

    # B writes a marker into the log
    marker = ""
    if b_pressed:
        b_pressed = False
        marker_count += 1
        marker = "M{}".format(marker_count)
        print("Marker", marker, "at", clock(elapsed_s))

    # ---- Alarms ----
    # Three alarms, because they answer three different questions:
    #   alarm_now   "something is happening right this second"
    #   alarm_stel  "the last 15 minutes have been bad for me"
    #   alarm_twa   "today as a whole has been bad for me"
    # A worker needs all three. The first one gets you out of the room;
    # the other two are what the regulations actually measure.
    # Each one answers a different question, so all three are checked.
    # A 10% gap on the way back down stops them flickering on and off.
    # Nothing alarms during warm-up, because those readings are not real.
    was_now = alarm_now
    was_stel = alarm_stel
    was_twa = alarm_twa

    if status == 1:
        alarm_now = False
        alarm_stel = False
        alarm_twa = False
    else:
        if ppb >= limit:
            alarm_now = True
        elif ppb < limit * 0.9:
            alarm_now = False

        if stel_done and stel_now >= STEL_LIMIT_PPB:
            alarm_stel = True
        elif stel_now < STEL_LIMIT_PPB * 0.9:
            alarm_stel = False

        if twa_done and twa_now >= TWA_LIMIT_PPB:
            alarm_twa = True
        elif twa_now < TWA_LIMIT_PPB * 0.9:
            alarm_twa = False

    alarm = alarm_now or alarm_stel or alarm_twa

    # Note the moment each alarm starts in the log
    if alarm_twa and not was_twa:
        marker = marker or "TWA"
        print("8-hour average over limit: {:.0f} ppb".format(twa_now))
    elif alarm_stel and not was_stel:
        marker = marker or "STEL"
        print("15-minute average over limit: {:.0f} ppb".format(stel_now))
    elif alarm_now and not was_now:
        marker = marker or "ALARM"
        print("Reading over warning level: {:.0f} ppb".format(ppb))

    ppm = ppb / 1000
    if alarm:
        color = RED
    else:
        color = safe(pick_color(ppb, limit), GREEN)

    # ---- Screen ----
    # The slower alarms matter more, so they take the top line if several
    # are going off at once.
    if alarm_twa:
        flash = not flash
        show(45, "!! 8HR LIMIT !!" if flash else "", RED)
    elif alarm_stel:
        flash = not flash
        show(45, "!! 15MIN LIMIT !!" if flash else "", RED)
    elif alarm_now:
        flash = not flash
        show(45, "!! OVER LIMIT !!" if flash else "", RED)
    # JOB 2
    # The words inside the quote marks below are what the screen shows.
    # Change them to whatever a worker would understand fastest.
    # Keep them short or they run off the edge of the screen.
    elif status == 1:
        show(45, "Warming up (2 min)", GRAY)
    elif status == 2:
        show(45, "Settling (<10 min)", ORANGE)
    else:
        show(45, "Ready", GREEN)

    if ppb >= SENSOR_MAX_PPB:
        show(80, "HCHO: ABOVE 2 ppm", RED)
    else:
        show(80, "HCHO: {:.0f} ppb".format(ppb), color)

    show(110, "Warn at: {} ppb  {}".format(
        limit, safe(status_word(safe(percent_of(ppb, limit), 0)), "")), BLACK)
    if stel_done:
        show(145, "15min: {:.3f} ppm {}%".format(
            stel_now / 1000,
            percent_of(stel_now, STEL_LIMIT_PPB)),
            RED if alarm_stel else BLACK)
    else:
        show(145, "15min: filling {}".format(
            clock(STEL_WINDOW_S - len(stel_values))), GRAY)

    if twa_done:
        show_small(178, "8hr so far: {:.3f} ppm {}% of limit".format(
            twa_now / 1000,
            percent_of(twa_now, TWA_LIMIT_PPB)),
            RED if alarm_twa else GRAY)
    else:
        show_small(178, "8hr so far: filling", GRAY)
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
    # One line per second, in CSV format, so the file opens in Excel or
    # Google Sheets and can be graphed. The file is opened and closed for
    # each line, which is slower but means nothing is lost if the board
    # loses power mid-run.
    if elapsed_s - last_log_s >= LOG_EVERY_S or marker:
        if not marker:
            last_log_s = elapsed_s
        line = "{},{},{},{:.1f},{:.1f},{:.1f},{:.2f},{:.1f},{},{},{},{},{}".format(
            elapsed_s, clock(elapsed_s), status, ppb,
            stel_now, twa_now, temp, hum, limit,
            1 if alarm_now else 0,
            1 if alarm_stel else 0,
            1 if alarm_twa else 0,
            marker)
        log_line(line)
        print(line)

    time.sleep(READ_EVERY_S)
