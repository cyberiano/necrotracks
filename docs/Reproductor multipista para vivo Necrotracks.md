# Reproductor multipista para vivo "Necrotracks"

## Objetivo

Desarrollar un reproductor autónomo basado en Raspberry Pi para reproducir pistas de audio y automatizaciones MIDI en vivo, con una interfaz simple, confiable y adaptable al hardware disponible en cada show.

El sistema debe poder utilizarse tanto en configuraciones simples como en shows que requieran multipista, click, guías y automatizaciones.

## Modos de uso

El reproductor deberá adaptarse automáticamente o mediante perfiles al hardware conectado.

### Modo simple

Pensado para shows donde solo se necesitan pistas estéreo.

- Salida FOH L/R.
- Uso del iRig Stomp I/O como interfaz de audio.
- Posibilidad de utilizar los controles del iRig para manejar el reproductor.
- Sin necesidad de click, guía ni automatizaciones MIDI.

### Modo multipista

Pensado principalmente para Necropolis.

- Click independiente.
- Pistas FOH estéreo.
- Pista de guía técnica.
- Automatizaciones MIDI.
- Interfaz de audio de al menos 4 salidas.
- Posibilidad de utilizar una pedalera MIDI independiente como controlador.

## Biblioteca de canciones

El sistema contará con una biblioteca central de canciones.

Cada canción podrá contener:

- Pistas FOH L/R.
- Click.
- Guía.
- Archivo de automatización MIDI.

No será obligatorio que todas las canciones tengan todos estos elementos.

Las canciones serán independientes del hardware utilizado. El perfil de hardware determinará qué pistas se reproducen y por qué salidas.

## Set Lists

Se podrán crear múltiples Set Lists para organizar diferentes shows, ensayos o presentaciones.

Cada Set List tendrá:

- Nombre.
- Orden de canciones.
- Posibilidad de reorganizar canciones.
- Configuración individual de cada canción dentro de esa Set List.

Una misma canción podrá formar parte de distintas Set Lists sin duplicar sus archivos.

## Bloques

Dentro de una Set List será posible agrupar canciones en bloques.

Ejemplo:

**Bloque 1**

- Canción 1
- Canción 2
- Canción 3

**Bloque 2**

- Canción 4
- Canción 5

Esto permitirá representar directamente la estructura real de un show.

Los bloques podrán reproducirse de manera continua y detenerse al finalizar.

## Comportamiento de cada canción

Cada aparición de una canción dentro de una Set List podrá tener su propio comportamiento.

Opciones principales:

- Detenerse al finalizar.
- Preparar automáticamente la siguiente canción y esperar Play.
- Reproducir automáticamente la siguiente.
- Esperar una cantidad determinada de segundos antes de iniciar la siguiente.
- Repetir la canción.

Durante una espera automática se podrá cancelar la transición o iniciar inmediatamente la siguiente canción.

## Control del reproductor

El reproductor podrá controlarse simultáneamente desde distintas fuentes:

- Pantalla TFT integrada en la Raspberry.
- Botones físicos conectados por GPIO.
- LEDs WS2812B para indicar estados del reproductor y estado del Wi-Fi.
- Interfaz web desde celular, tablet o computadora mediante `necrotracks.local`.
- Hotspot Wi-Fi propio, con posibilidad de encenderlo o apagarlo mediante una combinación de botones GPIO.
- Pedalera MIDI.
- Teclado QWERTY USB.
- Controles del iRig Stomp I/O.

Se incorporará MIDI Learn para poder asignar fácilmente botones o pedales a funciones como:

- Play/Pause.
- Stop.
- Canción siguiente.
- Canción anterior.

## Interfaz

La interfaz tendrá dos objetivos claramente separados.

### Configuración

Permitirá administrar:

- Biblioteca de canciones.
- Set Lists.
- Bloques.
- Comportamiento de las canciones.
- Perfiles de hardware.
- Routing de audio y MIDI.
- Asignación de controles.
- Importación y sincronización de archivos.

### Show Mode

Será una interfaz simplificada para usar durante el show.

Mostrará principalmente:

- Set List activa.
- Bloque actual.
- Canción actual.
- Próxima canción.
- Tiempo transcurrido/restante.
- Estado del reproductor.
- Información sobre Auto Next o esperas.

Durante este modo se evitarán configuraciones que puedan alterar accidentalmente el sistema.

## Perfiles de hardware

El reproductor permitirá guardar diferentes configuraciones según el equipo conectado.

Ejemplos:

- iRig Stomp I/O — estéreo.
- Interfaz multipista — 4 salidas + MIDI.
- Configuración de ensayo.

El objetivo es poder cambiar de hardware sin modificar las canciones ni las Set Lists.

## Gestión de archivos

Las canciones se almacenarán siempre localmente en la Raspberry para que la reproducción no dependa de Internet.

Se contemplan dos métodos principales de carga:

- Subida de canciones desde la interfaz web mediante archivos WAV, MIDI o carpetas ZIP.
- Sincronización con una carpeta compartida de Google Drive mediante un enlace público.

Para la sincronización con Google Drive se priorizará el acceso a Internet mediante Ethernet, ya que el Wi-Fi de la Raspberry se utilizará normalmente como hotspot para controlar Necrotracks.

La sincronización deberá detectar canciones nuevas o modificadas y permitir actualizar la biblioteca.

Las actualizaciones deberán realizarse de manera segura, manteniendo la versión anterior hasta comprobar que los nuevos archivos se hayan descargado correctamente.

Durante un show no se realizarán actualizaciones automáticas.

## Funcionamiento autónomo

El sistema estará pensado como un equipo dedicado para vivo.

