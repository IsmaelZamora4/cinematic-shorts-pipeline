# Auditoría de fuentes

Auditoría de solo lectura realizada el 2026-11-04. No se copió contenido de repositorios de terceros. Los enlaces son fuentes de investigación y documentación técnica, no recomendaciones comerciales. Revisa páginas mutables y licencias exactas antes de distribuir o usar recursos.

## Proyecto local y skills instaladas

- Se leyeron sin modificar `C:\VideosIA\videoia\AGENTS.md`, `ART_DIRECTION.md`, `README.md`, `remotion\AGENTS.md`, `remotion\README.md`, `remotion\package.json` y `blender\README.md`. Definen límites de autorización, versiones, dirección low-poly, animación por fotograma y restricciones de vista previa. Los informes del proyecto mencionan pruebas/renders locales exitosos; esta auditoría de la skill no los repitió.
- Se revisaron las entradas de las skills locales `remotion-best-practices`, `remotion-markup`, `remotion-captions`, `remotion-studio` y `remotion-render`. Sus recetas de implementación se reutilizan por referencia cuando se elige Remotion.
- El proyecto indica que los archivos KayKit incluidos conservan los textos de licencia originales y los identifica como CC0. Es documentación del proyecto, no una revisión legal nueva.

## Repositorios candidatos

- [mromidev/claude-animated-shorts](https://github.com/mromidev/claude-animated-shorts): se revisaron metadatos y README. El README describe cortos de 55–70 segundos, Three.js en Remotion, herramientas de QA por fotograma y estilo de personajes 2D en mundo 3D. La API de GitHub informó MIT y un archivo de licencia. Su estilo colorido y delineado, más su colección de fotogramas de referencia, difieren de la dirección de este proyecto; no se copió código ni arte. MIT cubre el material del repositorio bajo sus términos, no necesariamente cada imagen de referencia o recurso de terceros. Revisa procedencia por archivo antes de reutilizar.
- [debpalash/VoiceStudio](https://github.com/debpalash/VoiceStudio): se revisaron metadatos, README y endpoint de licencia. El README describe una aplicación de voz local; la API de GitHub informó AGPL-3.0. No se copiaron código ni modelos. Esto no prueba compatibilidad con Windows/GPU disponibles, calidad de voz en español, licencia de modelos ni integración práctica. Revisa documentación actual, requisitos de ejecución y licencias antes de adoptarlo.

## Fuentes oficiales de Remotion

- [Integración Three](https://www.remotion.dev/docs/three)
- [ThreeCanvas](https://www.remotion.dev/docs/three-canvas)
- [Tipo Caption](https://www.remotion.dev/docs/captions/caption)
- [CLI still](https://www.remotion.dev/docs/cli/still)

Las páginas respondieron HTTP 200 durante la investigación. Las skills locales y el manifiesto del proyecto aportan contexto de versión. No se creó una composición de producción ni se verificó cada detalle de API; al implementar, consulta la documentación de la versión activa.

## Nota de licencia

Este proyecto aplica MIT a documentación original y código validador propios. No relicencia repositorios citados, modelos, fuentes, música, imágenes, voces ni conjuntos de datos. VoiceStudio figura con AGPL-3.0 en los metadatos de GitHub; busca revisión adecuada antes de incorporar o distribuir código modificado. Comprueba permisos por recurso y paquete exactos.
