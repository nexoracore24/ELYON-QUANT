# ADR-0013: Login y configuración remota

- **Estado:** Accepted
- **Fecha:** 2026-09-04
- **Decisores:** CTO/Principal Architect, Security Lead, Execution Lead
- **Relacionado:** [ADR-0006](0006-deterministic-computing.md) ·
  [ADR-0011](0011-oms-duplicate-prevention.md) ·
  [ADR-0012](0012-live-market-data-feed.md)
- **Implementa:** `modules/api/domain/{accounts,control}.py`,
  `modules/session/domain/settings.py`

## Contexto

La superficie de control sabía contestar dos preguntas: *qué está pasando* y
*para*. Se autenticaba con un token que el motor imprimía al arrancar.

Eso está bien para algo que **miras y paras**, y mal para algo que **configuras
y arrancas**. Nadie se aprende 43 caracteres, así que el token acaba en una app
de notas — y una credencial que vive en una app de notas es una credencial de la
app de notas. Además no caduca, no se puede revocar sin reiniciar el motor, y no
distingue quién hizo qué.

Y hay una pregunta nueva que el modelo de token no responde: **¿qué se puede
cambiar en un motor que ya está corriendo?** Un fichero de configuración se edita
con todo parado, así que todos los ajustes son igual de seguros. Un motor vivo no
es así.

## Decisión

### 1. La contraseña no es la credencial de la API

Iniciar sesión **cambia** la contraseña por un token de sesión, y es el token el
que viaja en cada petición posterior. La contraseña cruza la red una vez por
sesión en lugar de cada cinco segundos, y lo que acaba en el almacenamiento del
navegador es algo que **caduca solo** (12h absolutas, 30min de inactividad) y que
se puede revocar sin tocar la contraseña.

Una contraseña no se puede retirar. Una sesión sí — y `revoke_all(usuario)` es
exactamente lo que alguien quiere después de perder el móvil.

### 2. PBKDF2-HMAC-SHA256, de la librería estándar

600.000 iteraciones, sal por cuenta, y **el número de iteraciones viaja dentro
del hash** para poder subir el coste sin invalidar las contraseñas existentes.

Sin argon2, sin bcrypt, sin dependencia: el mismo criterio que el resto de la API.
La monitorización de un sistema de trading no puede dejar de funcionar porque un
índice de paquetes tenga un mal día, y eso incluye lo que te deja entrar a usarla.

Un hash corrupto **no revienta**: devuelve «no coincide». Una línea mal escrita en
el fichero de operadores debe bloquear una cuenta, no tumbar la superficie de
control de un sistema con una posición abierta.

### 3. Un intento fallido cuesta tiempo, y el coste se dobla

Un formulario de login en un motor de trading que contesta al instante es un
formulario que merece la pena adivinar. Dos contadores, para dos amenazas
distintas:

- **Por dirección de cliente, estricto** (5 intentos). Es el que frena el
  guessing. El atacante controla su propia dirección: ralentizarla le cuesta a él
  y a nadie más.
- **Por cuenta, generoso** (50). Respaldo ante un intento distribuido, y
  deliberadamente difícil de disparar: **un bloqueo por cuenta apretado es una
  denegación de servicio que cualquiera puede apuntar al dueño.** Falla su login
  cinco veces y no puede llegar a su propio motor con una posición abierta. Ese
  intercambio no compensa.

### 4. Usuario incorrecto y contraseña incorrecta son la misma respuesta, y
tardan lo mismo

Un mensaje distinto convierte el formulario en un enumerador de cuentas: lo
primero que aprende un atacante es en qué nombre gastar los intentos. Un tiempo
distinto hace lo mismo aunque el mensaje sea igual, así que la rama del usuario
inexistente **gasta el mismo trabajo de hashing** y lo tira.

### 5. Los roles son las capacidades, no una idea nueva

`VIEWER → OBSERVE`, `OPERATOR → OBSERVE+PROTECT`, `OWNER → +COMMAND`. La
asimetría sobre la que está construido todo lo demás **sobrevive al login**:
parar es seguro, arrancar no. Un OPERATOR para el motor a las 3am desde el móvil;
solo un OWNER lo arranca, sube el riesgo o lo pone en LIVE.

La primera cuenta se crea **fuera de la red**: `elyon useradd`, en la máquina. No
hay login por defecto. Una credencial por defecto en algo que puede mandar
órdenes no es una comodidad, es una donación.

### 6. Lo que se puede cambiar depende de lo que tengas puesto

Cada ajuste declara un **alcance**, y el alcance es dato, no cortesía:

| Alcance | Cuándo | Por qué |
|---|---|---|
| `LIVE` | En la siguiente vela | Solo afecta a lo que venga después |
| `FLAT_ONLY` | Solo sin posición abierta | Cambia lo que un número *significa* |
| `RESTART` | Sesión nueva | El histórico acumulado pertenece al valor viejo |

El caso que justifica la tabla: si una posición se dimensionó contra 10.000 € de
equity y un 0,5% de riesgo, **editar el equity a 50.000 € con la posición abierta
no la redimensiona** — solo convierte en mentira cada número que se reporta sobre
ella. No es reconfigurar: es reescribir el significado de una operación que ya
está puesta.

