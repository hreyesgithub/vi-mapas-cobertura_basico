# 🚀 RF Optimizer Backend

API REST para optimización de cobertura WiFi y generación de informes técnicos. Este servicio es el cerebro detrás del simulador de RF de **Venezuela Insights**, permitiendo a los usuarios visualizar mapas de calor, optimizar ubicaciones de puntos de acceso (APs) y descargar informes ejecutivos en PDF.

Construido con **Flask (Python)**, desplegado en **Render** y utilizando **Supabase** como base de datos.

---

## 📋 Tabla de Contenidos

- [Características](#-características)
- [Tecnologías](#-tecnologías)
- [Requisitos Previos](#-requisitos-previos)
- [Configuración del Entorno](#-configuración-del-entorno)
- [Instalación y Ejecución Local](#-instalación-y-ejecución-local)
- [Endpoints de la API](#-endpoints-de-la-api)
- [Esquema de Base de Datos (Supabase)](#-esquema-de-base-de-datos-supabase)
- [Despliegue en Render](#-despliegue-en-render)
- [Estructura del Proyecto](#-estructura-del-proyecto)
- [Contribución](#-contribución)
- [Licencia](#-licencia)

---

## ✨ Características

- **Optimización Inteligente**: Algoritmo de *Simulated Annealing* que calcula las posiciones óptimas de los APs respetando paredes y obstáculos.
- **Modelo de Propagación Realista**: Implementa el modelo *Log-Distance Path Loss* con detección de obstáculos (Bresenham) para simular la atenuación de señal.
- **Generación de PDF Profesional**: Crea informes ejecutivos con métricas de cobertura, posiciones recomendadas y consejos técnicos usando `reportlab`.
- **Captura de Leads**: Guarda automáticamente la información de los usuarios (correo, industria, métricas) en Supabase.
- **CORS Habilitado**: Listo para comunicarse con frontends alojados en Netlify, Vercel o dominios personalizados.

---

## 🛠 Tecnologías

- **Python 3.10+**
- **Flask 3.0** - Framework web.
- **flask-cors** - Manejo de solicitudes entre dominios.
- **supabase-py** - Cliente oficial para Supabase (PostgreSQL).
- **reportlab** - Generación de PDFs.
- **python-dotenv** - Manejo de variables de entorno.

---

## 📦 Requisitos Previos

- Python 3.10 o superior instalado.
- Una cuenta en [Supabase](https://supabase.com) (Plan gratuito suficiente).
- (Opcional) Una cuenta en [Render](https://render.com) para despliegue.

---

## 🔧 Configuración del Entorno

Crea un archivo `.env` en la raíz del proyecto con las siguientes variables:

```env
# Obligatorias
SUPABASE_URL=https://tu-proyecto.supabase.co
SUPABASE_KEY=tu-anon-key-publica
```

# Opcional (Render asigna automáticamente el puerto)
PORT=5000

⚠️ Importante: No subas el archivo .env a GitHub. Añádelo a tu .gitignore.

## 💻 Instalación y Ejecución Local

```bash
    # 1. Clona el repositorio
    git clone https://github.com/tu-usuario/rf-optimizer-backend.git
    cd rf-optimizer-backend

    # 2. Crea un entorno virtual (recomendado)
    python -m venv venv
    source venv/bin/activate  # En Windows: venv\Scripts\activate

    # 3. Instala las dependencias
    pip install -r requirements.txt

    # 4. Crea el archivo .env con tus credenciales (ver sección anterior)

    # 5. Ejecuta el servidor en modo desarrollo
    python app.py
```

## 🌐 Endpoints de la API

1. Verificar estado del servidor
GET /health

Respuesta de ejemplo:

```json
{
  "status": "ok",
  "supabase_connected": true,
  "timestamp": "2026-08-16T12:00:00.000Z"
}
```

2. Optimizar ubicación de APs
POST /api/optimize

Calcula las mejores posiciones para tus puntos de acceso basándose en el espacio y los obstáculos.

Payload de ejemplo:

```json
{
  "width": 800,
  "height": 500,
  "ap_count": 2,
  "walls": [
    { "x1": 300, "y1": 0, "x2": 300, "y2": 250 },
    { "x1": 500, "y1": 200, "x2": 500, "y2": 500 }
  ]
}
```

Respuesta de ejemplo:

```json
{
  "aps": [
    { "x": 215.4, "y": 178.2 },
    { "x": 612.8, "y": 342.1 }
  ],
  "coverage_percent": 94.5
}
```

3. Generar informe ejecutivo (PDF)
POST /api/generate_report

Genera un PDF con el análisis detallado y guarda el lead en Supabase.

Payload de ejemplo:

```json
{
  "email": "cliente@ejemplo.com",
  "industry": "Oficina Corporativa",
  "aps": [ { "x": 200, "y": 200 }, { "x": 600, "y": 300 } ],
  "template": "office",
  "coverage": "96%",
  "avgRssi": "-52"
}
```

Respuesta: Archivo PDF (informe_rf_YYYYMMDD.pdf).

## 🗄 Esquema de Base de Datos (Supabase)

Ejecuta este script SQL en el editor de Supabase para crear la tabla de leads:

```sql
CREATE TABLE public.leads (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  email TEXT,
  industry TEXT,
  product TEXT,
  source TEXT,
  template_used TEXT,
  aps_count INTEGER,
  coverage NUMERIC,
  avg_rssi NUMERIC,
  metadata JSONB,
  timestamp TIMESTAMPTZ DEFAULT NOW(),
  generated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para rendimiento
CREATE INDEX idx_leads_email ON public.leads (email);
CREATE INDEX idx_leads_product ON public.leads (product);
```

## 🚀 Despliegue en Render

+ Sube el código a GitHub (repositorio público o privado).

+ Crea un Web Service en Render:

- Ve a render.com y haz clic en "New +" > "Web Service".

- Conecta tu repositorio de GitHub.

+ Configura los siguientes campos:

- Name: rf-optimizer-api (o el que prefieras).

- Environment: Python 3.

- Build Command: pip install -r requirements.txt.

- Start Command: gunicorn app:app (Recomendado para producción) o python app.py.

- Configura las variables de entorno en el panel de Render:

- SUPABASE_URL: URL de tu proyecto Supabase.

- SUPABASE_KEY: Clave anónima (pública) de Supabase.

Haz clic en "Create Web Service". Render desplegará la aplicación automáticamente.

Obtendrás una URL como: https://rf-optimizer-api.onrender.com. ¡Usa esa URL en tu frontend!

## 📁 Estructura del Proyecto

```text
rf-optimizer-backend/
├── app.py               # Código principal de la API
├── requirements.txt     # Dependencias de Python
├── .env                 # Variables de entorno (no subir a GitHub)
├── .gitignore           # Archivos ignorados por Git
└── README.md            # Este archivo
```

## 🤝 Contribución

Si deseas mejorar este proyecto, por favor:

+ Haz un fork del repositorio.

+ Crea una rama con tu funcionalidad (git checkout -b feature/nueva-funcionalidad).

+ Realiza tus cambios y haz commit (git commit -m 'Añade X funcionalidad').

+ Sube tu rama (git push origin feature/nueva-funcionalidad).

+ Abre un Pull Request.

## 📄 Licencia

Este proyecto es propiedad de Venezuela Insights y se distribuye bajo una licencia privada. Prohibida su reproducción total o parcial sin autorización expresa.

## 📧 Contacto

Si tienes dudas sobre este backend o la integración con el frontend, abre un issue en el repositorio o contacta al equipo de desarrollo de Venezuela Insights.

¡Listo para optimizar redes! 📶

```text

---

### 📌 ¿Cómo usarlo?

1. **Copia y pega** este contenido en un archivo llamado `README.md` en la raíz de tu proyecto de backend (junto a `app.py` y `requirements.txt`).

2. **Sustituye** los placeholders como `tu-usuario`, las URL de ejemplo y cualquier referencia específica si lo deseas.

3. **Sube el archivo a GitHub** para que Render lo muestre en la página de tu repositorio.

Este README es lo suficientemente detallado para que cualquiera pueda entender el propósito, la configuración y el uso de tu API. ¿Necesitas ajustar algún detalle o añadir alguna sección adicional?
```
