# Seguridad del pipeline local

## Alcance y evidencia

Revisión local de los validadores de storyboard y pipeline, el manifiesto, el DAG, los comandos CLI, el contrato TTS y el workflow. Las pruebas cubren las etapas narrativa heredada y genérica, el recurso UTF-8, proveedores allowlist, objetos estrictos, fingerprints y el carácter de solo lectura de los cuatro comandos para ambas configuraciones. Se ejecutaron pruebas de regresión en Windows. No se ejecutaron motores audiovisuales, se instalaron dependencias ni se escribió un manifiesto de producción. Las dos pruebas de symlink quedaron omitidas porque Windows no permitió crear enlaces; deben correr en CI Linux.

## Hallazgos reproducibles corregidos

| Severidad | Hallazgo | Reproducción y efecto | Corrección |
| --- | --- | --- | --- |
| Baja | Un manifiesto podía marcar como `completed` una etapa aunque su adaptador estuviera pendiente, una herramienta no estuviera verificada o su adaptador siguiera pendiente. | Preparar un manifiesto con hashes y fingerprint coherentes, pero con esos estados de herramienta/adaptador. `validate-manifest` lo aceptaba como completado. No hay reanudación ni ejecución en v0.5/v0.6, así que el efecto actual se limita a una decisión de validez incorrecta. | La validación rechaza `completed` si el adaptador de etapa no está implementado, la disponibilidad de una herramienta no está declarada `verified`, o el adaptador de herramienta no está implementado. |
| Baja | JSON con anidamiento extremo podía escapar como `RecursionError` en vez de error de validación. | Un archivo con 5.000 arrays anidados provocaba excepción inesperada en las superficies que comparten `parse_json`. | El parser compartido convierte `RecursionError` a `ValueError` controlado; los validadores/CLI existentes lo reportan sin traceback. |
| Baja | La aceptación de JSON demasiado anidado dependía del límite interno del parser de Python. | Una entrada con 5.000 niveles fue rechazada en una versión y aceptada por `json.loads()` en Python 3.13.5, haciendo fallar de forma distinta la suite entre runtimes. | Un escáner previo cuenta arrays/objetos fuera de cadenas y aplica un máximo documentado de 128 niveles; escapado y comillas no cuentan como estructura. |
| Baja | Un sustituto Unicode sin pareja podía pasar la validación y romper la serialización UTF-8 del fingerprint. | `tools[].version_requirement` con JSON `"\ud800"` llegaba a `_canonical_bytes()` y causaba `UnicodeEncodeError` en `validate-manifest`. | El parser compartido rechaza sustitutos sin pareja en claves y valores con un diagnóstico controlado; conserva texto Unicode válido y pares sustitutos emoji. El algoritmo de fingerprint no cambia. |
| Baja | Una cadena `aspect_ratio` de miles de dígitos llegaba a `int()` en la comparación del perfil y provocaba `ValueError` en Python 3.11+. | Un storyboard válido sintácticamente para la expresión regular, pero con componente de 5.000 dígitos, hacía fallar `pipeline validate` con traceback. | El contrato storyboard limita cada componente a 128 dígitos. El pipeline usa ese mismo límite antes de convertir los componentes. |

Las severidades son bajas porque los efectos se limitaban al proceso local de validación o a la integridad declarada del manifiesto; no existe ejecución automática de etapas que consuma estos estados.

## Controles comprobados

- `validate`, `plan`, `dry-run` y `validate-manifest` se ejecutan en una prueba con apertura de escritura, operaciones comunes de creación/borrado/reemplazo, procesos externos y sockets bloqueados. También se comparan rutas y contenido de todos los archivos antes y después.
- Las rutas de entrada/salida usan el helper de confinamiento del proyecto, que rechaza rutas absolutas, traversal, NUL y symlinks que escapan de la raíz. Las salidas se cotejan con inputs y entre sí para evitar sobrescrituras declaradas.
- La etapa TTS genérica acepta solo `kokoro` y `piper` como nombres de proveedor registrados por el contrato. El registro no inspecciona ni ejecuta binarios. Los objetos TTS y WAV rechazan claves desconocidas, incluyendo comandos, argumentos, imports, rutas ejecutables, URLs y código.
- `voiceover-script` exige recurso requerido, ruta confinada, archivo existente, UTF-8 válido y texto no vacío; lectura limitada a 1 MiB. Su hash entra al fingerprint de entradas y al fingerprint de etapa cuando se declara como input.
- Tanto configuración TTS como manifiestos mantienen `pipeline_version: 1` y `manifest_version: 1`. Las configuraciones y manifiestos heredados preservan sus IDs y se validan sin reescritura.
- Los DAGs producen olas ordenadas por ID independientemente del orden de declaración. Las etapas completadas requieren dependencias completas, salidas configuradas exactas, hashes correctos y fingerprints actuales.
- El parser JSON compartido limita anidamiento a 128 niveles antes de invocar `json.loads()` y rechaza sustitutos Unicode aislados antes de que una serialización UTF-8 o fingerprint falle.
- CI mantiene `contents: read`, fija las actions oficiales a SHAs completos y solicita una matriz `ubuntu-24.04`/`ubuntu-26.04`.

## Riesgos y evidencia pendientes

- La suite local en Windows omite las dos pruebas de symlink porque el sistema no permite crearlos. Quedan habilitadas en Linux; la matriz nueva todavía necesita una ejecución remota final para demostrar ambos runners.
- No se fijó un límite de bytes para JSON ni para otros archivos que se leen al calcular SHA-256. El guion TTS sí está limitado a 1 MiB; otros recursos y artefactos siguen sin límite explícito.
- No se probó una carrera que sustituya un symlink entre la resolución de ruta y lectura del guion/hash. Los comandos actuales son de solo lectura; cualquier ejecutor futuro debe volver a validar rutas justo antes de operar y evitar seguir enlaces durante aperturas de escritura.
- `availability: verified` es una declaración del archivo de configuración. `validate-manifest` no inspecciona el binario ni demuestra compatibilidad del runtime; las versiones deben registrarse y verificarse por un adaptador futuro antes de reutilizar resultados.
- Los IDs `kokoro` y `piper` son nombres aceptados por el contrato, no adaptadores TTS ejecutables. No se verificaron instalaciones, modelos, versiones, operación offline ni licencias; no se revisó calidad de audio y no se generaron medios.
- No se comprobó que un runtime real admita todas las tasas, canales y formatos WAV declarables. El adaptador futuro deberá verificar los parámetros efectivos y registrar los valores reales, sin asumir que el proveedor los respeta.

## Próximos controles antes de ejecución

Antes de ejecutar adaptadores, definir límites efectivos de tiempo/salida, verificar versiones y licencias, comprobar rutas con operaciones no-follow y añadir pruebas de fallo parcial e invalidación descendiente. Probar primero una interfaz de backend simulada; integrar un TTS real después de esa prueba. No declarar aislamiento de red ni presupuestos RAM/VRAM aplicados sin mecanismos que los impongan. La validación de un manifiesto no equivale a ejecutar ni a reanudar una tarea.
