"""
Formaldehyde Exposure Monitor (detector v5)

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

IF YOU ARE HERE TO WRITE CODE

    Thirteen jobs are marked in this file, JOB 1 to JOB 13. Search the file
    for the word JOB and you will land on each one.

    Each job is a short function with the middle missing. The job tells
    you what it has to do and shows you examples of what should come
    out. Working out the lines is the job. The longest answer is four
    lines, and none of them need anything that is not explained below.

    Do them in order. Each one sets up the next.

    Everything else in this file already works. The device runs right
    now. Your jobs change what it shows, what colour it shows it in,
    how it beeps, what it records, and when it warns you early.

    Jobs 1 to 6 are the gentlest. Jobs 7 to 13 are a step up and add
    things the device cannot do yet at all.

    You cannot break anything for good. The original is on GitHub.

HOW A FUNCTION WORKS

    A function is a job with a name. Here is one that is not in this
    file, written out in full so you can see the shape:

        def double(number):
            return number * 2

    `def` starts a function. `double` is its name. `number` is what
    gets handed to it. `return` means "here is my answer, give it back
    to whoever asked".

    So `double(7)` gives back 14. Inside the function, `number` is 7.

    Nothing after a `return` runs. The function stops there.

HOW AN IF CHAIN WORKS

    Three of the jobs need a function that picks between options. Here
    is one, again not from this file:

        def temp_word(celsius):
            if celsius >= 30:
                return "HOT"
            if celsius >= 15:
                return "WARM"
            return "COLD"

    Read it top to bottom. If the first question is true it gives back
    HOT and stops right there. If it is false it tries the next
    question. If no question is true it reaches the last line.

    `>=` means "the same as or bigger than".

    Two things that catch everybody the first time. The colon at the
    end of an `if` line is required. And the line underneath is pushed
    in from the left, because that spacing is how Python knows the line
    belongs to that `if`. The spacing is part of the code.

QUOTE MARKS

    Words for a person to read go in quote marks: "HOT"
    Numbers do not: 14
    Names the program already knows do not either: RED

    "RED" with quote marks is three letters. RED without them is a
    colour the program understands. They are different things.

WHEN YOU ARE STUCK

    Read the examples the job gives you and work backwards from them.
    If a job says 375 and 750 should give back 50, ask yourself what
    you would do to those two numbers by hand to get 50.

Notes:
    The SFA40 commands and conversion formulas follow the Sensirion SFA40
    datasheet; the sensor routines are adapted from DFRobot's MIT-licensed
    DFRobot_SFA40 library. The K10's own speaker functions do not work in
    this firmware, so the alarm writes audio to the amplifier directly.

Reading this file if you are new to Python:
    The file runs from top to bottom. It is organised in sections, each
    with a banner comment like "---- Settings ----". A good order to read
    them in is: Settings (the numbers you can change), the sensor section
    (how a reading is fetched), the rolling averages (the exposure math),
    then the main loop at the bottom, which ties everything together.

    Nothing here uses classes or anything advanced. If you can read a
    for loop, an if statement and a function, you can read all of it.

    Places marked TRY THIS are small, safe changes to experiment with.
    Start there, change one thing, run it, and see what happens on the
    screen. If you break something, the file is in version control.

Things that still need doing (good first contributions):
    - Battery level on the screen, so a worker knows when to charge it
    - A vibration motor on pin P0, for noisy rooms (see vibrate() below,
      the code is there and waiting for the hardware)
    - A peak-reading line: the highest value seen this session, and when
    - A summary written to the log when the device is switched off
    - Graphing scripts that read the CSV files and plot a session
    - Better handling if the sensor cable is unplugged mid-run
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

SENSOR_MAX_PPB = 2000  # SFA40 upper limit (Sensirion datasheet)

# Some standards are written in milligrams per cubic metre instead of
# ppm. The WHO indoor guideline, for example, is 0.1 mg/m3. For
# formaldehyde at room temperature, 1 ppm is about 1.23 mg/m3.
MG_PER_PPM = 1.23

# Warn at this fraction of a limit, before it is actually reached.
ATTENTION_PERCENT = 80

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
    return safe(average(stel_total, len(stel_values)), 0.0)


def twa_average():
    """Average of the 8-hour buffer, in ppb."""
    return safe(average(twa_total, len(twa_values)), 0.0)


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
    """Used while the jobs are still empty.

    An empty function hands back nothing at all. This swaps that for
    something sensible so the device keeps working while people are
    still filling the jobs in. Leave this one alone.
    """
    if value is None:
        return fallback
    return value


def ppb_to_ppm(ppb):
    """JOB 1

    Readings come out of the sensor in parts per billion. The legal
    limits are written in parts per million. This function converts
    one into the other.

    There are 1000 parts per billion in 1 part per million.

    What should come out:

        ppb_to_ppm(50)    gives back  0.05
        ppb_to_ppm(750)   gives back  0.75
        ppb_to_ppm(2000)  gives back  2.0

    You need: one line, using `return` and division. Look at the
    `double` example at the top of this file for the shape.

    Delete the word pass and put your line in its place.

    The ppm line on the device screen is blank until this works.
    """
    pass


def is_over(value, limit):
    """JOB 2

    This answers a yes or no question: has a reading reached a limit?

    In Python the two answers are `True` and `False`. Capital letter,
    no quote marks.

    What should come out:

        is_over(800, 750)  gives back  True
        is_over(750, 750)  gives back  True
        is_over(600, 750)  gives back  False

    Look carefully at the middle one. Reaching the limit counts as
    over, so you want "the same as or bigger than", which is `>=`.

    You need: one line. A comparison already produces True or False on
    its own, so you do not need an `if` here. You are returning the
    answer to the comparison itself.

    Delete the word pass and put your line in its place.

    All three alarms on this device run through this function. If this
    is wrong, every alarm is wrong.
    """
    pass


def percent_of(value, limit):
    """JOB 3

    This works out how far a value has got toward a limit, written as
    a percentage.

    What should come out:

        percent_of(375, 750)  gives back  50
        percent_of(750, 750)  gives back  100
        percent_of(0, 750)    gives back  0
        percent_of(900, 750)  gives back  120

    Note that it gives back 50 and not 50.0. Wrapping your answer in
    `int()` throws away the decimal part.

    You need: one line. Work out on paper how you would get 50 out of
    375 and 750, then write that.

    Delete the word pass and put your line in its place.
    """
    pass


def status_word(percent):
    """JOB 4

    This turns a percentage into a word, so somebody can glance at the
    screen and know where they stand without reading numbers.

    What should come out:

        status_word(120)  gives back  "OVER"
        status_word(100)  gives back  "OVER"
        status_word(60)   gives back  "MEDIUM"
        status_word(50)   gives back  "MEDIUM"
        status_word(10)   gives back  "LOW"

    So: 100 or more is OVER, 50 or more is MEDIUM, anything below that
    is LOW.

    You need: an if chain. The `temp_word` example at the top of this
    file is exactly this shape with different words and numbers.

    Words go in quote marks.

    Delete the word pass and put your lines in its place.

    Pick better words if you can think of them. "GET OUT" is a fine
    thing for a safety device to say.
    """
    pass


def pick_color(ppb, limit):
    """JOB 5

    Same shape as JOB 4. This picks the colour the big number on the
    screen is shown in. It gets the current reading and the warning
    level the user has chosen.

    What should come out, if the limit is 500:

        pick_color(600, 500)  gives back  RED
        pick_color(500, 500)  gives back  RED
        pick_color(300, 500)  gives back  ORANGE
        pick_color(250, 500)  gives back  ORANGE
        pick_color(100, 500)  gives back  GREEN

    So: at or above the limit is red, at or above half the limit is
    orange, anything lower is green.

    The middle rows are the interesting part. You are not given the
    number 250 anywhere, so you have to work out half the limit inside
    the function.

    You need: an if chain, same as JOB 4.

    RED, ORANGE and GREEN have no quote marks. They are names the
    program already knows.

    Delete the word pass and put your lines in its place.
    """
    pass


def beep_count(percent):
    """JOB 6

    Same shape again. This decides how many beeps the alarm makes each
    time it goes off. Worse air, more beeps.

    What should come out:

        beep_count(150)  gives back  3
        beep_count(100)  gives back  3
        beep_count(70)   gives back  2
        beep_count(50)   gives back  2
        beep_count(20)   gives back  1

    You need: an if chain, same as JOB 4 and JOB 5.

    Numbers have no quote marks.

    Delete the word pass and put your lines in its place.

    Pick your own numbers if you want. Five beeps for the worst case
    would be louder and more annoying, which might be exactly right
    for something people wear at work.
    """
    pass


def is_saturated(ppb):
    """JOB 7

    The sensor can only read up to 2000 ppb. If the real air is worse
    than that, the screen still says 2000, because that is as high as
    the hardware goes. A reading sitting at the top is a floor, not a
    measurement.

    This answers: is this reading pegged at the sensor's ceiling?

        is_saturated(1500)  gives back  False
        is_saturated(2000)  gives back  True
        is_saturated(2400)  gives back  True

    The ceiling is already stored in this file as SENSOR_MAX_PPB. Find
    it in the Settings section and use that name instead of typing
    2000, so this still works if we ever change sensors.

    You need: one line, same idea as JOB 2.

    Once this works the log marks saturated readings, so when we report
    an average we can say honestly that it is a lower bound.
    """
    pass


def time_left(collected, needed):
    """JOB 8

    The 15-minute average means nothing until a full 15 minutes has
    been collected. While it fills, the screen shows a countdown. This
    works out how many seconds are left.

        time_left(0, 900)    gives back  900
        time_left(300, 900)  gives back  600
        time_left(900, 900)  gives back  0
        time_left(950, 900)  gives back  0

    Look at the last one. Once the window is full it should stay at 0
    and never go negative. max() hands back the larger of two things,
    so max(0, 5) is 5 and max(0, -5) is 0.

    You need: one line.
    """
    pass


def highest(peak_so_far, reading):
    """JOB 9

    The first thing anyone asks about a session is what the worst
    reading was. The device does not track it yet.

    Each time a new reading arrives this gets the highest value seen so
    far and the new reading, and hands back whichever should be the new
    peak.

        highest(120, 340)  gives back  340
        highest(340, 120)  gives back  340
        highest(340, 340)  gives back  340

    You need: either an if, or the same max() from JOB 8. Both are
    correct. If you use max, say so in your pull request, because the
    shorter version is worth showing everyone else.
    """
    pass


def average(total, count):
    """JOB 10

    This is how the rolling averages work underneath. Instead of adding
    up 900 numbers twice a second, the device keeps a running total and
    a count, and divides.

        average(1500, 30)  gives back  50.0
        average(0, 0)      gives back  0.0

    The second one is the whole job. At startup there are no readings
    yet, so count is 0, and dividing by zero crashes the program. The
    device has to show something sensible before the first reading
    arrives.

    You need: an if that catches the empty case, then a division.

    Both exposure averages call this, so the 15min and 8hr lines on the
    screen stay empty until it works.
    """
    pass


def ppm_to_mg(ppm):
    """JOB 11

    OSHA writes its limits in ppm. The WHO writes its guideline in
    milligrams per cubic metre, 0.1 mg/m3. To compare our readings
    against both, the device records both units.

    For formaldehyde at room temperature, 1 ppm is about 1.23 mg/m3.
    That number is already in the Settings section as MG_PER_PPM.

        ppm_to_mg(1)     gives back  1.23
        ppm_to_mg(0.75)  gives back  about 0.92
        ppm_to_mg(0)     gives back  0

    You need: one line. Same shape as JOB 1, different operation.
    """
    pass


def alarm_message(now, stel, twa):
    """JOB 12

    Three alarms can go off at once. The top line of the screen only
    fits one message, so something has to decide which.

    This gets three yes or no answers: is the instant alarm on, is the
    15-minute alarm on, is the 8-hour alarm on. It hands back the
    message to display.

        alarm_message(True, False, False)   gives back  "!! OVER LIMIT !!"
        alarm_message(True, True, False)    gives back  "!! 15MIN LIMIT !!"
        alarm_message(True, True, True)     gives back  "!! 8HR LIMIT !!"
        alarm_message(False, False, False)  gives back  ""

    Work out the rule from those four. The order is deliberate: a bad 8
    hours matters more than a bad second, so the slower alarm wins when
    both are on. The empty quote marks mean show nothing.

    You need: an if chain, like temp_word at the top of this file.
    These ifs test True and False directly, so you write `if twa:` with
    nothing after it.
    """
    pass


def needs_attention(stel_percent, twa_percent):
    """JOB 13

    Telling somebody they are over the limit is already too late. This
    warns while there is still time to do something about it, at 80
    percent of either limit.

        needs_attention(85, 10)  gives back  True
        needs_attention(10, 85)  gives back  True
        needs_attention(85, 85)  gives back  True
        needs_attention(10, 10)  gives back  False

    Either one being high is enough. Both being low is the only case
    that gives back False.

    The 80 is already in the Settings section as ATTENTION_PERCENT.

    You need: one line. `or` joins two questions and is true when at
    least one of them is, as in `a >= 5 or b >= 5`.
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

