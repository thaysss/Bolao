from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import Dict
from datetime import datetime
from fastapi.responses import FileResponse
import httpx
import os
from supabase import create_client, Client
import asyncio

app = FastAPI()

# ---------------- CONFIGURAÇÕES E CHAVES ----------------

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://brbjlpcpiubtlneualrv.supabase.co") # A URL não é secreta, pode deixar
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "") 
FOOTBALL_API_KEY = os.getenv("FOOTBALL_API_KEY", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123") # admin123 fica só como um padrão provisório de teste

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# ---------------- MODELOS DE DADOS ----------------
class Aposta(BaseModel):
    nome: str
    whatsapp: str
    palpites: Dict[str, str]


class LoginRequest(BaseModel):
    senha: str

class NovoJogo(BaseModel):
    apiId: int = None
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
                
            h, a = int(resultado_oficial["home"]), int(resultado_oficial["away"])
            vencedor_real = "home" if h > a else "away" if a > h else "draw"
            
            if palpites.get(jogo_id) == vencedor_real:
                pontos += 1
        
        ranking.append({
            "nome": aposta["nome"],
            "pontos": pontos,
            "whatsapp": aposta["whatsapp"]
        })
    
    return sorted(ranking, key=lambda x: x["pontos"], reverse=True)

def verificar_admin(x_admin_token: str = Header(...)):
    """Verifica se o token enviado no cabeçalho bate com a senha do sistema"""
    if x_admin_token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Crachá inválido! Acesso negado.")


app = FastAPI() 
# ---------------- ENDPOINTS (ROTAS DA API) ----------------
@app.get("/")
async def pagina_inicial():
    """Quando alguém acessar o link do Railway, o Python entrega o site visual"""
    return FileResponse("index.html")

@app.get("/jogos-hoje")
async def buscar_jogos(data: str = None):
    """Busca jogos na API de Futebol e retorna ao frontend (Veio do seu banco.py)"""
    if not data:
        data = datetime.now().strftime("%Y-%m-%d")
        
    url = f"https://v3.football.api-sports.io/fixtures?date={data}&timezone=America/Sao_Paulo"
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        return response.json()

@app.post("/salvar-aposta")
async def salvar_aposta(aposta: Aposta):
    # ... resto do código continua igual ...
    """Recebe a aposta do frontend e salva REALMENTE no Supabase"""
    try:
        res = supabase.table("apostas").insert({
            "nome": aposta.nome,
            "whatsapp": aposta.whatsapp,
            "palpites": aposta.palpites
        }).execute()
        return {"status": "sucesso", "mensagem": "Aposta registrada com sucesso no banco de dados!"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/ranking-geral")
async def obter_ranking_geral():
    """Gera o ranking atualizado consultando o Supabase"""
    jogos = supabase.table("jogos").select("*").execute().data
    apostas = supabase.table("apostas").select("*").execute().data
    
    return calcular_ranking(jogos, apostas)

# ---------------- TAREFAS EM SEGUNDO PLANO ----------------

import asyncio # Adicione isso lá no topo do main.py junto com os outros imports

# ---------------- TAREFAS EM SEGUNDO PLANO (ATUALIZADO) ----------------

@app.on_event("startup")
async def iniciar_automacoes():
    """Esta função roda assim que o servidor liga no Railway"""
    # Dispara a função de sincronização para rodar no fundo
    asyncio.create_task(sincronizar_resultados_automatico())

async def sincronizar_resultados_automatico():
    """Loop infinito que atualiza os placares e dorme por 1 hora"""
    while True:
        try:
            jogos_pendentes = supabase.table("jogos").select("id, apiId").filter("result", "is", "null").execute().data
            
            if jogos_pendentes:
                async with httpx.AsyncClient() as client:
                    headers = {"x-apisports-key": FOOTBALL_API_KEY}
                    
                    for jogo in jogos_pendentes:
                        api_id = jogo.get("apiId")
                        if not api_id:
                            continue
                            
                        # Busca o resultado na API
                        response = await client.get(f"https://v3.football.api-sports.io/fixtures?id={api_id}", headers=headers)
                        dados = response.json()
                        
                        if dados.get("response"):
                            fixture = dados["response"][0]
                            status = fixture["fixture"]["status"]["short"]
                            
                            if status in ["FT", "AET", "PEN"]:
                                gols_casa = fixture["goals"]["home"]
                                gols_fora = fixture["goals"]["away"]
                                
                                supabase.table("jogos").update({
                                    "result": {"home": str(gols_casa), "away": str(gols_fora)}
                                }).eq("id", jogo["id"]).execute()
                                
            print("Sincronização de resultados finalizada com sucesso.")
        except Exception as e:
            print(f"Erro na sincronização: {e}")
        
        # O servidor 'dorme' nessa tarefa por 1 hora (3600 segundos) antes de repetir
        await asyncio.sleep(3600)



@app.post("/login-admin")
async def login_admin(req: LoginRequest):
    if req.senha == ADMIN_PASSWORD:
        # Se acertar a senha, devolvemos a própria senha para servir como 'Token' no Frontend
        return {"status": "sucesso", "token": ADMIN_PASSWORD}
    raise HTTPException(status_code=401, detail="Senha incorreta")


@app.post("/salvar-resultado", dependencies=[Depends(verificar_admin)])
async def salvar_resultado(jogo_id: int, gols_casa: int, gols_fora: int):
    # O Python SÓ chega nessa linha se o token estiver correto
    try:
        supabase.table("jogos").update({
            "result": {"home": str(gols_casa), "away": str(gols_fora)}
        }).eq("id", jogo_id).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

# ---------------- ROTAS PÚBLICAS ----------------

@app.get("/jogos")
async def listar_jogos():
    """Retorna todos os jogos cadastrados no banco para o frontend exibir"""
    res = supabase.table("jogos").select("*").order("datetime").execute()
    return res.data


@app.post("/adicionar-jogo", dependencies=[Depends(verificar_admin)])
async def adicionar_jogo(jogo: NovoJogo):
    """Admin adiciona um novo jogo ao bolão"""
    try:
        res = supabase.table("jogos").insert(jogo.model_dump()).execute()
        return {"status": "sucesso", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/remover-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def remover_jogo(jogo_id: str):
    """Admin remove um jogo do bolão"""
    try:
        supabase.table("jogos").delete().eq("id", jogo_id).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/alternar-status-jogo/{jogo_id}", dependencies=[Depends(verificar_admin)])
async def alternar_status_jogo(jogo_id: str, ativo: bool):
    """Admin pausa ou ativa um jogo (ex: fecha as apostas quando o jogo começa)"""
    try:
        supabase.table("jogos").update({"active": ativo}).eq("id", jogo_id).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/configuracoes")
async def obter_configuracoes():
    """Puxa as configurações do banco para todo mundo que abrir o site"""
    res = supabase.table("configuracoes").select("*").eq("id", 1).execute()
    if res.data:
        return res.data[0]
    return {}

@app.post("/salvar-configuracoes", dependencies=[Depends(verificar_admin)])
async def atualizar_configuracoes(config: Configuracoes):
    """Admin salva as configurações novas no banco"""
    try:
        supabase.table("configuracoes").update(config.model_dump()).eq("id", 1).execute()
        return {"status": "sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/obter-api-key", dependencies=[Depends(verificar_admin)])
async def obter_api_key():
    """Devolve a chave da API-Football salva no Railway para o painel Admin"""
    return {"api_key": FOOTBALL_API_KEY}