import sqlite3
import io
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import JSONResponse
import sqlite3
import qrcode

app = FastAPI(title="Control de Asistencia QR y Celular")

# (Asumiendo que ya tienes configurada tu app y tu base de datos attendance.db)

@app.post("/api/consultar")
def consultar_asistencia(codigo: str = Form(...)):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM asistentes WHERE codigo = ?", (codigo,))
    asistente = cursor.fetchone()
    conn.close()

    if not asistente:
        return JSONResponse(status_code=404, content={"status": "not_found", "message": "El código no existe"})
    
    # Supongamos que el campo 'estado' o 'ingresado' está en la columna 2 o similar
    # Devuelve el estado actual del asistente
    ya_ingreso = asistente[2] # 1 si ya entró, 0 si no
    return {
        "status": "success",
        "nombre": asistente[1],
        "codigo": asistente[0],
        "ya_ingreso": bool(ya_ingreso)
    }

@app.post("/api/registrar")
def registrar_asistencia(codigo: str = Form(...)):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM asistentes WHERE codigo = ?", (codigo,))
    asistente = cursor.fetchone()

    if not asistente:
        conn.close()
        return JSONResponse(status_code=404, content={"status": "not_found", "message": "El código no existe"})

    if asistente[2] == 1: # Ya había ingresado (Duplicado)
        conn.close()
        return JSONResponse(status_code=400, content={"status": "duplicate", "message": "¡Ya está dentro! (Acceso duplicado)"})

    # Registrar la llegada (cambiar estado a 1)
    cursor.execute("UPDATE asistentes SET ingresado = 1 WHERE codigo = ?", (codigo,))
    conn.commit()
    conn.close()
    return {"status": "registered", "message": "¡Ingreso registrado con éxito!", "nombre": asistente[1]}

# Habilitar CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configurar motor de plantillas Jinja2 (requiere una carpeta llamada 'templates')
templates = Jinja2Templates(directory="templates")

# Inicializar Base de Datos SQLite
def init_db():
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            phone TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (phone) REFERENCES users(phone),
            UNIQUE(session_id, phone)
        )
    """)
    conn.commit()
    conn.close()

init_db()

# Modelos Pydantic
class UserCreate(BaseModel):
    phone: str
    name: str

class AttendanceScan(BaseModel):
    session_id: str
    phone: str

# ==========================================
# API ENDPOINTS
# ==========================================

@app.post("/api/usuarios")
async def registrar_usuario(data: UserCreate):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR REPLACE INTO users (phone, name) VALUES (?, ?)",
            (data.phone, data.name)
        )
        conn.commit()
        return {
            "status": "success",
            "message": f"Usuario {data.name} registrado correctamente."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/qr/{phone}")
async def obtener_qr(phone: str):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM users WHERE phone = ?", (phone,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Generar QR con el número de celular
    qr_img = qrcode.make(phone)
    img_byte_arr = io.BytesIO()
    qr_img.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)

    return StreamingResponse(img_byte_arr, media_type="image/png")

@app.get("/api/dashboard-data")
async def dashboard_data(session_id: str = "SESION-001"):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT u.phone, u.name, a.timestamp 
        FROM users u
        LEFT JOIN attendance a ON u.phone = a.phone AND a.session_id = ?
    """, (session_id,))
    rows = cursor.fetchall()
    conn.close()
    
    users = []
    for r in rows:
        users.append({
            "phone": r[0],
            "name": r[1],
            "status": "Entró" if r[2] else "Pendiente",
            "timestamp": r[2] or "-"
        })
    return {"session_id": session_id, "users": users}

@app.post("/api/asistencia")
async def verificar_asistencia(data: AttendanceScan):
    conn = sqlite3.connect("attendance.db")
    cursor = conn.cursor()

    # 1. Verificar si existe
    cursor.execute("SELECT name FROM users WHERE phone = ?", (data.phone,))
    user = cursor.fetchone()
    
    if not user:
        conn.close()
        return {
            "status": "not_exists",
            "message": f"❌ El cliente con celular {data.phone} NO EXISTE."
        }

    name = user[0]

    # 2. Verificar si ya entró
    cursor.execute(
        "SELECT timestamp FROM attendance WHERE session_id = ? AND phone = ?",
        (data.session_id, data.phone)
    )
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return {
            "status": "already_entered",
            "message": f"⚠️ YA HA ENTRADO: {name} ({data.phone}).",
            "timestamp": existing[0]
        }

    # 3. Registrar entrada
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO attendance (session_id, phone, timestamp) VALUES (?, ?, ?)",
        (data.session_id, data.phone, timestamp)
    )
    conn.commit()
    conn.close()

    return {
        "status": "success",
        "message": f"✅ ENTRANDO: {name} ({data.phone}).",
        "timestamp": timestamp
    }

# ==========================================
# VISTAS WEB
# ==========================================

@app.get("/", response_class=HTMLResponse)
async def admin_panel(request: Request):
    return templates.TemplateResponse(request, "admin.html", {})

@app.get("/scanner", response_class=HTMLResponse)
async def scanner_view(request: Request):
    return templates.TemplateResponse(request, "scanner.html", {})