show(10, "HCHO Detector v5", BLUE)
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
show(10, "HCHO Detector v5", BLUE)

log_name = new_log_name()
log_line("elapsed_s,elapsed,status,hcho_ppb,hcho_mg_m3,saturated,peak_ppb,"
         "stel_15min_ppb,twa_8hr_ppb,temp_c,humidity_pct,warn_level_ppb,"
         "alarm_now,alarm_stel,alarm_twa,attention,marker")
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
peak_ppb = 0.0
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

    if status != 1:
        peak_ppb = safe(highest(peak_ppb, ppb), peak_ppb)

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
        if safe(is_over(ppb, limit), False):
            alarm_now = True
        elif ppb < limit * 0.9:
            alarm_now = False

        if stel_done and safe(is_over(stel_now, STEL_LIMIT_PPB), False):
            alarm_stel = True
        elif stel_now < STEL_LIMIT_PPB * 0.9:
            alarm_stel = False

        if twa_done and safe(is_over(twa_now, TWA_LIMIT_PPB), False):
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

    ppm = safe(ppb_to_ppm(ppb), 0.0)
    if alarm:
        color = RED
    else:
        color = safe(pick_color(ppb, limit), GREEN)

    # ---- Screen ----
    # alarm_message() decides which of the three alarms gets the top
    # line, because only one fits and the slower ones matter more.
    message = safe(alarm_message(alarm_now, alarm_stel, alarm_twa), "")
    stel_percent = safe(percent_of(stel_now, STEL_LIMIT_PPB), 0) if stel_done else 0
    twa_percent = safe(percent_of(twa_now, TWA_LIMIT_PPB), 0) if twa_done else 0

    if message:
        flash = not flash
        show(45, message if flash else "", RED)
    elif status == 1:
        show(45, "Warming up (2 min)", GRAY)
    elif status == 2:
        show(45, "Settling (<10 min)", ORANGE)
    elif safe(needs_attention(stel_percent, twa_percent), False):
        show(45, "CHECK EXPOSURE", ORANGE)
    else:
        show(45, "Ready", GREEN)

    if safe(is_saturated(ppb), False):
        show(80, "HCHO: ABOVE 2 ppm", RED)
    else:
        show(80, "HCHO: {:.0f} ppb".format(ppb), color)

    show(110, "Warn at: {} ppb  {}".format(
        limit, safe(status_word(safe(percent_of(ppb, limit), 0)), "")), BLACK)
    if stel_done:
        show(145, "15min: {:.3f} ppm {}%".format(
            safe(ppb_to_ppm(stel_now), 0.0),
            safe(percent_of(stel_now, STEL_LIMIT_PPB), 0)),
            RED if alarm_stel else BLACK)
    else:
        show(145, "15min: filling {}".format(
            clock(safe(time_left(len(stel_values), STEL_WINDOW_S), 0))), GRAY)

    if twa_done:
        show_small(178, "8hr so far: {:.3f} ppm {}% of limit".format(
            safe(ppb_to_ppm(twa_now), 0.0),
            safe(percent_of(twa_now, TWA_LIMIT_PPB), 0)),
            RED if alarm_twa else GRAY)
    else:
        show_small(178, "8hr so far: filling", GRAY)
    show(200, "{:.1f} C   {:.0f} %RH".format(temp, hum), BLACK)
    show_small(235, "{}  {}  pk{:.0f}".format(
        log_name, clock(elapsed_s), peak_ppb), GRAY)
    screen.show_draw()

    # ---- Alarm outputs ----
    if alarm:
        rgb.write(num=-1, color=RED if flash else 0x000000)
        vibrate(flash)
        if flash:
            for i in range(safe(beep_count(
                    safe(percent_of(ppb, limit), 0)), 1)):
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
        line = ("{},{},{},{:.1f},{:.3f},{},{:.1f},{:.1f},{:.1f},"
                "{:.2f},{:.1f},{},{},{},{},{},{}").format(
            elapsed_s, clock(elapsed_s), status, ppb,
            safe(ppm_to_mg(safe(ppb_to_ppm(ppb), 0.0)), 0.0),
            1 if safe(is_saturated(ppb), False) else 0,
            peak_ppb,
            stel_now, twa_now, temp, hum, limit,
            1 if alarm_now else 0,
            1 if alarm_stel else 0,
            1 if alarm_twa else 0,
            1 if safe(needs_attention(stel_percent, twa_percent), False) else 0,
            marker)
        log_line(line)
        print(line)

    time.sleep(READ_EVERY_S)
