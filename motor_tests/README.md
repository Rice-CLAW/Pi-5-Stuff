# CLAW motor control bench test

Use a Raspberry Pi 5 to drive one to three DORHEA N20 12 V, 30 RPM brushed DC gearmotors through TB6612FNG breakout boards. Start with one motor on Board 1, channel A. Add the other two channels after the first test passes. The three-motor layout uses two breakout boards and ten GPIOs.

![Raspberry Pi 5 physical header with CLAW motor connections highlighted](assets/pi5-motor-pinout.svg)

The diagram shows the physical header as viewed from above, with pin 1 at the upper left. Follow the numbers on the header. Python uses **BCM GPIO numbers**. For example, GPIO18 is physical pin 12. See the [official Raspberry Pi header reference](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio).

## Power and motor connections

Shut down the Pi, turn off the bench supply output, and disconnect the Uno from the driver before changing the wiring.

| From | To |
| --- | --- |
| Pi USB-C power supply | Pi 5 USB-C power port |
| Pi 3.3 V, physical pin 1 | `VCC` on both driver boards |
| Pi GND, physical pin 6 | Common ground shared with both driver `GND` pins and the bench supply black terminal |
| WANPTEK red positive terminal, set to 12.0 V | `VM` on both driver boards |
| Motor 1's two leads | Board 1 `AO1` and `AO2` |
| Motor 2's two leads | Board 1 `BO1` and `BO2` |
| Motor 3's two leads | Board 2 `AO1` and `AO2` |

For a one-motor test, wire only Board 1 and Motor 1. Leave unused motor outputs open. Route the motor current back to the bench supply through the driver ground wiring. The Pi ground wire provides the GPIO reference. Keep the **12 V supply on `VM` only** and use **3.3 V on `VCC`**.

## GPIO connections

| Function | Driver board pin | BCM GPIO | Physical header pin |
| --- | --- | ---: | ---: |
| Motor 1 direction 1 | Board 1 `AIN1` | 23 | 16 |
| Motor 1 direction 2 | Board 1 `AIN2` | 24 | 18 |
| Motor 1 PWM | Board 1 `PWMA` | 18 | 12 |
| Motor 2 direction 1 | Board 1 `BIN1` | 16 | 36 |
| Motor 2 direction 2 | Board 1 `BIN2` | 20 | 38 |
| Motor 2 PWM | Board 1 `PWMB` | 12 | 32 |
| Motor 3 direction 1 | Board 2 `AIN1` | 19 | 35 |
| Motor 3 direction 2 | Board 2 `AIN2` | 26 | 37 |
| Motor 3 PWM | Board 2 `PWMA` | 13 | 33 |
| Shared standby | `STBY` on both boards | 25 | 22 |

Both boards enter standby when GPIO25 is low. Each motor has its own direction and software PWM outputs. If the physical direction is opposite to what you want, turn off motor power and swap that motor's two leads.

## Run the test

1. Copy this folder into the repo and open a terminal in it on the Pi. Install the system packages on Raspberry Pi OS.

   ```bash
   sudo apt update
   sudo apt install python3-gpiozero python3-lgpio
   ```

2. With the motor output disabled, secure the motor with its shaft free and start the test. Use the system Python so it can see the packages installed by `apt`.

   ```bash
   /usr/bin/python3 motor_test.py
   ```

3. At the `Motor>` prompt, enable the bench supply at **12.0 V** with the current limit that worked for the unloaded Arduino test. Enter `f`, `s`, `r`, then `s`. Wait for the shaft to stop before commanding the opposite direction. Enter `q` or press Ctrl+C to leave standby active and exit.

4. To test all three channels, turn off the supply, wire the remaining motors, and restart in three-motor mode.

   ```bash
   /usr/bin/python3 motor_test.py --motors 3
   ```

| Command | One motor | Three motors |
| --- | --- | --- |
| Forward | `f` | `f1`, `f2`, `f3` |
| Reverse | `r` | `r1`, `r2`, `r3` |
| Coast one motor | `s` | `s1`, `s2`, `s3` |
| Put both boards in standby | `s` | `s` |
| Exit in standby | `q` or Ctrl+C | `q` or Ctrl+C |

Press Enter after each command. A motor keeps running at about **35% duty cycle** until stopped or given a new direction. A direct reversal inserts a 200 ms coast interval. Use the bench supply output control to cut motor power if needed.

Test each motor separately before running several together. If the supply displays **CC** and the motor only twitches, it reached its current limit. Check the motor's startup current and the driver board's current and thermal limits before raising the supply limit or running both channels of Board 1 together. The three-motor pin map and script are for unloaded bench testing and have not been verified on hardware.

## References

- [Raspberry Pi hardware and GPIO documentation](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#gpio)
- [Toshiba TB6612FNG datasheet and control truth table](https://toshiba-semicon-storage.com/info/TB6612FNG_datasheet_en_20141001.pdf?did=10660&prodName=TB6612FNG)
- [GPIO Zero Pi 5 and lgpio compatibility notes](https://gpiozero.readthedocs.io/en/stable/changelog.html)
