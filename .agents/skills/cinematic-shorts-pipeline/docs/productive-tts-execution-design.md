# Phase 4A — Diseño de ejecución productiva de TTS

**Estado:** Phase 4A aprobada; Phase 4B implementada, suite local aprobada; CI multiplataforma pendiente
**Fecha:** 2026-11-05
**Base revisada:** commit 0694b0e083af1bc9fa53d82d57df8b373d5bc0a6
**Rama de diseño:** design/productive-tts-execution-v0.10
**PR #11:** estado remoto no verificado desde este checkout

## Decisiones aprobadas antes de Phase 4B

- Stage 01 declara la pseudentrada reservada pipeline-config; su valor es fingerprint(config) sobre JSON parseado y canonicalizado. El algoritmo stage_fingerprint no cambia.
- TTS productivo declara como inputs tanto resource-voiceover-script como validated-plan. Así, cambios del plan validado alteran la huella de TTS mediante la dependencia ya existente.
- Provenance usa estructura JSON estable para runtime Python, worker, dependencias y assets. Python/dependencias salen del reporte observado por el worker; worker se identifica por SHA-256 del script ejecutado; modelo y voces se identifican por SHA-256 de bytes. No inferir versiones de nombres de archivo.
- availability=verified habilita elegibilidad declarativa; worker y padre comprueban runtime, proveedor, modelos, voz, formatos y hashes reales antes de completar etapas.
- La recuperación comprobada en Phase 4B es consistencia tras interrupción y rechazo de artefactos huérfanos. No incluye resume, reintentos ni adopción automática.

## 1. Estado actual y brechas

La skill ya valida contratos pipeline v1 y manifiestos v1, calcula fingerprints, planifica un DAG determinista, simula TTS y publica simulaciones. El comando run-tts-kokoro también puede ejecutar una inferencia Kokoro local real mediante un worker fijo.

Ese comando es un piloto independiente. Es deliberadamente estricto: requiere la etapa neutral 03-tts-narration con adapter_status pendiente, publica en .pipeline/tts-local-runs/run-<id>/ y no toca outputs configurados ni manifiestos. Esta conducta se mantiene sin cambios.

El ejecutor productivo no existe. new_pending_manifest solo devuelve un objeto en memoria; validate-manifest valida un archivo existente. La etapa 01 ya declara builtin-validation implementado, pero no materializa validated-plan. El validador de manifiestos acepta completed solo si las dependencias, adaptador, herramientas declaradas, fingerprints y todos los outputs coinciden. La validación de herramienta es estructural: availability=verified por sí sola no inspecciona un runtime.

Brechas que Phase 4B/4C resolvería:

- Contrato de elegibilidad productiva para Kokoro, manteniendo Piper declarativo y los contratos heredados intactos.
- Un artefacto validated-plan determinista que represente contenido validado y tenga entradas cubiertas por el fingerprint de Stage 01.
- Creación y actualización persistente de un manifiesto v1 con transiciones coherentes.
- Ejecución limitada a 01-validate-project y 03-tts-narration.
- Publicación verificada en las dos rutas configuradas, no en el directorio standalone.
- Manejo explícito de colisiones, artefactos parciales, cambios concurrentes de inputs y escrituras interrumpidas.

## 2. Arquitectura propuesta

Añadir un comando dedicado run-productive-tts CONFIG --python ABSOLUTE_PYTHON --model ABSOLUTE_MODEL --voices ABSOLUTE_VOICES. El comando ejecutará solo el subconjunto autorizado 01-validate-project y 03-tts-narration. No será un runner genérico del DAG ni inspeccionará o invocará otros adaptadores. En éxito, 02-blender-scenes, 04-remotion-compose, 05-ffmpeg-finalize y 06-quality-control permanecerán pending.

El nuevo comando reutiliza el worker scripts/tts-kokoro-worker.py y el contrato validado de TTS. No lee ejecutables, argumentos, imports, código, descargas ni rutas de modelo desde pipeline.json. Python, modelo y voces continúan como rutas absolutas explícitas de CLI. El script UTF-8 se captura una vez, pasa por stdin al worker y no aparece en argv, logs ni provenance.

run-tts-kokoro sigue como operación standalone con su función y publicaciones actuales. La nueva ruta productiva debe usar una función distinta de orquestación/publicación; se pueden extraer helpers internos comunes si su comportamiento observable standalone queda igual y sus pruebas existentes permanecen válidas.

El runner valida el proyecto completo antes de crear el estado de ejecución; comprueba los outputs configurados y el destino de manifiesto para evitar colisiones; escribe un manifiesto pending; ejecuta y confirma Stage 01; persiste ese checkpoint; ejecuta Kokoro; verifica ambos outputs; actualiza el manifiesto; y llama a la misma validación semántica usada por validate-manifest. Si Stage 01 falla, no inicia TTS. El comando nunca ejecuta otras etapas aunque aparezcan como implemented en la configuración.

