Professional Modbus TCP PLC Simulator

Python ve PyModbus 3.13.1 kullanılarak geliştirilmiş endüstriyel PLC / motor-pompa simülatörüdür.

Uygulama, Modbus TCP üzerinden bir PLC gibi davranır ve motor devri, akım, basınç, sıcaklık, alarm, fault ve çalışma süresi gibi proses değerlerini simüle eder.

Özellikler

Modbus TCP Server

PyModbus 3.13.1

Unit ID: 1

Default IP: 127.0.0.1

Default Port: 5020

500 ms PLC scan cycle

Motor START / STOP kontrolü

MANUAL / AUTO çalışma modu

RPM acceleration / deceleration simülasyonu

Akım simülasyonu

Basınç simülasyonu

Sıcaklık simülasyonu

Emergency Stop

Alarm ve fault yönetimi

Status word

Heartbeat

Runtime counter

32-bit Holding Register desteği

Sistem Mimarisi
                 Modbus TCP Client
                 (Modbus Poll vb.)
                         |
                         v
              +---------------------+
              |   Modbus TCP Server  |
              |    127.0.0.1:5020    |
              +----------+----------+
                         |
                         v
              +---------------------+
              |  Register Database   |
              +----------+----------+
                         |
                         v
              +---------------------+
              |    PLC Simulator     |
              |                      |
              | Command Processing   |
              | Motor Model          |
              | Process Model        |
              | Protection           |
              +----------+-----------+
                         |
                         v
              +---------------------+
              | Simulated Process    |
              |                      |
              | RPM / Current        |
              | Pressure / Temp      |
              | Alarm / Fault        |
              +---------------------+

Gereksinimler

Python 3.x

PyModbus 3.13.1

Kurulum:

python -m pip install pymodbus==3.13.1


Kurulu sürümü kontrol etmek için:

python -c "import pymodbus; print(pymodbus.__version__)"


Beklenen çıktı:

3.13.1

Çalıştırma

Projeyi çalıştırmak için:

python .\server.py


Başarılı başlangıçta aşağıdakine benzer bir çıktı görülür:

PROFESSIONAL MODBUS TCP PLC
IP       : 127.0.0.1
PORT     : 5020
UNIT ID  : 1
SCAN     : 0.500 s


Server:

127.0.0.1:5020


üzerinden Modbus TCP bağlantılarını kabul eder.

Modbus Register Map

Register adresleri uygulama içerisinde zero-based olarak tanımlanmıştır.

Modbus Holding Register gösteriminde aşağıdaki tablo kullanılabilir.

Address	Holding Register	Name	Description
0	40001	MOTOR_COMMAND	Motor start / stop
1	40002	MODE	Manual / Auto
2-3	40003-40004	SPEED_SETPOINT	Motor hedef devri
4-5	40005-40006	ACTUAL_RPM	Gerçek motor devri
6-7	40007-40008	CURRENT	Motor akımı
8-9	40009-40010	PRESSURE_SETPOINT	Hedef basınç
10-11	40011-40012	ACTUAL_PRESSURE	Gerçek basınç
12-13	40013-40014	TEMPERATURE	Sistem sıcaklığı
14	40015	ALARM	Alarm durumu
15	40016	EMERGENCY_STOP	Acil durdurma
16	40017	HEARTBEAT	PLC heartbeat
17	40018	STATUS_WORD	PLC durum bilgileri
18	40019	FAULT_CODE	Hata kodu
19-20	40020-40021	RUNTIME_SECONDS	Motor çalışma süresi
Register Detayları
40001 — MOTOR_COMMAND

Motor çalışma komutudur.

Value	Description
0	STOP
1	START

Örnek:

40001 = 1


Motorun çalışması istenir.

40001 = 0


Motor durdurulur.

40002 — MODE

PLC çalışma modudur.

Value	Description
0	MANUAL
1	AUTO
40003-40004 — SPEED_SETPOINT

Motor hedef RPM değeridir.

32-bit unsigned integer olarak tutulur.

40003 = High Word
40004 = Low Word


Örneğin:

1500 RPM


değeri 32-bit olarak:

0x000005DC


şeklindedir.

Maksimum değer:

3000 RPM

40005-40006 — ACTUAL_RPM

Motorun simülasyondaki gerçek RPM değeridir.

32-bit unsigned integer olarak tutulur.

Motor START edildiğinde gerçek RPM hedef RPM'e doğru kademeli olarak yükselir.

Varsayılan hızlanma:

100 RPM / scan


Varsayılan scan:

500 ms

40007-40008 — CURRENT

Motor akımıdır.

Değer ×10 ölçeğinde tutulur.

Örneğin:

Register = 95


ise:

Current = 95 / 10
        = 9.5 A

40009-40010 — PRESSURE_SETPOINT

Hedef basınç değeridir.

Değer ×10 ölçeğinde tutulur.

Örneğin:

Register = 50


ise:

Pressure Setpoint = 50 / 10
                  = 5.0 bar


Default değer:

5.0 bar

40011-40012 — ACTUAL_PRESSURE

Simülasyon tarafından hesaplanan gerçek basınçtır.

×10 ölçeği kullanılır.

Örneğin:

Register = 35


ise:

Actual Pressure = 3.5 bar


Motor durduğunda basınç:

0.0 bar


olur.

40013-40014 — TEMPERATURE

Simüle edilen sıcaklıktır.

×10 ölçeği kullanılır.

Örneğin:

Register = 350


ise:

Temperature = 35.0 °C


Başlangıç sıcaklığı:

25.0 °C


Motor RPM'i arttıkça sıcaklık da yükselir.

