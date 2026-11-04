# Descubrimiento local de skills en Codex

## Documentación consultada

La [documentación oficial de Codex sobre skills](https://developers.openai.com/codex/skills/) indica que Codex descubre skills de repositorio en `.agents/skills/` desde el directorio de trabajo hasta la raíz del repositorio. Codex sigue enlaces simbólicos situados en rutas descubiertas; los cambios pueden requerir reiniciar Codex.

## Ubicación organizada

- Raíz exterior del proyecto: `C:\VideosIA\cinematic-shorts-pipeline`.
- Skill: `C:\VideosIA\cinematic-shorts-pipeline\.agents\skills\cinematic-shorts-pipeline\SKILL.md`.
- La carpeta `.agents/skills/cinematic-shorts-pipeline` contiene instrucciones, referencias, scripts, plantillas, ejemplos, pruebas, documentación y copia MIT.
- README y LICENSE de proyecto permanecen en la raíz; la skill incluye su propia copia de LICENSE para distribución independiente.
- El directorio activo de la auditoría (2026-11-04) estaba fuera del proyecto. Los comandos se ejecutaron usando la raíz exterior como directorio de trabajo.

**Resultado de ruta:** la estructura está en la ubicación de descubrimiento documentada si Codex abre la raíz exterior. El usuario confirmó que la skill apareció en el selector y se activó desde esta carpeta de proyecto.

## Uso

Abre `C:\VideosIA\cinematic-shorts-pipeline` como raíz del proyecto. Invoca con `$cinematic-shorts-pipeline` desde el selector confirmado. No se modificó configuración global.
