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

## Siguiente bloque — implementación de animación

1. Resolver asset/rig exacto, licencia, jerarquía, mapping y controles con evidencia.
2. Solo con autorización, implementar y revisar root motion, contactos, mirada/manos, props y empalmes en una secuencia controlada.
3. Mantener adaptadores Blender/Remotion, renders y animación fuera de este bloque de planificación.

## Pendiente de implementación

No hay motor de render, adaptador TTS, alineador de subtítulos, descargador de assets, verificador legal automático ni QA audiovisual temporal. Los documentos de benchmark y rúbrica son protocolos, no resultados medidos.
