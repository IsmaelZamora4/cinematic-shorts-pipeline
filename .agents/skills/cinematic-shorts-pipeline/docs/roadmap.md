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

**Verificación de v0.5 (2026-11-04):** 55 pruebas ejecutadas; 54 aprobadas y 1 omitida porque el entorno Windows no permitió crear symlinks. `validate-skill.py`, los cuatro storyboards, las dos configuraciones del pipeline y `dry-run` pasaron. `validate-manifest` pasó mediante pruebas unitarias. El workflow de GitHub Actions está configurado, pero no se ejecutó en GitHub durante esta preparación.

## Siguiente bloque — adaptadores de producción (futuro)

1. Verificar versiones locales, procedencia de recursos, licencias y requisitos de cada adaptador antes de declarar disponibilidad.
2. Implementar invocación controlada sin shell, límites de tiempo/recursos, registro de versiones y salidas hash verificables.
3. Resolver rig/asset exacto y validar poses, apoyos, root motion, mirada, manos/props y continuidad en una secuencia autorizada.
4. Implementar persistencia y reanudación segura solo después de probar invalidación de entradas, configuración y dependencias.

## Pendiente de implementación

No hay motor de render, adaptador TTS, alineador de subtítulos, descargador de assets, verificador legal automático ni QA audiovisual temporal. Los documentos de benchmark y rúbrica son protocolos, no resultados medidos.