### Diagrama de secuencia

```mermaid
sequenceDiagram
    participant CLI as run-productive-tts
    participant Runner as pipeline.py
    participant FS as Proyecto y workspace local
    participant Worker as tts-kokoro-worker.py
    participant Kokoro as Runtime Kokoro local

    CLI->>Runner: config + rutas absolutas python/model/voices
    Runner->>FS: leer/capturar config, storyboard, recursos y hashes
    Runner->>Runner: validar DAG, elegibilidad Kokoro y recursos
    Runner->>FS: comprobar destinos y crear run-id exclusivo
    Runner->>FS: persistir manifiesto v1 con etapas pending
    Runner->>Runner: crear validated-plan determinista
    Runner->>FS: publicar/verificar validated-plan
    Runner->>FS: persistir Stage 01 completed
    Runner->>Worker: proceso fijo; guion UTF-8 por stdin
    Worker->>Kokoro: inicializar modelo, comprobar voz/proveedor y sintetizar
    Kokoro-->>Worker: muestras mono a 24 kHz
    Worker-->>Runner: WAV por stdout; versiones/mediciones por stderr
    Runner->>Runner: verificar WAV, runtime, hashes e inputs sin cambios
    Runner->>FS: publicar y releer narration.wav + provenance JSON
    Runner->>FS: persistir Stage 03 completed mediante reemplazo atómico del manifiesto
    Runner->>Runner: validar manifiesto final
    Runner-->>CLI: resumen sin guion + ruta del manifiesto
```

El manifiesto es la autoridad sobre el estado completed. El sidecar provenance describe la ejecución y los bytes; no declara por sí solo que la etapa está completada.

## 3. Contrato de elegibilidad y compatibilidad de estados

### Configuración productiva neutral TTS

Permitir 03-tts-narration con adapter_status=implemented solo cuando se cumpla toda la combinación siguiente:

- tts.provider es kokoro; Piper continúa admitiendo solamente adapter_status=pending.
- adapter es tts-narration y tools contiene exactamente kokoro.
- tts.voice=em_alex, tts.language=es y el audio solicitado es WAV mono PCM16 little-endian a 24 kHz, que es el contrato soportado por el worker actual.
- script_input es resource-voiceover-script; el recurso existe, es required, tiene kind voiceover-script, está confinado al proyecto y pasa los límites UTF-8 vigentes.
- 03-tts-narration depende directamente de 01-validate-project y declara exactamente narration (.wav) y narration-provenance (.json).
- Para adapter_status=implemented, inputs de TTS incluyen resource-voiceover-script y validated-plan. Contratos neutrales pending anteriores conservan su lista de inputs actual.
- La entrada tools de kokoro declara adapter_status=implemented, availability=verified y un version_requirement que corresponda a Kokoro ONNX 0.6.1, CPUExecutionProvider y el rango Python que admite el worker.
- Las otras etapas mantienen sus adaptadores pendientes en el perfil de ejecución Phase 4.

Estas condiciones hacen que el JSON sea apto para solicitar la ruta productiva; no convierten sus declaraciones en evidencia del runtime. validate/plan/dry-run siguen siendo inspecciones declarativas. El runner debe verificar los archivos locales, invocar el worker fijo y comprobar el runtime realmente cargado. Solo la evidencia devuelta por el worker puede satisfacer una ejecución real. La herramienta configurada nunca se ejecuta como comando.

Para una ejecución completada, exigir evidencia runtime independiente del JSON:

- Python dentro del rango que acepta el worker y el ejecutable suministrado es un archivo local absoluto comprobado.
- Modelo y archivo de voces son archivos locales absolutos comprobados y sus SHA-256 no cambian durante la inferencia.
- El worker confirma kokoro-onnx 0.6.1, firmas compatibles, voz em_alex, idioma es y proveedor efectivo exactamente CPUExecutionProvider.
- El WAV devuelto cumple el contrato efectivo, no está vacío ni silencioso, no tiene muestras no finitas ni clipping digital severo y satisface los límites actuales de tamaño/duración.
- El manifiesto registra versiones reales. validate-manifest comprueba consistencia estructural; no reejecuta ni vuelve a verificar runtime o procedencia.

### Compatibilidad v1 heredada

No alterar 03-kokoro-narration, sus inputs/outputs, permisos ni reglas de validación. No cambiar los pipelines heredados de templates/pipeline.json o examples/cave-exploration/pipeline.json. Los contratos neutrales Piper/kokoro con adapter_status=pending siguen siendo declarativos. Los manifiestos v1 ya existentes conservan IDs, hashes, campos y semántica.

La configuración neutral nueva puede usar adapter_status=implemented solo con las condiciones anteriores. Los manifests generados por la nueva ruta conservan manifest_version=1 y el esquema actual; no se añaden campos de estado, progreso, errores de escritura ni resume. Un run_id y la fecha se generan en la ejecución. Cualquier validación estática que acepte la configuración productiva no simula una ejecución ni garantiza que exista el runtime.

