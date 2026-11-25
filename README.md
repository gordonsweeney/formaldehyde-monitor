# Formaldehyde Exposure Monitor (work in progress)

MicroPython firmware for a prototype wearable formaldehyde monitor, built on a
UNIHIKER K10 with a Sensirion SFA40 sensor.

This repository is the **code side** of a larger CHEM096 research project on
formaldehyde exposure monitoring in anatomy labs and similar workplaces. It is
not the whole project and it is not finished. It is the working prototype
firmware as it stands today, plus the data collected with it so far.

## What it does

- Reads formaldehyde twice per second with no averaging, and shows it in ppb
  and ppm on the K10's screen along with temperature, humidity and sensor status
- Lets the user pick an instantaneous warning level from published occupational
  standards with button A, saved on the board so it survives a power cycle
- Keeps rolling 15-minute and 8-hour averages and compares them against OSHA's
  short-term (2 ppm) and 8-hour (0.75 ppm) limits, which are defined as averages
- Alarms three ways: a single reading over the warning level, the 15-minute
  average over the STEL, or the 8-hour average over the TWA. Each one flashes
  the screen, flashes the LEDs, beeps, and is logged separately
- Warns at 80% of either exposure limit, before the limit is actually reached
- Tracks the highest reading of the session and shows it on screen
- Flags readings pegged at the sensor's 2000 ppb ceiling, so an average that
  includes them can be reported honestly as a lower bound
- Records concentration in ppb, ppm and mg/m³, since different standards use
  different units
- Saves one reading per second to a CSV file on the board, so a whole session
  can be graphed afterward
- Button B drops a numbered marker in the log to label events during a test

## Alarm limits

| ppb | ppm | Standard | Shown on screen as |
|-----|------|----------|--------------------|
| 1 | 0.001 | (test only) | Alarms right away, for testing only |
| 16 | 0.016 | NIOSH REL, 10 hr | US health agency's safest advice |
| 80 | 0.08 | WHO indoor guideline, 30 min | World Health Org., home air |
| 100 | 0.1 | NIOSH ceiling, 15 min | Never go above this for even 15 min |
| 300 | 0.3 | ACGIH TLV ceiling | Hygienists' limit, never go above |
| 500 | 0.5 | OSHA action level | Employer must start monitoring workers |
| 750 | 0.75 | OSHA PEL, 8 hr TWA | Legal max average over an 8 hr shift |
| 2000 | 2.0 | OSHA STEL, 15 min | Legal max for any 15 minute period |

Limits are chosen from a fixed list rather than typed in, so a user does not
have to guess what concentration is harmful, and each one is explained on the
screen in plain English.

## Hardware

- UNIHIKER K10 (ESP32-S3), running MicroPython v0.9.8 flashed with Mind+ V2
- Gravity SFA40 formaldehyde sensor on the 4-pin I2C port (address 0x5D)
- K10 I2C pins: SDA 47, SCL 48
- K10 audio pins used for the alarm beep: BCLK 0, LRCK 38, data-out 45

## Running it

1. Flash MicroPython to the K10 (Mind+ V2, Erase then Burn)
2. Copy `main.py` to the board with Thonny
3. Plug in the sensor and power the board; it starts on its own

Controls:

- **A** steps through the alarm limits while running
- **B** writes a marker into the log file
- **Hold A at startup** disables the program so Thonny can connect for editing
- **Hold B at startup** re-enables it

## Log files

Each run writes a new `logN.csv` on the board:

```
elapsed_s, elapsed, status, hcho_ppb, hcho_mg_m3, saturated, peak_ppb,
stel_15min_ppb, twa_8hr_ppb, temp_c, humidity_pct, warn_level_ppb,
alarm_now, alarm_stel, alarm_twa, attention, marker
```

`saturated` is 1 when the reading sat at the sensor's 2000 ppb ceiling. Any
average covering saturated readings is a lower bound on the true exposure and
should be reported that way.

`status` is 0 ready, 1 warming up, 2 settling, 3 read error. Every reading is
checksum-verified; failed readings are discarded rather than logged.

## Data so far

- `data/floor_test_log1.csv` and `data/floor_test_chart.png`: a 70-minute
  chamber test checking whether a wood floor was a formaldehyde source. The
  level under the container fell from 51.7 to 35.7 ppb and did not rise, so no
  evidence of a source at that spot.

## Known limitations

These are findings as much as problems, since the point of the project is to
work out what a wearable badge can and cannot do.

- **Accuracy is ±20 ppb** in the 0–200 ppb range, so small changes are not
  meaningful without a reference instrument.
- **Response takes up to 2 minutes**, and recovery after a strong exposure is
  slower still, so a badge will overstate exposure after a spike.
