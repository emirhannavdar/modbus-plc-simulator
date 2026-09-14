import time
from threading import Thread

from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.datastore import ModbusSlaveContext
from pymodbus.datastore import ModbusServerContext
from pymodbus.server.sync import StartTcpServer


# ============================================================
# MODBUS REGISTER MAP
# ============================================================
#
# HR0       = Motor Start/Stop              16-bit
# HR1       = Auto/Manual                   16-bit
#
# HR2-HR3   = Speed Setpoint                32-bit
# HR4-HR5   = Actual RPM                    32-bit
# HR6-HR7   = Current (A x 10)              32-bit
# HR8-HR9   = Pressure Setpoint (bar x 10)  32-bit                                     
#
# HR10-HR11 = Actual Pressure (bar x 10)    32-bit                                   
#
# HR12-HR13 = Temperature (°C x 10)         32-bit
#
# HR14      = Alarm                         16-bit
# HR15      = Emergency Stop                16-bit
#
# ============================================================


initial_values = [
    # HR0
    0,

    # HR1
    0,

    # HR2-HR3
    0,
    0,

    # HR4-HR5
    0,
    0,

    # HR6-HR7
    0,
    0,

    # HR8-HR9
    0,
    50,

    # HR10-HR11
    0,
    0,

    # HR12-HR13
    0,
    0,

    # HR14
    0,

    # HR15
    0
]


store = ModbusSlaveContext(
    hr=ModbusSequentialDataBlock(
        0,
        initial_values
    ),
    zero_mode=True
)


context = ModbusServerContext(
    slaves=store,
    single=True
)

# 32-BIT REGISTER FONKSİYONLARI

def read_uint32(high_word, low_word):
    """
    İki adet 16-bit registerı
    tek bir 32-bit unsigned integer'a çevirir.
    """

    return (
        (high_word << 16)
        | low_word
    )

def write_uint32(address, value):
    """
    32-bit değeri iki adet 16-bit registera böler.

    HIGH WORD → address
    LOW WORD  → address + 1
    """

    high_word = (value >> 16) & 0xFFFF
    low_word = value & 0xFFFF

    context[0].setValues(
        3,
        address,
        [
            high_word,
            low_word
        ]
    )

# PLC SIMULATOR

def plc_sim():

    actual_rpm = 0

    while True:
        # REGISTERLARI OKU

        values = context[0].getValues(
            3,
            0,
            count=16
        )

        # KOMUTLAR

        motor_start = values[0]
        auto_mode = values[1]

        # SPEED SETPOINT
        # HR2 + HR3

        speed_setpoint = read_uint32(
            values[2],
            values[3]
        )

        # PRESSURE SETPOINT
        # HR8 + HR9

        pressure_setpoint = read_uint32(
            values[8],
            values[9]
        )

        # EMERGENCY STOP

        emergency_stop = values[15]

        # MOTOR CONTROL

        if emergency_stop == 1:

            actual_rpm = 0


        elif motor_start == 1:
            # Motor hedef devire doğru ilerliyor

            if actual_rpm < speed_setpoint:

                actual_rpm += 50

                if actual_rpm > speed_setpoint:
                    actual_rpm = speed_setpoint


            elif actual_rpm > speed_setpoint:

                actual_rpm -= 50

                if actual_rpm < speed_setpoint:
                    actual_rpm = speed_setpoint


        else:
            # Motor kapalıysa yavaşça dur

            if actual_rpm > 0:
                actual_rpm -= 100

                if actual_rpm < 0:
                    actual_rpm = 0


        # CURRENT

        if actual_rpm > 0:

            current = 80 + int(
                actual_rpm * 0.02
            )

        else:

            current = 0

        # PRESSURE

        if actual_rpm > 0 and speed_setpoint > 0:

            pressure = int(
                (actual_rpm / speed_setpoint)
                * pressure_setpoint
            )

        else:

            pressure = 0

        # TEMPERATURE

        temperature = (
            250
            + int(actual_rpm * 0.02)
        )

        # ALARM

        alarm = 0

        if temperature >= 800:
            alarm = 1

        if actual_rpm > 10000:
            alarm = 1

        if emergency_stop == 1:
            alarm = 1

        # ACTUAL RPM
        # HR4 + HR5

        write_uint32(
            4,
            actual_rpm
        )

        # CURRENT
        # HR6 + HR7

        write_uint32(
            6,
            current
        )

        # ACTUAL PRESSURE
        # HR10 + HR11

        write_uint32(
            10,
            pressure
        )

        # TEMPERATURE
        # HR12 + HR13

        write_uint32(
            12,
            temperature
        )

        # ALARM

        context[0].setValues(
            3,
            14,
            [alarm]
        )

        # PLC MONITOR

        print(
            f"MOTOR={'ON' if motor_start else 'OFF'} | "
            f"AUTO={'ON' if auto_mode else 'OFF'} | "
            f"SPEED_SETPOINT={speed_setpoint} RPM | "
            f"ACTUAL_RPM={actual_rpm} RPM | "
            f"CURRENT={current / 10:.1f} A | "
            f"PRESSURE_SETPOINT="
            f"{pressure_setpoint / 10:.1f} bar | "
            f"PRESSURE={pressure / 10:.1f} bar | "
            f"TEMP={temperature / 10:.1f} °C | "
            f"EMERGENCY_STOP="
            f"{'ON' if emergency_stop else 'OFF'} | "
            f"ALARM={'ON' if alarm else 'OFF'}"
        )

        time.sleep(1)

# PLC THREAD

plc_thread = Thread(
    target=plc_sim,
    daemon=True
)

plc_thread.start()

# SERVER

print("========================================================")
print("              MODBUS TCP PLC SIMULATOR")
print("========================================================")
print("IP      : 127.0.0.1")
print("PORT    : 15020")
print("UNIT ID : 1")
print("========================================================")
print("PLC calisiyor...")
print()


StartTcpServer(
    context,
    address=("127.0.0.1", 15020)
)