### Stage 01 implementado

La etapa 01 ya tiene adapter_status=implemented. Phase 4 debe materializar el output declarado y completar ese estado solo tras publicar y releer un validated-plan válido. No debe crear un archivo vacío para satisfacer el validador.

Stage 01 valida configuración completa, storyboard y recursos requeridos, y construye un recibo determinista. Stage 01 productivo declara inputs pipeline-config, storyboard y cada recurso requerido que representa. pipeline-config es una pseudentrada reservada, conocida solo para 01; _project_input_hashes devuelve fingerprint(config) sobre el objeto parseado/canonicalizado. Rechazar su uso en cualquier otra etapa. No usar bytes JSON crudos: cambios de espacios, orden de claves o encoding no alteran huella semántica. El validador ya rechaza rutas absolutas y separadores no portables; el artefacto contiene rutas relativas POSIX, sin timestamps ni datos de host.

Con esto stage_fingerprint sigue usando el algoritmo actual. Para Stage 01, inputs incluyen pipeline-config, storyboard y recursos requeridos. Para TTS productivo, inputs incluyen resource-voiceover-script y validated-plan; validate_manifest ya obtiene el hash del artefacto antecesor y lo añade al fingerprint. Cambios del plan invalidan TTS. Configuraciones y manifiestos anteriores no declaran pipeline-config ni validated-plan en TTS; conservan sus fingerprints.

### Contenido de validated-plan.json

Recomiendo JSON UTF-8 determinista, con claves ordenadas, indentación estable y newline final. Sin fecha, run_id, rutas absolutas, contenido del guion ni valores dependientes del host. Estructura mínima:

- validated_plan_version: 1.
- pipeline_version y project.id.
- config_fingerprint: fingerprint semántico canonicalizado de pipeline.json.
- profile completo y workspace con rutas relativas declaradas.
- validation: identificadores/versiones de validadores y resultados PASS para config, storyboard, DAG y recursos obligatorios.
- inputs ordenados por ID: id, ruta relativa POSIX, tamaño de bytes y SHA-256 capturado. No incluir texto de recursos.
- execution_waves y etapas ordenadas topológicamente: ID, adaptador, dependencias, inputs, outputs, tools y estado del adaptador.
- stage_fingerprint de 01 calculado con el algoritmo actual sobre config/profile/stage/input hashes/tool_versions vacías.

Validación: parsear el JSON con el parser estricto existente; rechazar claves/campos extra o faltantes; volver a verificar tipos, orden normalizado, IDs y hashes; recalcular config_fingerprint, stage_fingerprint y hashes de inputs; confirmar equivalencia con config, storyboard y waves actuales. Verificar archivo con límite razonable de tamaño, tipo regular, ruta confinada, ausencia de symlink/reparse point y hash SHA-256 después de publicar. El manifest registra la ruta configurada exacta y hash de los bytes reread.

El recibo nunca incorpora texto narrativo. Las reglas de storyboard existentes siguen siendo fuente de validación; no se duplican aquí validadores de calidad visual, rig, cámara o ejecución.

## 4. Secuencia detallada de ejecución

1. Resolver config y project root. Cargar el JSON con el parser estricto, ejecutar load_and_validate_config y obtener config, errores, warnings y waves. Capturar los bytes/hashes de storyboard y recursos usados y retener los bytes UTF-8 validados del guion.
2. Validar que el perfil corresponde exactamente a Stage 01 + 03-tts-narration productivo y que ninguna regla legacy/productiva conflictúa. Rechazar Piper, la etapa legacy 03-kokoro-narration, salidas distintas del contrato y adaptadores adicionales requeridos como ejecutables.
3. Comprobar rutas absolutas y tipo/tamaño de Python, modelo y voces usando los límites locales existentes. Verificar outputs de ambas etapas y destino de manifiesto. Crear directorios por componentes después de comprobar que ninguno es symlink, junction o reparse point. Rechazar destinos existentes antes de iniciar producción.
4. Crear run_id UUID y un directorio exclusivo .pipeline/runs/run-<id>/. Escribir el manifiesto inicial completo con todas las etapas pending, attempts=0 y colecciones vacías. Persistirlo mediante archivo temporal sibling y os.replace dentro del directorio exclusivo.
5. Ejecutar Stage 01 en memoria: validar entradas, calcular hashes/fingerprints, producir validated-plan determinista. Abrir el output configurado con creación exclusiva; reread, parsear y verificar equivalencia, tamaño y hash. Actualizar manifest.stages["01-validate-project"] a completed, attempts=1, error=null, dependencias exactas, artifact validated-plan con ruta/hash, stage_fingerprint y tool_versions vacías. Persistir manifiesto atómicamente antes de empezar Kokoro.
6. Comprobar de nuevo el guion y su hash inmediatamente antes del worker. Pasar exactamente los bytes capturados y validados por stdin. Lanzar solo el worker fijo con shell=False, CPUExecutionProvider, variables acotadas según el contrato actual, timeout 300 s y sin reintentos.
7. El parent verifica exit code, registro worker, versiones, provider, voice/language, WAV y hash. Captura SHA-256 del worker fijo antes de lanzar y comprueba que no cambió; captura modelo/voces antes y después. Comprueba también config, storyboard y recursos requeridos antes de declarar TTS completed.
8. Crear provenance productiva estable. Forma recomendada: runtime.python_version; runtime.worker_sha256; runtime.dependencies con claves fijas kokoro-onnx, onnxruntime, numpy, phonemizer, espeakng-loader y soundfile; runtime.onnx_execution_providers. assets.model y assets.voices contienen file_name, size_bytes, sha256 y version_identity="sha256:<digest>". La identidad SHA-256 es la versión efectiva de bytes; no inferir etiqueta semántica que los archivos no proporcionan. Añadir run_id, project_id, dag_stage_id, provider, voice/language, audio solicitado/efectivo, hashes de script/WAV/validated-plan, timing y límites. Sin texto de guion ni rutas absolutas. No declarar completed antes del commit del manifiesto.

