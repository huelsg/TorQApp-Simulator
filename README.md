# Simulador de bomba hidráulica Modbus TCP

Simulador Python de una unidad de potencia hidráulica para pruebas de clientes Modbus TCP. Las tres instancias Compose son independientes: cada una tiene su propio estado, contador y registros.

> Modbus TCP no ofrece autenticación ni cifrado. Usa este simulador en una LAN de pruebas confiable y no publiques sus puertos directamente en Internet.

## Requisitos

- Linux con Docker Engine y el plugin Docker Compose v2.
- Para instalar en Arch Linux/CachyOS, instala los paquetes `docker` y `docker-compose` desde los repositorios configurados y habilita el servicio:

  ```sh
  sudo systemctl enable --now docker
  docker --version
  docker compose version
  ```

- El usuario debe tener permisos para usar Docker. En Arch se puede añadir al grupo `docker` con `sudo usermod -aG docker "$USER"`; cierra e inicia sesión de nuevo para que el cambio tenga efecto. El grupo `docker` equivale prácticamente a acceso root, así que úsalo solo con cuentas de confianza. Como alternativa, antepone `sudo` a los comandos Docker.

## Despliegue Bridge

Desde el directorio descargado/clonado del proyecto:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f
```

La primera orden construye la imagen Python e inicia las tres bombas. `Ctrl+C` deja de seguir los logs, pero no detiene los contenedores porque se iniciaron en segundo plano. Para detenerlas:

```sh
docker compose down
```

El modo bridge es el recomendado para las pruebas desde la tablet. Compose publica los puertos en todas las interfaces IPv4 del host:

| Servicio | Nombre de contenedor | Slave ID | Puerto TCP en el host | Puerto dentro del contenedor |
| --- | --- | ---: | ---: | ---: |
| `pump1` | `torque-pump-1` | 1 | 5021 | 502 |
| `pump2` | `torque-pump-2` | 2 | 5022 | 502 |
| `pump3` | `torque-pump-3` | 3 | 5023 | 502 |

Obtén la IP de la interfaz conectada a la misma LAN/Wi-Fi que la tablet:

```sh
ip -brief address
ip route get 1.1.1.1
```

En la aplicación Modbus de Android configura **Modbus TCP**, servidor `<IP_LAN_DEL_PC>`, puerto `5021`, `5022` o `5023` y Slave/Unit ID `1`, `2` o `3`, respectivamente. No uses la IP interna del contenedor. No hace falta abrir el puerto 502 en el host para esta variante; el 502 interno se traduce al puerto elegido de la tabla.

Si no conecta, comprueba que el PC y la tablet estén en la misma red, que el router no tenga activado el aislamiento de clientes Wi-Fi (AP/client isolation), y que el cortafuegos admita TCP entrante en `5021-5023`.

Ejemplos de reglas de firewall:

```sh
# UFW
sudo ufw allow 5021:5023/tcp