Al encenderlo deberá iniciar automáticamente y quedar listo para reproducir, sin necesidad de monitor, teclado o acceso al escritorio de Linux.

La reproducción deberá continuar funcionando aunque se pierda la conexión Wi-Fi o se cierre la interfaz web.

---

# Propuesta técnica

Necrotracks se plantea como un sistema compuesto por módulos independientes. La reproducción de audio y MIDI deberá estar separada de la interfaz gráfica y de los servicios de administración, priorizando ante todo la estabilidad durante un show.

## Player Engine

Será el núcleo del sistema y el componente encargado de la reproducción.

Sus responsabilidades principales serán:

- Reproducción sincronizada de pistas de audio.
- Envío de automatizaciones MIDI.
- Manejo de Play, Stop, Next, Pause y transiciones.
- Auto Next y tiempos de espera.
- Routing hacia las diferentes salidas físicas.
- Mantener el estado actual de reproducción.

El Engine deberá funcionar independientemente de la interfaz web. Si el navegador, el Wi-Fi o cualquier componente de administración falla, la reproducción deberá continuar normalmente.

## Control Manager

Centralizará todas las formas de controlar el reproductor.

Recibirá comandos desde:

- GPIO.
- Pedaleras y dispositivos MIDI.
- iRig Stomp I/O.
- Teclado USB.
- Pantalla TFT.
- Interfaz web.

Todos estos controles actuarán sobre el mismo Player Engine, evitando que cada dispositivo implemente su propia lógica de reproducción.

También será responsable de las asignaciones configurables y del MIDI Learn.

## Device Manager

Será responsable de detectar el hardware conectado y relacionarlo con los perfiles configurados.

Administrará principalmente:

- Interfaces de audio.
- Cantidad y disponibilidad de salidas.
- Dispositivos MIDI.
- Controladores externos.

Permitirá que Necrotracks pueda pasar de una configuración estéreo con iRig a una configuración multipista sin modificar las canciones ni las Set Lists.

## Backend

Funcionará como capa de administración del sistema.

Se encargará de:

- Biblioteca de canciones.
- Set Lists y bloques.
- Configuraciones.
- Perfiles de hardware.
- Importación de archivos.
- Sincronización con Google Drive.
- Comunicación entre la interfaz web y el reproductor.

La configuración y metadata del sistema podrá almacenarse localmente en una base de datos liviana.

## Interfaz Web

Será la interfaz principal para configurar Necrotracks desde una computadora, tablet o celular.

Se comunicará con el Backend para realizar configuraciones y con el sistema de reproducción para mostrar su estado en tiempo real.

También incluirá el Show Mode, enfocado exclusivamente en las funciones necesarias durante una presentación.

## Interfaz TFT

La pantalla integrada será una interfaz local simplificada.

No buscará reemplazar la interfaz web de configuración, sino permitir operar el equipo sin depender de otro dispositivo.

Mostrará información esencial del show y permitirá acceder a las principales funciones de reproducción.

## Stack técnico tentativo

Como punto de partida para evaluar el MVP se propone:

- **Sistema operativo:** Raspberry Pi OS Lite / Linux.
- **Audio:** JACK o PipeWire/JACK.
- **MIDI:** ALSA/JACK MIDI.
- **Player Engine:** Rust o C++, priorizando estabilidad y ejecución en tiempo real.
- **Backend:** Python + FastAPI.
- **Interfaz Web:** React.
- **Comunicación en tiempo real:** WebSockets.
- **Base de datos local:** SQLite.
- **Interfaz TFT:** aplicación gráfica liviana independiente de la interfaz web.
- **Servicios del sistema:** systemd para inicio automático y supervisión de los componentes.
- **Almacenamiento:** archivos de audio/MIDI locales + metadata en SQLite.

El stack definitivo deberá validarse durante el desarrollo del MVP, especialmente sobre Raspberry Pi 3B+, priorizando bajo consumo de recursos y estabilidad sobre complejidad tecnológica.

## Arquitectura general propuesta

```text
                 Interfaz Web
                      │
                      ▼
                   Backend
                      │
        ┌─────────────┼─────────────┐
        │             │             │
     Library      Set Lists      Config
                      │
                      ▼
                 Player Engine
                 ┌────┴────┐
                 │         │
               Audio      MIDI
                 │         │
                 ▼         ▼
             Device Manager
                 │
        ┌────────┴─────────┐
        │                  │
     iRig 2 OUT      Interfaz 4+ OUT


 GPIO ──────┐
 MIDI ──────┤
 TFT ───────┼──► Control Manager ──► Player Engine
 USB ───────┤
 WEB ───────┘
```

La premisa principal será mantener el **Player Engine aislado de las funciones no críticas**, de forma que la interfaz, sincronización de archivos o conectividad de red nunca comprometan la reproducción durante un show.

## Prioridades del MVP

El primer MVP deberá enfocarse en:

- Reproducción estéreo y multipista.
- Automatizaciones MIDI.
- Biblioteca de canciones.
- Set Lists.
- Bloques.
- Auto Next, Stop y esperas entre canciones.
- Perfiles de hardware.
- Control desde web, GPIO y MIDI.
- MIDI Learn.
- Importación de archivos.
- Funcionamiento autónomo y confiable.

Funciones más avanzadas como marcadores, BPM, loops de secciones o herramientas específicas de ensayo podrán evaluarse posteriormente.

## Principio general

El sistema deberá mantenerse simple durante el uso en vivo, pero flexible en su configuración.

La arquitectura conceptual será:

**Canciones → Set Lists → Bloques → Configuración de reproducción → Perfil de hardware → Player**

El objetivo del MVP será validar que esta experiencia sea estable, rápida de configurar y confiable para utilizarla en shows reales.