Forma exacta: runtime es objeto con python_version:string, worker_sha256:SHA-256 lowercase, dependencies:map<string,string> de los seis distributions devueltos por worker y onnx_execution_providers:array<string>. assets.model y assets.voices son objetos con file_name:string, size_bytes:integer, sha256:SHA-256 lowercase y version_identity:string igual a sha256:<sha256>. Las claves son obligatorias, extra keys rechazadas.
9. Publicar exactamente las rutas outputs narration y narration-provenance configuradas. Rechazar overwrite. Usar creación exclusiva; verificar tipo/identidad; reread ambos; volver a validar WAV/JSON, correspondencia de hashes, parámetros y demás campos. Solo después construir artifacts con IDs/rutas configurados/hashes.
10. Calcular tool_versions.kokoro como string compacto, canónico y ordenado por clave. Incluye Python/dependencias observadas, SHA-256 del worker, del modelo y voces, y provider efectivo. El sidecar preserva esos datos en JSON estructurado. Calcular stage_fingerprint con hashes de script y validated-plan más ese valor.

Codificación exacta: compact JSON UTF-8 de un objeto con runtime.python_version, runtime.worker_sha256, runtime.dependencies, runtime.onnx_execution_providers, assets.model.sha256 y assets.voices.sha256; serialización con sort_keys=true y separators=(',',':'). La misma cadena se calcula antes del manifest update y persiste establemente en tool_versions.kokoro.
11. Actualizar TTS a completed y persistir el manifiesto mediante temp sibling + flush/fsync disponible + replace atómico. Confirmar el JSON reread con validate_manifest_file/validate_manifest. Publicar éxito al usuario solo tras PASS final.
12. Reportar resumen que no incluya el guion: run_id, estados de 01/03 y restantes, hashes, audio efectivo, versiones, rutas de artifacts y manifiesto, y validación final.

## 5. Transiciones de estado del manifiesto

Todas las etapas empiezan pending, attempts=0, error=null, artifacts=[], stage_fingerprint=null y tool_versions={}. El manifiesto inicial se escribe antes de cualquier output de etapa. Las etapas no ejecutadas nunca se completan.

| Situación | 01-validate-project | 03-tts-narration | Otras etapas |
| --- | --- | --- | --- |
| Éxito total | completed, attempts=1, validated-plan verificado | completed, attempts=1, WAV+JSON verificados, tool_versions efectivas | pending |
| Error validando config/input antes de crear run | sin manifiesto de ejecución | sin manifiesto de ejecución | pending |
| Error Stage 01 durante la ejecución | failed, attempts=1, error breve, artifacts vacíos | blocked, attempts=0, dependencia fallida, sin artifacts | pending |
| Runtime local ausente/incompatible detectado por worker | completed | failed, attempts=1, sin outputs reutilizables | pending |
| Timeout/error worker | completed | failed, attempts=1, error breve, artifacts vacíos | pending |
| WAV o provenance inválidos | completed | failed, attempts=1, artifacts vacíos | pending |
| Colisión de outputs antes de ejecutar | sin nueva ejecución | sin nueva ejecución | pending |
| Error publicando el segundo output | completed | failed, attempts=1, artifacts vacíos; limpiar solo archivos propios verificables | pending |
| Input cambia tras Stage 01 o durante TTS | invalidar como failed y quitar artifacts del estado; error de input cambiado | failed si TTS ya empezó; de lo contrario blocked | pending |
| Terminación abrupta | queda el último estado persistido (pending o checkpoint de 01) | no afirmar completed sin persistencia posterior a verificación | pending |

