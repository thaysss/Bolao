from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import httpx
import os
from supabase import create_client, Client
import asyncio

app = FastAPI()

# Liberação de acesso para o navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- CONFIGURAÇÕES ----------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://brbjlpcpiubtlneualrv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") 
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- MODELOS ----------------
class Aposta(BaseModel):
    nome: str
    whatsapp: str
    palpites: Dict[str, str]

class NovoJogo(BaseModel):
    apiId: Optional[int] = None
    home: str
    away: str
    championship: str
    datetime: str

class Configuracoes(BaseModel):
    pix_key: str
    pix_name: str
    pix_amount: str
    whatsapp: str
    prize_1: str
    prize_2: str
    prize_3: str

# ---------------- SEGURANÇA ----------------
def verificar_admin(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Acesso negado.")

# ---------------- ROTAS ----------------

@app.get("/")
async def home():
    return FileResponse("index.html")

@app.get("/jogos")
async def listar_jogos():
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data

@app.get("/buscar-api")
async def buscar_api(data: str = None):
    """Busca jogos na API-Football usando o seu servidor como ponte (Proxy)"""
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?date={data}&timezone=America/Sao_Paulo"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return r.json()

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    supabase.table("apostas").insert(aposta.model_dump()).execute()
    return {"status": "sucesso"}

@app.post("/login-admin")
async def login(req: Dict):
    if req.get("senha") == ADMIN_PASSWORD:
        return {"token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401)

@app.post("/adicionar-jogo", dependencies=[Depends(verificar_admin)])
async def add_jogo(jogo: NovoJogo):
    supabase.table("jogos").insert(jogo.model_dump()).execute()
    return {"status": "sucesso"}

@app.get("/configuracoes")
async def get_config():
    res = supabase.table("configuracoes").select("*").eq("id", 1).execute()
    return res.data[0] if res.data else {}

@app.post("/salvar-configuracoes", dependencies=[Depends(verificar_admin)])
async def save_config(config: Configuracoes):
    supabase.table("configuracoes").update(config.model_dump()).eq("id", 1).execute()
    return {"status": "sucesso"}