# firewalld
sudo firewall-cmd --permanent --add-port=5021-5023/tcp
sudo firewall-cmd --reload
```

Con nftables/iptables, revisa las reglas de entrada y las cadenas de forwarding/DNAT creadas por Docker para esos puertos (`sudo nft list ruleset` o `sudo iptables -S`). Los puertos publicados por Docker pueden recorrer sus propias cadenas y no siempre se comportan como una conexión local ordinaria filtrada únicamente por UFW; verifica la política efectiva del host. No desactives el firewall como método de diagnóstico permanente.

## Registros Modbus

Usa Holding Registers, función 03 para leer, 06 para escribir un registro y 16 para escribir varios. Las direcciones siguientes son offsets de protocolo empezando en cero; algunos clientes los muestran como `40001`-`40006`, mientras otros piden directamente `0`-`5`.

| Offset | Nombre | Lectura/escritura y escala |
| ---: | --- | --- |
| 0 | Control | Escritura: `0` STOP, `1` START, `2` EMERGENCY. Cualquier otro valor también se interpreta como parada. |
| 1 | Setpoint | Escritura en bar x 10. Inicia en `2000` = 200.0 bar; la simulación limita el valor usado a un máximo de 700.0 bar. |
| 2 | Presión actual | Solo telemetría, bar x 10. |
| 3 | Estado | Solo telemetría: `0` Idle, `1` Retrayendo, `2` En consigna, `3` Avanzando. |
| 4 | Contador de ciclos | Solo telemetría: golpes completados desde el arranque del proceso. |
| 5 | Temperatura de aceite | Solo telemetría, grados C x 10. Inicia y permanece en `350` = 35.0 °C; no hay modelo térmico. |

Al probar desde un cliente, escribe primero `1` en offset `0` para iniciar y lee offsets `0`-`5` para observar el ciclo. Unidades y nombres visibles pueden variar según la aplicación cliente.

## Ciclo simulado actual

- El proceso actualiza la telemetría aproximadamente cada 50 ms (20 Hz). Al arrancar, Control=0, Setpoint=2000, presión=0, estado=0, contador=0 y temperatura=350.
- Al recibir Control=1 desde reposo, pasa a Avanzando (estado 3) y parte de 10 bar. La rampa es por etapas: 4 bar por tick (aprox. 80 bar/s) por debajo de 70 bar y 1.75 bar por tick (aprox. 35 bar/s) a partir de 70 bar, hasta el setpoint (limitado a 700 bar).
- Al alcanzar el setpoint, pasa a En consigna (estado 2) durante 10 ticks, aproximadamente 0.5 s.
- Luego pasa a Retrayendo (estado 1): baja inmediatamente a 35 bar y desciende 0.5 bar por tick hasta 25 bar durante la retracción, que dura 24 ticks (aprox. 1.2 s).
- Al terminar incrementa el contador, vuelve a Idle y presión cero, y escribe Control=0. Por tanto, **no repite continuamente**: para comenzar otro golpe hay que volver a escribir Control=1.
- Si Control no es 1 (STOP, EMERGENCY u otro valor), el ciclo se aborta, la presión vuelve a cero y el estado pasa a Idle. No hay una diferencia de respuesta entre STOP y EMERGENCY en la física actual.

El contador y el resto del estado de simulación están en memoria y vuelven a sus valores iniciales al recrear/reiniciar el contenedor. `PUMP_NAME` cambia la etiqueta que aparece en los logs; por defecto es `Bomba-Simulada`. `MODBUS_PORT` define el puerto TCP dentro del contenedor (502 en Compose) y `SLAVE_ID` define el identificador Modbus (1, 2 y 3 en Compose). El nivel de logs muestra los comandos recibidos y el progreso del ciclo:

```sh
docker compose logs -f pump1
```

## Variante opcional: macvlan

El final de `docker-compose.yml` contiene un ejemplo macvlan comentado para Linux. Con macvlan cada contenedor recibe una IP propia de la LAN y escucha en el puerto 502; Android conectaría a `192.168.1.201:502`, `.202:502` o `.203:502` con su Slave ID correspondiente.

Antes de activarlo:

1. Identifica la interfaz Ethernet física, la subred y la puerta de enlace con `ip -brief link` y `ip -4 route`. Sustituye `enp3s0`, `192.168.1.0/24` y `192.168.1.1` en el bloque `networks` por los valores reales.
2. Sustituye las IP `192.168.1.201`-`.203` en los tres servicios por direcciones libres, reservadas o excluidas del rango DHCP.
3. Descomenta `networks` en cada servicio y el bloque superior `networks` de nivel raíz. Quita `ports` de los servicios; en macvlan no se usa la publicación de puertos bridge.
4. Recrea con `docker compose up --build -d` y permite TCP `502` hacia esas IPs en el firewall de la LAN/host, según la configuración de la interfaz.

El host Docker normalmente no puede comunicarse directamente con sus propios contenedores macvlan sin crear una interfaz macvlan adicional en el host. Además, muchos adaptadores y puntos de acceso Wi-Fi no permiten macvlan porque el enlace espera una sola dirección MAC; usa bridge en ese caso. La tablet y los contenedores deben poder comunicarse en la misma VLAN/subred.

## Variables de entorno

| Variable | Default del programa | Uso |
| --- | --- | --- |
| `MODBUS_PORT` | `502` | Puerto TCP donde escucha el servidor dentro del contenedor. |
| `SLAVE_ID` | `1` | Unit/Slave ID Modbus de esa instancia. |
| `PUMP_NAME` | `Bomba-Simulada` | Etiqueta de identificación en los logs. |

Compose fija los valores de puerto y Slave ID para cada bomba y no define `PUMP_NAME`, por lo que los logs usan el nombre por defecto. Para cambiar estos ajustes, modifica la sección `environment` del servicio correspondiente en `docker-compose.yml`.