La tabla describe el manifiesto final o el último checkpoint duradero; no existe estado running en manifest v1. El runner debe permanecer serial. attempts=1 cuenta una única invocación del adaptador; no hay retry interno ni automático. Una nueva invocación del comando crea un run_id nuevo, pero no reanuda el anterior y fallará con colisión si las rutas productivas ya contienen outputs.

Las pruebas de recuperación cubren terminación abrupta: conservar el último manifiesto completo, no adoptar outputs huérfanos, rechazar colisiones al volver a ejecutar y permitir una ejecución nueva con rutas configuradas libres. No implementan resume ni reconstrucción de estado.

Los estados blocked requieren reason no vacío y ningún artifact/tool_versions. Los estados failed requieren attempts>=1 y error no vacío. Conservar solo los campos que manifest v1 ya acepta. stage_fingerprint puede ser null si no se reunieron suficientes versiones/inputs para calcularlo tras un fallo; completed siempre exige huella verificable.

## 6. Modelo de artefactos y manifiesto

### Outputs

- Stage 01: exactamente el output configurado validated-plan bajo intermediate_dir o artifact_dir.
- Stage 03: exactamente los dos outputs configurados narration y narration-provenance. WAV y JSON deben seguir siendo los dos nombres/rutas declarados por el proyecto.
- No publicar en .pipeline/tts-local-runs ni .pipeline/simulations/tts desde el comando productivo.
- El manifiesto reside en workspace.manifest_dir/run-<uuid>/manifest.json. Todas las rutas de outputs se almacenan como paths POSIX relativos a project root tal como declara la configuración.
- Artefacto WAV: validar RIFF/WAVE, PCM16, mono, 24000 Hz, frames/duración dentro de límites, señal finita y no silenciosa, clipping y SHA-256.
- Sidecar: JSON estricto con provenance_version=1. Debe coincidir con el resultado worker, config TTS y hash del WAV reread; su propio SHA-256 se guarda en manifest, no dentro de sí mismo.
- Validated-plan: JSON estricto, determinista y recalculable con el config/input snapshot. Su SHA-256 se guarda en manifest.

### Manifest v1

Usar exactamente los campos actuales:

- Nivel raíz: manifest_version, run_id, project_id, created_at, config_fingerprint, inputs_fingerprint, stages.
- Cada etapa: status, attempts, error, depends_on, artifacts, stage_fingerprint, tool_versions.
- Completada 01: artifacts=[{id:"validated-plan", path:<ruta configurada>, sha256:<hash reread>}], stage_fingerprint recalculada, tool_versions={}.
- Completada 03: artifacts contiene narration y narration-provenance una vez cada uno con rutas configuradas y hashes post-write. tool_versions contiene únicamente kokoro como JSON compacto canonicalizado con claves ordenadas; incluye versiones observadas, worker_sha256, model_sha256, voices_sha256 y provider. Provenance conserva estructura JSON equivalente.
- Bloqueada/fallida: artifacts vacíos; no se registran parciales como resultados reutilizables.
- 02/04/05/06 quedan pending incluso si alguna dependencia ya terminó. validate_manifest no exige que una etapa pending tenga dependencias pendientes.
- Validación final requiere todas las etapas exactly matching IDs configurados, Stage 03 depende de Stage 01, completadas tienen outputs exactos, archivos presentes/confinados/hash correcto, fingerprints actuales y tool versions exactas.

La creación del directorio run exclusivo y los reemplazos atómicos del único archivo manifest reducen el riesgo de truncar JSON. Un temp-file sibling se escribe completamente, se flush/fsync cuando la plataforma lo permite y se renombra con os.replace dentro del nuevo directorio. El directorio por run evita reemplazar un manifest anterior. Esto garantiza visibilidad atómica de una versión de manifiesto en sistemas de archivos locales que implementen rename en el mismo volumen; no garantiza durabilidad ante corte eléctrico ni transacción entre outputs y manifest.

## 7. Fingerprints y compatibilidad

La función actual stage_fingerprint canonicaliza SHA-256 de un objeto con pipeline_version, profile, el objeto completo de stage, input hashes, las definiciones completas de sus tools y tool_versions efectivos. La propuesta conserva el algoritmo y añade pipeline-config como input pseudo solo para el Stage 01 productivo.

