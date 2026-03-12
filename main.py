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

# 1. Instância Única do App
app = FastAPI()

# 2. Configuração de CORS (Essencial para o site funcionar)
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
            resultado_oficial = jogo.get("result")
            
            if not resultado_oficial or resultado_oficial.get("home") == "":
                continue
                
            try:
                h, a = int(resultado_oficial["home"]), int(resultado_oficial["away"])
                vencedor_real = "home" if h > a else "away" if a > h else "draw"
                
                if palpites.get(jogo_id) == vencedor_real:
                    pontos += 1
            except: continue
        
        ranking.append({
            "nome": aposta["nome"],
            "pontos": pontos,
            "whatsapp": aposta["whatsapp"]
        })
    
    return sorted(ranking, key=lambda x: x["pontos"], reverse=True)

def verificar_admin(x_admin_token: str = Header(None)):
    """Verifica se o token enviado no cabeçalho bate com a senha do sistema"""
    if not x_admin_token or x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Acesso negado. Token inválido.")

# ---------------- ENDPOINTS (ROTAS DA API) ----------------

@app.get("/")
async def pagina_inicial():
    """Entrega o site visual (index.html deve estar na mesma pasta)"""
    return FileResponse("index.html")

@app.get("/jogos-hoje")
async def buscar_jogos(data: str = None):
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")
        
    url = f"https://v3.football.api-sports.io/fixtures?date={data}&timezone=America/Sao_Paulo"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        return response.json()

@app.get("/jogos")
async def listar_jogos():
    """Puxa os jogos salvos no banco para o frontend exibir"""
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    try:
        dados = aposta.model_dump()
        supabase.table("apostas").insert(dados).execute()
        return {"status": "sucesso"}
    except Exception as e:
        print(f"ERRO CRÍTICO NO BANCO: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ranking-geral")
async def obter_ranking_geral():
    jogos = supabase.table("jogos").select("*").execute().data
    apostas = supabase.table("apostas").select("*").execute().data
    return calcular_ranking(jogos, apostas)

# ---------------- ROTAS ADMIN ----------------

@app.post("/login-admin")
async def login_admin(req: LoginRequest):
    if req.senha == ADMIN_PASSWORD:
        return {"status": "sucesso", "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401, detail="Senha incorreta")

@app.get("/obter-api-key", dependencies=[Depends(verificar_admin)])
async def obter_api_key():
    return {"api_key": FOOTBALL_API_KEY}

@app.post("/adicionar-jogo", dependencies=[Depends(verificar_admin)])
async def adicionar_jogo(jogo: NovoJogo):
    try:
        supabase.table("jogos").insert(jogo.model_dump()).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/configuracoes")
async def obter_configuracoes():
    res = supabase.table("configuracoes").select("*").eq("id", 1).execute()
    return res.data[0] if res.data else {}

@app.post("/salvar-configuracoes", dependencies=[Depends(verificar_admin)])
async def atualizar_configuracoes(config: Configuracoes):
    try:
        supabase.table("configuracoes").update(config.model_dump()).eq("id", 1).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ---------------- TAREFAS EM SEGUNDO PLANO ----------------

@app.on_event("startup")
async def iniciar_automacoes():
    asyncio.create_task(sincronizar_resultados_automatico())

async def sincronizar_resultados_automatico():
    while True:
        try:
            jogos_pendentes = supabase.table("jogos").select("id, apiId").filter("result", "is", "null").execute().data
            if jogos_pendentes:
                async with httpx.AsyncClient() as client:
                    headers = {"x-apisports-key": FOOTBALL_API_KEY}
                    for jogo in jogos_pendentes:
                        api_id = jogo.get("apiId")
                        if not api_id: continue
                        
                        response = await client.get(f"https://v3.football.api-sports.io/fixtures?id={api_id}", headers=headers)
                        dados = response.json()
                        
                        if dados.get("response"):
                            fixture = dados["response"][0]
                            status = fixture["fixture"]["status"]["short"]
                            if status in ["FT", "AET", "PEN"]:
                                gols = fixture["goals"]
                                supabase.table("jogos").update({
                                    "result": {"home": str(gols["home"]), "away": str(gols["away"])}
                                }).eq("id", jogo["id"]).execute()
            print("Sincronização finalizada.")
        except Exception as e:
            print(f"Erro na sincronização: {e}")
        await asyncio.sleep(3600)