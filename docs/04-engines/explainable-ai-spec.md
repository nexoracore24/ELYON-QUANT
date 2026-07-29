<!--
title: ELYON QUANT — Explainable AI (XAI) Standard
id: ENG-010 (Explainable AI — estándar transversal de núcleo)
owner: ML Lead
reviewers: [Quant Lead, CTO/Principal Architect, Security Lead, Design Lead]
status: draft
version: 0.1
last_updated: 2026-07-28
supersedes: refuerza trading-engine-bible.md §40
-->

# ELYON QUANT — EXPLAINABLE AI STANDARD (ENG-010)

> **Invariante de núcleo (⛔):** toda decisión del motor —entrar o no entrar— debe
> poder explicarse **exactamente**. El sistema **nunca** responde *"entró porque
> sí"*. Si una decisión no puede explicarse con evidencia trazable, **no debe
> tomarse**.

Este estándar es **transversal**: aplica al Trading Engine (ENG-001), al Smart
Money Engine (ENG-002), al AI Engine (ENG-003) y se materializa a través del
Decision Replay Engine (ENG-009) y el Dashboard (DES-006). La explicabilidad no es
una función cosmética: es un **requisito de confianza, auditoría y cumplimiento**
para una plataforma que opera capital real.

---

## 1. Qué debe explicar toda decisión (contrato mínimo)

Para **cada** decisión, el sistema debe ser capaz de responder, con datos
trazables al `DecisionRecord` (ENG-009):

1. **Qué detectó** — features Smart Money observadas (tendencia, liquidez, sweep,
   BOS/CHoCH/MSS, OB/FVG, Fibonacci/OTE, sesión, ATR, spread, noticias).
