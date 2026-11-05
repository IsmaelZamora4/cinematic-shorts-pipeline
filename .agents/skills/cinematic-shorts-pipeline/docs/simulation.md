# Simulaciones de planificación

Tres encargos ficticios; solo paquetes de planificación. Sin imágenes, renders, voz, audio ni modelos.

| Caso | Premisa/decisión | Paquete |
| --- | --- | --- |
| Cueva | Cuerda recién cortada y agua ascendente fuerzan elegir una repisa | [plan](../examples/cave-exploration/plan.md), [storyboard](../examples/cave-exploration/storyboard.json) |
| Bosque y tormenta | Árbol caído bloquea ruta; relámpago revela refugio temporal | [plan](../examples/forest-storm/plan.md), [storyboard](../examples/forest-storm/storyboard.json) |
| Estación científica | Baliza reactivada recibe señal desde cuarto sellado | [plan](../examples/abandoned-research-station/plan.md), [storyboard](../examples/abandoned-research-station/storyboard.json) |

Cada paquete incluye encargo/premisa, arco en beats, dirección artística, motor técnico candidato y controles automáticos, visuales y humanos. Valida la estructura JSON; no demuestra calidad de render ni fluidez temporal.

## Publicación TTS simulada

`python scripts/pipeline.py save-tts-simulation examples/tts-contract/pipeline.json` publica un segundo de silencio digital generado por el backend `mock` y un JSON de procedencia bajo `.pipeline/simulations/tts/run-<uuid>/`. La procedencia marca `simulation: true`, identifica `backend: mock`, registra el proveedor solo como solicitado y contiene hashes del guion y del WAV publicado. El texto del guion no se imprime.

Esta operación no escribe en las salidas productivas declaradas por el contrato, no modifica manifiestos y no completa etapas. `simulate-tts` conserva su garantía de solo lectura. La publicación supone un proyecto local confiable cuyos directorios padres no son modificados concurrentemente por actores hostiles. Rechaza symlinks y reparse points visibles antes de usar el destino y crea archivos sin reemplazo; esas comprobaciones no prometen protección TOCTOU completa, aislamiento del sistema de archivos ni publicación transaccional del par WAV/JSON. No ejecuta Kokoro, Piper ni herramientas de audio/video.
