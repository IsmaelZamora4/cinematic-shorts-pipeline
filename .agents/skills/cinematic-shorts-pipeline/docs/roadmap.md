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

## Siguiente bloque — animación 3D

1. Especificar compatibilidad de rigs, jerarquías/huesos y clips para recursos exactos.
2. Planear apoyos, peso, desplazamiento raíz, mirada, manos/props, anticipación y transiciones de actuación.
3. Revisar continuidad de actuación en secuencia reproducida; no inferirla de stills. No iniciar adaptadores de Blender/Remotion hasta definir ese contrato.

## Pendiente de implementación

No hay motor de render, adaptador TTS, alineador de subtítulos, descargador de assets, verificador legal automático ni QA audiovisual temporal. Los documentos de benchmark y rúbrica son protocolos, no resultados medidos.
