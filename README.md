# cinematic-shorts-pipeline

Proyecto local de código abierto para la skill Codex `cinematic-shorts-pipeline`. Paquete descubrible: [.agents/skills/cinematic-shorts-pipeline/SKILL.md](.agents/skills/cinematic-shorts-pipeline/SKILL.md). No contiene ni produce medios audiovisuales.

## Abrir y activar en Codex

Abre `C:\VideosIA\cinematic-shorts-pipeline` como raíz del proyecto. Codex busca skills de proyecto bajo `.agents/skills/`. Si el selector no muestra la skill después de abrir la raíz, reinicia/recarga Codex y comprueba el selector antes de invocarla con `$cinematic-shorts-pipeline`.

## Validar

Python estándar; sin dependencias nuevas.

```powershell
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-skill.py .agents/skills/cinematic-shorts-pipeline
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-storyboard.py .agents/skills/cinematic-shorts-pipeline/examples/cave-exploration/storyboard.json
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-storyboard.py .agents/skills/cinematic-shorts-pipeline/examples/forest-storm/storyboard.json
python -B .agents/skills/cinematic-shorts-pipeline/scripts/validate-storyboard.py .agents/skills/cinematic-shorts-pipeline/examples/abandoned-research-station/storyboard.json
python -B -m unittest discover -s .agents/skills/cinematic-shorts-pipeline/tests -v
```

`validate-skill.py` comprueba estructura y cronología de los tres storyboards de `examples/` y `templates/storyboard.json`, además de sintaxis JSON y enlaces Markdown. Los captions no pueden solaparse salvo excepción intencional documentada con `overlap_exception`.

Storyboards `schema_version: 1` pueden añadir por plano un bloque opcional `cinematography` con foco, tamaño/ángulo, posición focal relativa, continuidad, cámara, fuente narrativa, información visible y corte. Sus coordenadas son proporciones 2D del cuadro, no posiciones 3D. La validación estructural no aprueba calidad cinematográfica.

Guarda paquetes nuevos en `deliverables/<slug>/` (brief/plan y `storyboard.json`). No muevas ni sobrescribas entregables existentes al añadir otro paquete.

El benchmark es un protocolo, no mediciones. Los ejemplos son paquetes ficticios de planificación. Ningún video, imagen, audio, voz, render ni modelo 3D forma parte del proyecto.

## Licencia

Proyecto y documentación original bajo MIT: [LICENSE](LICENSE). La skill conserva una copia de la licencia en `.agents/skills/cinematic-shorts-pipeline/LICENSE` para distribución independiente.