Y el símbolo, el timeframe o el periodo del ATR no se cambian en caliente porque
el constructor de velas está cortado para uno, y el ATR es un valor corriente
sobre una ventana fija. Cambiarlos a mitad de stream no produce la configuración
nueva: produce **un híbrido que nunca existió** sobre un histórico que pertenece
a la vieja.

La tabla vive en el **dominio** (`session/domain/settings.py`), y
`TradingSession.reconfigure()` es quien de verdad decide. La superficie de
control decide qué *ofrecer*; solo puede rechazar antes o con mejores palabras,
nunca permitir lo que el dominio prohíbe.

### 7. Un cambio rechazado no deja nada a medias

La configuración nueva se construye y se valida **entera** antes de intercambiar
nada. No existe un estado en el que la fracción de riesgo entró y la lista de
estrategias no, porque no existe un instante en el que una esté escrita y la otra
no. Una edición rechazada deja el motor exactamente como estaba.

### 8. Pasar a LIVE se escribe, no se pulsa

Cualquier otro ajuste se deshace volviéndolo a poner. Este no: **una orden que
llegó a un broker real no se deshace cambiando el modo después.** Hay que teclear
`TRADE REAL MONEY`. Salir de LIVE, en cambio, no pide ceremonia — todas las
salvaguardas apuntan en la misma dirección: que reducir riesgo sea fácil.

### 9. Arrancar se comprueba, no solo se permite

Pulsar Start en un motor que no puede operar —ninguna estrategia calibrada, feed
muerto, LIVE contra un broker de papel— produce **un bot que parece sano y no
hace nada**, que es el fallo con el que más tiempo se pierde. El preflight lo
dice antes.

La frontera entre bloqueante y aviso es una pregunta: *¿arrancar sería un error o
solo silencio?* LIVE apuntando a un `PaperBroker` es un error. PAPER sin nada
calibrado es silencio — y el silencio es exactamente cómo una estrategia se gana
un tier.

Hay un `force`, porque una comprobación puede equivocarse y un motor que no se
puede arrancar es peor que uno que avisa. Se **registra** cuando se usa: «lo
forzamos» es la primera pregunta después de un mal día.

### 10. Cada cambio queda con un nombre encima

Un cambio de configuración es tan consecuente como una orden: decide cómo será
cada orden posterior. El registro dice **quién** cambió **qué**, y de qué valor a
qué valor. «Debíamos estar corriendo con otros ajustes» no es una explicación que
nadie pueda comprobar seis meses después.

## Alternativas descartadas

- **Seguir con el token impreso.** Funciona para mirar y parar; no distingue
  personas, no caduca, y no se puede revocar sin reiniciar el motor.
- **Cookies de sesión.** Traen CSRF consigo. Un token en `Authorization` desde JS
  no lo tiene, y no hay nada que un formulario de terceros pueda enviar.
- **JWT.** Firma sin estado, revocación imposible — justo la propiedad que hace
  útil una sesión aquí. Con un solo proceso, una lista en memoria es más simple y
  más revocable.
- **Bloqueo por cuenta estricto.** Es la respuesta intuitiva y le da a cualquiera
  un botón para dejar al dueño fuera de su propio motor.
- **Mensajes distintos para usuario y contraseña.** Convierte el formulario en un
  enumerador de cuentas.
- **Aplicar los ajustes de uno en uno según se tocan.** Deja configuraciones a
  medias en el cable y hace imposible validar el conjunto.
- **Permitir cambiar el símbolo en caliente reiniciando la sesión por dentro.**
  Parecería que el ajuste «funciona» mientras tira silenciosamente todo el
  histórico y las posiciones. Un `RESTART` explícito es más honesto.
- **Un interruptor para LIVE.** Un interruptor se roza. Un modo que decide si las
  pérdidas son reales no tiene esa forma.
- **Rol de administrador con permisos arbitrarios.** Los roles aquí son nombres
  de capacidades existentes; un rol que no se pueda expresar en OBSERVE /
  PROTECT / COMMAND sería un permiso que nadie ha graduado por riesgo.

## Consecuencias

**Positivas.** Se puede llegar al motor desde el móvil, ver por qué no opera,
ajustar riesgo y estrategias, comprobar si arrancar serviría de algo y arrancar —
con la asimetría intacta y con registro de quién hizo cada cosa. La tabla de
alcances hace imposible por construcción cambiar un ajuste que corrompería lo que
ya está corriendo.

**Negativas / coste asumido.** Las sesiones viven en memoria: reiniciar el motor
echa a todo el mundo. Es aceptable en un proceso único y evita persistir tokens.
El fichero de operadores es un JSON, no una base de datos: sin auditoría de
accesos, sin rotación, sin 2FA. Y **sigue sin haber TLS aquí** (ADR previo): un
login sobre HTTP plano expuesto a internet entrega la contraseña, así que el
servidor sigue escuchando en `127.0.0.1` y gritando si lo mueves de ahí.

**Deuda registrada.** Sin 2FA. Sin recuperación de contraseña (a propósito: un
flujo de recuperación es una segunda puerta, y en un sistema de un solo dueño la
puerta es el acceso a la máquina). Sin caducidad de contraseñas ni historial. El
registro de cambios vive en memoria junto a la sesión; debería ir al mismo
`EventStore` que las órdenes.