2. **Qué confirmó** — qué criterios se cumplieron y **sumaron** al score.
3. **Qué descartó** — qué criterios **no** se cumplieron y por qué (p.ej. "no hay
   FVG en el desplazamiento", "POI ya mitigado", "precio en equilibrium").
4. **Qué peso tuvo cada criterio** — la contribución numérica de cada factor.
5. **Qué score obtuvo** — total por lado y umbral aplicado.
6. **Qué reglas se activaron** — confirmaciones/gatillos que dispararon.
7. **Qué reglas bloquearon la entrada** — vetos duros evaluados y cuál(es)
   impidieron operar (spread, noticias, límites de riesgo, conflicto de bias…).

Una explicación que omita cualquiera de estos siete puntos es **incompleta** y se
considera un defecto (test de cobertura, §6).

---

## 2. Por qué ELYON QUANT es explicable **por diseño** (no post-hoc)

La ventaja de la arquitectura del motor: **el núcleo de decisión es intrínsecamente
interpretable**, no una caja negra que requiera aproximaciones.

- El **Scoring Engine** (ENG-001 §26) es una **suma ponderada de factores objetivos**
  (confluencia lineal transparente). La contribución de cada factor es **exacta**,
  no estimada — no necesitamos SHAP/LIME para *aproximar* la importancia: la
  conocemos con precisión porque es la propia fórmula.
- Cada **detector** (ENG-002) es una regla determinista con condición explícita →
  su activación es autoexplicativa ("CHoCH alcista confirmado por cierre en `j` con
  displacement `1.8·ATR`").
- Los **vetos** son reglas booleanas nombradas → el motivo de bloqueo es literal.

> **Principio:** preferimos un modelo **interpretable de raíz** a un modelo opaco
> con explicación posterior. La explicación es la **verdad del cálculo**, no una
> narrativa plausible construida a posteriori.

### 2.1 Restricciones sobre componentes de ML (AI Engine, ENG-003)
Si el AI Engine aporta *features* al scoring (p.ej. probabilidad de continuación),
esas features quedan sujetas a XAI (⛔):
- **Sin override opaco:** un modelo ML **nunca** decide por su cuenta ni anula el
  scoring de forma no explicable; **entra como un factor más**, con su peso visible.
- **Atribución obligatoria:** cada feature ML expone su contribución (feature
  attribution) y sus *inputs*.
- **Restricciones de forma:** preferencia por modelos con **monotonicidad** y
  restricciones interpretables; si se usa un modelo complejo, debe acompañarse de
  atribuciones fieles y validadas, y **degradar con gracia** (si la explicación no
  es fiable, el factor no puntúa).
- **Model/Data cards** (estilo OpenAI/Google): cada modelo documenta datos,
  límites, sesgos y métricas (gobernado por ENG-003).

---

## 3. Dos capas de explicación, una única verdad

1. **Estructurada (máquina):** el `DecisionRecord` (ENG-009) con el desglose
   factor-a-factor, reglas y vetos. Es la **fuente de verdad**.
2. **Narrativa (humano):** texto claro generado **determinísticamente** desde el
   registro mediante plantillas (§4). Opcionalmente, un **resumen en lenguaje
   natural por LLM** (ENG-003), **estrictamente derivado** del registro.

### 3.1 Guardarraíl del narrador LLM (⛔)
- El LLM **narra, no decide**. No participa en la decisión de trading.
- **Fidelidad estricta:** toda afirmación de la narrativa debe **mapear** a un
  campo del `DecisionRecord`. Prohibido introducir factores, cifras o causas **no
  presentes** en el registro (anti-alucinación).
- **Verificación:** un validador comprueba que cada aserción de la narrativa tiene
  respaldo en el registro; si no, la narrativa se rechaza y se muestra la versión
  por plantilla.
- **Determinismo de contenido:** mismas entradas ⇒ mismas afirmaciones (la
  redacción puede variar, los **hechos no**).

---

## 4. Formato de explicación (plantillas deterministas)

### 4.1 Esquema estructurado (contrato `Explanation`)
```
Explanation {
  decision_id, action, side, score, threshold,
  detected:   [ {feature, value, source_detector} ],
  confirmed:  [ {factor, points, condition} ],
  discarded:  [ {factor, reason, expected_condition} ],
  weights:    [ {factor, weight, points_awarded} ],
  rules_fired:   [ rule_id ],
  vetoes_blocked:[ {veto_id, reason} ],       // vacío si no hubo bloqueo
  primary_reason,                              // motivo exacto
  narrative_text
}
```

### 4.2 Plantilla — Entrada
> **[LONG XAUUSD · score 88/100 · umbral 70]**
> **Detecté:** bias H4 alcista (HH/HL), precio en discount (Fib 0.70, OTE), un
> bullish Order Block sin mitigar y un FVG a favor; London killzone; ATR normal;
> spread OK; sin noticias.
> **Confirmé:** sweep de SSL con rechazo (+12), CHoCH M5 con displacement (+15),
> POI en OTE 0.705 (+6+8 discount), imbalance FVG (+10), killzone (+8)…
> **Descarté/faltó:** volumen no confirmó (+0).
> **Score:** 88 ≥ 70. **Reglas activadas:** sweep, choch, poi_unmitigated,
> discount, ote. **Vetos:** ninguno.
> **Decisión:** ENTER LONG. Entrada en 0.705, SL bajo la mecha del sweep (1R),
> TP en BSL/Fib 1.618 (RR 3.0).

### 4.3 Plantilla — No-entrada
> **[NO-TRADE GBPUSD · score 58/100 · umbral 70]**
> **Detecté:** CHoCH alcista y sweep de mínimos.
> **Descarté:** precio en **premium** (no discount), POI **ya mitigado**, **sin
> FVG** en el desplazamiento → faltaron 12 puntos.
> **Reglas que bloquearon:** **veto de noticias** (evento GBP de alto impacto en
> 11 min) y score < umbral.
> **Decisión:** NO-TRADE. Motivo exacto: `veto:news_window` + `score_below_threshold`.
> **Watchlist:** sí (score 55–69).

---

## 5. Dónde vive la explicación (UX)

- **Dashboard (DES-006):** cada operación y cada descarte muestra su explicación;
  el **Decision Replay** (ENG-009) la reproduce paso a paso con el gráfico anotado.
- **Consultas agregadas:** "¿por qué el motor rechazó el 70 % de los setups hoy?"
  → distribución de motivos y contribución de factores.
- **Notificaciones (ENG-011/notifications):** las alertas de operación incluyen el
  resumen explicativo, nunca un aviso sin causa.

---

## 6. Verificación y testing

- **Cobertura 100 % (⛔):** toda decisión tiene `Explanation` completa (los 7
  puntos de §1). Test de invariante: `decisiones_sin_explicacion == 0`.
- **Fidelidad:** test que verifica que cada afirmación de la narrativa mapea a un
  campo del registro (para plantilla y para LLM).
- **Suma consistente:** `Σ points_awarded == score` y `score ≥ threshold ⇔ action ≠ no_trade`
  (salvo veto) — verificado por test.
- **Anti-alucinación LLM:** *golden set* de registros → narrativas validadas;
  cualquier aserción no respaldada falla el test.
- **Reproducibilidad:** misma entrada ⇒ mismas afirmaciones (determinismo de
  hechos).

---

## 7. Relaciones

- **Trading Engine (ENG-001 §26/§40):** origen del score, reglas y vetos que se
  explican.
- **Smart Money Engine (ENG-002):** cada feature explicada es salida de un
  detector (incl. Fibonacci D32).
- **Decision Replay Engine (ENG-009):** provee el registro y reproduce la
  explicación paso a paso.
- **AI Engine (ENG-003):** narrador LLM (bajo guardarraíl) y features ML
  explicables; model/data cards.
- **Security/Compliance (SEC-000):** la explicabilidad es requisito de
  auditoría y de cara al usuario (transparencia).

> **Versión 0.1 — Borrador (🟨).** Estándar transversal de núcleo. Aprobación (🟩)
> requiere revisión de ML Lead, Quant Lead, CTO, Security y Design. Prerrequisito
> del gate D4. *"Entró porque sí" es un fallo del sistema, no una respuesta.*
