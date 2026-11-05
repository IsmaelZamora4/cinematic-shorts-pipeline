# Hoja de ruta

## v0.2.1 — preparada

- Validación semántica de storyboards de ejemplos y plantilla mediante el validador compartido.
- Solapes de captions rechazados por defecto; excepción intencional requiere motivo.
- Enlaces Markdown inline/de referencia, destinos con espacios y exclusión de código.
- Convención de paquetes nuevos bajo `deliverables/<slug>/`.
- Tres paquetes ficticios, rúbrica cinematográfica y protocolo reproducible de benchmark.
- Skill conservada en `.agents/skills/cinematic-shorts-pipeline`; selector confirmado por el usuario.

**Verificación ejecutada 2026-11-04:** suite completa, 26 pruebas aprobadas; `validate-skill.py` aprobó; los tres storyboards de ejemplo y `templates/storyboard.json` pasaron `validate-storyboard.py`.

## v0.3 — cinematografía 3D

- Referencia operativa para óptica, 9:16, jerarquía, eje, mirada, movimiento, luz narrativa y revisión móvil.
- Contrato `cinematography` opcional en `schema_version: 1`, con coordenadas focales relativas y validación de tipos/rangos.
- Rúbrica y puertas separan intención de plan, still y secuencia reproducida.
- Simulación de cueva ampliada con ficha técnica por plano y decisiones correctivas; sin medios generados.

**Verificación de v0.3 (2026-11-04):** 29/29 pruebas aprobadas; `validate-skill.py` aprobó; los tres storyboards de ejemplo y `templates/storyboard.json` pasaron `validate-storyboard.py`.

## v0.4 — planificación de animación 3D

- Contrato `animation` opcional y compatible con `schema_version: 1`; storyboards v0.3 permanecen válidos.
- Validador comprueba estado/requisitos de rig, beats contiguos por plano, poses, peso/apoyos, root motion, mirada, manos/props y continuidad declarada.
- Guía y rúbrica separan planificación declarada de evidencia de rig, still y secuencia reproducida.
- Ejemplo de cueva documenta beats por plano; sin implementación de personaje ni producción audiovisual.

**Verificación de v0.4 (2026-11-04):** 33/33 pruebas aprobadas; `validate-skill.py` y los tres storyboards de ejemplo más `templates/storyboard.json` pasan sus validadores. La aprobación audiovisual sigue pendiente: no se generó ni reprodujo una secuencia.

## v0.5 — arquitectura y orquestación local

- Contrato independiente `pipeline_version: 1`, plantilla y ejemplo de cueva; rutas relativas confinadas a la raíz del proyecto.
- DAG de seis etapas, detección de dependencias/ciclos, olas deterministas y candidatos a paralelismo; concurrencia objetivo conservadora.
- CLI estándar de Python `validate`, `plan`, `dry-run` y `validate-manifest`; no invoca motores ni escribe artefactos.
- Contrato/validador de manifiesto con estados, intentos, dependencias, fingerprints y hash de salidas; reanudación selectiva queda diseñada, no ejecutada.
- Workflow de CI mínimo para pruebas y validadores; adaptadores audiovisuales siguen pendientes.

