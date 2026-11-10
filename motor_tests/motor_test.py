from time import sleep

from gpiozero import DigitalOutputDevice, PWMOutputDevice

DUTY_CYCLE = 90 / 255  # Same duty cycle as the Arduino sketch.

with (
    DigitalOutputDevice(25, initial_value=False) as standby_pin,
    DigitalOutputDevice(23, initial_value=False) as ain1,
    DigitalOutputDevice(24, initial_value=False) as ain2,
    PWMOutputDevice(18, initial_value=0, frequency=500) as pwm,
):
    active_direction = None

    try:
        print("f = forward, r = reverse, s = standby, q = quit")

        while True:
            command = input("Motor> ").strip().lower()

            if command == "q":
                break

            if command == "s":
                standby_pin.off()
                pwm.value = 0
                active_direction = None
                print("Standby")
                continue

            if command not in ("f", "r") or command == active_direction:
                continue

            if active_direction is not None:
                standby_pin.off()
                pwm.value = 0
                sleep(0.2)

            ain1.value = command == "f"
            ain2.value = command == "r"
            pwm.value = DUTY_CYCLE
            standby_pin.on()
            active_direction = command

            print("Forward" if command == "f" else "Reverse")

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        standby_pin.off()
        pwm.value = 0