- **Range is 0–2000 ppb**, which covers the OSHA 8-hour limit but only just
  reaches the 2 ppm short-term limit.
- **The first ~90 seconds are not valid.** The sensor ramps up from near zero
  after a restart, so the code marks the first two minutes as warm-up and
  suppresses the alarm during it.
- **The averages need a full window before they mean anything.** The 15-minute
  average does not raise an alarm until a full 15 minutes has been collected,
  because an average over 20 seconds is not a 15-minute average. The screen
  shows a countdown until then.
- **The K10's own speaker library does not work** in this firmware: its
  `play_tone` is pointed at the microphone's I2S bus and errors, and its WAV
  players accept a valid file and stay silent. The alarm writes audio to the
  speaker amplifier directly instead.
- **No vibration hardware.** The K10 has no vibration motor; a Gravity module
  on P0 is supported in the code but not yet fitted.
- **No touchscreen**, so the whole interface has to work with two buttons.

## To do

- Test against a known formaldehyde source (raw MDF, or formalin in a
  supervised lab), with a control run alongside
- Compare readings against a calibrated reference instrument
- Fit a vibration motor, so the badge can alert through touch in a noisy lab
- Measure in a working anatomy lab

## Project layout

```
main.py    the whole program; runs on the board
data/      logs and charts from test sessions
```

One file, top to bottom, no classes and no frameworks. Section banners
mark each part.

## For students who want to work on this

You do not need any experience with microcontrollers, and you do not
need much Python. Thirteen jobs are marked in `main.py` with the word JOB,
so you can search for it and land on each one. Every job is a short
function with the middle missing, and the longest answer is four lines.

Do them in order. Each one sets up the next.

| Job | Function | What it does | Python in it |
|---|---|---|---|
| 1 | `ppb_to_ppm` | converts a reading to the units the legal limits use | function, `return`, division |
| 2 | `is_over` | answers whether a reading has reached a limit | `True` / `False`, `>=` |
| 3 | `percent_of` | how far a value is toward a limit, as a percent | `int()` |
| 4 | `status_word` | turns a percent into OVER, MEDIUM or LOW | `if`, words in quotes |
| 5 | `pick_color` | picks the colour the big number is shown in | `if`, names without quotes |
| 6 | `beep_count` | how many beeps the alarm makes | `if`, numbers |
| 7 | `is_saturated` | spots a reading pegged at the sensor's ceiling | a named constant |
| 8 | `time_left` | the countdown while the 15-minute window fills | `max()` |
| 9 | `highest` | tracks the worst reading of the session | comparing two values |
| 10 | `average` | the division behind both rolling averages | guarding against divide-by-zero |
| 11 | `ppm_to_mg` | converts to the units the WHO guideline uses | a conversion constant |
| 12 | `alarm_message` | decides which alarm gets the top line of the screen | `if` on True/False |
| 13 | `needs_attention` | warns at 80% of a limit, before it is reached | `or` |

Jobs 1 to 6 are the gentlest. Jobs 7 to 13 are a step up and add things the
device cannot do at all yet.

Each job says what the function is for and shows what should come out for a
handful of inputs. Working out the lines is the job. The file header has two
worked examples, deliberately from outside this project, covering `def` and
`return` and the shape of an `if` chain.

Everything else in the file already works. The device runs as it stands;
these change what it shows, what it records, how it sounds, and when it
warns you. A `safe()` helper keeps it running while the jobs are still
empty, so an unfinished job cannot stop the device.

### How to contribute

1. Fork this repository
2. Open `main.py` in your fork and click the pencil icon to edit it in
   the browser. Nothing to install.
3. Search for `JOB`, write your lines, commit
4. Open a pull request

Claim a job in the issues tab before you start, so two people do not
write the same one. Questions in the issue comments are welcome.

### Other contributions, in rough order of difficulty

- Show the battery level on screen, so a worker knows when to charge it
- Write a summary line to the log when the device is switched off
- Add vibration support on pin P0 (the code is there, the motor is not)
- Write a script that reads a log CSV and plots the session
- Handle the sensor cable being unplugged mid-run without stopping

### Verifying the device after a change

1. Load `main.py` onto the board with Thonny
2. Press A until the warning level reads TEST (1 ppb); the alarm should
   fire within a second or two: flashing screen, red LEDs, beeping
3. Press A again to move the level back up; the alarm should clear
4. Leave it running and watch the 15min line count down from "filling"
   to a real number at 15:00
5. Pull the log CSV off the board and check it has a line per second

## Credits

- Sensor commands and conversion formulas follow the Sensirion SFA40 datasheet
- Sensor routines adapted from DFRobot's MIT-licensed
  [DFRobot_SFA40](https://github.com/DFRobot/DFRobot_SFA40) library