**Verificación de v0.5 (2026-11-04):** 61 pruebas ejecutadas; 60 aprobadas y 1 omitida porque el entorno Windows no permitió crear symlinks. `validate-skill.py`, los cuatro storyboards, las dos configuraciones del pipeline y `dry-run` pasaron. `validate-manifest` pasó mediante pruebas unitarias. GitHub Actions terminó correctamente en el commit `862bb4896f539177aefef46c15ab9053106da547` (PR #2); el squash integrado en `main` es `b78e3ea347cde94f68c9b010f276ea4ea0106406`.

## v0.6 — Quality Assurance & Security Hardening (integrada en `main` mediante PR #3)

- Endurecer aceptación de manifiestos completos: rechazar adaptadores pendientes, herramientas no verificadas, dependencias incompletas y artefactos/fingerprints inconsistentes.
- Convertir errores previsibles de JSON profundo y relaciones de aspecto malformadas en diagnósticos controlados; fijar el límite de 128 dígitos por componente de `aspect_ratio`.
- Verificar de forma automatizada que `validate`, `plan`, `dry-run` y `validate-manifest` no escriben ni borran archivos, no lanzan procesos y no abren conexiones.
- Actualizar `actions/checkout` a v7.0.1 y `actions/setup-python` a v7.1.0; ambos manifiestos oficiales declaran Node.js 24 y el workflow los fija por SHA completo.
- Sustituir `ubuntu-latest` por una matriz pequeña `ubuntu-24.04`/`ubuntu-26.04`, mantener `contents: read` y validar también dry-run y la plantilla storyboard.
- Registrar hallazgos, severidad, evidencia y riesgos pendientes en `references/pipeline-security.md`.

**Verificación local (2026-11-04):** 87 pruebas ejecutadas en Python 3.12 y 3.13; 85 aprobadas y 2 omitidas porque este entorno Windows no permite crear symlinks. `validate-skill.py`, los cuatro storyboards, las dos configuraciones del pipeline y ambos `dry-run` pasaron. `validate-manifest` y la garantía de solo lectura de las cuatro operaciones CLI se cubrieron en la suite. Se fija un máximo explícito de 128 niveles JSON y se rechazan sustitutos Unicode sin pareja; tildes, eñes y pares sustitutos emoji válidos se conservan. `git diff --check` pasó. El workflow y los comandos se revisaron localmente; no hay parser YAML ni `actionlint` instalado, así que la sintaxis no recibió validación automatizada local. **Pendiente de evidencia remota:** ambos jobs de GitHub Actions deben terminar correctamente antes de declarar compatibles las imágenes 24.04/26.04; no se hizo push ni se abrió PR en esta fase.

## v0.7 — Benchmarking & Storytelling (integrada en `main` mediante PR #4)

- Convertir criterios de hook, progresión causal, función de plano, giro/cierre y claridad vertical en un protocolo reusable con evidencia por beat/corte, sin métricas de retención inventadas.
- Separar metadatos reportados del piloto Gengar, inferencias y datos pendientes; sus 40.53 s, 1080×1920, 30 FPS y ~−14.4 LUFS no son objetivos universales.
- Definir audio como reloj cuando hay grabación; anotar estimaciones, medidas y revisiones manuales sin afirmar sync palabra-a-palabra sin evidencia.
- Añadir `narrative_timing` opcional a `schema_version: 1` para beats, pausas y cortes enlazados a escenas existentes; storyboards anteriores siguen válidos.
- Incluir un ejemplo original de estación nocturna para demostrar el método; no generar imagen, audio o video.

**Verificación local y CI (2026-11-04):** localmente se ejecutaron 93 pruebas; 91 aprobadas y 2 omitidas en Windows por permisos de symlink. `validate-skill.py`, los cuatro storyboards de `examples/` y la plantilla, las dos configuraciones del pipeline, ambos `dry-run` y `git diff --check` pasaron. La suite conserva los storyboards anteriores y cubre el bloque `narrative_timing` válido/inválido, referencias temporales, cortes de transición y voz que cruza un corte; prueba que las cuatro operaciones CLI siguen siendo de solo lectura. GitHub Actions ejecutó las 93 pruebas sin omisiones ni fallos en Ubuntu 24.04 y 26.04 para el commit `69cb9c8134a8703a48d249fa6823b2a504b1a3d8` (run [41921199387](https://github.com/IsmaelZamora4/cinematic-shorts-pipeline/actions/runs/41921199387)); el squash integrado en `main` es `530188cc37c570939b58e5dfbc64190afb7df0f7`. No se creó ni reprodujo audio/video; no hay evidencia audiovisual de ritmo, sync, continuidad temporal ni claridad real.

## v0.8 — Production Adapters: fase 1, contratos TTS

- El validador acepta exactamente una etapa narrativa: `03-kokoro-narration` (heredada) o `03-tts-narration` (neutral). Conserva los IDs declarados, los contratos `pipeline_version: 1` y `manifest_version: 1`, y la validación de fingerprints/manifiestos anteriores.
- El contrato neutral valida proveedor conocido (`kokoro` o `piper`), voz, idioma, parámetros WAV acotados, recurso de guion explícito y dos salidas futuras: WAV y JSON de procedencia.
- `voiceover-script` debe ser recurso requerido, confinado, UTF-8 válido, no vacío y de hasta 1 MiB. Su hash participa en los fingerprints. No se usa texto de captions.
- Se conserva el ejemplo legado de cueva y la plantilla original. `examples/tts-contract/` demuestra la configuración neutral; declara proveedor no verificado y adaptadores pendientes.
- Sin ejecución TTS productiva, WAV ni sidecar publicados, reintentos o recuperación. Disponibilidad, licencias y versiones no se infieren de la allowlist.

**Estado:** integrado mediante PR #6 y squash `694d4c9de26b840af7794d08273f6d9b218a009c`.

## v0.8 — fase 2A: simulación TTS en memoria

- Comando explícito `simulate-tts --backend mock`, separado de las cuatro órdenes originales de planificación/validación.
- Validar pipeline, storyboard, dependencia TTS directa de `01-validate-project` y guion UTF-8 explícito; limitar lectura a 1 MiB y calcular el hash de los mismos bytes capturados.
- Construir y verificar en memoria un segundo de silencio RIFF/WAVE PCM de 16/24 bits o IEEE float de 32 bits. Límite WAV: 512 KiB. Resumen JSON sin el texto privado del guion.
- Procedencia en memoria con `simulation: true`, backend `mock`, proveedor solicitado, proveedor productivo no ejecutado, parámetros efectivos y hashes. No publicar archivos, llamar servicios, ejecutar herramientas ni completar etapas.
- No imponer límites CPU/RAM/VRAM ni timeout; no afirmar aislamiento de red ni resistencia TOCTOU. El contrato y los fingerprints v1 permanecen intactos.

**Estado:** implementación local en `feature/tts-executor-mock-v0.8-phase2`; pendiente de revisión. Sin TTS real ni artefactos publicados.

## v0.8 — fase 2B: publicación segura (pendiente)

- Diseñar y probar publicación de WAV y sidecar: temporales, validación del destino y promoción final con protección contra traversal, symlinks, colisiones y sustitución de rutas.
- Definir limpieza de resultados parciales y límites efectivos que puedan imponerse en Windows y Ubuntu; analizar explícitamente carreras TOCTOU por plataforma.
- Mantener separado este trabajo de reintentos, recuperación automática y TTS real. Ninguna de estas capacidades queda aprobada o implementada por fase 2A.

## Siguiente bloque — adaptadores productivos (futuro)

1. Verificar versiones locales, procedencia del modelo, licencia y requisitos del proveedor antes de implementar o declarar disponibilidad.
2. Integrar un proveedor TTS real solo tras revisar la publicación segura de fase 2B y autorizar dicha integración.
3. Añadir persistencia, recuperación e invalidación de descendientes en fases separadas; no añadir reintentos implícitos.
4. Resolver rig/asset exacto y validar poses, apoyos, root motion, mirada, manos/props y continuidad en una secuencia autorizada.

## Pendiente de implementación

No hay ejecutor ni proveedor TTS, motor de render, alineador de subtítulos, descargador de assets, verificador legal automático ni QA audiovisual temporal. Los documentos de benchmark y rúbrica son protocolos, no resultados medidos.
