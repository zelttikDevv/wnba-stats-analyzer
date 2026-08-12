# WNBA Stats Analyzer

Aplicación CLI para Termux/Python para análisis probabilístico de puntos totales
en partidos WNBA.

NO es una herramienta de apuestas garantizadas. Produce estimaciones con
incertidumbre y métricas de error. El rendimiento pasado no garantiza resultados
futuros.

---

## Instalación en Termux

```bash
pkg update
pkg install python git

git clone <repo>
cd wnba_stats_analyzer

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
```

Por defecto:

```bash
ENABLE_ODDS=false
SPORTS_PROVIDER=demo
```

Esto permite probar todo sin red y sin API keys.

---

## Uso

### Demo offline

```bash
python main.py --demo
```

Para actualizar continuamente:

```bash
python main.py --demo --watch
```

### Live

```bash
python main.py --live
```

Si `SPORTS_PROVIDER=demo`, `--live` seguirá usando datos ficticios.

Para intentar datos reales:

```bash
SPORTS_PROVIDER=espn python main.py --live --watch
```

Advertencia: ESPN no es una API oficial WNBA y puede cambiar.

### Backtest puro

Siempre disponible, sin odds:

```bash
python main.py --backtest
```

Calcula:

- MAE
- RMSE
- Sesgo
- Desglose por equipo

### Backtest de señales

Solo si:

```bash
ENABLE_ODDS=true
```

y hay líneas guardadas en `odds_snapshots`:

```bash
python main.py --backtest --signals
```

---

## Configuración clave

En `.env`:

```bash
ENABLE_ODDS=false
SPORTS_PROVIDER=demo
HIST_WINDOW=10
WNBA_MIN_HIST_GAMES=8
SNAPSHOT_QUARTER=3
```

### `WNBA_MIN_HIST_GAMES`

Es el umbral mínimo de partidos históricos por equipo antes de confiar
razonablemente en promedios históricos.

Si un equipo tiene menos partidos:

- el modelo histórico se encoge hacia el promedio de liga;
- la incertidumbre aumenta;
- el modelo combinado sube el peso del partido actual si hay cuartos;
- si no hay histórico ni cuartos, usa modelos simples.

---

## Proveedores de datos

### Demo

`SPORTS_PROVIDER=demo`

- Sin red.
- Histórico sintético.
- Partido en vivo guionado.
- Útil para tests y demo.

### ESPN no oficial

`SPORTS_PROVIDER=espn`

Usa endpoints públicos no oficiales tipo:

```text
https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard
```

Limitaciones:

- No es API oficial.
- Puede cambiar sin aviso.
- Puede no devolver cuartos.
- Puede no devolver histórico.
- Puede tener límites de tasa no documentados.

### FileProvider

`SPORTS_PROVIDER=file`

Lee:

```text
data/scoreboard.json
data/history.json
```

Esto permite conectar otra fuente real sin tocar el núcleo.

---

## Odds opcionales

La proyección de puntos NO depende de odds.

Las odds solo sirven para:

1. convertir una proyección en señal binaria OVER/UNDER contra una línea;
2. calcular métricas hipotéticas tipo apuesta en backtest de señales.

Por defecto:

```bash
ENABLE_ODDS=false
```

Si se activa, hay que verificar explícitamente:

- si el proveedor tiene plan gratuito real;
- si cubre WNBA;
- si entrega mercados totals;
- si conserva histórico accesible.

No se debe asumir que existe una API gratuita de cuotas WNBA suficiente.
Si no la hay, la app sigue funcionando sin odds.

---

## Modelos

### 1. `naive`

Base obligatoria:

```text
Q1 + Q2 + Q3 + promedio(Q1, Q2, Q3)
```

Generalizado para cualquier cantidad de cuartos conocidos:

```text
total_conocido + promedio_conocido * cuartos_restantes
```

### 2. `current`

Usa solo el partido actual:

- promedio ponderado por recencia;
- pendiente de tendencia sobre cuartos conocidos;
- ajuste amortiguado y limitado;
- incertidumbre basada en variabilidad de cuartos.

### 3. `historical`

Usa histórico de equipos:

- puntos anotados y permitidos;
- media ofensiva/defensiva combinada;
- splits local/visitante si hay muestra suficiente;
- head-to-head opcional con peso pequeño;
- shrinkage hacia promedio de liga si falta histórico;
- aumento de incertidumbre si falta histórico o hay back-to-back.

### 4. `combined`

Mezcla `current` e `historical`.

Peso base por tiempo transcurrido:

```text
Q1 -> 0.25 actual
Q2 -> 0.45 actual
Q3 -> 0.70 actual
Q4 -> 0.90 actual
```

Si falta histórico, se aumenta el peso del partido actual.

---

## Cómo se combina histórico + partido en curso

La proyección combinada hace:

```text
central = w_current * current_central + w_hist * historical_central
```

Donde:

```text
w_current = tiempo_partido + (1 - tiempo_partido) * (1 - confianza_histórica)
```

La confianza histórica depende de:

```text
min(partidos_hist_local, partidos_hist_visitante) / WNBA_MIN_HIST_GAMES
```

Esto evita:

- usar histórico insuficiente como si fuera confiable;
- ignorar el partido actual cuando ya hay mucha evidencia en vivo;
- inventar datos cuando no hay histórico.

---

## Backtesting

### Backtest puro

Para cada partido finalizado, reconstruye el estado al final del cuarto
`SNAPSHOT_QUARTER`, normalmente Q3.

Solo usa datos anteriores al partido para histórico.

Métricas:

- MAE: error absoluto medio.
- RMSE: raíz del error cuadrático medio.
- Sesgo: promedio de `predicción - real`.

Interpretación:

- MAE bajo: error típico más bajo.
- RMSE alto respecto a MAE: errores grandes ocasionales.
- Sesgo positivo: sobreestima.
- Sesgo negativo: subestima.

### Backtest de señales

Solo con odds guardadas.

Métricas:

- hit rate;
- ROI hipotético;
- beneficio hipotético;
- drawdown máximo;
- rendimiento por señal, confianza, modelo y diferencia vs línea.

No debe interpretarse como ganancia futura garantizada.

---

## Tests

```bash
python -m unittest discover -s tests -v
```

---

## Limitaciones conocidas

- Sin posesiones reales, el “pace” es un proxy de puntos por cuarto.
- ESPN no oficial puede no entregar cuartos.
- No hay garantía de histórico WNBA gratuito y estable.
- El backtest de señales solo funciona con líneas guardadas previamente.
- OT no está modelado explícitamente.
- Parámetros por defecto no están optimizados para maximizar ROI.

---

## Mejoras futuras

- Importar CSV/box scores históricos reales.
- Pace real con posesiones.
- Modelo bayesiano jerárquico por equipo.
- Calibración de intervalos.
- Ajuste por descanso con datos fiables.
- Proveedor de odds con histórico real verificable.
