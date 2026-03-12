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

# 1. Instância única do App
app = FastAPI()

# 2. Configuração de CORS (Essencial para o site não travar no navegador)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- CONFIGURAÇÕES E CHAVES ----------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://brbjlpcpiubtlneualrv.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") 
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------------- MODELOS DE DADOS ----------------
class Aposta(BaseModel):
    nome: str
    whatsapp: str
    palpites: Dict[str, str]

class LoginRequest(BaseModel):
    senha: str

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

# ---------------- FUNÇÕES DE APOIO ----------------
def calcular_ranking(jogos_oficiais, todas_apostas):
    ranking = []
    for aposta in todas_apostas:
        pontos = 0
        palpites = aposta.get("palpites", {})
        for jogo in jogos_oficiais:
            jogo_id = str(jogo["id"])
            res = jogo.get("result")
            if not res or res.get("home") == "" or res.get("home") is None:
                continue
            
            # Lógica de vencedor
            h, a = int(res["home"]), int(res["away"])
            vencedor_real = "home" if h > a else "away" if a > h else "draw"
            
            if palpites.get(jogo_id) == vencedor_real:
                pontos += 1
        
        ranking.append({"nome": aposta["nome"], "pontos": pontos, "whatsapp": aposta["whatsapp"]})
    return sorted(ranking, key=lambda x: x["pontos"], reverse=True)

def verificar_admin(x_admin_token: str = Header(None)):
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Acesso negado.")

# ---------------- ROTAS DA API ----------------

@app.get("/")
async def pagina_inicial():
    return FileResponse("index.html")

@app.get("/jogos")
async def listar_jogos():
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    try:
        supabase.table("apostas").insert(aposta.model_dump()).execute()
        return {"status": "sucesso"}
    except Exception as e:
        print(f"ERRO NO BANCO: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ranking-geral")
async def obter_ranking():
    jogos = supabase.table("jogos").select("*").execute().data
    apostas = supabase.table("apostas").select("*").execute().data
    return calcular_ranking(jogos, apostas)

@app.post("/login-admin")
async def login(req: LoginRequest):
    if req.senha == ADMIN_PASSWORD:
        return {"status": "sucesso", "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401)

@app.get("/obter-api-key", dependencies=[Depends(verificar_admin)])
async def get_key():
    return {"api_key": FOOTBALL_API_KEY}

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

@app.delete("/remover-jogo/{id}", dependencies=[Depends(verificar_admin)])
async def del_jogo(id: str):
    supabase.table("jogos").delete().eq("id", id).execute()
    return {"status": "sucesso"}

@app.on_event("startup")
async def startup():
    asyncio.create_task(sincronizar_resultados_automatico())

async def sincronizar_resultados_automatico():
    while True:
        try:
            jogos = supabase.table("jogos").select("id, apiId").filter("result", "is", "null").execute().data
            if jogos:
                async with httpx.AsyncClient() as client:
                    headers = {"x-apisports-key": FOOTBALL_API_KEY}
                    for j in jogos:
                        if not j.get("apiId"): continue
                        r = await client.get(f"https://v3.football.api-sports.io/fixtures?id={j['apiId']}", headers=headers)
                        dados = r.json()
                        if dados.get("response"):
                            fix = dados["response"][0]
                            if fix["fixture"]["status"]["short"] in ["FT", "AET", "PEN"]:
                                goals = fix["goals"]
                                supabase.table("jogos").update({"result": {"home": str(goals["home"]), "away": str(goals["away"])}}).eq("id", j["id"]).execute()
            print("Sync ok.")
        except Exception as e: print(f"Erro sync: {e}")
        await asyncio.sleep(3600)