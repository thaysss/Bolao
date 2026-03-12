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


def calcular_ranking(jogos_oficiais, todas_apostas):
    ranking = []
    
    for aposta in todas_apostas:
        pontos = 0
        palpites = aposta.get("palpites", {})
        
        for jogo in jogos_oficiais:
            jogo_id = str(jogo["id"])
            resultado_real = jogo.get("result") # Ex: {"home": "2", "away": "1"}
            
            # Pula o jogo se ele ainda não tiver resultado oficial
            if not resultado_real or resultado_real.get("home") is None or resultado_real.get("home") == "":
                continue
            
            try:
                # 1. Descobre quem ganhou na vida real
                gols_h = int(resultado_real["home"])
                gols_a = int(resultado_real["away"])
                vencedor_real = "home" if gols_h > gols_a else "away" if gols_a > gols_h else "draw"
                
                # 2. Compara com o palpite do usuário para esse jogo específico
                meu_palpite = palpites.get(jogo_id)
                
                if meu_palpite == vencedor_real:
                    pontos += 1
            except Exception as e:
                print(f"Erro ao processar pontos do jogo {jogo_id}: {e}")
                continue
        
        # Adiciona o resumo desse apostador na lista
        ranking.append({
            "nome": aposta.get("nome", "Anônimo"),
            "pontos": pontos,
            "whatsapp": aposta.get("whatsapp", "")
        })
    
    # Ordena: quem tem mais pontos fica no topo (🥇)
    return sorted(ranking, key=lambda x: x["pontos"], reverse=True)

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
async def obter_ranking_geral():
    """Gera o ranking atualizado apenas com apostas PAGAS"""
    try:
        # Puxa todos os jogos para conferir os resultados
        jogos = supabase.table("jogos").select("*").execute().data
        
        # 🎯 O PULO DO GATO: Filtra apenas quem tem 'pago' como True
        apostas_pagas = supabase.table("apostas").select("*").eq("pago", True).execute().data
        
        if not apostas_pagas:
            return []
            
        # Chama a função de cálculo que criamos acima
        return calcular_ranking(jogos, apostas_pagas)
        
    except Exception as e:
        print(f"Erro ao gerar ranking: {e}")
        return []
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


@app.get("/admin/apostas", dependencies=[Depends(verificar_admin)])
async def listar_apostas_admin():
    """Lista TODAS as apostas para o admin conferir e aprovar"""
    res = supabase.table("apostas").select("*").order("created_at", desc=True).execute()
    return res.data

@app.put("/admin/aprovar-pagamento/{aposta_id}", dependencies=[Depends(verificar_admin)])
async def aprovar_pagamento(aposta_id: str, status: bool):
    """Muda o status de pagamento de uma aposta"""
    try:
        supabase.table("apostas").update({"pago": status}).eq("id", aposta_id).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))