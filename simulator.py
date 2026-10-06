import os
import time
import threading
import sys
from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSequentialDataBlock, ModbusSlaveContext, ModbusServerContext

# Forzar flush inmediato en consola
def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

PORT = int(os.getenv("MODBUS_PORT", 502))
SLAVE_ID = int(os.getenv("SLAVE_ID", 1))
PUMP_NAME = os.getenv("PUMP_NAME", "Bomba-Simulada")

# Registros iniciales:
# Reg 0: Control (0: STOP, 1: START, 2: EMERGENCY)
# Reg 1: Setpoint (bar * 10) -> 2000 = 200.0 bar
# Reg 2: Presión actual (bar * 10)
# Reg 3: Estado (0: Idle, 1: Retrayendo, 2: En Consigna, 3: Avanzando)
# Reg 4: Contador de ciclos
# Reg 5: Temp (degC * 10)
initial_registers = [0, 2000, 0, 0, 0, 350] + [0] * 10

class CustomDataBlock(ModbusSequentialDataBlock):
    """Intercepta escrituras de la app en tiempo real"""
    def setValues(self, address, values):
        super().setValues(address, values)
        # Offset 1 en pymodbus corresponde al registro 0 en lógica de PLC
        if address == 1:
            val = values[0]
            desc = {0: "PARADA (STOP)", 1: "MARCHA (AUTO CYCLE)", 2: "EMERGENCIA"}.get(val, "OTRO")
            log(f"[{PUMP_NAME}] >>> COMANDO RECIBIDO -> Control = {val} ({desc})")
        elif address == 2:
            val = values[0] / 10.0
            log(f"[{PUMP_NAME}] >>> NUEVA CONSIGNA -> Setpoint = {val:.1f} bar")

datablock = CustomDataBlock(0, initial_registers)
context = ModbusServerContext(slaves={SLAVE_ID: ModbusSlaveContext(hr=datablock)}, single=False)

def physics_loop():
    slave_ctx = context[SLAVE_ID]
    current_pressure = 0.0
    cycles = 0
    state = 0            # 0: Idle, 1: Retracting, 2: Target Reached, 3: Advancing
    dwell_counter = 0

    log(f"[{PUMP_NAME}] Bucle físico operativo (Calibración Realista). Esperando conexión...")

    while True:
        try:
            # Leer registros
            values = slave_ctx.getValues(3, 0, count=6)
            control = values[0]
            setpoint = min(values[1] / 10.0, 700.0)

            if control == 1:  # START CICLO
                if state == 0:
                    state = 3  # Iniciar Avance
                    current_pressure = 10.0
                    log(f"[{PUMP_NAME}] Válvula de avance activa. Presurizando hacia {setpoint:.1f} bar...")

                if state == 3:  # Avanzando
                    # Bomba de 2 etapas:
                    # En baja presión (< 70 bar) avanza a ~80 bar/s (4.0 bar/tick)
                    # En alta presión (>= 70 bar) empuja a ~35 bar/s (1.75 bar/tick)
                    step = 4.0 if current_pressure < 70.0 else 1.75
                    current_pressure += step

                    if current_pressure >= setpoint:
                        current_pressure = setpoint
                        state = 2  # Target Reached
                        # Sostener presión por 0.5 s (10 ticks a 50 ms) para asegurar deformación elástica
                        dwell_counter = 10  
                        log(f"[{PUMP_NAME}] Presión objetivo {current_pressure:.1f} bar alcanzada. Manteniendo...")

                elif state == 2:  # En consigna
                    dwell_counter -= 1
                    if dwell_counter <= 0:
                        state = 1  # Conmutar a Retracción
                        current_pressure = 35.0  # Caída instantánea a presión de retorno
                        # Retracción por resorte: 1.2 segundos (24 ticks a 50 ms)
                        dwell_counter = 24  
                        log(f"[{PUMP_NAME}] Solenoide conmutado. Retrayendo vástago por resorte...")

                elif state == 1:  # Retrayendo
                    dwell_counter -= 1
                    # Ligera caída gradual de la contrapresión durante la retracción
                    if current_pressure > 25.0:
                        current_pressure -= 0.5

                    if dwell_counter <= 0:
                        cycles += 1
                        state = 0  # Reposo
                        current_pressure = 0.0
                        log(f"[{PUMP_NAME}] Golpe #{cycles} finalizado. Auto-reset a STOP.")
                        
                        # Apagar automáticamente el registro de marcha en la bomba
                        slave_ctx.setValues(3, 0, [0])

            else:
                # STOP manual o Emergencia
                if state != 0:
                    log(f"[{PUMP_NAME}] Ciclo abortado por parada manual.")
                current_pressure = 0.0
                state = 0

            # Escribir telemetría de vuelta
            slave_ctx.setValues(3, 2, [int(current_pressure * 10)])
            slave_ctx.setValues(3, 3, [state])
            slave_ctx.setValues(3, 4, [cycles])

        except Exception as e:
            log(f"[{PUMP_NAME}] Error en physics_loop: {e}")

        # 20 lecturas por segundo: fluido en pantalla y sin sobrecargar la red
        time.sleep(0.05)
# Iniciar hilo de simulación
threading.Thread(target=physics_loop, daemon=True).start()

# Iniciar servidor TCP
log(f"[{PUMP_NAME}] Servidor escuchando en puerto {PORT} (Slave ID: {SLAVE_ID})")
StartTcpServer(context=context, address=("0.0.0.0", PORT))