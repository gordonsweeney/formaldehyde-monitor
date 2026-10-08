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
- Lets the user pick an alarm limit from published occupational standards with
  button A, saved on the board so it survives a power cycle
- Alarms over that limit: flashing red screen, flashing red LEDs, and a beep
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
2. Save main.py onto the board with Thonny (File → Save as → MicroPython device, named main.py
3. Plug in the sensor and power the board; it starts on its own

Controls:

- **A** steps through the alarm limits while running
- **B** writes a marker into the log file
- **Hold A at startup** disables the program so Thonny can connect for editing
- **Hold B at startup** re-enables it

## Log files

Each run writes a new `logN.csv` on the board:

```
elapsed_s, elapsed, status, hcho_ppb, temp_c, humidity_pct, limit_ppb, alarm, marker
```

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
- **Alarms compare against instantaneous readings**, while the OSHA limits are
  averages (8 hour and 15 minute). Rolling averages are on the to-do list.
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
- Add rolling 15-minute and 8-hour averages, so readings can be compared with
  the OSHA limits as they are actually written
- Fit a vibration motor, so the badge can alert through touch in a noisy lab
- Measure in a working anatomy lab

## Credits

- Sensor commands and conversion formulas follow the Sensirion SFA40 datasheet
- Sensor routines adapted from DFRobot's MIT-licensed
  [DFRobot_SFA40](https://github.com/DFRobot/DFRobot_SFA40) library