40015 — ALARM

Genel alarm durumudur.

Value	Description
0	Alarm yok
1	Alarm aktif
40016 — EMERGENCY_STOP

Acil durdurma girişidir.

Value	Description
0	Emergency Stop aktif değil
1	Emergency Stop aktif

Emergency Stop aktif olduğunda motor durdurulur ve fault oluşturulur.

40017 — HEARTBEAT

PLC'nin çalıştığını gösteren sayaçtır.

Her PLC scan cycle'da değeri artırılır.

Örneğin:

1
2
3
4
5
...


şeklinde ilerler.

Heartbeat değerinin düzenli olarak değişmesi PLC simulation loop'unun çalıştığını gösterir.

40018 — STATUS_WORD

Birden fazla PLC durum bilgisini tek register içerisinde taşır.

Bit	Value	Description
Bit 0	1	STOPPED
Bit 1	2	RUNNING
Bit 2	4	AUTO_MODE
Bit 3	8	MANUAL_MODE
Bit 4	16	ALARM_ACTIVE
Bit 5	32	EMERGENCY_STOP
Bit 6	64	FAULT_ACTIVE
Bit 7	128	READY

Örneğin:

STATUS_WORD = 130


şu bitlerin aktif olduğu anlamına gelir:

RUNNING = 2
READY   = 128

2 + 128 = 130

40019 — FAULT_CODE

PLC'nin aktif hata kodudur.

Code	Fault
0	NONE
1	EMERGENCY_STOP
2	OVER_TEMPERATURE
3	OVER_SPEED
4	OVER_CURRENT
5	OVER_PRESSURE
6	WATCHDOG_TIMEOUT
7	INVALID_SETPOINT
40020-40021 — RUNTIME_SECONDS

Motorun çalışma süresidir.

32-bit unsigned integer olarak saniye cinsinden tutulur.

40020 = High Word
40021 = Low Word


Örneğin:

3600 seconds


şu anlama gelir:

60 minutes
1 hour

Motor Simülasyonu

Motor modeli START komutu ve SPEED_SETPOINT değerine göre çalışır.

Örneğin:

MOTOR_COMMAND = 1
SPEED_SETPOINT = 1500


olduğunda motor:

0 RPM
100 RPM
200 RPM
300 RPM
...
1500 RPM


şeklinde hedef devire doğru ilerler.

STOP komutu verildiğinde motor:

1500
1350
1200
1050
...
0 RPM


şeklinde yavaşlar.

Varsayılan değerler:

Acceleration = 100 RPM / scan
Deceleration = 150 RPM / scan
Scan time    = 500 ms

Process Simulation

Motor RPM değerine göre proses değerleri hesaplanır.

Current

Temel model:

Current = 2.0 + RPM × 0.005


Motor durduğunda:

Current = 0 A

Pressure

Basınç motor RPM'ine ve pressure setpoint değerine bağlıdır.

Basitleştirilmiş model:

Pressure =
    RPM / Speed Setpoint
    × Pressure Setpoint


Motor hedef RPM'e ulaştığında gerçek basınç hedef basınca yaklaşır.

Temperature

Sıcaklık modeli:

Temperature = 25.0 + RPM × 0.01


Örneğin:

RPM = 1000

Temperature = 25 + 1000 × 0.01
            = 35 °C

Protection

PLC aşağıdaki durumları kontrol eder:

Emergency Stop

Maximum temperature

Maximum RPM

Maximum current

Maximum pressure

Invalid motor command

Invalid operation mode

Invalid setpoint

Varsayılan limitler:

Maximum RPM         = 3000
Maximum Pressure    = 10 bar
Maximum Temperature = 100 °C
Maximum Current     = 30 A

Modbus Poll ile Test

Modbus Poll gibi bir Modbus TCP client ile aşağıdaki bağlantı bilgileri kullanılabilir:

Protocol : Modbus TCP
IP       : 127.0.0.1
Port     : 5020
Unit ID  : 1


Test için örnek komut:

40001 = 1


Ardından:

40003-40004 = 1500


yazılırsa motor 1500 RPM hedefine doğru hızlanmaya başlar.

İzlenebilecek registerlar:

40005-40006 → Actual RPM
40007-40008 → Current
40011-40012 → Actual Pressure
40013-40014 → Temperature
40015       → Alarm
40017       → Heartbeat
40018       → Status Word
40019       → Fault Code
40020-40021 → Runtime

Örnek Test Senaryosu

PLC başlatıldıktan sonra:

1. Motoru çalıştır
40001 = 1

2. RPM hedefini belirle
40003-40004 = 1500

3. Basınç hedefini belirle

5 bar için:

40009-40010 = 50

4. Sonuçları izle
ACTUAL_RPM
CURRENT
ACTUAL_PRESSURE
TEMPERATURE
STATUS_WORD
HEARTBEAT
RUNTIME_SECONDS


Motor hedef RPM'e ulaştıkça proses değerleri değişecektir.

Proje Yapısı

Önerilen proje yapısı:

modbus-plc-simulator/
│
├── server.py
├── README.md
└── .gitignore

Teknolojiler

Python

PyModbus

Modbus TCP

Threading

Dataclasses

Enum / IntFlag

Logging

Not

Bu proje bir simülasyon / test ortamıdır. Gerçek bir PLC, motor veya endüstriyel ekipman kontrolü için doğrudan kullanılmak üzere tasarlanmamıştır.

PyModbus'un yeni sürümlerinde eski datastore API'leri deprecated durumuna geçmiştir. Proje PyModbus 3.13.1 ile test edilmektedir.