| Cambio | Consecuencia con el algoritmo existente | Tratamiento |
| --- | --- | --- |
| 03 adapter_status pending -> implemented | El objeto stage cambia y cambia su fingerprint | Manifiestos anteriores nunca se reescriben; nuevo manifest usa fingerprint de config productiva |
| Tool availability not_checked -> verified | El objeto tool entero entra en stage_fingerprint y cambia la huella de TTS | Nuevo manifest únicamente; runtime se verifica aparte |
| Tool adapter_status pending -> implemented | El objeto tool cambia y cambia la huella | Exigir en config productiva, registrar runtime verificado durante la ejecución |
| Registro/tool version_requirement/executable cambia | La definición completa de tool cambia la huella, incluso si executable es descriptivo | Conservarlo; aceptar invalidación conservadora; executable nunca se invoca |
| Versiones reales efectivas cambian | tool_versions entra en huella de etapa | Guardar versión observada exacta; cambia fingerprint |
| Inputs cambian | Input hashes alteran fingerprint y el fingerprint global de inputs | No reusar outputs; ejecución fresca y rutas libres |
| Outputs/rutas cambian | Outputs forman parte del objeto stage; huella y rutas de artifacts deben cambiar | No copiar o trasladar artifacts/huellas de una config distinta |
| Parámetros profile cambian | El objeto profile entra en cada stage fingerprint | Nueva ejecución, no reutilización |
| Cambia cualquier otro stage | pipeline-config cambia; Stage 01 cambia; validated-plan hash cambia; TTS lo incluye como input | Invalida TTS conservadoramente; no hay reuso en Phase 4B |
| Bytes de output cambian | No están en stage_fingerprint; su hash aparece en artifacts | validate-manifest verifica archivo/hash y estado; registrar todos los bytes post-write |

El config_fingerprint del manifiesto advierte cuando cambia config. inputs_fingerprint cubre storyboard y recursos con el algoritmo actual. El pseudoinput pipeline-config para Stage 01 liga su validated-plan al pipeline semántico sin alterar fingerprints heredados porque las configuraciones previas no declaraban ese input.

No mutar manifests viejos para alinearlos con el nuevo adapter_status ni considerar una huella de provider neutral igual a una huella productiva Kokoro. En esta fase solo se crean ejecuciones frescas. Resume, invalidación selectiva y migración de manifests quedan fuera; una fase posterior debe especificar esas reglas.

## 8. Seguridad, fallos y límites

### Runtime y guion

- Rechazar rutas no absolutas, inexistentes, no regulares, symlink/reparse, tamaños fuera de los límites y archivos cuya identidad/hash cambie entre comprobaciones.
- Invocar solo el worker fijo, shell=False, argumentos construidos por código y subprocess timeout 300 s. No reintentar.
- Script UTF-8 por stdin con máximo actual de 8 KiB para piloto. Nunca argv, stderr, stdout JSON summary ni provenance.
- No usar ni instalar dependencias, no descargar assets, no abrir red, no ejecutar npx/Blender/Remotion/FFmpeg. La garantía es sobre el comportamiento conocido del worker, no sandbox de red.
- El worker confirma distribución Kokoro, firma de API, voz, provider, WAV y versiones; el padre inspecciona y hashifica bytes antes de confiar.
- No se establecen cuotas de memoria/CPU/VRAM. El límite WAV se evalúa tras generar muestras, igual que el standalone actual.

### Fallos definidos

- Timeout: marcar TTS failed con attempts=1, no publicar outputs; persistir el estado con error corto y sin script o valores sensibles.
- Runtime ausente, incompatible, voice missing o provider distinto: fallo de TTS; ningún manifest completed.
- WAV inválido, corrupto, silencioso, demasiado grande, fuera de duración/formato o hash distinto al registro worker: descartar bytes en memoria y fallar antes de publicar.
- Provenance que no concuerda con bytes/config: no publicar completed; fallo y cleanup de archivos que el proceso pueda probar que creó.
- Output existente: preflight lo rechaza antes de iniciar inferencia y no reemplaza, trunca ni limpia archivos ajenos. Rutas alternativas requieren editar explícitamente outputs en config y eso cambia config/stage fingerprints.
- Symlink, junction o cualquier reparse point detectado en cualquier componente existente de output/workspace/manifest: rechazar antes de abrir. Crear componentes solo después de inspeccionarlos.
- Segundo artefacto falla tras publicar el primero: intentar borrar únicamente archivos propios cuya identidad capturada con fstat siga igual al verificar con lstat. Si no se puede probar identidad o parent seguro, conservar el parcial y reportar ruta. Marcar Stage 03 failed y artifacts vacíos.
- Crash después de outputs y antes del update: el último manifest válido permanece pending/failed para TTS o completed solo para 01. Los outputs no incluidos en el manifest son huérfanos y jamás se adoptan automáticamente. Rutas ocupadas hacen fallar el próximo run.
- Crash al guardar manifest: actualizar mediante temp sibling exclusivo y rename en el mismo directorio de run. Un reader ve el manifest previo o el nuevo, no JSON truncado. El temporal puede quedar y se reporta; no se reanuda ni limpia material no verificado.
- Inputs/config cambian durante ejecución: capturar hashes antes, procesar exactamente bytes validados, comprobarlos otra vez antes de TTS completed. Si cambia cualquiera, no publicar completed; limpiar outputs propios cuando sea seguro, marcar las etapas afectadas failed/blocked, artifacts vacíos y reportar cambios. No intentar protegerse frente a actores hostiles que sustituyen rutas concurrentemente.
- Fallo al guardar estado failed: el manifiesto conserva último checkpoint duradero; CLI informa error de persistencia y nunca reporta éxito. No afirmar estado fallido persistido si no se relee/valida.

