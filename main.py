from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict
from datetime import datetime
import httpx
import os
from supabase import create_client, Client
from fastapi_utils.tasks import repeat_every

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
    
# ---------------- ENDPOINTS (ROTAS DA API) ----------------

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

@app.post("/enviar-aposta")
async def salvar_aposta(aposta: Aposta):
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

@app.on_event("startup")
@repeat_every(seconds=60 * 60)  # Roda a cada 1 hora automaticamente
async def sincronizar_resultados_automatico():
    """Atualiza placares automaticamente buscando na API-Football"""
    jogos_pendentes = supabase.table("jogos").select("id, apiId").filter("result", "is", "null").execute().data
    
    if not jogos_pendentes:
        return 

    async with httpx.AsyncClient() as client:
        headers = {"x-apisports-key": FOOTBALL_API_KEY}
        
        for jogo in jogos_pendentes:
            api_id = jogo.get("apiId")
            if not api_id:
                continue
                
            try:
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
            except Exception as e:
                print(f"Erro ao atualizar jogo {api_id}: {e}")



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