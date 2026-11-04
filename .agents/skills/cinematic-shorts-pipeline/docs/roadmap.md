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

## Siguiente bloque — adaptadores de producción (futuro)

1. Verificar versiones locales, procedencia de recursos, licencias y requisitos de cada adaptador antes de declarar disponibilidad.
2. Implementar invocación controlada sin shell, límites de tiempo/recursos, registro de versiones y salidas hash verificables.
3. Resolver rig/asset exacto y validar poses, apoyos, root motion, mirada, manos/props y continuidad en una secuencia autorizada.
4. Implementar persistencia y reanudación segura solo después de probar invalidación de entradas, configuración y dependencias.

## Pendiente de implementación

No hay motor de render, adaptador TTS, alineador de subtítulos, descargador de assets, verificador legal automático ni QA audiovisual temporal. Los documentos de benchmark y rúbrica son protocolos, no resultados medidos.
