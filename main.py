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

# Permite que o site visual converse com o servidor
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

# ---------------- ROTAS DO SITE ----------------

@app.get("/")
async def home():
    return FileResponse("index.html")

@app.get("/jogos")
async def listar_jogos():
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data

@app.get("/buscar-api")
async def buscar_api(data: str = None):
    """Puxa jogos da internet usando sua chave secreta"""
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")
    url = f"https://v3.football.api-sports.io/fixtures?date={data}&timezone=America/Sao_Paulo"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)
        return r.json()

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    try:
        supabase.table("apostas").insert(aposta.model_dump()).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ranking-geral")
async def ranking():
    # Lógica simplificada de ranking (calculada na hora)
    jogos = supabase.table("jogos").select("*").execute().data
    apostas = supabase.table("apostas").select("*").execute().data
    resultado = []
    for a in apostas:
        pts = 0
        palpites = a.get("palpites", {})
        for j in jogos:
            j_id = str(j["id"])
            res = j.get("result")
            if res and res.get("home") is not None:
                h, v = int(res["home"]), int(res["away"])
                venc = "home" if h > v else "away" if v > h else "draw"
                if palpites.get(j_id) == venc: pts += 1
        resultado.append({"nome": a["nome"], "pontos": pts, "whatsapp": a["whatsapp"]})
    return sorted(resultado, key=lambda x: x["pontos"], reverse=True)

# ---------------- ROTAS ADMIN ----------------

@app.post("/login-admin")
async def login(req: Dict):
    if req.get("senha") == ADMIN_PASSWORD:
        return {"status": "sucesso", "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401)

@app.get("/obter-api-key", dependencies=[Depends(verificar_admin)])
async def get_key():
    return {"api_key": FOOTBALL_API_KEY}

@app.post("/adicionar-jogo", dependencies=[Depends(verificar_admin)])
async def add_jogo(jogo: NovoJogo):
    supabase.table("jogos").insert(jogo.model_dump()).execute()
    return {"status": "sucesso"}

@app.delete("/remover-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def remover_jogo(jogo_id: str):
    supabase.table("jogos").delete().eq("id", jogo_id).execute()
    return {"status": "sucesso"}

@app.put("/alternar-status-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def status_jogo(jogo_id: str, ativo: bool):
    supabase.table("jogos").update({"active": ativo}).eq("id", jogo_id).execute()
    return {"status": "sucesso"}

@app.get("/configuracoes")
async def get_config():
    res = supabase.table("configuracoes").select("*").eq("id", 1).execute()
    return res.data[0] if res.data else {}

@app.post("/salvar-configuracoes", dependencies=[Depends(verificar_admin)])
async def save_config(config: Configuracoes):
    supabase.table("configuracoes").update(config.model_dump()).eq("id", 1).execute()
    return {"status": "sucesso"}

# ---------------- AUTOMAÇÃO ----------------
@app.on_event("startup")
async def startup():
    asyncio.create_task(sincronizar_resultados())

async def sincronizar_resultados():
    while True:
        try:
            jogos = supabase.table("jogos").select("id, apiId").filter("result", "is", "null").execute().data
            if jogos:
                async with httpx.AsyncClient() as client:
                    headers = {"x-apisports-key": FOOTBALL_API_KEY}
                    for j in jogos:
                        if not j.get("apiId"): continue
                        r = await client.get(f"https://v3.football.api-sports.io/fixtures?id={j['apiId']}", headers=headers)
                        d = r.json()
                        if d.get("response"):
                            fix = d["response"][0]
                            if fix["fixture"]["status"]["short"] in ["FT", "AET", "PEN"]:
                                g = fix["goals"]
                                supabase.table("jogos").update({"result": {"home": str(g["home"]), "away": str(g["away"])}}).eq("id", j["id"]).execute()
        except: pass
        await asyncio.sleep(3600)