El modelo de amenaza sigue limitado a proyecto local confiable. No prometer protección TOCTOU completa, aislamiento de filesystem/red, atomicidad WAV+JSON+manifest ni recuperación automática tras terminación abrupta. Puede persistir un parcial que requiere inspección manual.

### Estado completed y punto de commit lógico

El orden obligatorio es: escribir todos los outputs con exclusividad; releer y verificar cada archivo y sus hashes; comprobar de nuevo inputs/runtime invariants; construir el nuevo estado de manifiesto; persistirlo atómicamente; reabrir y validar el manifest final; solo entonces reportar éxito.

El sistema de archivos no puede comprometer tres archivos distintos en una transacción. El manifest completed es el último punto visible que acredita ejecución; ante un crash antes de ese replace, outputs presentes no equivalen a etapa completada.

## 9. Matriz de pruebas posterior

Pruebas unitarias/integración local con procesos worker sustituidos por fakes. CI no instala Kokoro, no descarga modelo/voices y no ejecuta inferencia real. Mantener matriz existente Ubuntu 24.04, Ubuntu 26.04 y Windows 2025.

| Área | Casos y resultado requerido |
| --- | --- |
| Éxito productivo | Runtime/worker fake; exactamente etapas 01/03 completed, 02/04/05/06 pending; proceso y argumentos fijos; sin shell |
| Stage 01 | JSON determinista, semántico y no vacío; recompone/valida; cubre config, storyboard y recursos declarados; outputs/hash/manifiesto validan |
| TTS output | WAV fake técnicamente válido y non-silent; sidecar ligado al WAV y script; versiones efectivas incluidas; manifest final PASS |
| Compatibilidad heredada | Templates pipeline, cave, etapa 03-kokoro-narration y manifests v1 anteriores validan byte/huella/ID sin cambio |
| Piper | Contrato pending se valida y planifica; adapter_status implemented o comando productivo con provider piper se rechaza antes del worker |
| Config/status | Combinaciones inválidas provider/voice/lang/audio/tool/status/deps/outputs se rechazan |
| Fingerprints | Cada campo etapa/herramienta, script, storyboard, profile, configuración y outputs cambia los hashes según tabla; ninguna huella copiada |
| Runtime actual | Version incorrecta, Python fuera de rango, CPU provider distinto, voz ausente, modelo/voces cambiados o no regulares rechazan ejecución |
| Timeout/error worker | Una llamada máximo; attempts 1; sin outputs completados; manifiesto failed validable si persiste |
| Publicación | Colisión en cada output, fallo WAV, fallo JSON segundo, escritura parcial, verificación post-write y cleanup solo de propiedad probada |
| Rutas | Absolutas, traversal, separadores no admitidos, symlink, junction, atributo genérico reparse, parent file y salida fuera workspace rechazados |
| Interrupciones | Fallo entre etapas, entre outputs, antes/después del replace del manifest; no estado completed antes de verificación |
| Inputs concurrentes | Cambiar storyboard, script, recurso, modelo, voices o config en puntos controlados impide estado completed |
| Read-only | validate, plan, dry-run, validate-manifest, simulate-tts conservan comportamiento sin archivos/procesos/red; snapshots antes/después |
| Contratos existentes | save-tts-simulation y run-tts-kokoro mantienen destino, metadata, límites y semántica standalone |
| Cross-platform | POSIX symlink tests; Windows junction y reparse tests; symlink omitido solo con WinError 1314 documentado |
| Sin retries | timeout/error invokes worker una vez, no reintenta ni reutiliza manifiestos/output de corridas anteriores |

Verificación local Phase 4B: suite unittest completa, 169 pruebas aprobadas y 3 skips por permisos de symlink en Windows; validate-skill.py, pipeline validate/dry-run heredados, cuatro storyboards y git diff --check pasan. No se generó audio real. CI de Ubuntu 24.04, Ubuntu 26.04 y Windows 2025 sigue pendiente de PR.

## 10. Archivos previstos para Phase 4B y 4C

### Phase 4B — implementada

- scripts/pipeline.py: elegibilidad Kokoro, pseudoentrada pipeline-config, validated-plan determinista, comando run-productive-tts, publicación exclusiva, checkpoints manifest v1 y verificación final.
- tests/test_productive_tts_execution.py: éxito, fallos, huellas, invalidación, manifiestos, publicación parcial, cambios concurrentes y recuperación sin resume.
- scripts/tts-kokoro-worker.py: reutilizado sin cambios; la síntesis mantiene un único worker.
- Regresión local Windows: 169 pruebas; 3 skips por permisos de symlink. Audio de integración sintético.

### Phase 4C — documentación y gates

