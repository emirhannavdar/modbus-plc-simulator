from pymodbus.client.sync import ModbusTcpClient

client = ModbusTcpClient(
    host="127.0.0.1",
    port=5020
)

print("Connecting...")

if not client.connect():
    print("CONNECTION FAILED")
    exit()

print("CONNECTED")

result = client.read_holding_registers(
    address=0,
    count=16,
    unit=1
)

print("RESULT:")
print(result)

if result.isError():
    print("MODBUS ERROR")
else:
    print("REGISTERS:")
    print(result.registers)

client.close()
