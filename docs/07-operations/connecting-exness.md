<!--
title: ELYON QUANT — Conectar Exness (MetaTrader 5)
id: OPS-001
owner: Execution Lead
status: draft
version: 0.1
-->

# Conectar Exness (MetaTrader 5)

> **Lee la sección 1 antes de conectar nada.** Hay una diferencia entre MT5 y el
> resto de venues que cambia el análisis de seguridad del OMS, y no es opcional
> entenderla.

---

## 1. MT5 no tiene client order id

Todo el diseño anti-duplicados del OMS descansa en tres defensas
independientes. Contra MT5, **una de las tres no existe**.

`order_send` acepta un `magic` (entero) y un `comment` (~31 caracteres). Ninguno
de los dos es una clave de deduplicación: manda la misma petición dos veces y
tienes **dos posiciones**. Cualquier otro venue rechazaría el `client_order_id`
repetido; MT5 la ejecuta encantado.

Qué queda en pie y qué no:

| Defensa | Contra MT5 |
|---|---|
| La máquina de estados (`QUEUED` es el único camino a `SENT`) | ✅ intacta — el OMS no puede enviar dos veces por su cuenta |
| Preguntar antes de reenviar (`query`) | ✅ intacta — **y ahora carga todo el peso** |
| Deduplicación del venue por client order id | ❌ **no existe** |

El OMS **nunca hace un reenvío a ciegas**: solo reenvía después de que el venue
haya dicho que la orden no existe. Así que sigue siendo seguro — *siempre que
`query` sea fiable*. Por eso el adaptador busca la orden en **cuatro sitios**:

1. Órdenes pendientes (`orders_get`)
2. Posiciones abiertas (`positions_get`)
3. Historial de deals reciente (`history_deals_get`)
4. Y si ninguna la encuentra, **y solo entonces**, responde `exists=False`

Una orden que se ejecutó y se cerró mientras la conexión estaba caída sigue
teniendo que encontrarse. Buscar en menos sitios significa responder
«no existe» sobre una orden que sí existe — y en MT5 eso significa duplicar.

### Dos riesgos residuales que ningún código elimina

**La ventana en vuelo.** Si `order_send` da timeout y la orden todavía se está
procesando, `query` puede no verla aún. `settle_seconds` (1s por defecto) espera
antes de la primera consulta para estrechar esa ventana. **No la cierra.**

**El tag `magic`/`comment`.** Es cómo el adaptador reconoce sus propias órdenes.
Si otra cosa opera la misma cuenta con el mismo `magic`, este adaptador
confundirá esas operaciones con las suyas. Usa un `magic` exclusivo.

---

## 2. Requisitos

El paquete `MetaTrader5` es **solo Windows** y necesita:

- Terminal MT5 instalado y **corriendo**
- Sesión iniciada en tu cuenta Exness
- **Trading algorítmico habilitado** (Herramientas → Opciones → Expert Advisors)

```bash
pip install MetaTrader5
```

En Linux/macOS hace falta Wine, o correr el terminal en una VM Windows y
conectar el motor por red. No está resuelto en este repositorio.

---

## 3. Credenciales

Nunca en el código, nunca en `session.json`, nunca en git. El adaptador **no
tiene campos para ellas** a propósito: una contraseña en un objeto acaba en un
`repr`, una línea de log o un traceback tarde o temprano.

```python
import os
from elyon.modules.execution.infrastructure.mt5 import connect, Mt5Adapter, Mt5Config

connect(
    login=int(os.environ["EXNESS_LOGIN"]),
    password=os.environ["EXNESS_PASSWORD"],
    server=os.environ["EXNESS_SERVER"],      # p.ej. "Exness-MT5Trial7"
)
```

El nombre del servidor tiene que coincidir **exacto** con el que aparece en tu
terminal. Es el error más común al conectar.

El `.gitignore` ya bloquea `.env`, `credentials*`, `secrets*`, `*.key` y
`session.json`. Un secreto commiteado no se borra borrándolo: vive en el
historial.

---

## 4. Sufijo de símbolo

Exness añade un sufijo al símbolo según el tipo de cuenta:

| Cuenta | Símbolo |
|---|---|
| Standard / Cent | `EURUSDm` |
| Pro / Raw / Zero | `EURUSD` |

Se configura en el adaptador, **no** renombrando cosas en la estrategia — la
capa de estrategia no debería saber qué cuenta abriste:

```python
Mt5Config(symbol_suffix="m", magic=20260101)
```

Compruébalo en tu terminal: Ver → Símbolos.

---

## 5. Verificar antes de operar

```bash
elyon conformance --adapter elyon.modules.execution.infrastructure.mt5:build
```

> ⚠️ **Estas comprobaciones colocan órdenes reales.** Cuenta **demo**.

Qué esperar contra MT5:

| Comprobación | Resultado esperado |
|---|---|
| `query` sobre una orden desconocida | ✅ debe pasar |
| `place` y luego `query` | ✅ debe pasar — si falla, **no conectes**: es el bug de duplicación |
| duplicate place is deduplicated | ❌ **fallará** — MT5 no deduplica. Es un hecho del venue, no un fallo del adaptador |
| errors are typed | ✅ o «could not provoke» |
| cancel is reflected in query | ✅ (no crítica) |

El fallo de deduplicación es **esperado y está documentado arriba**. Los demás
críticos no: si alguno falla, el análisis de seguridad de la sección 1 deja de
sostenerse.

---

## 6. Cómo se clasifican los retcodes

El OMS pregunta una sola cosa: **¿el resultado es un hecho o una pregunta?**

- **Hecho** (rechazo) → se registra y se acabó.
- **Pregunta** (timeout) → se concilia consultando al venue.

Un código **no reconocido se trata como pregunta**, nunca como rechazo. La
dirección peligrosa es la contraria: llamar «rechazada» a una orden que sí se
aplicó deja una posición que nadie está siguiendo. Llamar «desconocida» a un
rechazo cuesta una consulta desperdiciada.

> ⚠️ **Los números de retcode en `mt5.py` están transcritos de memoria y hay que
> verificarlos** contra la documentación de tu terminal antes de operar en real.
> La *estructura* es segura —lo no listado se trata como desconocido—, pero un
> número mal puesto en esa tabla es una decisión equivocada sobre si conciliar.

---

## 7. Orden de puesta en marcha

1. **Demo, conformance.** Sección 5. Sin eso, nada más.
2. **Demo, `mode: PAPER`.** El motor decide pero no manda nada al venue.
3. **Calibra.** Recién instalado el bot **no opera**: todo está en ⚪ y el gate
   lo rechaza. Necesitas `elyon calibrate` con datos **out-of-sample**.
4. **Demo, `mode: LIVE`.** Órdenes reales en cuenta demo. Deja el `--journal`
   activado y revisa el log.
5. **Real, tamaño mínimo.** Semanas, no días.

En cada paso, `stopped_at_counts()` te dice en qué etapa se para el pipeline.
Si no opera, esa tabla dice dónde mirar.