- references/pipeline-orchestration.md: contrato productivo y CLI, Stage 01 receipt, manifest lifecycle, fingerprints y frontera standalone/productivo.
- references/pipeline-security.md: modelo de publicación, fallos/partials, runtime checks, límites TOCTOU/atomicidad y evidencia CI.
- SKILL.md: marcar capacidad y restricciones exactas; comandos explícitos; no sugerir render.
- docs/roadmap.md y README.md: fase v0.10, estado y prueba, sin afirmar producción audiovisual completa.
- .github/workflows/validate.yml: solo modificar si la matriz no cubre las nuevas pruebas o se requiere ajustar umbrales de timeout; mantener Python 3.12 y tres OS targets, no agregar servicios/deps.

No cambiar templates/pipeline.json, examples/cave-exploration/pipeline.json ni ejemplos existentes. Si se necesita un fixture de configuración productiva, construirlo temporalmente en tests; no declarar como verificado un runtime ausente de CI. No añadir dependencias ni descargar assets.

## 11. Riesgos y límites conocidos

1. **Pseudentrada pipeline-config.** Aprobada. Hash semántico de config parseada/canonicalizada. No altera fingerprints heredados porque ningún config anterior declara esa entrada.
2. **Runtime verificado.** availability=verified y adapter_status=implemented habilitan elegibilidad declarativa. Worker y padre comprueban runtime real; validate-manifest verifica consistencia registrada, no reejecuta runtime.
3. **Invalidación conservadora.** validated-plan contiene fingerprint del pipeline completo y TTS lo consume como input. Cambios a cualquier etapa invalidan TTS, aunque alguna etapa no afecte narración. Se acepta porque Phase 4B no reusa resultados.
4. **Provenance y versiones.** Aprobada estructura JSON estable. Registrar versiones observadas de Python y dependencias, SHA-256 del worker, modelo y voces; version_identity de modelo/voces es su digest, no una etiqueta inferida.
5. **Tamaño de validated-plan.** Limitar receipt determinista a 1 MiB; no copiar recursos textuales. Verificar límite antes de publicar.
6. **Colisiones y recuperación manual.** Rechazar outputs ocupados; no cleanup general, --force, resume ni adopción. Rutas nuevas requieren cambio explícito de config.
7. **Runtime reproducible.** Versiones y hashes identifican ejecución local, pero no garantizan autenticidad del modelo, aislamiento de red ni WAV bit a bit idéntico.
8. **Compatibilidad.** Plantillas heredadas mantienen storyboard como input de Stage01. Config productiva lista pipeline-config, storyboard y recursos requeridos representados en el recibo.

## 12. Criterios objetivos de aceptación

Phase 4B conserva estos gates de aceptación:

- Un config productivo neutral Kokoro v1 pasa validación únicamente con la combinación de eligibility definida; Piper implemented se rechaza y todos los ejemplos/contratos heredados siguen aceptados.
- run-productive-tts ejecuta exactamente Stage 01 y TTS, usa el worker fijo con script por stdin y ningún otro proceso, y no modifica comportamiento de comandos existentes.
- validated-plan contiene contenido reproducible y auditable, deriva de inputs capturados, valida contra config actual, no incluye texto de guion y su hash está en el manifest.
- Stage 01 y TTS se marcan completed solamente después de outputs existentes, reread, validación semántica y SHA-256 correctos.
- El manifest conserva manifest_version=1 y los campos actuales, se crea en ruta exclusiva, pasa validate-manifest sin warnings de config/input y deja las otras etapas pending.
- Fingerprints usan el algoritmo vigente; el pseudoinput solo se aplica al nuevo Stage 01 y ningún manifest viejo se altera o se usa como resultado productivo.
- Todo timeout, error de worker, output existente, ruta insegura, input cambiado, publicación parcial o persistencia fallida evita un falso completed y nunca sobrescribe archivos ajenos.
- No existen retries ni resume implícitos; invocación nueva produce run-id nuevo y no adopta artefactos viejos.
- Los comandos readonly y pilotos existentes conservan sus contratos; pruebas cubren Ubuntu 24.04, Ubuntu 26.04 y Windows 2025 con reglas de symlink documentadas.
- CI pasa, validadores de skill/storyboard/pipeline/manifiestos pasan, y el reporte separa evidencia de tests fake de cualquier piloto real local.
- Ningún render, video, Blender, Remotion, FFmpeg, descarga de modelos ni servicio de pago es requerido por CI o por el nuevo comando.

## Recomendación

Decisiones de pseudentrada pipeline-config y provenance/versiones ya aprobadas. Phase 4B implementa dos etapas, publicación sin overwrite, un intento, cero retries y cero resume. Phase 4C documentará capacidad final y evidencia CI. Mantener explícita la frontera standalone/productiva y los assets no incluidos.

El diseño se contrastó con pipeline.py, el worker y los contratos heredados antes de implementar. Phase 4B conserva pipeline_version=1 y manifest_version=1. Phase 4C sigue pendiente.
